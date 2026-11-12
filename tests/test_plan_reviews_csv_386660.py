"""Synthetic tests for the readonly planning gate and deterministic group selection."""
from types import SimpleNamespace
from unittest import TestCase, mock
import sys

from scripts import plan_reviews_csv_386660 as plan


def doc(review, number, *, product=plan.PRODUCT, source=plan.SOURCE, dataset=plan.DATASET):
    return SimpleNamespace(review_id=review, stable_versioned_opinion_key=f'{review}-{number}',
                           product_id=product, source=source, dataset_version=dataset)


class PlanTests(TestCase):
    def test_default_never_opens_database(self):
        with mock.patch.object(sys, 'argv', ['plan']), mock.patch.object(plan, 'readonly_plan') as call:
            self.assertEqual(plan.main(), 0)
        call.assert_not_called()

    def test_excludes_usable_and_caps_complete_groups(self):
        docs = [doc(i, n) for i in range(30) for n in range(2)]
        chosen, conflicts = plan.select_groups(docs, {'0-0', '0-1'}, {'0-0', '0-1'})
        self.assertEqual(len(chosen), 50)
        self.assertEqual(conflicts, 0)
        self.assertFalse({'0-0','0-1'} & {d.stable_versioned_opinion_key for d in chosen})
        self.assertTrue(all(sum(d.review_id == rid for d in chosen) == 2 for rid in {d.review_id for d in chosen}))

    def test_group_exceeding_remaining_budget_is_not_split(self):
        docs = [doc(i, 0) for i in range(49)] + [doc(49, n) for n in range(2)]
        chosen, _ = plan.select_groups(docs, set(), set())
        self.assertEqual(len(chosen), 49)
        self.assertNotIn(49, {d.review_id for d in chosen})

    def test_conflicting_existing_key_skips_entire_review(self):
        docs = [doc(1, 0), doc(1, 1), doc(2, 0)]
        chosen, conflicts = plan.select_groups(docs, set(), {'1-0'})
        self.assertEqual(conflicts, 1)
        self.assertEqual([d.review_id for d in chosen], [2])

    def test_wrong_product_dataset_or_duplicate_are_rejected(self):
        for docs in ([doc(1,0,product='100013588053')], [doc(1,0,source='amazon_reviews_2023')],
                     [doc(1,0,dataset='other')], [doc(1,0),doc(1,0)]):
            with self.subTest(docs=docs), self.assertRaises(plan.StopPlan):
                plan.select_groups(docs, set(), set())

    def test_usable_key_must_belong_to_corpus(self):
        with self.assertRaisesRegex(plan.StopPlan, 'USABLE_KEY_OUTSIDE_CORPUS'):
            plan.select_groups([doc(1,0)], {'unknown'}, set())

    def test_source_authentication_failure_never_returns_plan(self):
        from contextlib import contextmanager
        class Connection:
            def __init__(self, *args, **kwargs):
                self.raw = SimpleNamespace(closed=False)
                self.rollback_called = False
            def execute(self, sql, parameters=None):
                class Cursor:
                    def fetchone(inner):
                        if 'current_database()' in sql:
                            return ('reviews','rag_audit_ro','rag_audit_ro','repeatable read','on','UTC','snapshot','time')
                        return (1, '2021-01-01', '2021-01-02')
                    def fetchall(inner): return [(False, False, True)] * 9
                return Cursor()
            def rollback(self): self.rollback_called = True
            def close(self): self.raw.closed = True
        connection = Connection()
        corpus = SimpleNamespace(documents=[doc(1,0)], reviews=[SimpleNamespace(review_id=1)], label_profile_key=plan.PROFILE)
        class Repository:
            def __init__(self, conn): pass
            @contextmanager
            def snapshot(self, scope):
                yield SimpleNamespace(corpus=lambda: corpus, load_sources=lambda keys: [])
        with mock.patch('data_analysis_agent.core.database.PostgresConnection', return_value=connection), \
             mock.patch('data_analysis_agent.reviews.store.check_review_schema'), \
             mock.patch('data_analysis_agent.reviews.semantic_retrieval.PostgresSemanticRepository', Repository), \
             mock.patch('data_analysis_agent.reviews.semantic_retrieval._scope', return_value='synthetic'), \
             mock.patch('data_analysis_agent.reviews.semantic_retrieval._verified_sources', side_effect=RuntimeError('bad source')):
            with self.assertRaisesRegex(RuntimeError, 'bad source'):
                plan.readonly_plan(('synthetic', 'synthetic', 'synthetic'))
        self.assertTrue(connection.rollback_called and connection.raw.closed)

    def test_failed_readonly_attempt_cannot_save_plan(self):
        with mock.patch.object(sys, 'argv', ['plan', '--plan-authorized']), \
             mock.patch.object(plan, 'local_preflight', return_value=('synthetic',)*3), \
             mock.patch.object(plan, 'readonly_plan', side_effect=plan.StopPlan('SOURCE_VERIFICATION_FAILED')), \
             mock.patch.object(plan, 'OUTPUT') as output:
            self.assertEqual(plan.main(), 2)
        output.mkdir.assert_not_called()
