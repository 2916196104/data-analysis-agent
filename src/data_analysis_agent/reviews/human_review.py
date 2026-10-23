"""独立于模型输出的评论浏览和只追加人工决策。"""

import json
from contextlib import closing

from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import insert_id, table_names
from data_analysis_agent.core.validation import validate_integer

from data_analysis_agent.reviews.label_store import check_labeling_schema
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS, OpinionValidationError, validate_opinions
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.scope import current_reviews_sql, review_scope
from data_analysis_agent.reviews.store import connect_review_database

PROCESSING_STATUSES = {"pending": "待标注", "success": "标注成功", "failed": "标注失败", "empty": "空正文"}
HUMAN_STATUSES = {"unreviewed": "待复核", "confirmed": "已确认", "corrected": "已纠正"}
SCHEMA = """
CREATE TABLE review_workbench_schema (version INTEGER PRIMARY KEY);
INSERT INTO review_workbench_schema VALUES (1);
CREATE TABLE review_human_reviews (
    id INTEGER PRIMARY KEY,
    annotation_id INTEGER NOT NULL REFERENCES review_annotations(id),
    previous_id INTEGER REFERENCES review_human_reviews(id),
    action TEXT NOT NULL CHECK(action IN ('confirmed','corrected')),
    reviewer TEXT NOT NULL CHECK(length(reviewer)>0),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX human_reviews_annotation ON review_human_reviews(annotation_id, id);
CREATE TABLE review_human_opinions (
    id INTEGER PRIMARY KEY,
    human_review_id INTEGER NOT NULL REFERENCES review_human_reviews(id),
    aspect TEXT NOT NULL CHECK(aspect IN ('quality','effectiveness','price','packaging','logistics','service')),
    sentiment TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral','unknown')),
    quote TEXT NOT NULL CHECK(quote<>''),
    quote_start INTEGER NOT NULL CHECK(quote_start>=0),
    quote_end INTEGER NOT NULL CHECK(quote_end>quote_start),
    UNIQUE(human_review_id, aspect, sentiment, quote_start, quote_end)
);
CREATE INDEX human_opinions_review ON review_human_opinions(human_review_id);
"""


class ReviewConflict(ReviewDataError):
    """当前展示的决策或原文已经不是最新版本。"""


class ReviewNotFound(ReviewDataError):
    """请求的评论或模型配置不存在。"""


def check_workbench_schema(connection):
    """在调用方连接上校验标注和人工复核数据表。"""
    check_labeling_schema(connection)
    names = table_names(connection) & {"review_workbench_schema", "review_human_reviews", "review_human_opinions"}
    if len(names) != 3:
        raise ReviewDataError("工作台表未初始化，请由你执行 review_workbench init --db 指定路径。")
    versions = connection.execute("SELECT version FROM review_workbench_schema").fetchall()
    if len(versions) != 1 or versions[0][0] != 1:
        raise ReviewDataError("工作台表版本不支持。")


def init_workbench(db_path):
    """只有此显式命令会修改数据表结构；现有数据表保持不变。"""
    with closing(connect_review_database(db_path)) as connection:
        check_labeling_schema(connection)
        if any(name.startswith("review_human_") or name == "review_workbench_schema"
               for name in table_names(connection)):
            check_workbench_schema(connection)
            return {"created": False, "workbench_schema_version": 1}
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
        except Exception:
            connection.rollback()
            raise
    return {"created": True, "workbench_schema_version": 1}


def check_workbench(db_path):
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        check_workbench_schema(connection)


