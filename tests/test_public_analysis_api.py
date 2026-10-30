"""公开实例只读白名单与旧本机审计/导出接口的隔离回归。"""

from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_review_compression as fixture
from test_review_workbench import WorkbenchFixture
from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, finish_task, get_task
from data_analysis_agent.reviews.monitor_store import init_monitoring


class PublicAnalysisApiTests(WorkbenchFixture, unittest.TestCase):
    scope = fixture.CompressionTests.scope
    result = fixture.CompressionTests.result

    def setUp(self):
        super().setUp()
        init_monitoring(self.db)
        self.tasks = self.root / "tasks.sqlite3"
        init_tasks(self.tasks, self.db)
        self.full, _ = self.result("好评主要方面")
        self.task_id = enqueue(self.tasks, "analysis", {**self.scope(), "question": "好评主要方面"})["task"]["task_id"]
        worker_started(self.tasks, "fixture-worker")
        claim_task(self.tasks, "fixture-worker")
        finish_task(self.tasks, self.task_id, "fixture-worker", status="succeeded", result=self.full)
        app = create_app(self.db, self.tasks, import_root=self.root, public_only=True)
        self.client = self.enterContext(TestClient(app, base_url="http://127.0.0.1"))

    def test_public_route_contains_statistics_but_no_raw_snapshot_or_identity(self):
        response = self.client.get(f"/api/v1/public/tasks/{self.task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["result"]
        self.assertEqual(set(body), {"task_id", "status", "public_analysis", "public_answer"})
        self.assertEqual(body["public_answer"]["status"], "ok")
        self.assertEqual(body["public_analysis"]["status"], "ok")
        self.assertEqual(body["public_analysis"]["source_result_ids"], ["r1"])
        for marker in ("base_url", "prompt_version", "trace", "model_identity", "private-agent-marker", "results", "validation"):
            self.assertNotIn('"' + marker + '"', response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_public_only_has_no_legacy_audit_or_mutation_escape(self):
        for path in (f"/api/v1/tasks/{self.task_id}", "/api/v1/tasks", "/api/v1/reviews/1", "/api/v1/options", "/api/v1/health"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.post("/api/v1/tasks", json={}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/public/tasks/{self.task_id}?view=internal").json()["result"]["public_analysis"]["status"], "ok")

    def test_existing_owner_api_keeps_full_historical_payload(self):
        app = create_app(self.db, self.tasks, import_root=self.root, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as owner:
            saved = owner.get(f"/api/v1/tasks/{self.task_id}").json()["result"]
            self.assertEqual(saved["result"], self.full)
            self.assertIn("trace", saved["result"])
        self.assertEqual(get_task(self.tasks, self.task_id)["result"], self.full)

    def test_public_route_rejects_untrusted_host_origin_and_nonexistent_task(self):
        path = f"/api/v1/public/tasks/{self.task_id}"
        for headers in ({"Host": "untrusted.invalid"}, {"Origin": "https://untrusted.invalid"}):
            self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/public/tasks/missing").status_code, 404)

    def test_historical_public_reads_do_not_requery_generate_or_write(self):
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.db, self.tasks)]
        with patch('data_analysis_agent.agents.reviews.run_review_agent', side_effect=AssertionError('no generation')) as agent, \
             patch('data_analysis_agent.reviews.analytics.get_positive_aspects', side_effect=AssertionError('no statistics')) as query:
            first = self.client.get(f'/api/v1/public/tasks/{self.task_id}').json()
            self.assertEqual(self.client.get(f'/api/v1/public/tasks/{self.task_id}').json(), first)
            agent.assert_not_called()
            query.assert_not_called()
        self.assertEqual([hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.db, self.tasks)], before)

    def test_partial_and_unknown_historical_shape_do_not_publish(self):
        for status, result, expected in (("partial", deepcopy(self.full), "partial"), ("succeeded", [self.full], "failed")):
            new_id = enqueue(self.tasks, "analysis", {**self.scope(), "question": "好评主要方面"})["task"]["task_id"]
            claim_task(self.tasks, "fixture-worker")
            finish_task(self.tasks, new_id, "fixture-worker", status=status, result=result)
            response = self.client.get(f"/api/v1/public/tasks/{new_id}")
            self.assertEqual(response.status_code, 200, response.text)
            public = response.json()["result"]["public_analysis"]
            self.assertEqual(public["status"], expected)
            self.assertEqual(public["statistics"], [])
            self.assertEqual(public["evidence_refs"], [])


if __name__ == "__main__":
    unittest.main()
