"""The single entry point for reading and validating sales data."""

import csv
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

REQUIRED_FIELDS = ("date", "product", "sales_amount")


class SalesRecord(TypedDict):
    date: str
    product: str
    sales_amount: str


class SalesDataError(ValueError):
    """The CSV does not meet the documented sales data contract."""


def validate_sales_fields(fieldnames: Sequence[str] | None) -> None:
    """Check required, nonempty and unique CSV headers; allow extra columns."""
    if not fieldnames:
        raise SalesDataError("CSV 缺少表头。")
    if any(not name.strip() for name in fieldnames):
        raise SalesDataError("CSV 表头包含空字段名。")
    if len(fieldnames) != len(set(fieldnames)):
        raise SalesDataError("CSV 表头包含重复字段。")
    missing = set(REQUIRED_FIELDS) - set(fieldnames)
    if missing:
        raise SalesDataError(f"CSV 缺少必要字段：{', '.join(sorted(missing))}。")


def load_sales_csv(input_path: str | Path) -> list[SalesRecord]:
    """Read UTF-8/BOM CSV and return normalized, JSON-compatible sales rows.

    Dates must be YYYY-MM-DD, products nonempty, and amounts nonnegative plain
    decimals with at most two fractional digits. Extra columns are ignored.
    Invalid input raises SalesDataError with a line number; I/O errors propagate.
    """
    records: list[SalesRecord] = []
    try:
        with Path(input_path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            validate_sales_fields(reader.fieldnames)
            for row in reader:
                line = reader.line_num
                if None in row or any(value is None for value in row.values()):
                    raise SalesDataError(f"第 {line} 行：列数与表头不一致。")
                values = {field: row[field].strip() for field in REQUIRED_FIELDS}
                raw_date = values["date"]
                try:
                    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
                        raise ValueError
                    date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise SalesDataError(f"第 {line} 行：date 必须是有效的 YYYY-MM-DD 日期。") from exc
                if not values["product"]:
                    raise SalesDataError(f"第 {line} 行：product 不能为空。")
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", values["sales_amount"]):
                    raise SalesDataError(
                        f"第 {line} 行：sales_amount 必须是非负金额，最多两位小数。"
                    )
                records.append(
                    SalesRecord(
                        date=raw_date,
                        product=values["product"],
                        sales_amount=format(Decimal(values["sales_amount"]), ".2f"),
                    )
                )
    except UnicodeError as exc:
        raise SalesDataError("CSV 必须使用 UTF-8 或 UTF-8 BOM 编码。") from exc
    except csv.Error as exc:
        raise SalesDataError("CSV 格式不正确，请检查引号和分隔符。") from exc
    if not records:
        raise SalesDataError("CSV 没有数据行。")
    return records
