"""Natural-language CLI; the original python -m data_analysis_agent stays unchanged."""

import argparse
import json
import sys
from pathlib import Path

from .agent import run_agent_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="HelloAgents 单 Agent 销售数据分析")
    parser.add_argument("--input", type=Path, default=Path("data/samples/sales_mock.csv"))
    parser.add_argument("--sheet", help="Excel 工作表名称；多工作表时必填")
    parser.add_argument("--column-map", type=Path, help="UTF-8 JSON 字段映射文件（标准字段 → 原始表头）")
    parser.add_argument("--question", required=True, help="使用自然语言提出分析问题")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="每次创建独立运行子目录")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="显式读取的本地配置文件")
    args = parser.parse_args()
    try:
        result = run_agent_analysis(args.input, args.question, args.output_dir, env_file=args.env_file,
                                    sheet=args.sheet, column_map=args.column_map)
    except OSError:
        print("分析失败：无法创建或保存运行记录，请检查输出目录权限。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"ok": 0, "error": 1, "unsupported": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
