"""跨业务路由和独立应用实例的 HTTP 回归检查。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.database import DatabaseError
from data_analysis_agent.jobs.store import claim_task, finish_task, init_tasks, worker_started
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.monitoring import run_analysis
from data_analysis_agent.reviews.store import init_database
import test_review_monitor as fixtures


class BusinessRouterTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.MonitorTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.task_db = fixture.root / "tasks.sqlite3"
        init_tasks(self.task_db, fixture.db)
        self.run = run_analysis(fixture.db, **fixture.options())
        app = create_app(fixture.db, self.task_db, import_root=fixture.root, embedded_worker=False)
        self.client = self.enterContext(TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False))
        self.scope = {k: v for k, v in fixture.options().items() if k not in {"rules", "baseline_from", "baseline_to"}}

    def get(self, path, params=None):
        response = self.client.get("/api/v1" + path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        return response.json()["result"]

    def test_read_routes_keep_results_across_business_groups(self):
        review_id = next(iter(self.fixture.annotations))
        alert_id = self.run["alert_occurrences"][0]["alert_id"]
        self.assertEqual(self.get("/quality")["review_count"], 25)
        catalog = self.get("/options")
        self.assertIn("datasets", catalog)
        self.assertEqual(sum(item["review_count"] for item in catalog["datasets"]), 25)
        for dataset in catalog["datasets"]:
            self.assertEqual({item["profile_id"] for item in dataset["profiles"]},
                             {item["profile_id"] for item in catalog["profiles"]})
        self.assertFalse(self.get("/health")["worker_available"])
        paths = [
            ("/products", None), ("/options", None), ("/reviews", None),
            (f"/reviews/{review_id}", None), ("/labeling/status", None),
            ("/labeling/opinions", self.scope), (f"/labeling/evidence/{review_id}", None),
            ("/analytics/trend", self.scope), ("/analytics/compare-products", self.scope),
            ("/analytics/top-issues", {**self.scope, "baseline_from": "2019-12-01", "baseline_to": "2019-12-31"}),
            ("/analytics/evidence", {**self.scope, "sentiment": "all"}),
            ("/analytics/data-quality", self.scope),
            ("/analytics/aspect-trend", {**self.scope, "aspect": "packaging"}),
            ("/analytics/rating-distribution", {**self.scope, "group_by": "month"}),
            ("/analytics/compare-windows", {**self.scope, "baseline_from": "2019-12-01", "baseline_to": "2019-12-31"}),
            ("/analysis-runs", None), (f"/analysis-runs/{self.run['run_id']}", None),
            ("/alerts", None), (f"/alerts/{alert_id}", None), ("/tasks", None),
        ]
        for path, params in paths:
            with self.subTest(path=path):
                self.assertIsInstance(self.get(path, params), dict)
        summary = self.get("/analytics/trend", self.scope)["summary"]
        self.assertEqual(summary["review_count"], 4)
        self.assertEqual(summary["negative_review_count"], 3)

    def test_extended_analytics_reject_invalid_dimensions_and_overlapping_windows(self):
        for path, extra in [
            ("aspect-trend", {"aspect": "unknown"}), ("rating-distribution", {"group_by": "rating"}),
            ("compare-windows", {"baseline_from": self.scope["date_from"], "baseline_to": self.scope["date_to"]}),
            ("data-quality", {"unexpected": "field"}),
        ]:
            with self.subTest(path=path):
                response = self.client.get("/api/v1/analytics/" + path, params={**self.scope, **extra})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["status"], "error")

    def test_write_routes_keep_status_location_idempotency_and_retry(self):
        review_id, annotation_id = next(iter(self.fixture.annotations.items()))
        response = self.client.post(f"/api/v1/reviews/{review_id}/human-reviews", json={
            "annotation_id": annotation_id, "expected_revision_id": None,
            "action": "confirmed", "reviewer": "SIMULATED router test",
        })
        self.assertEqual(response.status_code, 201, response.text)
        alert_id = self.run["alert_occurrences"][0]["alert_id"]
        response = self.client.post(f"/api/v1/alerts/{alert_id}/actions", json={
            "status": "confirmed", "actor": "SIMULATED router test",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get(f"/alerts/{alert_id}")["status"], "confirmed")

        body = {"kind": "analysis", "parameters": self.fixture.options()}
        headers = {"Idempotency-Key": "router-regression"}
        response = self.client.post("/api/v1/tasks", json=body, headers=headers)
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["result"]["task"]["task_id"]
        self.assertEqual(response.headers["Location"], "/api/v1/tasks/" + task_id)
        repeated = self.client.post("/api/v1/tasks", json=body, headers=headers)
        self.assertTrue(repeated.json()["result"]["reused"])
        self.assertEqual(repeated.json()["result"]["task"]["task_id"], task_id)
        self.assertEqual(self.get(f"/tasks/{task_id}")["status"], "queued")
        worker_started(self.task_db, "simulated-worker")
        self.assertEqual(claim_task(self.task_db, "simulated-worker")["task_id"], task_id)
        finish_task(self.task_db, task_id, "simulated-worker", status="failed", error={"message": "simulated"})
        retried = self.client.post(f"/api/v1/tasks/{task_id}/retry")
        self.assertEqual(retried.status_code, 202, retried.text)
        child = retried.json()["result"]["task"]
        self.assertEqual(child["parent_task_id"], task_id)
        self.assertEqual(retried.headers["Location"], "/api/v1/tasks/" + child["task_id"])

    def test_apps_do_not_share_databases_queues_or_import_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            business, tasks = root / "reviews.db", root / "tasks.db"
            init_database(business)
            init_labeling(business)
            init_workbench(business)
            init_monitoring(business)
            init_tasks(tasks, business)
            app = create_app(business, tasks, import_root=root, embedded_worker=False)
            source = self.fixture.root / "scope-check.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            body = {"kind": "import", "parameters": {"reviews_path": str(source)}}
            with TestClient(app, base_url="http://127.0.0.1") as other:
                self.assertEqual(other.get("/api/v1/quality").json()["result"]["review_count"], 0)
                self.assertEqual(self.get("/quality")["review_count"], 25)
                response = self.client.post("/api/v1/tasks", json=body)
                self.assertEqual(response.status_code, 202, response.text)
                task_id = response.json()["result"]["task"]["task_id"]
                self.assertEqual(other.get(f"/api/v1/tasks/{task_id}").status_code, 404)
                self.assertEqual(other.post("/api/v1/tasks", json=body).status_code, 400)

    def test_validation_security_not_found_and_conflicts_still_use_error_envelopes(self):
        cases = [
            ("get", "/products?page_size=101", {}, 422, "validation_error"),
            ("get", "/reviews/999999", {}, 404, "not_found"),
            ("get", "/labeling/evidence/999999", {}, 404, "not_found"),
            ("get", "/analysis-runs/missing", {}, 404, "not_found"),
            ("get", "/alerts/999999", {}, 404, "not_found"),
            ("get", "/tasks/missing", {}, 404, "not_found"),
            ("get", "/missing-route", {}, 404, "http_error"),
            ("get", "/quality", {"headers": {"Origin": "https://untrusted.invalid"}}, 403, "origin_not_allowed"),
            ("get", "/quality", {"headers": {"Host": "untrusted.invalid"}}, 403, "host_not_allowed"),
        ]
        for method, path, kwargs, status, code in cases:
            with self.subTest(path=path, kwargs=kwargs):
                response = getattr(self.client, method)("/api/v1" + path, **kwargs)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["error"]["code"], code)
        response = self.client.post("/api/v1/tasks", json={"kind": "analysis", "parameters": self.fixture.options()})
        task_id = response.json()["result"]["task"]["task_id"]
        response = self.client.post(f"/api/v1/tasks/{task_id}/retry")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "conflict")

    def test_route_failures_keep_database_and_internal_errors_private(self):
        for error in (sqlite3.OperationalError("private info"), DatabaseError("private info"), RuntimeError("private info")):
            with self.subTest(error=type(error).__name__), patch(
                    "data_analysis_agent.api.routes.reviews.get_quality_summary", side_effect=error):
                response = self.client.get("/api/v1/quality")
                self.assertEqual(response.status_code, 500 if isinstance(error, RuntimeError) else 503)
                self.assertNotIn("private info", response.text)
                self.assertEqual(response.json()["status"], "error")


if __name__ == "__main__":
    unittest.main()