def _profile(connection, profile_id):
    if profile_id is not None:
        validate_integer(profile_id, "profile_id")
        row = connection.execute("SELECT requested_model, base_url, prompt_version "
                                 "FROM review_annotations WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise ReviewNotFound("模型版本不存在，请刷新页面。")
    else:
        row = connection.execute("SELECT requested_model, base_url, prompt_version "
                                 "FROM review_annotations ORDER BY id DESC LIMIT 1").fetchone()
    return tuple(row) if row else (None, None, None)


def _current(connection, profile_id):
    return current_reviews_sql(connection, _profile(connection, profile_id))


def workbench_options(db_path, profile_id=None):
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        check_workbench_schema(connection)
        with connection:
            connection.execute("BEGIN")
            cte, scope = _current(connection, profile_id)
            profiles = [dict(row) for row in connection.execute(
                "SELECT MIN(id) profile_id, requested_model, base_url, prompt_version, MAX(id) newest_id "
                "FROM review_annotations GROUP BY requested_model, base_url, prompt_version ORDER BY newest_id DESC"
            )]
            selected = next((p["profile_id"] for p in profiles
                             if (p["requested_model"], p["base_url"], p["prompt_version"]) == scope), None)
            products = [dict(row) for row in connection.execute(cte + """
                SELECT source, dataset_version, product_id, product_title, COUNT(*) review_count,
                    SUM(CASE WHEN has_text=1 THEN 1 ELSE 0 END) eligible_count, SUM(CASE WHEN processing_status='empty' THEN 1 ELSE 0 END) empty_count,
                    SUM(CASE WHEN processing_status='pending' THEN 1 ELSE 0 END) pending_count, SUM(CASE WHEN processing_status='success' THEN 1 ELSE 0 END) success_count,
                    SUM(CASE WHEN processing_status='failed' THEN 1 ELSE 0 END) failed_count, SUM(CASE WHEN human_status<>'unreviewed' THEN 1 ELSE 0 END) reviewed_count
                FROM current GROUP BY source, dataset_version, product_id, product_title ORDER BY product_id, source, dataset_version
            """, scope)]
            for product in products:
                product["key"] = json.dumps([product["source"], product["dataset_version"], product["product_id"]])
            return {"profiles": profiles, "selected_profile_id": selected, "products": products,
                    "aspects": ASPECTS, "sentiments": SENTIMENTS,
                    "processing_statuses": PROCESSING_STATUSES, "human_statuses": HUMAN_STATUSES}


def _filters(product_key, date_from, date_to, aspect, sentiment, processing_status, human_status):
    conditions, values = [], []
    if product_key:
        try:
            key = json.loads(product_key)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ReviewDataError("商品筛选格式错误。") from exc
        if not isinstance(key, list) or len(key) != 3 or any(not isinstance(v, str) or not v for v in key):
            raise ReviewDataError("商品筛选格式错误。")
        conditions.append("c.source=? AND c.dataset_version=? AND c.product_id=?")
        values.extend(key)
    date_where, date_values, _ = review_scope(date_from=date_from, date_to=date_to, alias="c")
    conditions.append(date_where)
    values.extend(date_values)
    for name, value, allowed in (("aspect", aspect, ASPECTS), ("sentiment", sentiment, SENTIMENTS),
                                ("processing_status", processing_status, PROCESSING_STATUSES),
                                ("human_status", human_status, HUMAN_STATUSES)):
        if value is not None and value not in allowed:
            raise ReviewDataError(f"无效的 {name}。")
    for field, value in (("processing_status", processing_status), ("human_status", human_status)):
        if value:
            conditions.append(f"c.{field}=?")
            values.append(value)
    if aspect or sentiment:
        parts = ["o.review_id=c.id"]
        for field, value in (("aspect", aspect), ("sentiment", sentiment)):
            if value:
                parts.append(f"o.{field}=?")
                values.append(value)
        conditions.append("EXISTS (SELECT 1 FROM effective_opinions o WHERE " + " AND ".join(parts) + ")")
    return " AND ".join(conditions) or "TRUE", values


def list_reviews(db_path, *, profile_id=None, product_key=None, date_from=None, date_to=None,
                 aspect=None, sentiment=None, processing_status=None, human_status=None, page=1, page_size=20):
    validate_integer(page, "page", maximum=1000000)
    validate_integer(page_size, "page_size", maximum=100)
    where, filters = _filters(product_key, date_from, date_to, aspect, sentiment, processing_status, human_status)
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        check_workbench_schema(connection)
        with connection:
            connection.execute("BEGIN")
            cte, scope = _current(connection, profile_id)
            total = connection.execute(cte + "SELECT COUNT(*) FROM current c WHERE " + where,
                                       (*scope, *filters)).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            items = [dict(row) for row in connection.execute(cte + """
                SELECT c.id, c.source, c.dataset_version, c.product_id, c.product_title, c.title, c.text,
                    c.rating, c.published_at, c.processing_status, c.human_status, c.error_code,
                    c.annotation_id, c.revision_id FROM current c WHERE """ + where
                + " ORDER BY c.published_at DESC, c.id DESC LIMIT ? OFFSET ?",
                (*scope, *filters, page_size, (page - 1) * page_size))]
            if items:
                ids = [item["id"] for item in items]
                opinions = connection.execute(cte + "SELECT * FROM effective_opinions WHERE review_id IN ("
                                              + ",".join("?" for _ in ids) + ")", (*scope, *ids)).fetchall()
                grouped = {item_id: [] for item_id in ids}
                for row in opinions:
                    grouped[row["review_id"]].append({k: row[k] for k in row.keys() if k != "review_id"})
                for item in items:
                    item["opinions"] = grouped[item["id"]]
            return {"total": total, "page": page, "page_size": page_size, "pages": pages, "items": items}


def _opinions(connection, table, foreign_key, value):
    return [dict(row) for row in connection.execute(
        f"SELECT aspect, sentiment, quote, quote_start, quote_end FROM {table} WHERE {foreign_key}=? ORDER BY id",
        (value,))]


def review_detail(db_path, review_id, profile_id=None):
    validate_integer(review_id, "review_id")
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        check_workbench_schema(connection)
        with connection:
            connection.execute("BEGIN")
            cte, scope = _current(connection, profile_id)
            row = connection.execute(cte + "SELECT c.*, i.file_path, i.file_sha256 FROM current c "
                                     "JOIN import_runs i ON i.id=c.import_run_id WHERE c.id=?",
                                     (*scope, review_id)).fetchone()
            if row is None:
                raise ReviewNotFound("评论不存在。")
            # 网页响应会有意省略用户 ID 和完整原始 JSON。
            review = {key: row[key] for key in ("id", "source", "dataset_version", "product_id", "product_title",
                      "title", "text", "rating", "published_at", "content_sha256", "processing_status", "human_status",
                      "revision_id", "annotation_id", "line_number", "file_path", "file_sha256")}
            review["evidence_ref"] = f"sha256:{row['file_sha256']}#line={row['line_number']}"
            annotations = []
            for item in connection.execute("SELECT * FROM review_annotations WHERE review_id=? ORDER BY id DESC",
                                           (review_id,)).fetchall():
                annotation = dict(item)
                annotation["matches_current_content"] = item["content_sha256"] == row["content_sha256"]
                annotation["opinions"] = _opinions(connection, "review_opinions", "annotation_id", item["id"])
                annotations.append(annotation)
            selected = next((item for item in annotations if item["id"] == row["annotation_id"]), None)
            history = []
            for item in connection.execute("SELECT * FROM review_human_reviews WHERE annotation_id=? ORDER BY id DESC",
                                           (row["annotation_id"],)):
                event = dict(item)
                event["opinions"] = _opinions(connection, "review_human_opinions", "human_review_id", item["id"])
                history.append(event)
            effective = history[0]["opinions"] if history else (selected["opinions"] if selected else [])
            return {"review": review, "annotation": selected, "effective_opinions": effective,
                    "human_reviews": history, "model_history": annotations}


def _human_text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ReviewDataError(f"{name} 必须是最多 {maximum} 字的文本。")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ReviewDataError(f"{name} 包含无效字符。") from exc
    value = value.strip()
    if required and not value:
        raise ReviewDataError(f"请填写 {name}。")
    return value


def save_human_review(db_path, review_id, *, annotation_id, expected_revision_id, action,
                      reviewer, note="", opinions=None):
    validate_integer(review_id, "review_id")
    validate_integer(annotation_id, "annotation_id")
    if expected_revision_id is not None:
        validate_integer(expected_revision_id, "expected_revision_id")
    if action not in ("confirmed", "corrected"):
        raise ReviewDataError("人工操作仅支持 confirmed 或 corrected。")
    reviewer = _human_text(reviewer, "复核人", 80, required=True)
    note = _human_text(note, "备注", 2000)
    if action == "confirmed" and opinions is not None:
        raise ReviewDataError("确认模型原始结果时不能同时提交纠正观点。")
    if action == "corrected" and not isinstance(opinions, list):
        raise ReviewDataError("纠正时必须提交完整观点列表，可以为空。")
    with closing(connect_review_database(db_path)) as connection:
        check_workbench_schema(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT r.text, r.content_sha256, a.content_sha256 annotation_hash, a.status "
                                     "FROM reviews r JOIN review_annotations a ON a.review_id=r.id "
                                     "WHERE r.id=? AND a.id=?", (review_id, annotation_id)).fetchone()
            if row is None:
                raise ReviewNotFound("评论与模型标注不匹配。")
            if row["status"] != "success" or row["content_sha256"] != row["annotation_hash"]:
                raise ReviewConflict("只能复核与当前正文一致的成功标注，请刷新页面。")
            latest = connection.execute("SELECT MAX(id) FROM review_human_reviews WHERE annotation_id=?",
                                        (annotation_id,)).fetchone()[0]
            if latest != expected_revision_id:
                raise ReviewConflict("该结果已被其他操作复核，请重新打开评论后再提交。")
            if action == "confirmed":
                snapshot = _opinions(connection, "review_opinions", "annotation_id", annotation_id)
            else:
                try:
                    snapshot = validate_opinions(json.dumps({"opinions": opinions}), row["text"])
                except (OpinionValidationError, TypeError, ValueError) as exc:
                    raise ReviewDataError("纠正未保存：维度、情感须有效，引用必须逐字存在于正文。") from exc
            revision_id = insert_id(connection,
                "INSERT INTO review_human_reviews(annotation_id, previous_id, action, reviewer, note, created_at) "
                "VALUES (?,?,?,?,?,?)", (annotation_id, latest, action, reviewer, note, utc_now())
            )
            connection.executemany("INSERT INTO review_human_opinions "
                                   "(human_review_id, aspect, sentiment, quote, quote_start, quote_end) VALUES (?,?,?,?,?,?)",
                                   [(revision_id, p["aspect"], p["sentiment"], p["quote"], p["quote_start"], p["quote_end"])
                                    for p in snapshot])
            return {"revision_id": revision_id, "action": action, "opinion_count": len(snapshot)}
