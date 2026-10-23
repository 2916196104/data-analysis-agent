"""流式读取评论 CSV，保留原字段并适配中文表头。"""

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.loader import JsonlLine

SOURCE = "reviews_csv"
FIELDS = {
    "product_id": ("商品ID", "product_id", "parent_asin"),
    "text": ("评论内容", "text", "content", "review"),
    "published_at": ("评论时间", "published_at", "review_time"),
    "rating": ("打分", "rating", "score"),
    "user_id": ("用户ID", "user_id"),
    "source_record_id": ("评论ID", "review_id"),
    "product_title": ("商品名称", "product_name"),
    "brand": ("品牌", "brand"),
    "title": ("评论标题", "title"),
}


def iter_review_csv(path: Path):
    # 完整检查 UTF-8，避免英文表头、中文正文的 GB18030 文件被误判。
    encoding = "utf-8-sig"
    try:
        with path.open(encoding=encoding) as handle:
            while handle.read(64 * 1024):
                pass
    except UnicodeError:
        encoding = "gb18030"
    raw_parts = []
    try:
        with path.open("rb") as handle:
            def physical_lines():
                for raw in handle:
                    raw_parts.append(raw)
                    yield raw.decode(encoding)

            reader = csv.reader(physical_lines(), strict=True)
            headers = next(reader, [])
            headers = [header.strip() for header in headers]
            if not headers or len(set(headers)) != len(headers):
                raise ReviewDataError("CSV 需要非空表头，且列名不能重复。")
            missing = [FIELDS[field][0] for field in ("product_id", "text", "published_at")
                       if not any(alias in headers for alias in FIELDS[field])]
            if missing:
                raise ReviewDataError("评论 CSV 缺少列：" + "、".join(missing)
                                      + "。请上传评论数据；交易和用户行为数据不能用于评论分析。")
            while True:
                raw_parts.clear()
                number = reader.line_num + 1
                values = next(reader, None)
                if values is None:
                    break
                raw = b"".join(raw_parts)
                if not values:
                    yield JsonlLine(number, raw, "", None, None, handle.tell())
                elif len(values) != len(headers):
                    yield JsonlLine(number, raw, None, None, "CSV 数据列数与表头不一致。", handle.tell())
                else:
                    data = dict(zip(headers, values))
                    yield JsonlLine(number, raw, json.dumps(data, ensure_ascii=False), data, None, handle.tell())
    except (csv.Error, UnicodeError) as exc:
        raise ReviewDataError("CSV 格式或编码无效；支持 UTF-8 和 GB18030，整次导入已回滚。") from exc


def csv_value(data: dict, field: str) -> str:
    return next((data[name] for name in FIELDS[field] if name in data), "")


def normalize_csv_review(data: dict) -> dict:
    product_id = csv_value(data, "product_id").strip()
    if not product_id:
        raise ReviewDataError("商品ID 不能为空。")
    identifiers = {field: csv_value(data, field).strip() for field in ("user_id", "source_record_id")}
    if any(re.fullmatch(r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+", value)
           for value in (product_id, *identifiers.values())):
        raise ReviewDataError("ID 不能使用科学计数法；请从原始数据导出文本格式的 ID。")
    value = csv_value(data, "published_at").strip()
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        published = None
        for pattern in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
            try:
                published = datetime.strptime(value, pattern)
                break
            except ValueError:
                pass
        if published is None:
            raise ReviewDataError("评论时间须为 ISO 日期时间或 YYYY/M/D HH:mm[:ss]。")
    published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published.astimezone(timezone.utc)
    rating = csv_value(data, "rating").strip()
    try:
        rating = float(rating) if rating else None
    except ValueError as exc:
        raise ReviewDataError("打分必须为空或 1—5 之间的有限数字。") from exc
    if rating is not None and (not math.isfinite(rating) or not 1 <= rating <= 5):
        raise ReviewDataError("打分必须为空或 1—5 之间的有限数字。")
    return {"product_id": product_id, "text": csv_value(data, "text"), "title": csv_value(data, "title"),
            "rating": rating, "published_at": published.isoformat(timespec="milliseconds"), "asin": "",
            "product_title": csv_value(data, "product_title"), "brand": csv_value(data, "brand"), **identifiers}
