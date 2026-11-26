"""性能优化专项测试：启动模型预热、SSE 任务进度推送与结果/审计证据分离存储。"""

from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql

from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.database import table_names
from data_analysis_agent.jobs.analysis_state import has_saved_integrity
from data_analysis_agent.jobs.result_storage import AUDIT_STORAGE_VERSION, rehydrate_task_result, split_task_result
from data_analysis_agent.jobs.store import (
    claim_task,
    connect_task_database,
    delete_task,
    enqueue,
    finish_task,
    get_task,
    get_task_audit,
    get_task_summary,
    init_tasks,
    list_tasks,
    update_progress,
)
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.embedding_process import warmup_embedding_process
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import init_database


def _parse_sse(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = 'message'
    data_lines: list[str] = []
    for line in raw_text.splitlines():
        if not line:
            if data_lines:
                events.append((event_name, json.loads('\n'.join(data_lines))))
            event_name = 'message'
            data_lines = []
        elif line.startswith('event:'):
            event_name = line.split(':', 1)[1].strip()
        elif line.startswith('data:'):
            data_lines.append(line.split(':', 1)[1].strip())
    return events


class PerformanceOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'business.sqlite3'
        self.task_db = self.root / 'tasks.sqlite3'
        self.import_root = self.root / 'imports'
        self.import_root.mkdir()
        self.env_file = self.root / '.env'
        self.env_file.write_text(
            'LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n',
            encoding='utf-8',
        )
        init_database(self.db)
        init_labeling(self.db)
        init_workbench(self.db)
        init_monitoring(self.db)
        init_tasks(self.task_db, self.db)
        self.parameters = {
            'model': 'demo',
            'base_url': 'https://offline.invalid/v1',
            'prompt_version': 'v1',
            'product_ids': ['P1'],
            'source': 'reviews_csv',
            'dataset_version': 'reviews-csv',
            'date_from': '2023-01-01',
            'date_to': '2023-01-31',
            'baseline_from': '2022-12-01',
            'baseline_to': '2022-12-31',
            'question': '物流差评具体体现在哪里？',
        }

    def test_rag_warmup_config_and_worker_hook(self):
        values = {
            'LLM_MODEL_ID': 'demo',
            'LLM_API_KEY': 'fake-key',
            'LLM_BASE_URL': 'https://offline.invalid/v1',
        }
        self.assertTrue(AgentSettings.from_values(values).rag_warmup_on_start)
        self.assertFalse(AgentSettings.from_values({**values, 'RAG_WARMUP_ON_START': '0'}).rag_warmup_on_start)

        fake_proc = MagicMock()
        fake_proc.health.return_value = True
        fake_proc.last_metrics = {'cold_start': True, 'model_load_ms': 123.4}
        with patch('data_analysis_agent.reviews.embedding_process.shared_embedding_process', return_value=fake_proc) as getter:
            self.assertTrue(warmup_embedding_process('python.exe', str(self.root), timeout=120))
            getter.assert_called_once_with('python.exe', str(self.root), timeout=120)
            fake_proc.health.assert_called_once()

        fake_python = self.root / 'rag-python.exe'
        fake_python.write_text('', encoding='utf-8')
        fake_cache = self.root / 'bge-m3'
        fake_cache.mkdir()
        self.env_file.write_text(
            f'LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n'
            f'RAG_PYTHON={fake_python}\nRAG_MODEL_CACHE={fake_cache}\nRAG_TIMEOUT_SECONDS=180\n',
            encoding='utf-8',
        )
        worker = TaskWorker(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file)
        with patch('data_analysis_agent.reviews.embedding_process.start_embedding_warmup') as start_warmup:
            worker._maybe_start_rag_warmup()
            start_warmup.assert_called_once_with(str(fake_python), str(fake_cache), timeout=180)

        self.env_file.write_text(
            f'LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n'
            f'RAG_PYTHON={fake_python}\nRAG_MODEL_CACHE={fake_cache}\nRAG_WARMUP_ON_START=0\n',
            encoding='utf-8',
        )
        with patch('data_analysis_agent.reviews.embedding_process.start_embedding_warmup') as start_warmup:
            worker._maybe_start_rag_warmup()
            start_warmup.assert_not_called()

    def test_split_result_and_audit_storage_roundtrip_and_tamper_detection(self):
        heavy_items = [
            {'review_id': idx, 'product_id': 'P1', 'text': f'第 {idx} 条评论正文，物流配送很慢。' * 4, 'quote': '物流配送很慢'}
            for idx in range(1, 401)
        ]
        full_result = {
            'agent': True,
            'status': 'ok',
            'question': self.parameters['question'],
            'scope': {k: v for k, v in self.parameters.items() if k != 'question'},
            'public_answer': {'schema_version': '1.0', 'status': 'ok', 'summary': '物流差评主要集中在配送慢。', 'highlights': [], 'caveats': []},
            'result_ids': ['r1'],
            'results': {
                'r1': {
                    'result_id': 'r1',
                    'kind': 'aspect_drilldown',
                    'parameters': {'aspect': 'logistics', 'sentiment': 'negative'},
                    'data': {
                        'aspect': 'logistics',
                        'topics': [{'topic_id': 't1', 'name': '配送慢', 'review_count': 400}],
                        'items': heavy_items,
                    },
                },
            },
            'trace': [{'event': 'tool', 'tool': 'get_aspect_drilldown', 'status': 'ok'}],
            'answer_sections': [{'result_id': 'r1', 'kind': 'aspect_drilldown', 'text': '详细文本' * 50}],
        }

        light, audit, digest = split_task_result(full_result, threshold_bytes=1024)
        self.assertIsNotNone(audit)
        self.assertIsNotNone(digest)
        self.assertIn('_audit_ref', light)
        self.assertNotIn('trace', light)
        self.assertNotIn('items', light['results']['r1']['data'])
        self.assertEqual(rehydrate_task_result(light, audit), full_result)

        queued = enqueue(self.task_db, 'analysis', self.parameters)['task']
        claimed = claim_task(self.task_db, 'worker-1')
        self.assertEqual(claimed['task_id'], queued['task_id'])
        finish_task(self.task_db, queued['task_id'], 'worker-1', status='succeeded',
                    result=full_result, audit_split_threshold=1024)

        with closing(connect_task_database(self.task_db, readonly=True)) as connection:
            raw_result_json = connection.execute(
                'SELECT result_json FROM tasks WHERE task_id=?', (queued['task_id'],)
            ).fetchone()[0]
            audit_row = connection.execute(
                'SELECT storage_version, audit_sha256, audit_json FROM task_audit_blobs WHERE task_id=?',
                (queued['task_id'],),
            ).fetchone()
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row['storage_version'], AUDIT_STORAGE_VERSION)
        # 轻量主表记录显著小于分离出的重型审计载荷
        self.assertLess(len(raw_result_json.encode('utf-8')), len(audit_row['audit_json'].encode('utf-8')) // 4)

        full_loaded = get_task(self.task_db, queued['task_id'])
        self.assertEqual(full_loaded['result'], full_result)
        self.assertTrue(has_saved_integrity(full_loaded))

        light_loaded = get_task(self.task_db, queued['task_id'], include_audit=False)
        self.assertIn('_audit_ref', light_loaded['result'])
        self.assertNotIn('items', light_loaded['result']['results']['r1']['data'])
        self.assertEqual(light_loaded['result']['public_answer']['summary'], '物流差评主要集中在配送慢。')

        audit_record = get_task_audit(self.task_db, queued['task_id'])
        self.assertTrue(audit_record['stored_separately'])
        self.assertEqual(audit_record['audit_sha256'], audit_row['audit_sha256'])
        self.assertEqual(len(audit_record['audit']['tool_data']['r1']['items']), 400)

        # HTTP 端点校验（include_audit=false 与 /audit）
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url='http://127.0.0.1:8000') as client:
            res_light = client.get(f"/api/v1/tasks/{queued['task_id']}?include_audit=false")
            self.assertEqual(res_light.status_code, 200)
            self.assertNotIn('items', res_light.json()['result']['result']['results']['r1']['data'])
            res_audit = client.get(f"/api/v1/tasks/{queued['task_id']}/audit")
            self.assertEqual(res_audit.status_code, 200)
            self.assertTrue(res_audit.json()['result']['stored_separately'])

        # 篡改审计表内容后，复原失败关闭且完整性校验拒绝通过
        with closing(connect_task_database(self.task_db)) as connection, connection:
            tampered = json.loads(audit_row['audit_json'])
            tampered['tool_data']['r1']['items'][0]['text'] = '被篡改的原文'
            connection.execute(
                'UPDATE task_audit_blobs SET audit_json=? WHERE task_id=?',
                (json.dumps(tampered, ensure_ascii=False), queued['task_id']),
            )
        tampered_loaded = get_task(self.task_db, queued['task_id'])
        self.assertFalse(has_saved_integrity(tampered_loaded))

        # 删除任务同步清理 task_audit_blobs
        delete_task(self.task_db, queued['task_id'])
        with closing(connect_task_database(self.task_db, readonly=True)) as connection:
            self.assertIsNone(
                connection.execute('SELECT 1 FROM task_audit_blobs WHERE task_id=?', (queued['task_id'],)).fetchone()
            )

    def test_sse_task_events_stream_live_progress_and_terminal_done(self):
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url='http://127.0.0.1:8000') as client:
            missing = client.get('/api/v1/tasks/nonexistent-task/events')
            self.assertEqual(missing.status_code, 404)

            queued = enqueue(self.task_db, 'analysis', self.parameters)['task']
            task_id = queued['task_id']
            claim_task(self.task_db, 'worker-sse')

            def drive_task():
                time.sleep(0.08)
                update_progress(self.task_db, task_id, 'worker-sse', {
                    'stage': 'agent_tool', 'completed': 1, 'total': 2, 'unit': 'tools',
                })
                time.sleep(0.08)
                finish_task(self.task_db, task_id, 'worker-sse', status='succeeded', result={'status': 'ok'})

            driver = threading.Thread(target=drive_task)
            driver.start()
            try:
                with client.stream('GET', f'/api/v1/tasks/{task_id}/events') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
                    raw = ''.join(response.iter_text())
            finally:
                driver.join(timeout=5)

            events = _parse_sse(raw)
            self.assertGreaterEqual(len(events), 2)
            self.assertEqual(events[0][0], 'progress')
            self.assertEqual(events[-1][0], 'done')
            self.assertEqual(events[-1][1]['status'], 'succeeded')
            self.assertNotIn('result', events[-1][1])
            self.assertEqual(get_task_summary(self.task_db, task_id)['status'], 'succeeded')

            # 已结束任务直接返回单条 done 事件并关闭流
            with client.stream('GET', f'/api/v1/tasks/{task_id}/events') as finished_res:
                finished_events = _parse_sse(''.join(finished_res.iter_text()))
            self.assertEqual(len(finished_events), 1)
            self.assertEqual(finished_events[0][0], 'done')


