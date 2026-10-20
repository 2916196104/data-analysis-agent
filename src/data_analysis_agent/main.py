"""Compose the local workflow and expose a small command-line interface."""

import argparse
import json
import sys
from pathlib import Path

from .analysis import get_data_overview, summarize_sales_by_product
from .data_loader import SalesDataError, load_sales_csv
from .reporting import generate_sales_chart, write_markdown_report


def run_analysis(input_path: str | Path, output_dir: str | Path) -> dict[str, object]:
    """Read, validate, summarize and export a CSV; return JSON-compatible results.

    All reads go through load_sales_csv. Output files with the same names in
    output_dir are replaced on rerun. Invalid data is rejected before writing.
    """
    source = Path(input_path)
    records = load_sales_csv(source)
    overview = get_data_overview(records)
    totals = summarize_sales_by_product(records)
    destination = Path(output_dir).resolve()
    if source.resolve() in {
        (destination / "sales_by_product.png").resolve(),
        (destination / "report.md").resolve(),
    }:
        raise SalesDataError("输入文件路径不能与图表或报告的输出路径相同。")
    chart_path = generate_sales_chart(totals, destination / "sales_by_product.png")
    report_path = write_markdown_report(
        overview, totals, chart_path, destination / "report.md", source.name
    )
    return {
        "overview": overview,
        "sales_by_product": totals,
        "chart_path": chart_path,
        "report_path": report_path,
    }


def main() -> int:
    """Run the CLI, returning zero on success and one for data or file errors."""
    parser = argparse.ArgumentParser(description="本地 CSV 销售数据分析（第一阶段）")
    parser.add_argument(
        "--input", type=Path, default=Path("data/samples/sales_mock.csv"),
        help="UTF-8 CSV 路径；默认读取仓库中的模拟数据",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs"),
        help="图表和报告目录；同名产物会被覆盖（默认 outputs）",
    )
    args = parser.parse_args()
    try:
        result = run_analysis(args.input, args.output_dir)
    except (SalesDataError, OSError) as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
