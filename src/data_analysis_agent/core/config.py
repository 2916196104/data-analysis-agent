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
