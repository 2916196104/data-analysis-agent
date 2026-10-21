"""Streaming Amazon Reviews 2023 JSONL parsing and record validation."""

import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SOURCE = "amazon_reviews_2023"
DATASET_VERSION = "amazon-reviews-2023"


class ReviewDataError(ValueError):
    """A review input or database does not satisfy the import contract."""


@dataclass(frozen=True)
class JsonlLine:
    number: int
    raw: bytes
    text: str | None
    data: dict | None
    error: str | None


def file_sha256(path: Path) -> str:
    """Hash original file bytes, including compressed bytes for gzip inputs."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象包含重复键。")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("JSON 不接受 NaN 或 Infinity。")


def iter_jsonl(path: str | Path) -> Iterator[JsonlLine]:
    """Yield one physical line at a time, preserving bad lines for diagnostics.

    A blank line has neither data nor error. JSON syntax/encoding failures are
    row errors; file and decompression errors propagate to roll back the import.
    """
    path = Path(path)
    if not (path.name.lower().endswith(".jsonl") or path.name.lower().endswith(".jsonl.gz")):
        raise ReviewDataError("输入必须是 .jsonl 或 .jsonl.gz 文件。")
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rb") as handle:
        for number, raw in enumerate(handle, start=1):
            text = None
            try:
                text = raw.decode("utf-8-sig" if number == 1 else "utf-8").rstrip("\r\n")
                if not text.strip():
                    yield JsonlLine(number, raw, text, None, None)
                    continue
                data = json.loads(text, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
                if not isinstance(data, dict):
                    raise ValueError("每行必须是一个 JSON 对象。")
                # Reject escaped lone surrogates and overflowing floats before SQLite encoding.
                json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except UnicodeError:
                yield JsonlLine(number, raw, text, None, "记录必须使用 UTF-8 编码。")
            except (ValueError, RecursionError):
                yield JsonlLine(number, raw, text, None, "JSON 无效、存在重复键或不是对象。")
            else:
                yield JsonlLine(number, raw, text, data, None)


def content_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(data: dict, field: str, *, required: bool = False) -> str:
    value = data.get(field, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ReviewDataError(f"{field} 必须是{'非空' if required else ''}文本。")
    return value


def normalize_review(data: dict) -> dict:
    """Preserve review text; normalize Amazon millisecond timestamps to UTC."""
    product_id = _text(data, "parent_asin", required=True).strip()
    text, title = _text(data, "text"), _text(data, "title")
    timestamp = data.get("timestamp", data.get("sort_timestamp"))
    if ("timestamp" in data and "sort_timestamp" in data
            and data["timestamp"] != data["sort_timestamp"]):
        raise ReviewDataError("timestamp 与 sort_timestamp 不一致。")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ReviewDataError("timestamp 必须是 Unix 毫秒整数。")
    try:
        published = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=timestamp)
    except OverflowError as exc:
        raise ReviewDataError("timestamp 超出有效范围。") from exc
    if not 1996 <= published.year <= 2023:
        raise ReviewDataError("Amazon Reviews 2023 时间须在 1996—2023 年，单位为毫秒。")
    rating = data.get("rating")
    if rating is not None:
        if (isinstance(rating, bool) or not isinstance(rating, (int, float))
                or not 1 <= rating <= 5 or not math.isfinite(rating)):
            raise ReviewDataError("rating 必须为空或 1—5 之间的有限数字。")
        rating = float(rating)
    return {
        "product_id": product_id, "text": text, "title": title, "rating": rating,
        "published_at": published.isoformat(timespec="milliseconds"),
        "timestamp": timestamp, "asin": _text(data, "asin").strip(),
        "user_id": _text(data, "user_id").strip(),
    }


def normalize_product(data: dict) -> dict:
    details = data.get("details")
    if details is None:
        details = {}
    if not isinstance(details, dict):
        raise ReviewDataError("details 必须是对象或空值。")
    brand = details.get("Brand", "")
    if brand is None:
        brand = ""
    if not isinstance(brand, str):
        raise ReviewDataError("details.Brand 必须是文本或空值。")
    return {
        "product_id": _text(data, "parent_asin", required=True).strip(),
        "title": _text(data, "title").strip(), "brand": brand.strip(),
        "category": _text(data, "main_category").strip(),
    }
