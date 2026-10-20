"""Local chart and Markdown output, independent from the command-line entry."""

import html
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote


def generate_sales_chart(totals: Mapping[str, str], output_path: str | Path) -> str:
    """Write a headless PNG bar chart from product totals; return its absolute path."""
    if not totals:
        raise ValueError("图表需要至少一个商品的汇总结果。")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = Figure(figsize=(8, 4.5), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    amounts = [float(Decimal(amount)) for amount in totals.values()]
    bars = axes.bar(list(totals), amounts, color="#2878B5")
    axes.bar_label(bars, labels=list(totals.values()), padding=4)
    axes.set(title="Sales by product", xlabel="Product", ylabel="Sales amount")
    axes.set_ylim(0, max(amounts) * 1.15 if max(amounts) else 1)
    axes.spines[["top", "right"]].set_visible(False)
    figure.savefig(path, format="png", dpi=150)
    return str(path)


def _markdown_text(value: str) -> str:
    text = html.escape(value, quote=False).replace("\r", " ").replace("\n", " ")
    for character in "\\`*_[]|":
        text = text.replace(character, "\\" + character)
    return text


def write_markdown_report(
    overview: Mapping[str, int | str],
    totals: Mapping[str, str],
    chart_path: str | Path,
    output_path: str | Path,
    source_name: str,
) -> str:
    """Write a UTF-8 report with an image link relative to the report directory."""
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    relative_chart = Path(os.path.relpath(Path(chart_path).resolve(), path.parent)).as_posix()
    lines = [
        "# 销售数据分析报告",
        "",
        f"数据文件：{_markdown_text(source_name)}",
        "",
        "统计口径：一行是一条销售记录；金额按原始数据单位统计，不推断币种。",
        "",
        "## 数据概览",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 销售记录数 | {overview['row_count']} |",
        f"| 商品数 | {overview['product_count']} |",
        f"| 开始日期 | {overview['date_start']} |",
        f"| 结束日期 | {overview['date_end']} |",
        f"| 总销售额 | {overview['total_sales']} |",
        f"| 平均每条销售额 | {overview['average_sales']} |",
        f"| 最小单条销售额 | {overview['min_sale']} |",
        f"| 最大单条销售额 | {overview['max_sale']} |",
        "",
        "## 按商品汇总",
        "",
        "| 商品 | 销售额 |",
        "| --- | ---: |",
        *[f"| {_markdown_text(product)} | {amount} |" for product, amount in totals.items()],
        "",
        "## 销售额图表",
        "",
        f"![按商品汇总销售额]({quote(relative_chart)})",
        "",
        "本报告由本地确定性统计生成。金额使用 Decimal 计算；平均值四舍五入至两位小数。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
