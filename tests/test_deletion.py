"""导入批次和任务历史删除的 API/存储联调检查。"""

import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import claim_task, enqueue, finish_task, init_tasks, worker_started
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl, init_database


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.business = self.root / "reviews.sqlite3"
        self.tasks = self.root / "reviews.tasks.sqlite3"
        init_database(self.business)
        init_labeling(self.business)
        init_workbench(self.business)
        init_monitoring(self.business)
        init_tasks(self.tasks, self.business)
        self.source = self.root / "reviews.jsonl"
        self.source.write_text(json.dumps({
            "parent_asin": "P1", "user_id": "u1", "title": "title", "rating": 5,
            "text": "good", "timestamp": int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        }) + "\n", encoding="utf-8")

    def app(self):
        return create_app(self.business, self.tasks, import_root=self.root, embedded_worker=False)

    def test_delete_imported_data_then_delete_task_record(self):
        imported = import_amazon_jsonl(self.business, reviews_path=self.source)
        task = enqueue(self.tasks, "import", {"reviews_path": str(self.source)})["task"]
        worker_started(self.tasks, "deletion-test-worker")
        claim_task(self.tasks, "deletion-test-worker")
        finish_task(self.tasks, task["task_id"], "deletion-test-worker", status="succeeded", result=imported)
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            response = client.delete(f"/api/v1/tasks/{task['task_id']}/imported-data")
            self.assertEqual(response.status_code, 200, response.text)
            deletion = response.json()["result"]["deletion"]
            self.assertEqual(deletion["deleted_reviews"], 1)
            self.assertEqual(client.get("/api/v1/quality").json()["result"]["review_count"], 0)
            self.assertTrue(response.json()["result"]["task"]["result"]["import_data_deleted"])
            repeat = client.delete(f"/api/v1/tasks/{task['task_id']}/imported-data")
            self.assertEqual(repeat.status_code, 200, repeat.text)
            self.assertTrue(repeat.json()["result"]["reused"])
            removed = client.delete(f"/api/v1/tasks/{task['task_id']}")
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["result"]["deleted_tasks"], 1)
            self.assertEqual(client.get(f"/api/v1/tasks/{task['task_id']}").status_code, 404)
        with closing(connect_review_database(self.business, readonly=True)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_running_task_cannot_be_deleted(self):
        task = enqueue(self.tasks, "analysis", {})["task"]
        worker_started(self.tasks, "deletion-test-worker")
        claim_task(self.tasks, "deletion-test-worker")
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            response = client.delete(f"/api/v1/tasks/{task['task_id']}")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "conflict")


if __name__ == "__main__":
    unittest.main()
