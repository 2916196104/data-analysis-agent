"""Amazon Reviews 2023 JSONL 的流式解析和记录校验。"""

import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads

SOURCE = "amazon_reviews_2023"
DATASET_VERSION = "amazon-reviews-2023"


def import_suffix(filename: str, kind: str = "reviews") -> str:
    suffixes = (".jsonl.gz", ".jsonl", ".csv") if kind == "reviews" else (".jsonl.gz", ".jsonl")
    for suffix in suffixes:
        if filename.lower().endswith(suffix):
            return suffix
    raise ReviewDataError("评论支持 .csv、.jsonl 或 .jsonl.gz；商品元数据仅支持 .jsonl 或 .jsonl.gz。")


@dataclass(frozen=True)
class JsonlLine:
    number: int
    raw: bytes
    text: str | None
    data: dict | None
    error: str | None
    bytes_read: int = 0


def file_sha256(path: Path, progress: Callable[[int], None] | None = None) -> str:
    """对原始文件字节计算哈希，gzip 输入还包括压缩后的字节。"""
    digest = hashlib.sha256()
    completed = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            completed += len(chunk)
            if progress:
                progress(completed)
    return digest.hexdigest()


def iter_jsonl(path: str | Path) -> Iterator[JsonlLine]:
    """逐次产出一行物理行，并保留错误行用于诊断。

    A blank line has neither data nor error. JSON syntax/encoding failures are
    row errors; file and decompression errors propagate to roll back the import.
    """
    path = Path(path)
    if not (path.name.lower().endswith(".jsonl") or path.name.lower().endswith(".jsonl.gz")):
        raise ReviewDataError("输入必须是 .jsonl 或 .jsonl.gz 文件。")
    with ExitStack() as stack:
        source = stack.enter_context(path.open("rb"))
        handle = stack.enter_context(gzip.GzipFile(fileobj=source, mode="rb")) if path.name.lower().endswith(".gz") else source
        for number, raw in enumerate(handle, start=1):
            # gzip 的原始读取量含解压缓冲，不能作为已处理行数或事务完成度。
            bytes_read = source.tell()
            text = None
            try:
                text = raw.decode("utf-8-sig" if number == 1 else "utf-8").rstrip("\r\n")
                if not text.strip():
                    yield JsonlLine(number, raw, text, None, None, bytes_read)
                    continue
                data = strict_json_loads(text)
                if not isinstance(data, dict):
                    raise ValueError("每行必须是一个 JSON 对象。")
                # 在 SQLite 编码前拒绝转义的孤立代理项和溢出浮点数。
                json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except UnicodeError:
                yield JsonlLine(number, raw, text, None, "记录必须使用 UTF-8 编码。", bytes_read)
            except (ValueError, RecursionError):
                yield JsonlLine(number, raw, text, None, "JSON 无效、存在重复键或不是对象。", bytes_read)
            else:
                yield JsonlLine(number, raw, text, data, None, bytes_read)


def _text(data: dict, field: str, *, required: bool = False) -> str:
    value = data.get(field, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ReviewDataError(f"{field} 必须是{'非空' if required else ''}文本。")
    return value


def normalize_review(data: dict) -> dict:
    """保留评论正文；将 Amazon 的毫秒时间戳规范化为 UTC。"""
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
