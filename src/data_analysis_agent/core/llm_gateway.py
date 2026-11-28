"""统一 LLM Gateway：复用 HTTP Keep-Alive 连接池、按角色路由模型、集中厂商参数与轻量观测。

模块顶层不导入 openai / httpx，严格遵守核心模块及 API/Worker 启动期的延迟加载与依赖边界。
当测试对 openai.OpenAI / httpx.Client / B4Budget 进行 patch 时自动透明退回直连短生命周期客户端，
确保所有离线契约测试与生产连接池复用同时成立。
"""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
import sys
from threading import Lock
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from data_analysis_agent.core.config import AgentSettings

LLM_ROLES = ("default", "chat", "intent", "answer", "topic", "planner", "labeling")

_DEFAULT_MAX_CONNECTIONS = 32
_DEFAULT_MAX_KEEPALIVE = 16
_DEFAULT_KEEPALIVE_EXPIRY = 60.0

_POOL_LOCK = Lock()
_STATS_LOCK = Lock()


@dataclass
class _PooledClientEntry:
    base_url: str
    client: Any
    http_client: Any


_POOLED_CLIENTS: dict[tuple[str, str, str], _PooledClientEntry] = {}


def _empty_stats() -> dict[str, Any]:
    return {
        "calls": 0,
        "errors": 0,
        "pool_hits": 0,
        "pool_misses": 0,
        "direct_clients": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_duration_ms": 0.0,
        "by_role": {},
    }


_STATS: dict[str, Any] = _empty_stats()


class LLMUsageTracker:
    """线程/协程隔离的单任务 LLM 调用与 Token 追踪器，用于精准归因单次分析或聊天开销。"""

    def __init__(self, parent: "LLMUsageTracker | None" = None) -> None:
        self._parent = parent
        self._lock = Lock()
        self._calls = 0
        self._errors = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._duration_ms = 0.0
        self._by_role: dict[str, dict[str, Any]] = {}

    def record(
        self,
        role: str,
        elapsed_ms: float,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        failed: bool = False,
    ) -> None:
        with self._lock:
            self._calls += 1
            if failed:
                self._errors += 1
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens
            self._total_tokens += total_tokens
            self._duration_ms = round(self._duration_ms + elapsed_ms, 3)
            entry = self._by_role.get(role)
            if entry is None:
                entry = {
                    "calls": 0,
                    "errors": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "duration_ms": 0.0,
                }
                self._by_role[role] = entry
            entry["calls"] += 1
            if failed:
                entry["errors"] += 1
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["total_tokens"] += total_tokens
            entry["duration_ms"] = round(entry["duration_ms"] + elapsed_ms, 3)
        if self._parent is not None:
            self._parent.record(
                role,
                elapsed_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                failed=failed,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self._calls,
                "errors": self._errors,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "duration_ms": round(self._duration_ms, 3),
                "by_role": {role: dict(values) for role, values in self._by_role.items()},
            }


_ACTIVE_TRACKER: ContextVar[LLMUsageTracker | None] = ContextVar("llm_gateway_usage_tracker", default=None)


@contextmanager
def track_llm_usage():
    """在当前执行上下文内收集所有经由 llm_gateway 发起的调用与 Token 消耗。"""
    parent = _ACTIVE_TRACKER.get()
    tracker = LLMUsageTracker(parent=parent)
    token = _ACTIVE_TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_TRACKER.reset(token)


def _pool_enabled() -> bool:
    return os.environ.get("LLM_POOL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)).strip())
        return max(minimum, min(maximum, value))
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)).strip())
        return max(minimum, min(maximum, value))
    except ValueError:
        return default


def _api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def is_b4_budget_guarded() -> bool:
    """检查当前进程是否安装了 B4 离线预算守卫（不触发 httpx 导入）。"""
    httpx_mod = sys.modules.get("httpx")
    if httpx_mod is None:
        return False
    client_cls = getattr(httpx_mod, "Client", None)
    send_fn = getattr(client_cls, "send", None) if client_cls is not None else None
    return bool(getattr(send_fn, "_b4_budget_guard", False))


