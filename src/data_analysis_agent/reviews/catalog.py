"""统一评论服务的商品目录与数据集摘要。"""

from contextlib import closing

from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.reviews.analytics import count_rate
from data_analysis_agent.reviews.store import connect_review_database


def dataset_summaries(connection, profiles):
    """在目录的同一只读快照中汇总数据集；覆盖率包含全部日期和商品。"""
    datasets = [dict(row) for row in connection.execute("""
        SELECT p.source, p.dataset_version, COUNT(DISTINCT p.product_id) product_count,
            COUNT(r.id) review_count,
            COALESCE(SUM(CASE WHEN r.has_text=1 THEN 1 ELSE 0 END),0) eligible_text_reviews,
            MIN(r.published_at) published_at_start, MAX(r.published_at) published_at_end
        FROM products p LEFT JOIN reviews r ON p.source=r.source
            AND p.dataset_version=r.dataset_version AND p.product_id=r.product_id
        GROUP BY p.source,p.dataset_version ORDER BY p.source,p.dataset_version
    """)]
    # 与 current_reviews_sql 一致：成功优先、正文指纹须匹配；重试及多条观点不能放大计数。
    successes = {tuple(row[key] for key in ("source", "dataset_version", "requested_model", "base_url", "prompt_version")):
                 row["successful_reviews"] for row in connection.execute("""
        SELECT r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version,
            COUNT(DISTINCT r.id) successful_reviews
        FROM reviews r JOIN review_annotations a ON a.review_id=r.id AND a.content_sha256=r.content_sha256
        WHERE r.has_text=1 AND a.status='success'
        GROUP BY r.source,r.dataset_version,a.requested_model,a.base_url,a.prompt_version
    """)}
    for dataset in datasets:
        dataset["profiles"] = []
        for profile in profiles:
            identity = tuple(profile[key] for key in ("requested_model", "base_url", "prompt_version"))
            count = successes.get((dataset["source"], dataset["dataset_version"], *identity), 0)
            dataset["profiles"].append({"profile_id": profile["profile_id"], "successful_reviews": count,
                "labeling_coverage": count_rate(count, dataset["eligible_text_reviews"])})
    return datasets


def list_products(db_path, *, page=1, page_size=20, source=None, dataset_version=None):
    validate_integer(page, "page", maximum=1000000)
    validate_integer(page_size, "page_size", maximum=100)
    conditions, values = ["TRUE"], []
    for name, value in (("source", source), ("dataset_version", dataset_version)):
        if value is not None:
            conditions.append(f"p.{name}=?")
            values.append(value)
    where = " AND ".join(conditions)
    with closing(connect_review_database(db_path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM products p WHERE " + where, values).fetchone()[0]
        rows = connection.execute("""SELECT p.source,p.dataset_version,p.product_id,p.title,p.brand,p.category,
            COUNT(r.id) review_count,MIN(r.published_at) published_at_start,MAX(r.published_at) published_at_end
            FROM products p LEFT JOIN reviews r ON
            p.source=r.source AND p.dataset_version=r.dataset_version AND p.product_id=r.product_id
            WHERE """ + where + " GROUP BY p.source,p.dataset_version,p.product_id "
            "ORDER BY p.source,p.dataset_version,p.product_id LIMIT ? OFFSET ?",
            (*values, page_size, (page - 1) * page_size))
        return {"total": total, "page": page, "page_size": page_size, "items": [dict(row) for row in rows]}
