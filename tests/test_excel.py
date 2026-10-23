"""CSV/Excel 一致性、工作表选择、显式映射和输入失败检查。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from data_analysis_agent.agents.sales import run_agent_analysis
from data_analysis_agent.sales.loader import SalesDataError, load_sales_csv, load_sales_data, load_sales_xlsx
from data_analysis_agent.sales.workflow import run_analysis

SAMPLE = Path(__file__).resolve().parents[1] / "data/samples/sales_mock.csv"
CN_HEADERS = ["销售日期", "商品名称", "销售额"]


def make_workbook(path, rows=None, headers=CN_HEADERS, *, extra_sheet=False):
    """小型一次性测试夹具；不修改用户工作簿。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "销售明细"
    if headers is not None:
        sheet.append(headers)
    for row in rows or []:
        sheet.append(row)
    if extra_sheet:
        other = workbook.create_sheet("另一个月")
        other.append(CN_HEADERS)
        other.append(["2026-11-01", "Other", 123])
    workbook.save(path)
    workbook.close()
    return path


def sample_rows():
    return [[date.fromisoformat(row["date"]), row["product"], float(row["sales_amount"])]
            for row in load_sales_csv(SAMPLE)]


class ExcelTests(unittest.TestCase):
    def test_csv_excel_full_workflow_parity_and_source_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_workbook(root / "same.xlsx", sample_rows())
            original = path.read_bytes()
            self.assertEqual(load_sales_xlsx(path), load_sales_csv(SAMPLE))
            dataset = load_sales_data(path)
            self.assertEqual(dataset.source["sheet"], "销售明细")
            self.assertEqual(dataset.source["column_mapping"], dict(zip(
                ("date", "product", "sales_amount"), CN_HEADERS)))
            self.assertEqual(dataset.source["sha256"], hashlib.sha256(original).hexdigest())
            csv_result = run_analysis(SAMPLE, root / "csv")
            excel_result = run_analysis(path, root / "excel")
            for key in ("overview", "sales_by_product"):
                self.assertEqual(excel_result[key], csv_result[key])
            self.assertEqual(Path(csv_result["chart_path"]).read_bytes(),
                             Path(excel_result["chart_path"]).read_bytes())
            for result in (csv_result, excel_result):
                report = Path(result["report_path"]).read_text(encoding="utf-8")
                self.assertIn("| 总销售额 | 900.00 |", report)
                self.assertIn("| Coffee | 400.00 |", report)
                self.assertIn(result["source"]["sha256"], report)
            self.assertIn("销售明细", Path(excel_result["report_path"]).read_text(encoding="utf-8"))
            self.assertEqual(path.read_bytes(), original)

    def test_multiple_sheets_require_selection_and_only_selected_sheet_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(Path(directory) / "multi.xlsx", sample_rows(), extra_sheet=True)
            for sheet, message in ((None, "--sheet"), ("不存在", "工作表不存在")):
                with self.subTest(sheet=sheet), self.assertRaisesRegex(SalesDataError, message):
                    load_sales_data(path, sheet=sheet)
            chosen = load_sales_data(path, sheet="另一个月")
            self.assertEqual(len(chosen.records), 1)
            self.assertEqual(chosen.records[0]["sales_amount"], "123.00")
            self.assertEqual(chosen.source["sheet"], "另一个月")
            with self.assertRaisesRegex(SalesDataError, "CSV 不包含工作表"):
                load_sales_data(SAMPLE, sheet="销售明细")

    def test_custom_column_map_works_for_both_formats_and_records_actual_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            headers = ["成交日", "品名", "实收金额"]
            mapping = dict(zip(("date", "product", "sales_amount"), headers))
            map_path = root / "mapping.json"
            map_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8-sig")
            excel = make_workbook(root / "custom.xlsx", [["2026-10-01", "A", 0.1]], headers)
            csv = root / "custom.csv"
            csv.write_text("成交日,品名,实收金额\n2026-10-01,A,0.10\n", encoding="utf-8-sig")
            self.assertEqual(load_sales_data(excel, column_map=map_path).records,
                             load_sales_data(csv, column_map=mapping).records)
            self.assertEqual(load_sales_data(excel, column_map=map_path).source["column_mapping"], mapping)
            for content in ("not json", "[]", "null", '{"unknown":"品名"}', '{"date":null}'):
                map_path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(SalesDataError):
                    load_sales_data(excel, column_map=map_path)

    def test_ambiguous_missing_duplicate_and_many_to_one_headers_are_rejected(self):
        cases = [
            (["date", "销售日期", "product", "sales_amount"], {}, "多个候选列"),
            (["date", "product"], {}, "缺少必要字段"),
            (["date", "product", "sales_amount", "product"], {}, "重复字段"),
            (["date", "", "sales_amount"], {}, "空字段名"),
            (["date", "product", "sales_amount"], {"product": "date"}, "同一原始列"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.xlsx"
            for headers, mapping, message in cases:
                make_workbook(path, headers=headers)
                with self.subTest(headers=headers), self.assertRaisesRegex(SalesDataError, message):
                    load_sales_data(path, column_map=mapping)
            make_workbook(path, [["2026-10-01", "wrong", "A", 1]],
                          ["date", "销售日期", "product", "sales_amount"])
            self.assertEqual(load_sales_data(path, column_map={"date": "date"}).records[0]["date"], "2026-10-01")

    def test_native_dates_and_numbers_are_normalized_without_rounding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(Path(directory) / "types.xlsx", [
                [date(2026, 10, 1), " A ", 0.1],
                [datetime(2026, 10, 2), "B", 2],
                [None, None, None],
                ["2026-10-03", "C", "3.40"],
            ])
            rows = load_sales_xlsx(path)
            self.assertEqual([row["sales_amount"] for row in rows], ["0.10", "2.00", "3.40"])
            self.assertEqual(rows[0]["product"], "A")
            self.assertEqual(rows[1]["date"], "2026-10-02")

    def test_bad_excel_values_fail_before_outputs_or_model_calls(self):
        cases = [
            (["2026-02-30", "A", 1], "YYYY-MM-DD"),
            ([datetime(2026, 10, 1, 12), "A", 1], "含时间"),
            ([45931, "A", 1], "单元格类型"),
            (["2026-10-01", "", 1], "product"),
            (["2026-10-01", "A", -1], "sales_amount"),
            (["2026-10-01", "A", 0.001], "sales_amount"),
            (["2026-10-01", "A", True], "布尔值"),
            (["2026-10-01", "A", "=1+2"], "公式"),
            (["2026-10-01", "A", "#DIV/0!"], "Excel 错误"),
        ]
        with tempfile.TemporaryDirectory() as directory, patch(
            "data_analysis_agent.agents.sales._new_llm", side_effect=AssertionError("must not call model")
        ):
            root = Path(directory)
            path = root / "bad.xlsx"
            for row, message in cases:
                make_workbook(path, [row])
                with self.subTest(row=row):
                    with self.assertRaisesRegex(SalesDataError, message):
                        run_analysis(path, root / "rejected")
                    self.assertFalse((root / "rejected").exists())
                    outcome = run_agent_analysis(path, "总销售额是多少？", root / "agent")
                    self.assertEqual(outcome["error"]["code"], "data_error", outcome)

    def test_empty_corrupt_and_unsupported_workbooks_are_data_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for headers, message in ((None, "缺少表头"), (CN_HEADERS, "没有数据行")):
                path = make_workbook(root / "empty.xlsx", headers=headers)
                with self.assertRaisesRegex(SalesDataError, message):
                    load_sales_data(path)
            path.write_text("not an Excel ZIP", encoding="utf-8")
            with self.assertRaisesRegex(SalesDataError, "有效、未加密"):
                load_sales_data(path)
            old = root / "old.xls"
            old.write_bytes(b"not supported")
            with self.assertRaisesRegex(SalesDataError, "另存为 .xlsx"):
                load_sales_data(old)

    def test_both_cli_entries_accept_excel_and_persist_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_workbook(root / "cli.xlsx", sample_rows(), extra_sheet=True)
            result = subprocess.run([
                sys.executable, "-m", "data_analysis_agent", "--input", str(path),
                "--sheet", "销售明细", "--output-dir", str(root / "fixed"),
            ], env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["overview"]["total_sales"], "900.00")
            result = subprocess.run([
                sys.executable, "-m", "data_analysis_agent.cli.agent", "--input", str(path),
                "--sheet", "不存在", "--question", "总销售额是多少？", "--output-dir", str(root / "agent"),
            ], env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, encoding="utf-8", timeout=20)
            outcome = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1, outcome)
            self.assertEqual(outcome["error"]["code"], "data_error")
            self.assertIn("工作表不存在", outcome["error"]["message"])


if __name__ == "__main__":
    unittest.main()
