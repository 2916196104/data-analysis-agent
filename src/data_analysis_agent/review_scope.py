"""Shared UTC review filters and current model/human opinion selection."""

import re
from datetime import date, datetime, timedelta, timezone

from .database import table_names

from .review_loader import ReviewDataError


def _day(value, name):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ReviewDataError(f"{name} 请使用有效的 YYYY-MM-DD 日期。") from exc


def review_scope(*, product_ids=None, date_from=None, date_to=None, as_of=None,
                 source=None, dataset_version=None, alias="r"):
    """Return SQL, parameters and normalized scope; date endpoints include the UTC day."""
    if alias not in ("r", "c"):
        raise ValueError("Unsupported review alias")
    if product_ids is not None and (not isinstance(product_ids, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in product_ids)):
        raise ReviewDataError("product_ids 必须是非空商品 ID 的列表。")
    products = sorted({item.strip() for item in product_ids or ()})
    conditions, values = [], []
    if products:
        conditions.append(f"{alias}.product_id IN (" + ",".join("?" for _ in products) + ")")
        values.extend(products)
    for name, value in (("source", source), ("dataset_version", dataset_version)):
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ReviewDataError(f"{name} 必须是非空文本。")
            conditions.append(f"{alias}.{name}=?")
            values.append(value)
    start = _day(date_from, "date_from") if date_from is not None else None
    end = _day(date_to, "date_to") if date_to is not None else None
    if start and end and start > end:
        raise ReviewDataError("开始日期不能晚于结束日期。")
    for day, operator in ((start, ">="), (end, "<")):
        if day is not None:
            try:
                boundary = day if operator == ">=" else day + timedelta(days=1)
            except OverflowError as exc:
                raise ReviewDataError("结束日期须早于 9999-12-31。") from exc
            conditions.append(f"{alias}.published_at {operator} ?")
            values.append(boundary.isoformat() + "T00:00:00.000+00:00")
    cutoff = as_of
    if as_of is not None:
        if isinstance(as_of, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", as_of):
            try:
                boundary = _day(as_of, "as_of") + timedelta(days=1)
            except OverflowError as exc:
                raise ReviewDataError("as_of 日期须早于 9999-12-31。") from exc
            operator, timestamp = "<", boundary.isoformat() + "T00:00:00.000+00:00"
        else:
            try:
                if not isinstance(as_of, str) or not re.fullmatch(
                        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", as_of):
                    raise ValueError
                instant = datetime.fromisoformat(as_of).astimezone(timezone.utc)
                timestamp = cutoff = instant.isoformat(timespec="milliseconds")
                operator = "<="
            except (ValueError, OverflowError) as exc:
                raise ReviewDataError("as_of 请使用 UTC 日期或带时区的 ISO 8601 时间。") from exc
        conditions.append(f"{alias}.published_at {operator} ?")
        values.append(timestamp)
    scope = {"product_ids": products, "source": source, "dataset_version": dataset_version,
             "date_from": date_from, "date_to": date_to, "as_of": cutoff, "timezone": "UTC"}
    return " AND ".join(conditions) or "TRUE", values, scope


def model_identity(model, base_url, prompt_version, *, required=False):
    values = (model, base_url, prompt_version)
    if not required and all(value is None for value in values):
        return None
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ReviewDataError("请同时指定非空的 model、base_url 和 prompt_version。")
    return tuple(value.strip() for value in values)


def current_reviews_sql(connection, identity):
    """Use current content and one profile; an empty human snapshot stays empty."""
    expected = {"review_workbench_schema", "review_human_reviews", "review_human_opinions"}
    names = table_names(connection) & expected
    if names:
        if names != expected:
            raise ReviewDataError("人工复核表不完整，请检查工作台数据库结构。")
        versions = connection.execute("SELECT version FROM review_workbench_schema").fetchall()
        if len(versions) != 1 or versions[0][0] != 1:
            raise ReviewDataError("工作台表版本不支持。")
    human_columns = "h.id revision_id, COALESCE(h.action,'unreviewed') human_status," if names else (
        "NULL revision_id, 'unreviewed' human_status,")
    human_join = """LEFT JOIN review_human_reviews h ON h.id=(
        SELECT MAX(id) FROM review_human_reviews WHERE annotation_id=a.id)""" if names else ""
    human_opinions = """UNION ALL
        SELECT c.id, o.aspect, o.sentiment, o.quote, o.quote_start, o.quote_end
        FROM current c JOIN review_human_opinions o ON o.human_review_id=c.revision_id
    """ if names else ""
    return f"""WITH attempts AS (
        SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.review_id ORDER BY (a.status='success') DESC, a.id DESC) rn
        FROM review_annotations a JOIN reviews r ON r.id=a.review_id AND r.content_sha256=a.content_sha256
        WHERE a.requested_model=? AND a.base_url=? AND a.prompt_version=?
    ), current AS (
        SELECT r.id, r.source, r.dataset_version, r.product_id, r.title, r.text, r.has_text,
            r.rating, r.published_at, r.content_sha256, r.import_run_id, r.line_number,
            p.title product_title, a.id annotation_id, a.model_version, a.error_code, {human_columns}
            CASE WHEN r.has_text=0 THEN 'empty' WHEN a.id IS NULL THEN 'pending' ELSE a.status END processing_status
        FROM reviews r LEFT JOIN products p ON p.source=r.source AND p.dataset_version=r.dataset_version
            AND p.product_id=r.product_id
        LEFT JOIN attempts a ON a.review_id=r.id AND a.rn=1
        {human_join}
    ), effective_opinions AS (
        SELECT c.id review_id, o.aspect, o.sentiment, o.quote, o.quote_start, o.quote_end
        FROM current c JOIN review_opinions o ON o.annotation_id=c.annotation_id
        WHERE c.processing_status='success' AND c.revision_id IS NULL
        {human_opinions}
    ) """, identity
