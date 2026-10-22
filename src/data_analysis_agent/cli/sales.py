"""Command-line entry for sales.workflow."""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

import argparse
import json
import sys
from pathlib import Path

from data_analysis_agent.sales.loader import SalesDataError
from data_analysis_agent.sales.workflow import run_analysis


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
