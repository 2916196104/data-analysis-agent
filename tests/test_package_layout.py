"""Check canonical imports, entry points and packaged assets."""

import ast
import importlib
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/data_analysis_agent"
MODULES = {
    "agent": "agents.sales", "agent_tools": "agents.tools", "agent_cli": "cli.agent",
    "analysis": "sales.analysis", "data_loader": "sales.loader", "reporting": "sales.reporting",
    "main": "sales.workflow", "config": "core.config", "database": "core.database",
    "review_loader": "reviews.loader", "review_store": "reviews.store", "review_catalog": "reviews.catalog",
    "review_labeler": "reviews.labeling", "review_label_store": "reviews.label_store",
    "review_scope": "reviews.scope", "review_analytics": "reviews.analytics",
    "review_monitor": "reviews.monitoring", "review_monitor_store": "reviews.monitor_store",
    "review_workbench_store": "reviews.human_review", "review_task_store": "jobs.store",
    "review_task_worker": "jobs.worker", "review_api": "api.app", "review_api_schemas": "api.schemas",
    "review_workbench": "api.workbench", "review_cli": "cli.reviews", "review_label_cli": "cli.labeling",
    "review_analysis_cli": "cli.analytics", "review_monitor_cli": "cli.monitoring",
    "review_api_cli": "cli.server", "review_migrate": "cli.migrate",
}


class PackageLayoutTests(unittest.TestCase):
    def test_canonical_modules_replace_root_level_forwarders(self):
        for old, new in MODULES.items():
            with self.subTest(module=old):
                self.assertFalse((PACKAGE / f"{old}.py").exists())
                self.assertIsNotNone(importlib.import_module("data_analysis_agent." + new))
        labeling = importlib.import_module("data_analysis_agent.reviews.labeling")
        replacement = object()
        with patch("data_analysis_agent.reviews.labeling.OpenAI", replacement):
            self.assertIs(labeling.OpenAI, replacement)

    def test_implementation_imports_do_not_go_through_legacy_modules(self):
        legacy = {"data_analysis_agent." + name for name in MODULES}
        for folder in ("agents", "sales", "reviews", "jobs", "api", "cli", "core"):
            for path in (PACKAGE / folder).rglob("*.py"):
                with self.subTest(file=str(path.relative_to(PACKAGE))):
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            self.assertNotIn(node.module, legacy)
                            if node.module == "data_analysis_agent":
                                self.assertTrue(all(name.name not in MODULES for name in node.names))
                        elif isinstance(node, ast.Import):
                            self.assertTrue(all(name.name not in legacy for name in node.names))

    def test_console_targets_and_new_direct_entries(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = project["project"]["scripts"]
        self.assertEqual(set(scripts), {"sales-analysis", "sales-agent", "review-import", "review-label",
                                       "review-analysis", "review-monitor", "review-workbench", "review-api",
                                       "review-migrate"})
        with tempfile.TemporaryDirectory() as directory:
            for target in scripts.values():
                module_name, function = target.split(":")
                self.assertTrue(callable(getattr(importlib.import_module(module_name), function)))
                direct = ROOT / "src" / (module_name.replace(".", "/") + ".py")
                for command in ([str(direct)], ["-m", module_name, "--help"]):
                    with self.subTest(command=command):
                        env = {**os.environ, "PYTHONUTF8": "1"}
                        if command[0] == "-m":
                            env["PYTHONPATH"] = str(ROOT / "src")
                        else:
                            env.pop("PYTHONPATH", None)
                        result = subprocess.run([sys.executable, *command], cwd=directory, env=env,
                                                capture_output=True, text=True, encoding="utf-8", timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("--help", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_workbench_resources_and_idea_script_paths(self):
        from data_analysis_agent.api.workbench import ASSET_DIR
        import xml.etree.ElementTree as ET

        resources = files("data_analysis_agent.api").joinpath("workbench_assets")
        for name in ("index.html", "workbench.js", "workbench.css"):
            self.assertEqual(resources.joinpath(name).read_bytes(), (ASSET_DIR / name).read_bytes())
            self.assertGreater(len((ASSET_DIR / name).read_bytes()), 0)
        for config in (ROOT / ".run").glob("*.xml"):
            for option in ET.parse(config).iter("option"):
                value = option.get("value", "")
                if option.get("name") == "SCRIPT_NAME" and value.startswith("$PROJECT_DIR$/src/"):
                    with self.subTest(configuration=config.name):
                        self.assertTrue((ROOT / value.removeprefix("$PROJECT_DIR$/")).is_file())


if __name__ == "__main__":
    unittest.main()
