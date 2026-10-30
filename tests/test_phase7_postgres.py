"""真实 PostgreSQL 的独立统计 oracle、并发快照与查询超时。"""
from contextlib import closing
import json
import os
import unittest
from unittest.mock import patch

from data_analysis_agent.cli.migrate import migrate_sqlite
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.human_review import save_human_review
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl
from data_analysis_agent.agents.reviews import run_review_agent
from scripts.phase7_cases import confirmed
from scripts.phase7_evaluation import check_statistics
from test_agent import SETTINGS
import test_postgres as pg_fixture
import test_review_agent as agent_fixture
import test_review_workbench as workbench_fixture
from test_review_workbench import IDENTITY, opinion


@unittest.skipUnless(os.environ.get('TEST_POSTGRES_ADMIN_URL'), 'requires isolated PostgreSQL')
class Phase7PostgresTests(unittest.TestCase):
    new_database = pg_fixture.PostgresTests.new_database

    def setUp(self):
        self.admin_url = os.environ['TEST_POSTGRES_ADMIN_URL']
        self.target = self.new_database()
        self.fixture = workbench_fixture.WorkbenchStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        migrate_sqlite(self.fixture.db, self.target)
        self.scope = agent_fixture.ReviewAgentTests.scope(self.fixture)
        self.current = {k: v for k, v in self.scope.items() if not k.startswith('baseline_')}

    def test_positive_negative_multiaspect_counts_match_independent_raw_row_oracle(self):
        for tool in ('get_positive_aspects', 'get_top_issues'):
            result = run_review_agent(self.target, confirmed(tool), scope=self.scope, settings=SETTINGS)
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(check_statistics(self.target, result), [])
        result = analytics.get_positive_aspects(self.target, **self.current)
        self.assertEqual(result['summary']['positive_review_count'], 3)
        counts = {row['aspect']: row['positive_review_count'] for row in result['aspects']}
        self.assertEqual(counts, dict(effectiveness=2, quality=1, price=1, logistics=1, packaging=0, service=0))
        self.assertGreater(sum(counts.values()), result['summary']['positive_review_count'])
        broken = run_review_agent(self.target, confirmed('get_positive_aspects'), scope=self.scope, settings=SETTINGS)
        broken['results']['r1']['data']['aspects'][0]['positive_review_count'] += 1
        self.assertTrue(check_statistics(self.target, broken), 'oracle must reject wrong count')
        source = self.fixture.root / 'same-aspect.jsonl'
        source.write_text(json.dumps({**self.fixture.records[0], 'user_id': 'phase7-dedup',
            'text': '效果好，挺好用，包装破损。'}), encoding='utf-8')
        import_amazon_jsonl(self.target, reviews_path=source)
        row = next(r for r in pending_reviews(self.target, **IDENTITY, limit=100) if r['id'] > 6)
        save_annotation(self.target, row, **IDENTITY, status='success', opinions=validate_opinions(json.dumps({'opinions': [
            opinion('effectiveness', 'positive', '效果好'), opinion('effectiveness', 'positive', '挺好用'),
            opinion('packaging', 'negative', '包装破损')]}), row['text']))
        mixed = analytics.get_positive_aspects(self.target, **self.current)
        self.assertEqual(mixed['summary']['positive_review_count'], 4)
        self.assertEqual(mixed['summary']['negative_review_count'], 2)
        self.assertEqual(mixed['summary']['aspects']['effectiveness']['positive_review_count'], 3)

    def test_repeatable_snapshot_survives_import_correction_and_annotation_version_change(self):
        with closing(connect_review_database(self.target, readonly=True)) as connection, connection:
            connection.execute('BEGIN')
            before = analytics.get_positive_aspects(connection, **self.current)
            save_human_review(self.target, 1, annotation_id=self.fixture.annotations[1], expected_revision_id=None,
                              action='corrected', reviewer='phase7 isolated test', opinions=[])
            source = self.fixture.root / 'concurrent.jsonl'
            source.write_text(json.dumps({**self.fixture.records[0], 'user_id': 'phase7-new'}), encoding='utf-8')
            import_amazon_jsonl(self.target, reviews_path=source)
            pending = next(row for row in pending_reviews(self.target, **IDENTITY, limit=100) if row['id'] > 6)
            save_annotation(self.target, pending, **IDENTITY, status='success', opinions=validate_opinions(
                json.dumps({'opinions': [opinion()]}), pending['text']))
            new_identity = {**IDENTITY, 'prompt_version': 'phase7-new-version'}
            save_annotation(self.target, pending, **new_identity, status='success', opinions=validate_opinions(
                json.dumps({'opinions': [opinion()]}), pending['text']))
            self.assertEqual(analytics.get_positive_aspects(connection, **self.current), before)
        after = analytics.get_positive_aspects(self.target, **self.current)
        self.assertEqual(after['summary']['review_count'], 7)
        self.assertEqual(after['summary']['positive_review_count'], 3)
        self.assertEqual(after['summary']['negative_review_count'], 0)
        self.assertEqual(after['summary']['successful_reviews'], 4)
        version = analytics.get_positive_aspects(self.target, **{**self.current, 'prompt_version': 'phase7-new-version'})
        self.assertEqual(version['summary']['successful_reviews'], 1)
        self.assertEqual(version['summary']['positive_review_count'], 1)

    def test_missing_month_and_zero_success_denominators_stay_null(self):
        result = analytics.get_aspect_trend(self.target, **{**self.current, 'date_to': '2023-03-31'}, aspect='logistics')
        self.assertEqual([r['bucket'] for r in result['buckets']], ['2023-01', '2023-02', '2023-03'])
        for row in result['buckets'][1:]:
            self.assertEqual(row['review_count'], 0)
            self.assertIsNone(row['negative_rate'])
            self.assertIsNone(row['aspects']['logistics']['negative_rate'])
        for function in (analytics.get_positive_aspects, analytics.get_data_quality):
            empty = function(self.target, **{**self.current, 'model': 'UNLABELED'})
            self.assertEqual(empty['summary']['successful_reviews'], 0)
            self.assertIsNone(empty['summary']['negative_rate'])

    def test_postgres_statement_timeout_never_publishes_success(self):
        def timeout(connection, **kwargs):
            connection.execute("SET LOCAL statement_timeout = '5ms'")
            connection.execute('SELECT pg_sleep(0.1)')
        with patch('data_analysis_agent.agents.review_tools.analytics.get_positive_aspects', side_effect=timeout):
            result = run_review_agent(self.target, confirmed('get_positive_aspects'), scope=self.scope, settings=SETTINGS)
        self.assertEqual(result['status'], 'error')
        self.assertNotEqual(result['public_answer']['status'], 'ok')

    def test_agent_caps_sql_timeout_and_keeps_stricter_server_limit(self):
        original_query = analytics.get_positive_aspects
        for configured, expected in (('0', '30s'), ('2s', '2s')):
            seen = []
            def connect(*args, **kwargs):
                connection = connect_review_database(*args, **kwargs)
                connection.execute("SELECT set_config('statement_timeout', ?, false)", (configured,))
                return connection
            def inspect(connection, **kwargs):
                seen.append(connection.execute('SHOW statement_timeout').fetchone()[0])
                return original_query(connection, **kwargs)
            with patch('data_analysis_agent.agents.reviews.connect_review_database', side_effect=connect), \
                 patch('data_analysis_agent.agents.review_tools.analytics.get_positive_aspects', side_effect=inspect):
                result = run_review_agent(self.target, confirmed('get_positive_aspects'), scope=self.scope, settings=SETTINGS)
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(seen, [expected])


if __name__ == '__main__':
    unittest.main()
