"""依赖方向及共享连接/输入契约的回归检查。"""

import ast
import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from data_analysis_agent.api.schemas import TaskInput
from data_analysis_agent.core.database import connect_database
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.jobs.parameters import PARAMETERS
from data_analysis_agent.jobs.store import connect_task_database, init_tasks
from data_analysis_agent.reviews.store import connect_review_database, init_database

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/data_analysis_agent"


class DependencyBoundaryTests(unittest.TestCase):
    def test_implementation_uses_public_interfaces_and_one_connection_factory(self):
        for path in PACKAGE.rglob("*.py"):
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("data_analysis_agent"):
                        self.assertTrue(all(not name.name.startswith("_") for name in node.names), ast.unparse(node))
                        if path.parent.name == "core":
                            self.assertTrue(node.module.startswith("data_analysis_agent.core"), node.module)
                        if path.parent.name in {"jobs", "reviews"}:
                            self.assertFalse(node.module.startswith("data_analysis_agent.api"), node.module)
                        if path.parent.name == "routes":
                            self.assertNotEqual(node.module, "data_analysis_agent.api.app")
                    if isinstance(node, ast.Call) and path != PACKAGE / "core/database.py":
                        self.assertNotIn(ast.unparse(node.func),
                                         {"sqlite3.connect", "psycopg.connect", "PostgresConnection"})

    def test_database_and_worker_import_without_higher_layers(self):
        cases = [
            ("data_analysis_agent.core.database", ["data_analysis_agent." + name for name in
              ("reviews", "jobs", "api", "agents", "sales", "cli")]),
            ("data_analysis_agent.jobs.worker", ["data_analysis_agent.api", "fastapi", "starlette", "openai",
                                                "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.jobs.submission", ["openai", "data_analysis_agent.jobs.worker",
                                                    "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.api.routes.tasks", ["openai", "data_analysis_agent.jobs.worker",
                                                     "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.api.app", ["openai", "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.cli.labeling", ["openai", "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.core.serialization", ["dotenv", "data_analysis_agent.core.config"]),
            ("data_analysis_agent.reviews.opinions", ["openai", "data_analysis_agent.reviews.labeling",
                                                     "data_analysis_agent.core.config"]),
            ("data_analysis_agent.reviews.analytics", ["openai", "data_analysis_agent.reviews.labeling"]),
            ("data_analysis_agent.reviews.human_review", ["openai", "data_analysis_agent.reviews.labeling"]),
        ]
        for module, blocked in cases:
            with self.subTest(module=module):
                code = f"""
import importlib.abc
import sys
class BlockHigherLayers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in {blocked!r}):
            raise AssertionError('Unexpected dependency: ' + fullname)
sys.meta_path.insert(0, BlockHigherLayers())
import {module}
"""
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
                result = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                                        capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ConnectionContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_factory_never_creates_files_and_preserves_readonly_and_foreign_keys(self):
        path = self.root / "raw.sqlite3"
        for readonly in (False, True):
            with self.assertRaises(sqlite3.OperationalError):
                connect_database(path, readonly=readonly)
            self.assertFalse(path.exists())
        path.touch()
        with closing(connect_database(path)) as connection, connection:
            connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO child VALUES(42)")
            connection.execute("INSERT INTO parent VALUES(1)")
        with closing(connect_database(path, readonly=True)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM parent").fetchone()["id"], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM parent")

    def test_checked_connectors_validate_identity_and_preserve_queue_timeout(self):
        business, tasks = self.root / "reviews.db", self.root / "tasks.db"
        init_database(business)
        init_tasks(tasks, business)
        for connect, path, timeout in ((connect_review_database, business, 5000),
                                       (connect_task_database, tasks, 10000)):
            with closing(connect(path, readonly=True)) as connection:
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], timeout)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(ReviewDataError):
            connect_review_database(tasks)
        with self.assertRaises(ReviewDataError):
            connect_task_database(business)

    def test_failed_schema_check_closes_connection(self):
        path = self.root / "uninitialized.db"
        path.touch()
        for module_name, connect in (("reviews.store", connect_review_database),
                                     ("jobs.store", connect_task_database)):
            with self.subTest(module=module_name):
                connection = connect_database(path)
                module = importlib.import_module("data_analysis_agent." + module_name)
                with patch.object(module, "connect_database", return_value=connection):
                    with self.assertRaises(ReviewDataError):
                        connect(path)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")


class ParameterContractTests(unittest.TestCase):
    def setUp(self):
        identity = {"model": "demo", "base_url": "https://example.invalid/v1", "prompt_version": "v1"}
        self.payloads = {
            "import": {"reviews_path": "sample.jsonl", "max_reviews": 3},
            "label": {"product_ids": ["P1"], "workers": 2, "retry_transient": True},
            "analysis": {**identity, "date_from": "2020-01-01", "date_to": "2020-01-31",
                         "baseline_from": "2019-12-01", "baseline_to": "2019-12-31"},
            "replay": {**identity, "month_from": "2020-01", "month_to": "2020-03"},
        }

    def test_task_payloads_roundtrip_and_http_envelopes_keep_contract(self):
        adapter = TypeAdapter(TaskInput)
        for kind, payload in self.payloads.items():
            with self.subTest(kind=kind):
                task = PARAMETERS[kind].model_validate(payload)
                restored = PARAMETERS[kind].model_validate_json(task.model_dump_json())
                request = adapter.validate_python({"kind": kind, "parameters": payload})
                self.assertEqual(restored.model_dump(), task.model_dump())
                self.assertEqual(request.model_dump(), {"kind": kind, "parameters": task.model_dump()})
                self.assertEqual(type(request.parameters).__module__, "data_analysis_agent.jobs.parameters")
                for field in ("page", "page_size", "kind", "model_identity"):
                    with self.assertRaises(ValidationError):
                        PARAMETERS[kind].model_validate({**payload, field: 1})
        self.assertEqual(PARAMETERS["label"]().model_dump()["limit"], 20)
        self.assertEqual(PARAMETERS["analysis"](**self.payloads["analysis"]).rules.min_samples, 20)

    def test_invalid_persisted_inputs_and_http_requests_are_both_rejected(self):
        cases = [
            ("import", {"max_reviews": True}), ("import", {"meta_path": "meta.jsonl", "max_reviews": 1}),
            ("label", {"workers": True}), ("label", {"date_from": "2020-02-30"}),
            ("label", {"retry_transient": "true"}), ("label", {"retry_failed": True}),
            ("analysis", {"baseline_to": "2020-01-01"}),
            ("analysis", {"rules": {"min_samples": True}}),
            ("replay", {"month_to": "2019-12"}), ("replay", {"month_from": "0001-01"}),
        ]
        adapter = TypeAdapter(TaskInput)
        for kind, changes in cases:
            with self.subTest(kind=kind, changes=changes):
                payload = {**self.payloads[kind], **changes}
                if "meta_path" in changes:
                    payload.pop("reviews_path", None)
                with self.assertRaises(ValidationError):
                    PARAMETERS[kind].model_validate(payload)
                with self.assertRaises(ValidationError):
                    adapter.validate_python({"kind": kind, "parameters": payload})

    def test_shared_json_retains_duplicate_checks_and_stable_hashes(self):
        for value in ('{"a":1,"a":2}', '{"value":NaN}', '{"value":Infinity}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads(value)
        self.assertEqual(canonical_json({"b": "中文", "a": 1}), '{"a":1,"b":"中文"}')
        self.assertEqual(content_sha256({"b": 2, "a": 1}),
                         "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777")


if __name__ == "__main__":
    unittest.main()
