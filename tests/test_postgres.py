"""可选集成检查；只创建和删除名称唯一的测试数据库。"""

import hashlib
import io
import json
import os
import sqlite3
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from data_analysis_agent.core.database import (DatabaseError, PostgresConnection, _parameters,
                                          database_argument, database_identity, table_names)
from data_analysis_agent.api.app import create_app
from data_analysis_agent.cli.server import default_task_database
from data_analysis_agent.reviews.analytics import compare_products, get_evidence, get_top_issues, get_trend
from data_analysis_agent.reviews.catalog import list_products
from data_analysis_agent.reviews.label_store import init_labeling, labeling_status, pending_reviews, save_annotation
from data_analysis_agent.cli.migrate import migrate_sqlite
from data_analysis_agent.reviews.loader import ReviewDataError
from data_analysis_agent.reviews.monitoring import replay_monthly, run_analysis
from data_analysis_agent.reviews.monitor_store import get_analysis_run, init_monitoring, list_alerts, set_alert_status
from data_analysis_agent.reviews.store import connect_review_database, get_quality_summary, import_amazon_jsonl, init_database
from data_analysis_agent.jobs.store import (TaskConflict, claim_task, enqueue, finish_task, get_task,
                                                 init_tasks, queue_health, worker_started)
from data_analysis_agent.jobs.worker import TaskWorker, WorkerLock
from data_analysis_agent.reviews.human_review import (ReviewConflict, init_workbench, list_reviews,
                                                       review_detail, save_human_review, workbench_options)
import test_review_monitor as monitor_fixture

IDENTITY = monitor_fixture.IDENTITY


