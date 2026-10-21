"""Compose the local workflow and expose a small command-line interface."""

import argparse
import json
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    from _cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from .analysis import get_data_overview, summarize_sales_by_product
from .data_loader import ColumnMap, SalesDataError, load_sales_data
from .reporting import generate_sales_chart, write_markdown_report


def run_analysis(input_path: str | Path, output_dir: str | Path, *,
                 sheet: str | None = None, column_map: ColumnMap = None) -> dict[str, object]:
    """Read, validate, summarize and export CSV/Excel; return JSON-compatible results.

    All reads go through load_sales_data. Output files with the same names in
    output_dir are replaced on rerun. Invalid data is rejected before writing.
    """
    source = Path(input_path)
    destination = Path(output_dir).resolve()
    if source.resolve() in {
        (destination / "sales_by_product.png").resolve(),
        (destination / "report.md").resolve(),
    }:
        raise SalesDataError("输入文件路径不能与图表或报告的输出路径相同。")
    dataset = load_sales_data(source, sheet=sheet, column_map=column_map)
    overview = get_data_overview(dataset.records)
    totals = summarize_sales_by_product(dataset.records)
    chart_path = generate_sales_chart(totals, destination / "sales_by_product.png")
    report_path = write_markdown_report(
        overview, totals, chart_path, destination / "report.md", source.name, source=dataset.source,
    )
    return {
        "overview": overview,
        "sales_by_product": totals,
        "chart_path": chart_path,
        "report_path": report_path,
        "source": dataset.source,
    }


def main() -> int:
    """Run the CLI, returning zero on success and one for data or file errors."""
    parser = argparse.ArgumentParser(description="本地 CSV/Excel 销售数据分析")
    parser.add_argument(
        "--input", type=Path, default=Path("data/samples/sales_mock.csv"),
        help="UTF-8 CSV 或 .xlsx 路径；默认读取仓库中的模拟数据",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs"),
        help="图表和报告目录；同名产物会被覆盖（默认 outputs）",
    )
    parser.add_argument("--sheet", help="Excel 工作表名称；多工作表时必填")
    parser.add_argument("--column-map", type=Path, help="UTF-8 JSON 字段映射文件（标准字段 → 原始表头）")
    args = parser.parse_args()
    try:
        result = run_analysis(args.input, args.output_dir, sheet=args.sheet, column_map=args.column_map)
    except (SalesDataError, OSError) as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
