import json
import tempfile
import unittest
from pathlib import Path

from data_analysis_agent.analysis import get_data_overview, summarize_sales_by_product
from data_analysis_agent.data_loader import SalesDataError, load_sales_csv
from data_analysis_agent.main import run_analysis
from data_analysis_agent.reporting import generate_sales_chart, write_markdown_report

SAMPLE = Path(__file__).resolve().parents[1] / "data/samples/sales_mock.csv"
HEADER = "date,product,sales_amount\n"


class SalesWorkflowTests(unittest.TestCase):
    def test_mock_data_has_documented_statistics(self):
        rows = load_sales_csv(SAMPLE)
        self.assertEqual(get_data_overview(rows), {
            "row_count": 9, "product_count": 3,
            "date_start": "2026-10-01", "date_end": "2026-10-03",
            "total_sales": "900.00", "average_sales": "100.00",
            "min_sale": "50.00", "max_sale": "180.00",
        })
        totals = summarize_sales_by_product(rows)
        self.assertEqual(list(totals.items()), [
            ("Coffee", "400.00"), ("Juice", "250.00"), ("Tea", "250.00"),
        ])

    def test_exact_decimal_totals_and_half_up_average(self):
        rows = [
            {"date": "2026-10-01", "product": "A", "sales_amount": amount}
            for amount in ("0.10", "0.20")
        ]
        self.assertEqual(summarize_sales_by_product(rows), {"A": "0.30"})
        self.assertEqual(get_data_overview(rows)["average_sales"], "0.15")
        rows[0]["sales_amount"], rows[1]["sales_amount"] = "0.01", "0.02"
        self.assertEqual(get_data_overview(rows)["average_sales"], "0.02")

    def test_bom_whitespace_and_extra_column(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bom.csv"
            source.write_text(
                "date,product,sales_amount,data_source\n"
                "2026-10-01, Coffee , 0.5 ,SIMULATED\n",
                encoding="utf-8-sig",
            )
            self.assertEqual(load_sales_csv(source), [
                {"date": "2026-10-01", "product": "Coffee", "sales_amount": "0.50"},
            ])

    def test_rejects_invalid_csv_and_rows(self):
        cases = [
            ("", "表头"),
            (HEADER, "没有数据行"),
            ("date,product\n2026-10-01,A\n", "缺少必要字段"),
            ("date,product,sales_amount,product\n", "重复字段"),
            ("date,product,sales_amount,\n", "空字段名"),
            (HEADER + "2026-02-30,A,1.00\n", "第 2 行"),
            (HEADER + "20261001,A,1.00\n", "YYYY-MM-DD"),
            (HEADER + "2026-10-01, ,1.00\n", "product"),
            (HEADER + "2026-10-01,A\n", "列数"),
            (HEADER + "2026-10-01,A,1.00,extra\n", "列数"),
            (HEADER + '2026-10-01,"unclosed,1.00\n', "CSV 格式"),
            *[(HEADER + f"2026-10-01,A,{value}\n", "sales_amount")
              for value in ("", "abc", "-1", "NaN", "Infinity", "0.001", "1e2")],
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.csv"
            for content, message in cases:
                with self.subTest(content=content):
                    source.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(SalesDataError, message):
                        load_sales_csv(source)
            source.write_bytes(b"\xff\xfe\x00\x00")
            with self.assertRaisesRegex(SalesDataError, "UTF-8"):
                load_sales_csv(source)

    def test_missing_file_and_empty_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_sales_csv(Path(directory) / "missing.csv")
        for function in (get_data_overview, summarize_sales_by_product):
            with self.subTest(function=function.__name__):
                with self.assertRaises(SalesDataError):
                    function([])

    def test_full_workflow_and_invalid_input_has_no_outputs(self):
        original = SAMPLE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "results"
            result = run_analysis(SAMPLE, destination)
            json.dumps(result)
            self.assertEqual(result["overview"]["total_sales"], "900.00")
            self.assertEqual(Path(result["chart_path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            report = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("| Coffee | 400.00 |", report)
            self.assertIn("| 总销售额 | 900.00 |", report)
            self.assertIn("](sales_by_product.png)", report)
            bad_source = Path(directory) / "bad.csv"
            bad_source.write_text(HEADER + "2026-10-01,A,-1\n", encoding="utf-8")
            rejected_destination = Path(directory) / "rejected"
            with self.assertRaises(SalesDataError):
                run_analysis(bad_source, rejected_destination)
            self.assertFalse(rejected_destination.exists())
            collision = Path(directory) / "report.md"
            collision.write_bytes(original)
            with self.assertRaisesRegex(SalesDataError, "输出路径相同"):
                run_analysis(collision, directory)
            self.assertEqual(collision.read_bytes(), original)
        self.assertEqual(SAMPLE.read_bytes(), original)

    def test_zero_sales_chart_and_empty_chart_error(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = generate_sales_chart({"Zero": "0.00"}, Path(directory) / "zero.png")
            self.assertGreater(Path(chart).stat().st_size, 100)
            with self.assertRaises(ValueError):
                generate_sales_chart({}, Path(directory) / "empty.png")

    def test_report_escapes_product_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = write_markdown_report(
                get_data_overview(load_sales_csv(SAMPLE)),
                {"A|B\n<script>": "1.00"}, root / "chart.png", root / "report.md", "mock.csv",
            )
            content = Path(report).read_text(encoding="utf-8")
            self.assertIn("A\\|B &lt;script&gt;", content)
            self.assertNotIn("<script>", content)


if __name__ == "__main__":
    unittest.main()
