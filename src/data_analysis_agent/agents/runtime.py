"""销售与评论 Agent 共用的原生 OpenAI SDK 运行器、调用预算及结果校验。

已彻底移除对 hello-agents 运行时及私有属性 `_adapter._client` 的依赖，
所有模型调用统一走 core.llm_gateway（支持连接池复用、按角色路由与观测统计）。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import inspect
import json
import re
from typing import Any

from data_analysis_agent.core.config import AgentSettings, ConfigurationError
from data_analysis_agent.core.llm_gateway import chat_completion, open_llm_client, resolve_model
from data_analysis_agent.sales.loader import SalesDataError


class AgentExecutionError(RuntimeError):
    """Agent 未在调用预算内产出可核验的结果。"""


class FinalResponseFormatError(AgentExecutionError):
    """模型最终响应需要一次受控的 JSON 格式纠正。"""


class BudgetExceededError(RuntimeError):
    """超过单次 Agent 运行允许的模型调用次数预算。"""


@dataclass
class RunBudget:
    max_calls: int
    calls: int = 0

    def consume(self) -> int:
        if self.calls >= self.max_calls:
            raise BudgetExceededError("达到模型调用上限。")
        self.calls += 1
        return self.calls


@dataclass
class AgentEvent:
    data: dict[str, Any]


@dataclass
class AgentRunResult:
    status: str = "running"
    model_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)


def _emit_event(callback: Any, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    outcome = callback(AgentEvent(data=payload))
    if not inspect.isawaitable(outcome):
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(outcome)
        return
    # 若已处于事件循环内，先尝试在当前线程同步推进无挂起协程（保持 SQLite 线程亲和性）。
    if inspect.iscoroutine(outcome):
        try:
            outcome.send(None)
        except StopIteration:
            return
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, outcome).result()


class GatewayLLM:
    """绑定运行预算与统一 LLM Gateway 的客户端包装器，支持 with 上下文管理。"""

    def __init__(self, settings: AgentSettings, *, role: str = "planner"):
        self.settings = settings
        self.role = role
        self.model = resolve_model(settings, role)
        self.budget = RunBudget(max_calls=settings.max_steps)
        self._client_cm: Any = None
        self._client: Any = None

    def __enter__(self) -> "GatewayLLM":
        self._client_cm = open_llm_client(
            self.settings,
            timeout=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
        )
        self._client = self._client_cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        cm = self._client_cm
        self._client_cm = None
        self._client = None
        if cm is not None:
            cm.__exit__(exc_type, exc, tb)

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None):
        self.budget.consume()
        return chat_completion(
            self.settings,
            role=self.role,
            model=self.model,
            messages=messages,
            timeout=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
            temperature=0,
            max_tokens=2048,
            tools=tools or None,
            client=self._client,
        )


class ToolLoopAgent:
    """原生 OpenAI function-calling 多步工具调度循环，严格在当前线程执行工具以保证事务一致。"""

    def __init__(
        self,
        name: str,
        llm: GatewayLLM,
        *,
        system_prompt: str,
        tool_registry: Any,
        max_tool_iterations: int,
    ):
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations
        self.last_run: AgentRunResult | None = None
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def _tool_schemas(self) -> list[dict[str, Any]]:
        if self.tool_registry is None:
            return []
        return [tool.to_openai_schema() for tool in self.tool_registry.get_all_tools()]

    def run(self, prompt: str, *, on_step: Any = None, on_tool_call: Any = None) -> str:
        # 每次调用前重置当次统计，防止格式纠错重试触发预算异常时在调用方 finally 中重复累加。
        self.last_run = AgentRunResult(status="running", model_calls=0, usage={})
        self._messages.append({"role": "user", "content": prompt})
        tool_schemas = self._tool_schemas()
        step = 0

        while True:
            response = self.llm.complete(self._messages, tools=tool_schemas or None)
            step += 1
            self.last_run.model_calls += 1

            usage_obj = getattr(response, "usage", None)
            usage_dict: dict[str, int] | None = None
            if usage_obj is not None:
                prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage_obj, "total_tokens", 0) or (prompt_tokens + completion_tokens))
                usage_dict = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
                for key, value in usage_dict.items():
                    self.last_run.usage[key] = self.last_run.usage.get(key, 0) + value

            choices = getattr(response, "choices", None) or []
            if not choices:
                self.last_run.status = "incomplete"
                return ""
            choice = choices[0]
            message = choice.message
            finish_reason = getattr(choice, "finish_reason", None)
            raw_tool_calls = list(getattr(message, "tool_calls", None) or [])

            _emit_event(
                on_step,
                {
                    "step": step,
                    "usage": usage_dict,
                    "finish_reason": finish_reason,
                    "tool_count": len(raw_tool_calls),
                },
            )

            if raw_tool_calls:
                self._messages.append(
                    {
                        "role": "assistant",
                        "content": getattr(message, "content", None),
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": getattr(tc, "type", "function") or "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments or "{}",
                                },
                            }
                            for tc in raw_tool_calls
                        ],
                    }
                )
                for tc in raw_tool_calls:
                    tool_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                    except (TypeError, ValueError):
                        parsed_args = raw_args
                    _emit_event(
                        on_tool_call,
                        {
                            "name": tool_name,
                            "id": tc.id,
                            "arguments": parsed_args,
                        },
                    )
                    tool_input = parsed_args if isinstance(parsed_args, dict) else raw_args
                    tool_response = self.tool_registry.execute_tool(tool_name, tool_input)
                    tool_content = (
                        json.dumps(tool_response.to_dict(), ensure_ascii=False)
                        if hasattr(tool_response, "to_dict")
                        else str(tool_response)
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tool_name,
                            "content": tool_content,
                        }
                    )
                if step >= self.max_tool_iterations and self.llm.budget.calls < self.llm.budget.max_calls:
                    self.last_run.status = "max_iterations"
                    return ""
                continue

            content = getattr(message, "content", None) or ""
            self._messages.append({"role": "assistant", "content": content})
            self.last_run.status = "completed" if finish_reason == "stop" else (finish_reason or "incomplete")
            return content


def create_llm(settings: AgentSettings) -> GatewayLLM:
    return GatewayLLM(settings, role="planner")


def build_agent(
    settings: AgentSettings,
    context,
    llm: GatewayLLM,
    *,
    system_prompt: str,
    name: str = "sales_analyst",
    tool_registry=None,
) -> ToolLoopAgent:
    from data_analysis_agent.agents.tools import create_tool_registry

    registry = tool_registry if tool_registry is not None else create_tool_registry(context)
    return ToolLoopAgent(
        name,
        llm,
        system_prompt=system_prompt,
        tool_registry=registry,
        max_tool_iterations=settings.max_steps,
    )


def validate_final_result(text: str, context) -> tuple[str, list[str]]:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        final = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise FinalResponseFormatError("模型最终答复不是有效 JSON，无法确认任务完成。") from exc
    try:
        if not isinstance(final, dict) or set(final) != {"status", "result_ids"}:
            raise ValueError
        status, result_ids = final["status"], final["result_ids"]
        if status not in {"ok", "unsupported"} or not isinstance(result_ids, list) or not result_ids:
            raise ValueError
        if any(not isinstance(result_id, str) for result_id in result_ids):
            raise ValueError
        if len(result_ids) != len(set(result_ids)):
            raise ValueError
        for result_id in result_ids:
            context.get_result(result_id)
        if status == "unsupported" and "overview" not in result_ids:
            raise ValueError
    except (TypeError, ValueError, KeyError) as exc:
        raise AgentExecutionError("模型未返回有效的已计算结果引用，无法确认任务完成。") from exc
    return status, result_ids


def execution_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ConfigurationError):
        return "configuration_error", str(exc)
    if isinstance(exc, SalesDataError):
        return "data_error", str(exc)
    if isinstance(exc, FileNotFoundError):
        return "file_not_found", "输入或映射文件不存在，请检查 --input 和 --column-map 路径。"
    if isinstance(exc, AgentExecutionError):
        return "agent_error", str(exc)
    causes, cause = set(), exc
    while cause is not None and id(cause) not in causes:
        causes.add(id(cause))
        name = type(cause).__name__.lower()
        if "timeout" in name:
            return "timeout", "模型或工具调用超时；本次任务未确认完成。"
        if "budgetexceeded" in name:
            return "call_limit", "达到模型调用上限；本次任务未确认完成。"
        if "authentication" in name:
            return "authentication_error", "模型服务认证失败，请检查本地 API 密钥配置。"
        cause = cause.__cause__ or cause.__context__
    if isinstance(exc, OSError):
        return "file_error", "文件读写失败，请检查输入文件和输出目录的权限。"
    return "model_error", "模型或工具调用失败，请检查服务地址、模型名称及原生工具调用支持。"