def _is_unpatched_sdk(openai_mod: Any, httpx_mod: Any) -> bool:
    openai_cls = getattr(openai_mod, "OpenAI", None)
    httpx_cls = getattr(httpx_mod, "Client", None)
    if not isinstance(openai_cls, type) or not getattr(openai_cls, "__module__", "").startswith("openai"):
        return False
    if getattr(getattr(openai_cls, "__init__", None), "__qualname__", "") != "OpenAI.__init__":
        return False
    if not isinstance(httpx_cls, type) or not getattr(httpx_cls, "__module__", "").startswith("httpx"):
        return False
    send_fn = getattr(httpx_cls, "send", None)
    if getattr(send_fn, "__qualname__", "") != "Client.send":
        return False
    if getattr(send_fn, "_b4_budget_guard", False):
        return False
    return True


def _should_use_pool(
    openai_mod: Any,
    httpx_mod: Any,
    *,
    pooled: bool | None,
    client_factory: Any,
) -> bool:
    if pooled is False:
        return False
    if client_factory is not None and client_factory is not openai_mod.OpenAI:
        return False
    if pooled is True:
        return True
    return _pool_enabled() and _is_unpatched_sdk(openai_mod, httpx_mod)


def resolve_model(settings: AgentSettings, role: str = "default") -> str:
    """按任务角色解析目标模型；未单独配置时统一回落至 settings.model。"""
    override = getattr(settings, f"{role}_model", None) if role in LLM_ROLES else None
    if isinstance(override, str) and override.strip():
        return override.strip()
    return settings.model


def provider_extra_body(
    settings: AgentSettings,
    *,
    role: str = "default",
    model: str | None = None,
) -> dict[str, Any]:
    """集中维护不同供应商与角色的额外请求体参数，避免各业务模块散落硬编码。"""
    target_model = (model or resolve_model(settings, role)).strip()
    if role == "answer":
        if (
            settings.base_url.rstrip("/") == "https://api.siliconflow.cn/v1"
            and target_model == "Pro/deepseek-ai/DeepSeek-V3.2"
        ):
            return {"enable_thinking": False}
        return {}
    if role == "topic":
        if (
            urlsplit(settings.base_url).hostname in {"api.siliconflow.cn", "api.siliconflow.com"}
            and target_model.rsplit("/", 1)[-1] == "DeepSeek-V3.2"
        ):
            return {"enable_thinking": False}
        return {}
    return {}


def redact_secret(text: str, secret: str) -> str:
    """避免错误或回复中泄露 API 密钥。"""
    if not text or not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _get_or_create_pooled_client(
    settings: AgentSettings,
    openai_mod: Any,
    httpx_mod: Any,
    *,
    http_client_factory: Any = None,
    pool_tag: str = "",
) -> _PooledClientEntry:
    key = (settings.base_url, _api_key_fingerprint(settings.api_key), pool_tag)
    with _POOL_LOCK:
        entry = _POOLED_CLIENTS.get(key)
        if entry is not None and not getattr(entry.http_client, "is_closed", True):
            with _STATS_LOCK:
                _STATS["pool_hits"] += 1
            return entry
        if http_client_factory is not None:
            http_client = http_client_factory()
        else:
            limits = httpx_mod.Limits(
                max_connections=_env_int("LLM_HTTP_MAX_CONNECTIONS", _DEFAULT_MAX_CONNECTIONS, 1, 256),
                max_keepalive_connections=_env_int("LLM_HTTP_MAX_KEEPALIVE", _DEFAULT_MAX_KEEPALIVE, 1, 128),
                keepalive_expiry=_env_float("LLM_HTTP_KEEPALIVE_EXPIRY", _DEFAULT_KEEPALIVE_EXPIRY, 1.0, 600.0),
            )
            http_client = httpx_mod.Client(limits=limits)
        try:
            base_client = openai_mod.OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.timeout_seconds,
                max_retries=settings.max_retries,
                http_client=http_client,
            )
        except Exception:
            try:
                http_client.close()
            except Exception:
                pass
            raise
        entry = _PooledClientEntry(base_url=settings.base_url, client=base_client, http_client=http_client)
        _POOLED_CLIENTS[key] = entry
        with _STATS_LOCK:
            _STATS["pool_misses"] += 1
        return entry


