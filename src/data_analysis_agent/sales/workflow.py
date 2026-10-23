"""组合销售数据加载、统计和报告生成。"""

from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.sales.analysis import get_data_overview, summarize_sales_by_product
from data_analysis_agent.sales.loader import ColumnMap, SalesDataError, load_sales_data
from data_analysis_agent.sales.reporting import generate_sales_chart, write_markdown_report


def run_analysis(input_path: str | Path, output_dir: str | Path, *,
                 sheet: str | None = None, column_map: ColumnMap = None) -> dict[str, object]:
    """读取、校验、汇总并导出 CSV/Excel；返回兼容 JSON 的结果。

    所有读取都通过 load_sales_data。重新运行时会替换 output_dir
    中同名的输出文件。无效数据会在写入前被拒绝。
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


def main():
    """兼容入口；命令解析位于 cli/。"""
    from data_analysis_agent.cli.sales import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