class DatabaseConfigTests(unittest.TestCase):
    def test_secrets_and_parameters(self):
        target = "postgresql://user:private-password@localhost/sample?sslmode=require"
        self.assertEqual(database_identity(target), "postgresql://localhost:5432/sample")
        with patch.dict(os.environ, {"TEST_AGENT_DATABASE": target}):
            self.assertEqual(database_argument("env:TEST_AGENT_DATABASE"), target)
        self.assertEqual(default_task_database(target), target)
        self.assertEqual(_parameters("SELECT '?' AS question, ? WHERE '50%' <> ''"),
                         "SELECT '?' AS question, %s WHERE '50%%' <> ''")


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_ADMIN_URL"), "set TEST_POSTGRES_ADMIN_URL to enable PostgreSQL integration")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
        self.target = self.new_database()
        # 复用现有的模拟评论夹具，包含全部标注。
        fixture = monitor_fixture.MonitorTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.source = fixture.db
        self.options = fixture.options()

    def new_database(self):
        name = "agent_test_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        def cleanup():
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        self.addCleanup(cleanup)
        parts = urlsplit(self.admin_url)
        return urlunsplit(parts._replace(path="/" + name))

    def migrate(self, **options):
        return migrate_sqlite(self.source, self.target, **options)

    def test_migration_preserves_history_tools_and_sequences(self):
        invalid = self.fixture.root / "invalid.jsonl"
        invalid.write_bytes(b"\xff\n")
        import_amazon_jsonl(self.source, reviews_path=invalid)
        rid, annotation = next(iter(self.fixture.annotations.items()))
        saved = save_human_review(self.source, rid, annotation_id=annotation, expected_revision_id=None,
                                 action="confirmed", reviewer="迁移测试")
        original_run = run_analysis(self.source, **self.options)
        original_alert = original_run["alert_occurrences"][0]["alert_id"]
        set_alert_status(self.source, original_alert, status="confirmed", actor="迁移测试")
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.migrate()
        self.assertEqual(result["verified_tables"]["reviews"]["rows"], 25)
        self.assertEqual(result["verified_tables"]["import_errors"]["rows"], 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(get_quality_summary(self.target), get_quality_summary(self.source))
        self.assertEqual(workbench_options(self.target), workbench_options(self.source))
        self.assertEqual(list_products(self.target), list_products(self.source))
        self.assertEqual(labeling_status(self.target), labeling_status(self.source))
        self.assertEqual(list_reviews(self.target), list_reviews(self.source))
        self.assertEqual(review_detail(self.target, rid), review_detail(self.source, rid))
        self.assertEqual(get_analysis_run(self.target, original_run["run_id"]), get_analysis_run(self.source, original_run["run_id"]))
        self.assertEqual(list_alerts(self.target), list_alerts(self.source))
        common = {k: self.options[k] for k in ("model", "base_url", "prompt_version", "date_from", "date_to", "product_ids")}
        for tool in (get_trend, compare_products, get_evidence):
            self.assertEqual(tool(self.target, **common), tool(self.source, **common))
        issues = dict(common, baseline_from="2019-12-01", baseline_to="2019-12-31")
        self.assertEqual(get_top_issues(self.target, **issues), get_top_issues(self.source, **issues))
        self.assertTrue(run_analysis(self.target, **self.options)["reused"])
        changed = save_human_review(self.target, rid, annotation_id=annotation,
                                   expected_revision_id=saved["revision_id"], action="corrected", reviewer="PG", opinions=[])
        self.assertGreater(changed["revision_id"], saved["revision_id"])
        run = run_analysis(self.target, **self.options)
        self.assertFalse(run["reused"])
        replay = replay_monthly(self.target, **IDENTITY, month_from="2020-01", month_to="2020-03",
                                product_ids=["P1"], rules=self.options["rules"])
        self.assertTrue(replay["complete"], replay)

    def test_fresh_init_import_rollback_readonly_and_labeling(self):
        self.assertTrue(init_database(self.target)["created"])
        self.assertFalse(init_database(self.target)["created"])
        init_labeling(self.target)
        init_workbench(self.target)
        init_monitoring(self.target)
        init_tasks(self.target, self.target)
        source_file = next(self.fixture.root.glob("simulated-*.jsonl"))
        self.assertEqual(import_amazon_jsonl(self.target, reviews_path=source_file)["quality"]["review_count"], 25)
        self.assertEqual(import_amazon_jsonl(self.target, reviews_path=source_file)["imports"][0]["inserted_records"], 0)
        with closing(connect_review_database(self.target, readonly=True)) as connection:
            with self.assertRaises(DatabaseError):
                connection.execute("DELETE FROM reviews")
        review = pending_reviews(self.target, **IDENTITY, limit=1)[0]
        with self.assertRaises(ReviewDataError):
            save_annotation(self.target, review, **IDENTITY, status="success", opinions=[
                {"aspect": "quality", "sentiment": "positive", "quote": "伪造", "quote_start": 0, "quote_end": 2}])
        self.assertEqual(labeling_status(self.target)["profiles"], [])
        save_annotation(self.target, review, **IDENTITY, status="success", opinions=[])
        self.assertEqual(labeling_status(self.target)["profiles"][0]["successful_reviews"], 1)

    def test_migration_refuses_existing_target_and_rolls_back_failure(self):
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("UPDATE reviews SET text=? WHERE id=1", ("含\x00字符",))
        with self.assertRaisesRegex(ReviewDataError, "空字符"):
            self.migrate()
        with closing(PostgresConnection(self.target)) as connection:
            self.assertFalse(table_names(connection))
        with closing(sqlite3.connect(self.source)) as connection, connection:
            connection.execute("UPDATE reviews SET text='恢复' WHERE id=1")
        self.migrate()
        before = get_quality_summary(self.target)
        with self.assertRaisesRegex(ReviewDataError, "目标已有"):
            self.migrate()
        self.assertEqual(get_quality_summary(self.target), before)

    def test_task_history_and_live_queue_worker(self):
        task_file = self.source.with_name("tasks.sqlite3")
        init_tasks(task_file, self.source)
        worker_started(task_file, "old-worker")
        parent = enqueue(task_file, "analysis", {"demo": 1})["task"]["task_id"]
        claim_task(task_file, "old-worker")
        finish_task(task_file, parent, "old-worker", status="failed")
        child = enqueue(task_file, "analysis", {"demo": 1}, parent_task_id=parent)["task"]["task_id"]
        with self.assertRaisesRegex(ReviewDataError, "排队"):
            self.migrate(source_tasks=task_file)
        claim_task(task_file, "old-worker")
        finish_task(task_file, child, "old-worker", status="succeeded", result={"done": True})
        self.migrate(source_tasks=task_file)
        self.assertEqual(get_task(self.target, child), get_task(task_file, child))
        self.assertIsNone(queue_health(self.target)["worker"])
        worker = TaskWorker(self.target, self.target, import_root=self.fixture.root, env_file=self.fixture.root / ".env")
        worker.start()
        self.addCleanup(worker.stop)
        request = {k: v for k, v in self.options.items() if k != "rules"}
        submitted = enqueue(self.target, "analysis", request, idempotency_key="test-run")
        task_id = submitted["task"]["task_id"]
        self.assertTrue(enqueue(self.target, "analysis", request, idempotency_key="test-run")["reused"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            task = get_task(self.target, task_id)
            if task["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(task["status"], "succeeded", task)

    def test_concurrent_decisions_queue_and_worker_lock(self):
        self.migrate()
        rid, annotation = next(iter(self.fixture.annotations.items()))
        def save(_):
            try:
                return save_human_review(self.target, rid, annotation_id=annotation, expected_revision_id=None,
                                         action="confirmed", reviewer="并发测试")
            except ReviewConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(save, range(2)))
        self.assertEqual(decisions.count("conflict"), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: enqueue(self.target, "analysis", {}, idempotency_key="same"), range(4)))
        self.assertEqual(sum(not r["reused"] for r in results), 1)
        first, second = WorkerLock(self.target), WorkerLock(self.target)
        first.acquire()
        self.addCleanup(first.release)
        with self.assertRaises(TaskConflict):
            second.acquire()
        first.release()
        second.acquire()
        second.release()

    def test_http_and_cli_use_postgres_without_exposing_dsn(self):
        from fastapi.testclient import TestClient
        from data_analysis_agent.cli.reviews import main

        self.migrate()
        app = create_app(self.target, self.target, import_root=self.fixture.root, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            for path in ("health", "quality", "products", "options", "reviews", "labeling/status", "tasks", "analysis-runs", "alerts"):
                response = client.get("/api/v1/" + path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(self.target, response.text)
            with patch("data_analysis_agent.api.routes.reviews.get_quality_summary", side_effect=DatabaseError("safe failure")):
                self.assertEqual(client.get("/api/v1/quality").status_code, 503)
        output = io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": self.target}), patch("sys.argv", ["review_cli", "summary", "--db", "env:DATABASE_URL"]), redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())["quality"]["review_count"], 25)
