"""Small migration examples using CSV/SQLite; original legacy tests stay unchanged."""
import unittest
from unittest.mock import patch

import test_p1a_independent_validation as old
import test_p6a_contract as p6a
import test_review_followup_p7b as followup
from p6a_csv_fixture import csv_fixture_setup


class MigrationSamples(unittest.TestCase):
    def test_existing_p6_assertions_reachable_with_csv_worker_and_trusted_ranking(self):
        names = ('test_goal_is_deeply_immutable_serializes_and_rejects_wrong_versions',
                 'test_plan_cannot_drop_duplicate_replace_reorder_or_use_statistics')
        suite = unittest.TestSuite(p6a.P6AContractTests(name) for name in names)
        result = unittest.TestResult()
        with patch.object(old.IndependentStateValidation, 'setUp', csv_fixture_setup):
            suite.run(result)
        self.assertEqual(result.testsRun, len(names))
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_legacy_source_remains_rejected(self):
        fixture = old.IndependentStateValidation()
        with patch.object(old.IndependentStateValidation, 'setUp', csv_fixture_setup):
            try:
                fixture.setUp()
                task = fixture.work('差评主要集中在哪些方面？', source='amazon_reviews_2023',
                                    dataset_version='independent-state')
                self.assertEqual(task['status'], 'failed')
                self.assertEqual(task['error']['code'], 'business_error')
                self.assertFalse((task.get('result') or {}).get('task_acceptance', {}).get('complete', False))
            finally:
                fixture.doCleanups()

    def test_timeout_is_injected_into_actual_sql_tool_path(self):
        fixture = followup.FollowupP7BTests()
        try:
            fixture.setUp()
            from data_analysis_agent.agents.review_tools import ReviewToolContext
            original = ReviewToolContext.query

            def fail_issues(context, name, **kwargs):
                if name == 'get_top_issues':
                    raise TimeoutError('synthetic issue query timeout')
                return original(context, name, **kwargs)

            with patch.object(ReviewToolContext, 'query', fail_issues):
                task = fixture.work(followup.FIRST)
            self.assertEqual(task['status'], 'failed', task)
            self.assertEqual(task['error']['code'], 'business_error')
            self.assertFalse((task.get('result') or {}).get('task_acceptance', {}).get('complete', False))
        finally:
            fixture.doCleanups()

    def test_timeout_after_actual_tool_preserves_diagnostics(self):
        fixture = followup.FollowupP7BTests()
        try:
            fixture.setUp()
            with patch('data_analysis_agent.agents.review_evidence_loop.complete_evidence',
                       side_effect=TimeoutError('synthetic post-query timeout')) as fault:
                task = fixture.work(followup.FIRST)
            fault.assert_called_once()
            self.assertEqual(task['status'], 'failed')
            self.assertEqual(task['error']['code'], 'timeout')
            result = task['result']
            self.assertIn('r1', result['results'])
            self.assertEqual(result['results']['r1']['kind'], 'issues')
            self.assertFalse(result['task_acceptance']['complete'])
            self.assertNotIn('answer', result)
        finally:
            fixture.doCleanups()
