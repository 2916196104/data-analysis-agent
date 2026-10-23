"""用于持久化数据和哈希的严格 JSON 解析与稳定序列化。"""

import hashlib
import json
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象包含重复键。")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("JSON 不接受 NaN 或 Infinity。")


def strict_json_loads(value):
    """解析 JSON 时拒绝重复键以及 NaN/Infinity 常量。"""
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def canonical_json(value) -> str:
    """以确定性方式序列化有限 JSON 值，同时保持现有哈希不变。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_sha256(value) -> str:
    """对规范 JSON 的 UTF-8 字节计算哈希。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_json(value: object, secret: str = "", *, indent: int | None = 2) -> str:
    """序列化前脱敏字符串值，同时保持 JSON 结构和数字不变。"""
    def scrub(item):
        if isinstance(item, str):
            return item.replace(secret, "[REDACTED]") if secret else item
        if isinstance(item, dict):
            return {key: scrub(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [scrub(child) for child in item]
        return item

    return json.dumps(scrub(value), ensure_ascii=False, indent=indent)


def write_json(path: Path, value: object, secret: str = "") -> None:
    """写入已脱敏密钥的 UTF-8 运行产物。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe_json(value, secret) + "\n", encoding="utf-8")
