"""销售与评论 Agent 共用的 HelloAgents 运行配置、预算及结果校验。"""

import json
import re

from data_analysis_agent.core.config import AgentSettings, ConfigurationError
from data_analysis_agent.sales.loader import SalesDataError


class AgentExecutionError(RuntimeError):
    """Agent 未在调用预算内产出可核验的结果。"""


class FinalResponseFormatError(AgentExecutionError):
    """模型最终响应需要一次受控的 JSON 格式纠正。"""


def create_llm(settings: AgentSettings):
    from hello_agents import HelloAgentsLLM
    from hello_agents.core.budget import RunBudget
    from openai import OpenAI

    llm = HelloAgentsLLM(
        model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
        timeout=settings.timeout_seconds, temperature=0, max_tokens=2048,
        provider="openai", budget=RunBudget(max_calls=settings.max_steps),
    )
    # 当前固定版本没有注入 SDK client 的公开参数；重试必须交给 SDK client，
    # 不能塞进 completion 参数，因此集中保留在这里。
    llm._adapter._client = OpenAI(
        api_key=settings.api_key, base_url=settings.base_url,
        timeout=settings.timeout_seconds, max_retries=settings.max_retries,
    )
    return llm


def build_agent(settings: AgentSettings, context, llm, *, system_prompt,
                 name="sales_analyst", tool_registry=None):
    from hello_agents import Config, SimpleAgent
    from hello_agents.context.token_counter import TokenCounter
    from hello_agents.core.components import AgentComponents

    from data_analysis_agent.agents.tools import create_tool_registry

    class LocalTokenCounter(TokenCounter):
        def _get_encoding(self):
            # 使用框架的字符估算，避免首次运行下载 tokenizer。
            return None

    config = Config(
        trace_enabled=False, session_enabled=False, skills_enabled=False,
        subagent_enabled=False, todowrite_enabled=False, devlog_enabled=False,
        enable_smart_compression=False,
        tool_output_dir=str(context.run_dir / "tool-output"),
        llm_async_timeout=settings.timeout_seconds * (settings.max_retries + 1) + 10,
        tool_async_timeout=30,
    )
    return SimpleAgent(
        name, llm, system_prompt=system_prompt, config=config,
        tool_registry=tool_registry if tool_registry is not None else create_tool_registry(context),
        max_tool_iterations=settings.max_steps,
        components=AgentComponents(token_counter=LocalTokenCounter(settings.model),
                                   session_store=None, skill_loader=None),
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


