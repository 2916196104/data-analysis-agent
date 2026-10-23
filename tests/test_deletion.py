"""导入批次和任务历史删除的 API/存储联调检查。"""

import json
import gzip
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import claim_task, enqueue, finish_task, init_tasks, worker_started
from data_analysis_agent.jobs.worker import TaskWorker
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

    def test_browser_upload_accepts_any_local_filename_and_returns_safe_import_path(self):
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            response = client.post(
                "/api/v1/import-files?kind=reviews&filename=C%3A%2FUsers%2Fme%2F%E4%BB%BB%E6%84%8F%E7%9B%AE%E5%BD%95%2Freviews.jsonl",
                content=b'{"parent_asin":"P1"}\n',
            )
            self.assertEqual(response.status_code, 201, response.text)
            result = response.json()["result"]
            self.assertEqual(result["filename"], "reviews.jsonl")
            self.assertTrue(result["path"].startswith(".uploads/"))
            self.assertEqual((self.root / result["path"]).read_bytes(), b'{"parent_asin":"P1"}\n')
            invalid = client.post("/api/v1/import-files?kind=reviews&filename=reviews.xlsx", content=b"x")
            self.assertEqual(invalid.status_code, 400, invalid.text)

    def test_uploaded_comments_and_gzip_metadata_reach_real_worker(self):
        content = self.source.read_bytes()
        metadata = gzip.compress(json.dumps({"parent_asin": "P1", "title": "本机中文商品"}).encode() + b"\n")
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            paths = {}
            for kind, filename, body in (("reviews", "评论 样本.JSONL", content), ("meta", "商品信息.jsonl.gz", metadata)):
                uploaded = client.post("/api/v1/import-files", params={"kind": kind, "filename": filename}, content=body)
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                paths[f"{kind}_path"] = uploaded.json()["result"]["path"]
                self.assertEqual((self.root / paths[f"{kind}_path"]).read_bytes(), body)
            submitted = client.post("/api/v1/tasks", json={"kind": "import", "parameters": paths})
            self.assertEqual(submitted.status_code, 202, submitted.text)
            task_id = submitted.json()["result"]["task"]["task_id"]
            worker = TaskWorker(self.business, self.tasks, import_root=self.root, env_file=self.root / 'unused.env')
            worker_started(self.tasks, worker.worker_id)
            worker._execute(claim_task(self.tasks, worker.worker_id))
            task = client.get(f"/api/v1/tasks/{task_id}").json()["result"]
            self.assertEqual(task["status"], "succeeded", task["error"])
            self.assertEqual(client.get("/api/v1/quality").json()["result"]["review_count"], 1)
        with closing(connect_review_database(self.business, readonly=True)) as connection:
            row = connection.execute("SELECT text FROM reviews").fetchone()
            self.assertEqual(row["text"], "good")
            self.assertEqual(connection.execute("SELECT title FROM products").fetchone()[0], "本机中文商品")

    def test_uploaded_chinese_csv_reaches_worker_and_keeps_source_and_product(self):
        content = ('商品ID,用户ID,打分,商品名称,评论内容,评论时间\n'
                   '000123,000456,5,模拟商品,好用,2024/8/21 17:30\n').encode('gb18030')
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            uploaded = client.post('/api/v1/import-files', params={'kind': 'reviews', 'filename': '京东 评论.CSV'}, content=content)
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            path = uploaded.json()['result']['path']
            submitted = client.post('/api/v1/tasks', json={'kind': 'import', 'parameters': {'reviews_path': path, 'dataset_version': 'csv-test'}})
            self.assertEqual(submitted.status_code, 202, submitted.text)
            task_id = submitted.json()['result']['task']['task_id']
            worker = TaskWorker(self.business, self.tasks, import_root=self.root, env_file=self.root / 'unused.env')
            worker_started(self.tasks, worker.worker_id)
            worker._execute(claim_task(self.tasks, worker.worker_id))
            task = client.get(f'/api/v1/tasks/{task_id}').json()['result']
            self.assertEqual(task['status'], 'succeeded', task['error'])
            self.assertEqual(task['result']['imports'][0]['source'], 'reviews_csv')
            self.assertEqual(client.post('/api/v1/import-files', params={'kind': 'meta', 'filename': 'meta.csv'}, content=content).status_code, 400)
        with closing(connect_review_database(self.business, readonly=True)) as connection:
            row = connection.execute('SELECT product_id, user_id, text FROM reviews').fetchone()
            self.assertEqual(tuple(row), ('000123', '000456', '好用'))
            self.assertEqual(connection.execute('SELECT title FROM products').fetchone()[0], '模拟商品')

    def test_failed_uploads_leave_no_partial_files(self):
        with TestClient(self.app(), base_url="http://127.0.0.1") as client:
            empty = client.post("/api/v1/import-files", params={"kind": "reviews", "filename": "empty.jsonl"}, content=b"")
            self.assertEqual(empty.status_code, 400, empty.text)
            with patch("data_analysis_agent.api.routes.tasks.UPLOAD_LIMIT", 4):
                too_big = client.post("/api/v1/import-files", params={"kind": "reviews", "filename": "large.jsonl"}, content=b"12345")
                self.assertEqual(too_big.status_code, 400, too_big.text)
                streamed = client.post("/api/v1/import-files", params={"kind": "reviews", "filename": "stream.jsonl"}, content=iter([b"12", b"345"]))
                self.assertEqual(streamed.status_code, 400, streamed.text)
            self.assertEqual(list((self.root / '.uploads').iterdir()), [])


if __name__ == "__main__":
    unittest.main()
