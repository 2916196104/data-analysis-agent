"""显式的本地模型配置。"""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

# 保留之前的公共导入，以兼容现有调用。
from data_analysis_agent.core.serialization import safe_json, write_json


class ConfigurationError(ValueError):
    """必需的模型设置缺失或无效。"""


def read_settings(env_file: str | Path = ".env") -> dict[str, str]:
    """只读取指定的 dotenv 文件；进程环境变量优先。"""
    try:
        values = {key: value for key, value in dotenv_values(env_file, interpolate=False).items()
                  if value is not None}
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError("无法读取本地配置文件，请检查路径、权限和 UTF-8 编码。") from exc
    return {**values, **os.environ}


@dataclass(frozen=True)
class AgentSettings:
    model: str
    api_key: str = field(repr=False)
    base_url: str
    max_steps: int = 8
    max_tool_calls: int = 12
    timeout_seconds: float = 30
    max_retries: int = 1

    @classmethod
    def from_values(cls, values: dict[str, str]) -> "AgentSettings":
        """校验设置时不在错误消息中包含密钥值。"""
        required = ("LLM_MODEL_ID", "LLM_API_KEY", "LLM_BASE_URL")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ConfigurationError(f"请在本地 .env 配置：{', '.join(missing)}。")
        url = values["LLM_BASE_URL"].strip()
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError
            parsed.port
        except ValueError as exc:
            raise ConfigurationError("LLM_BASE_URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址。") from exc

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                result = int(values.get(name, str(default)))
                if not minimum <= result <= maximum:
                    raise ValueError
            except ValueError as exc:
                raise ConfigurationError(f"{name} 必须是 {minimum} 至 {maximum} 的整数。") from exc
            return result

        try:
            timeout = float(values.get("LLM_TIMEOUT_SECONDS", "30"))
            if not math.isfinite(timeout) or not 0 < timeout <= 300:
                raise ValueError
        except ValueError as exc:
            raise ConfigurationError("LLM_TIMEOUT_SECONDS 必须大于 0 且不超过 300。") from exc
        return cls(
            model=values["LLM_MODEL_ID"].strip(),
            api_key=values["LLM_API_KEY"].strip(),
            base_url=url,
            max_steps=integer("AGENT_MAX_STEPS", 8, 1, 30),
            max_tool_calls=integer("AGENT_MAX_TOOL_CALLS", 12, 1, 40),
            timeout_seconds=timeout,
            max_retries=integer("LLM_MAX_RETRIES", 1, 0, 3),
        )


@dataclass(frozen=True)
class LabelQueueSettings:
    redis_url: str = field(repr=False)
    quota_scope: str
    rpm: int
    tpm: int
    concurrency: int = 2
    chunk_size: int = 100
    batch_size: int = 4
    lease_seconds: int = 120
    max_calls: int = 6
    max_retries: int = 2
    backoff_seconds: int = 10
    stream: str = "review-labels"
    group: str = "label-workers"
    coordinator_timeout_seconds: int = 3600

    @classmethod
    def from_values(cls, values, *, timeout_seconds=30):
        required = ("LABEL_REDIS_URL", "LABEL_QUOTA_SCOPE", "LABEL_RPM", "LABEL_TPM")
        if any(not values.get(name, "").strip() for name in required):
            raise ConfigurationError("请配置 LABEL_REDIS_URL、LABEL_QUOTA_SCOPE、LABEL_RPM 和 LABEL_TPM；不推测服务商额度。")
        if urlsplit(values["LABEL_REDIS_URL"]).scheme not in {"redis", "rediss"}:
            raise ConfigurationError("LABEL_REDIS_URL 必须是 redis:// 或 rediss:// 地址。")
        def number(name, default, low, high):
            try:
                value = int(values.get(name, str(default)))
                if not low <= value <= high:
                    raise ValueError
                return value
            except ValueError:
                raise ConfigurationError(f"{name} 必须是 {low} 至 {high} 的整数。") from None
        lease = number("LABEL_LEASE_SECONDS", max(120, int(timeout_seconds * 2 + 30)), 30, 3600)
        if lease <= timeout_seconds + 10:
            raise ConfigurationError("LABEL_LEASE_SECONDS 必须至少比请求超时长 10 秒。")
        return cls(redis_url=values["LABEL_REDIS_URL"], quota_scope=values["LABEL_QUOTA_SCOPE"].strip(),
                   rpm=number("LABEL_RPM", 0, 1, 1000000000), tpm=number("LABEL_TPM", 0, 1, 1000000000000),
                   concurrency=number("LABEL_MAX_CONCURRENCY", 2, 1, 100),
                   chunk_size=number("LABEL_DB_CHUNK_SIZE", 100, 1, 1000), batch_size=number("LABEL_BATCH_SIZE", 4, 1, 8),
                   lease_seconds=lease, max_calls=number("LABEL_MAX_CALLS_PER_REVIEW", 6, 1, 100),
                   max_retries=number("LABEL_MAX_RETRIES", 2, 0, 20),
                   backoff_seconds=number("LABEL_BACKOFF_SECONDS", 10, 1, 3600),
                   coordinator_timeout_seconds=number("LABEL_COORDINATOR_TIMEOUT_SECONDS", 3600, 1, 604800),
                   stream=values.get("LABEL_STREAM", "review-labels"), group=values.get("LABEL_GROUP", "label-workers"))