@unittest.skipUnless(
    os.environ.get('TEST_POSTGRES_ADMIN_URL'),
    'set TEST_POSTGRES_ADMIN_URL to run live PostgreSQL performance storage/SSE tests',
)
class PostgresPerformanceOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ['TEST_POSTGRES_ADMIN_URL']
        self.target = self._new_database()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.import_root = self.root / 'imports'
        self.import_root.mkdir()
        self.env_file = self.root / '.env'
        self.env_file.write_text(
            'LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n',
            encoding='utf-8',
        )
        init_database(self.target)
        init_labeling(self.target)
        init_workbench(self.target)
        init_monitoring(self.target)
        init_tasks(self.target, self.target)
        self.parameters = {
            'model': 'demo',
            'base_url': 'https://offline.invalid/v1',
            'prompt_version': 'v1',
            'product_ids': ['P1'],
            'source': 'reviews_csv',
            'dataset_version': 'reviews-csv',
            'date_from': '2023-01-01',
            'date_to': '2023-01-31',
            'baseline_from': '2022-12-01',
            'baseline_to': '2022-12-31',
            'question': '物流差评具体体现在哪里？',
        }

    def _new_database(self):
        name = 'agent_perf_test_' + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))

        def cleanup():
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))

        self.addCleanup(cleanup)
        parts = urlsplit(self.admin_url)
        return urlunsplit(parts._replace(path='/' + name))

    def _heavy_result(self):
        heavy_items = [
            {'review_id': idx, 'product_id': 'P1', 'text': f'第 {idx} 条评论正文，物流配送很慢。' * 4, 'quote': '物流配送很慢'}
            for idx in range(1, 301)
        ]
        return {
            'agent': True,
            'status': 'ok',
            'question': self.parameters['question'],
            'scope': {k: v for k, v in self.parameters.items() if k != 'question'},
            'public_answer': {'schema_version': '1.0', 'status': 'ok', 'summary': '物流差评主要集中在配送慢。', 'highlights': [], 'caveats': []},
            'result_ids': ['r1'],
            'results': {
                'r1': {
                    'result_id': 'r1',
                    'kind': 'aspect_drilldown',
                    'parameters': {'aspect': 'logistics', 'sentiment': 'negative'},
                    'data': {
                        'aspect': 'logistics',
                        'topics': [{'topic_id': 't1', 'name': '配送慢', 'review_count': 300}],
                        'items': heavy_items,
                    },
                },
            },
            'trace': [{'event': 'tool', 'tool': 'get_aspect_drilldown', 'status': 'ok'}],
            'answer_sections': [{'result_id': 'r1', 'kind': 'aspect_drilldown', 'text': '详细文本' * 40}],
        }

    def test_postgres_audit_split_rehydrate_tamper_upgrade_and_sse(self):
        with closing(connect_task_database(self.target, readonly=True)) as connection:
            self.assertIn('task_audit_blobs', table_names(connection))

        full_result = self._heavy_result()
        queued = enqueue(self.target, 'analysis', self.parameters)['task']
        task_id = queued['task_id']
        claim_task(self.target, 'pg-worker-1')
        finish_task(self.target, task_id, 'pg-worker-1', status='succeeded',
                    result=full_result, audit_split_threshold=1024)

        with closing(connect_task_database(self.target, readonly=True)) as connection:
            raw_result_json = connection.execute(
                'SELECT result_json FROM tasks WHERE task_id=?', (task_id,)
            ).fetchone()[0]
            audit_row = connection.execute(
                'SELECT storage_version, audit_sha256, audit_json FROM task_audit_blobs WHERE task_id=?',
                (task_id,),
            ).fetchone()
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row['storage_version'], AUDIT_STORAGE_VERSION)
        self.assertLess(len(raw_result_json.encode('utf-8')), len(audit_row['audit_json'].encode('utf-8')) // 4)

        full_loaded = get_task(self.target, task_id)
        self.assertEqual(full_loaded['result'], full_result)
        self.assertTrue(has_saved_integrity(full_loaded))

        light_loaded = get_task(self.target, task_id, include_audit=False)
        self.assertIn('_audit_ref', light_loaded['result'])
        self.assertNotIn('items', light_loaded['result']['results']['r1']['data'])

        audit_record = get_task_audit(self.target, task_id)
        self.assertTrue(audit_record['stored_separately'])
        self.assertEqual(len(audit_record['audit']['tool_data']['r1']['items']), 300)

        listed = list_tasks(self.target, page=1, page_size=10)
        self.assertEqual(listed['total'], 1)
        self.assertNotIn('result', listed['items'][0])

        # HTTP + SSE 实机验证
        app = create_app(self.target, self.target, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url='http://127.0.0.1:8000') as client:
            res_light = client.get(f'/api/v1/tasks/{task_id}?include_audit=false')
            self.assertEqual(res_light.status_code, 200)
            self.assertNotIn('items', res_light.json()['result']['result']['results']['r1']['data'])

            res_audit = client.get(f'/api/v1/tasks/{task_id}/audit')
            self.assertEqual(res_audit.status_code, 200)
            self.assertTrue(res_audit.json()['result']['stored_separately'])

            live_task = enqueue(self.target, 'analysis', {**self.parameters, 'question': 'SSE实时测试'})['task']
            live_id = live_task['task_id']
            claim_task(self.target, 'pg-worker-sse')

            def drive_pg():
                time.sleep(0.08)
                update_progress(self.target, live_id, 'pg-worker-sse', {
                    'stage': 'agent_tool', 'completed': 1, 'total': 2, 'unit': 'tools',
                })
                time.sleep(0.08)
                finish_task(self.target, live_id, 'pg-worker-sse', status='succeeded', result={'status': 'ok'})

            driver = threading.Thread(target=drive_pg)
            driver.start()
            try:
                with client.stream('GET', f'/api/v1/tasks/{live_id}/events') as stream_res:
                    self.assertEqual(stream_res.status_code, 200)
                    events = _parse_sse(''.join(stream_res.iter_text()))
            finally:
                driver.join(timeout=5)
            self.assertGreaterEqual(len(events), 2)
            self.assertEqual(events[0][0], 'progress')
            self.assertEqual(events[-1][0], 'done')
            self.assertEqual(events[-1][1]['status'], 'succeeded')

        # 篡改 task_audit_blobs 后完整性校验失败关闭
        with closing(connect_task_database(self.target)) as connection, connection:
            tampered = json.loads(audit_row['audit_json'])
            tampered['tool_data']['r1']['items'][0]['text'] = 'PG篡改文本'
            connection.execute(
                'UPDATE task_audit_blobs SET audit_json=? WHERE task_id=?',
                (json.dumps(tampered, ensure_ascii=False), task_id),
            )
        self.assertFalse(has_saved_integrity(get_task(self.target, task_id)))

        # 删除任务同步清理 task_audit_blobs
        delete_task(self.target, task_id)
        with closing(connect_task_database(self.target, readonly=True)) as connection:
            self.assertIsNone(
                connection.execute('SELECT 1 FROM task_audit_blobs WHERE task_id=?', (task_id,)).fetchone()
            )

        # 模拟旧版无 task_audit_blobs 表的 PostgreSQL 库：先删表，校验读/删兼容及写入自动建表
        with closing(connect_task_database(self.target)) as connection, connection:
            connection.execute('DROP TABLE task_audit_blobs')
        self.assertFalse(get_task_audit(self.target, live_id)['stored_separately'])
        delete_task(self.target, live_id)

        upgraded = enqueue(self.target, 'analysis', {**self.parameters, 'question': '升级自动建表测试'})['task']
        claim_task(self.target, 'pg-worker-upgrade')
        finish_task(self.target, upgraded['task_id'], 'pg-worker-upgrade', status='succeeded',
                    result=full_result, audit_split_threshold=1024)
        self.assertTrue(get_task_audit(self.target, upgraded['task_id'])['stored_separately'])
        self.assertTrue(has_saved_integrity(get_task(self.target, upgraded['task_id'])))


if __name__ == '__main__':
    unittest.main()
