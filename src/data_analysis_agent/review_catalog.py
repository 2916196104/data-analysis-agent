"""Paginated product catalog for the unified review service."""

from contextlib import closing

from .review_store import _connect


def list_products(db_path, *, page=1, page_size=20, source=None, dataset_version=None):
    conditions, values = ["1"], []
    for name, value in (("source", source), ("dataset_version", dataset_version)):
        if value is not None:
            conditions.append(f"p.{name}=?")
            values.append(value)
    where = " AND ".join(conditions)
    with closing(_connect(db_path, readonly=True)) as connection, connection:
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