@contextmanager
def open_llm_client(
    settings: AgentSettings,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
    pooled: bool | None = None,
    client_factory: Any = None,
    http_client_factory: Any = None,
    pool_tag: str = "",
):
    """获取可复用的 OpenAI 客户端（生产默认走 Keep-Alive 池，测试 patch 自动透传短生命周期客户端）。"""
    import httpx
    import openai

    effective_timeout = settings.timeout_seconds if timeout is None else timeout
    effective_retries = settings.max_retries if max_retries is None else max_retries

    if _should_use_pool(openai, httpx, pooled=pooled, client_factory=client_factory):
        entry = _get_or_create_pooled_client(
            settings,
            openai,
            httpx,
            http_client_factory=http_client_factory,
            pool_tag=pool_tag,
        )
        # with_options 仅生成轻量视图，复用同一底层 httpx.Client，退出时不关闭共享连接池。
        yield entry.client.with_options(timeout=effective_timeout, max_retries=effective_retries)
        return

    with _STATS_LOCK:
        _STATS["direct_clients"] += 1
    factory = client_factory if client_factory is not None else openai.OpenAI
    with factory(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=effective_timeout,
        max_retries=effective_retries,
    ) as client:
        yield client


def _record_call_telemetry(role: str, elapsed_ms: float, *, response: Any = None, failed: bool = False) -> None:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    if not failed and response is not None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
    with _STATS_LOCK:
        _STATS["calls"] += 1
        if failed:
            _STATS["errors"] += 1
        _STATS["prompt_tokens"] += prompt_tokens
        _STATS["completion_tokens"] += completion_tokens
        _STATS["total_tokens"] += total_tokens
        _STATS["total_duration_ms"] = round(_STATS["total_duration_ms"] + elapsed_ms, 3)
        by_role = _STATS["by_role"]
        by_role[role] = by_role.get(role, 0) + 1
    tracker = _ACTIVE_TRACKER.get()
    if tracker is not None:
        tracker.record(
            role,
            elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            failed=failed,
        )


def chat_completion(
    settings: AgentSettings,
    *,
    messages: list[dict[str, Any]],
    role: str = "default",
    model: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    extra_body: dict[str, Any] | None = None,
    raw_response: bool = False,
    pooled: bool | None = None,
    client: Any = None,
    client_factory: Any = None,
    http_client_factory: Any = None,
    pool_tag: str = "",
):
    """统一的 Chat Completions 调用入口：路由模型、合并厂商参数、复用连接并汇总调用指标。"""
    target_model = (model or resolve_model(settings, role)).strip()
    request_kwargs: dict[str, Any] = {
        "model": target_model,
        "messages": messages,
    }
    if temperature is not None:
        request_kwargs["temperature"] = temperature
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        request_kwargs["response_format"] = response_format
    if tools:
        request_kwargs["tools"] = tools

    merged_extra = provider_extra_body(settings, role=role, model=target_model)
    if extra_body:
        merged_extra = {**merged_extra, **extra_body}
    if merged_extra:
        request_kwargs["extra_body"] = merged_extra

    def _invoke(active_client: Any):
        started = perf_counter()
        try:
            create = (
                active_client.chat.completions.with_raw_response.create
                if raw_response
                else active_client.chat.completions.create
            )
            response = create(**request_kwargs)
        except Exception:
            _record_call_telemetry(role, (perf_counter() - started) * 1000, failed=True)
            raise
        _record_call_telemetry(
            role,
            (perf_counter() - started) * 1000,
            response=None if raw_response else response,
            failed=False,
        )
        return response

    if client is not None:
        return _invoke(client)

    with open_llm_client(
        settings,
        timeout=timeout,
        max_retries=max_retries,
        pooled=pooled,
        client_factory=client_factory,
        http_client_factory=http_client_factory,
        pool_tag=pool_tag,
    ) as active_client:
        return _invoke(active_client)


