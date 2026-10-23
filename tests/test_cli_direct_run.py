"""验证与 IDE Run 按钮相同的基于文件的调用方式。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "data_analysis_agent"


class DirectRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_cli(self, name, *args, module=False):
        target = ["-m", f"data_analysis_agent.{name}"] if module else [str(PACKAGE / f"{name}.py")]
        env = {**os.environ, "PYTHONUTF8": "1"}
        env.pop("PYTHONPATH", None)
        if module:
            env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, *target, *args], cwd=self.directory, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_all_direct_entries_without_arguments_show_help(self):
        for name in ("cli/reviews", "cli/labeling", "cli/analytics",
                     "cli/monitoring", "api/workbench", "cli/server", "cli/migrate",
                     "cli/agent", "sales/workflow", "__main__"):
            with self.subTest(entry=name):
                result = self.run_cli(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("--help", result.stdout)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_direct_arguments_preserve_working_directory_and_match_module_results(self):
        result = self.run_cli("cli/reviews", "init", "--db", "reviews.sqlite3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")
        self.assertTrue((self.directory / "reviews.sqlite3").is_file())
        direct = self.run_cli("cli/reviews", "summary", "--db", "reviews.sqlite3")
        module = self.run_cli("cli.reviews", "summary", "--db", "reviews.sqlite3", module=True)
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.assertEqual(module.returncode, 0, module.stdout + module.stderr)
        self.assertEqual(json.loads(direct.stdout), json.loads(module.stdout))

    def test_direct_invalid_arguments_and_module_required_arguments_still_fail(self):
        for name, args, module in (("cli/reviews", ("summary",), False),
                                   ("cli.reviews", (), True)):
            with self.subTest(args=args, module=module):
                result = self.run_cli(name, *args, module=module)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("required", result.stderr)
                self.assertNotIn("ImportError", result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_sales_canonical_entries_produce_the_same_result(self):
        arguments = ("--input", str(ROOT / "data/samples/sales_mock.csv"),
                     "--output-dir", str(self.directory / "results"))
        expected = None
        for entry, module in (("sales.workflow", True), ("cli.sales", True), ("cli/sales", False)):
            with self.subTest(entry=entry):
                result = self.run_cli(entry, *arguments, module=module)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actual = json.loads(result.stdout)
                if expected is None:
                    expected = actual
                self.assertEqual(actual, expected)
                self.assertTrue(Path(actual["report_path"]).is_file())
                self.assertTrue(Path(actual["chart_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
