"""Pure sales statistics over normalized records from the unified data loader."""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP

from data_analysis_agent.sales.loader import SalesDataError, SalesRecord


def get_data_overview(records: Sequence[SalesRecord]) -> dict[str, int | str]:
    """Summarize validated rows; amounts are exact decimal strings for JSON.

    Average sales is rounded to two decimal places using ROUND_HALF_UP.
    """
    if not records:
        raise SalesDataError("无法概览空数据。")
    amounts = [Decimal(row["sales_amount"]) for row in records]
    total = sum(amounts, Decimal("0"))
    dates = [row["date"] for row in records]
    average = (total / len(records)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "row_count": len(records),
        "product_count": len({row["product"] for row in records}),
        "date_start": min(dates),
        "date_end": max(dates),
        "total_sales": format(total, ".2f"),
        "average_sales": format(average, ".2f"),
        "min_sale": format(min(amounts), ".2f"),
        "max_sale": format(max(amounts), ".2f"),
    }


def summarize_sales_by_product(records: Sequence[SalesRecord]) -> dict[str, str]:
    """Sum validated rows by product; sort by sales descending, then name.

    Return {product: amount} with two-decimal strings, ready for tool JSON.
    """
    if not records:
        raise SalesDataError("无法汇总空数据。")
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in records:
        totals[row["product"]] += Decimal(row["sales_amount"])
    return {
        product: format(amount, ".2f")
        for product, amount in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    }
