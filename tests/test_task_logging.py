"""任务日志展示结果和计数，不转储载荷或服务商错误。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.jobs.worker import TaskWorker


class TaskLoggingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.worker = TaskWorker(root / 'reviews.db', root / 'tasks.db', import_root=root, env_file=root / '.env')
        self.task = {'task_id': 'test-task', 'kind': 'import', 'parameters': {'reviews_path': 'private-input.jsonl'}}
        self.enterContext(patch('data_analysis_agent.jobs.worker.import_path', return_value='private-input.jsonl'))
        self.enterContext(patch('data_analysis_agent.jobs.worker.update_progress'))
        self.finish = self.enterContext(patch('data_analysis_agent.jobs.worker.finish_task'))

    def test_partial_import_logs_committed_counts_without_private_fields(self):
        result = {'status': 'partial', 'imports': [{'kind': 'reviews', 'inserted_records': 2,
                  'duplicate_records': 1, 'invalid_records': 1, 'raw_text': 'PRIVATE_REVIEW'}]}
        with patch('data_analysis_agent.jobs.worker.import_amazon_jsonl', return_value=result), self.assertLogs(
                'data_analysis_agent.jobs.worker', level='INFO') as logs:
            self.worker._execute(self.task)
        output = '\n'.join(logs.output)
        self.assertIn('任务开始 task_id=test-task', output)
        self.assertIn('新增=2 重复=1 无效=1', output)
        self.assertIn('status=partial', output)
        self.assertIn('耗时=', output)
        self.assertNotIn('PRIVATE_REVIEW', output)
        self.assertNotIn('private-input.jsonl', output)
        self.assertEqual(self.finish.call_args.kwargs['status'], 'partial')

    def test_error_logs_code_and_type_without_exception_message(self):
        with patch('data_analysis_agent.jobs.worker.import_amazon_jsonl', side_effect=RuntimeError('SECRET_KEY')), self.assertLogs(
                'data_analysis_agent.jobs.worker', level='INFO') as logs:
            self.worker._execute(self.task)
        output = '\n'.join(logs.output)
        self.assertIn('任务失败 task_id=test-task', output)
        self.assertIn('error_code=execution_error exception=RuntimeError', output)
        self.assertNotIn('SECRET_KEY', output)
        self.assertEqual(self.finish.call_args.kwargs['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