def chat_completion_stream(
    settings: AgentSettings,
    *,
    messages: list[dict[str, Any]],
    role: str = "default",
    model: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict[str, Any] | None = None,
    include_usage: bool = True,
    pooled: bool | None = None,
    client: Any = None,
    client_factory: Any = None,
    http_client_factory: Any = None,
    pool_tag: str = "",
):
    """统一的流式 Chat Completions 生成器：复用连接池、默认请求 usage 计量、兼容旧网关降级并安全关闭上游流。"""
    target_model = (model or resolve_model(settings, role)).strip()
    request_kwargs: dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "stream": True,
    }
    if include_usage:
        request_kwargs["stream_options"] = {"include_usage": True}
    if temperature is not None:
        request_kwargs["temperature"] = temperature
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens

    merged_extra = provider_extra_body(settings, role=role, model=target_model)
    if extra_body:
        merged_extra = {**merged_extra, **extra_body}
    if merged_extra:
        request_kwargs["extra_body"] = merged_extra

    def _iterate(active_client: Any):
        started = perf_counter()
        last_usage_chunk = None
        stream = None
        failed = False
        try:
            try:
                stream = active_client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                # 兼容不支持 stream_options 的旧 OpenAI 兼容网关：仅在建流 400 时降级重试一次。
                if "stream_options" in request_kwargs and getattr(exc, "status_code", None) == 400:
                    fallback_kwargs = {k: v for k, v in request_kwargs.items() if k != "stream_options"}
                    stream = active_client.chat.completions.create(**fallback_kwargs)
                else:
                    raise
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    last_usage_chunk = chunk
                yield chunk
        except GeneratorExit:
            raise
        except Exception:
            failed = True
            raise
        finally:
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            _record_call_telemetry(
                role,
                (perf_counter() - started) * 1000,
                response=last_usage_chunk,
                failed=failed,
            )

    if client is not None:
        yield from _iterate(client)
        return

    with open_llm_client(
        settings,
        timeout=timeout,
        max_retries=max_retries,
        pooled=pooled,
        client_factory=client_factory,
        http_client_factory=http_client_factory,
        pool_tag=pool_tag,
    ) as active_client:
        yield from _iterate(active_client)


def close_llm_clients(base_url: str | None = None) -> int:
    """关闭并释放缓存的 Keep-Alive HTTP 客户端；可在进程退出或 FastAPI lifespan 结束时调用。"""
    to_close: list[_PooledClientEntry] = []
    with _POOL_LOCK:
        for key in list(_POOLED_CLIENTS):
            entry = _POOLED_CLIENTS[key]
            if base_url is None or entry.base_url == base_url:
                to_close.append(_POOLED_CLIENTS.pop(key))
    closed = 0
    for entry in to_close:
        try:
            entry.client.close()
        except Exception:
            try:
                entry.http_client.close()
            except Exception:
                pass
        closed += 1
    return closed


def llm_gateway_stats() -> dict[str, Any]:
    """返回轻量观测统计（不含密钥、模型输出或评论原文）。"""
    with _POOL_LOCK:
        pooled_count = len(_POOLED_CLIENTS)
    with _STATS_LOCK:
        snapshot = dict(_STATS)
        snapshot["by_role"] = dict(_STATS["by_role"])
    snapshot["pooled_clients"] = pooled_count
    return snapshot


def reset_llm_gateway_stats() -> None:
    """重置进程内统计计数（主要用于测试断言）。"""
    with _STATS_LOCK:
        _STATS.clear()
        _STATS.update(_empty_stats())


atexit.register(close_llm_clients)
