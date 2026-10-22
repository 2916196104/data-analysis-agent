"""Strict JSON parsing and stable serialization for persisted data and hashes."""

import hashlib
import json


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
    """Parse JSON while rejecting duplicate keys and NaN/Infinity constants."""
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def canonical_json(value) -> str:
    """Serialize finite JSON values deterministically, preserving existing hashes."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_sha256(value) -> str:
    """Hash the UTF-8 bytes of canonical JSON."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
