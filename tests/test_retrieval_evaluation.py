"""只使用合成检索结果测试指标和冻结/隐私边界。"""

from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext, redirect_stderr, redirect_stdout

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.retrieval_evaluation import (
    APPROVED_SCOPE, PROFILE_KEY, EvaluationError, label_template, run_binding, score_run,
    validate_manifest, validate_options, validate_run,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retrieval_cli', ROOT / 'scripts/evaluate_retrieval_quality.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def synthetic_run(count=5):
    manifest = json.loads((ROOT / 'evaluation/rag-retrieval-v1.json').read_text(encoding='utf-8'))
    searches = []
    for q in manifest['queries']:
        for mode in ('unfiltered', 'filtered'):
            filters = q['filters'] if mode == 'filtered' else {}
            searches.append(dict(query_id=q['id'], mode=mode, filters=filters, repeat_identical=True, elapsed_ms=0,
                hits=[dict(rank=i, key=f'synthetic:{i}', review_id=1, annotation_id=1,
                    aspect=filters.get('aspect', 'service'), sentiment=filters.get('sentiment', 'negative'),
                    cosine_distance=i / 10, evidence_valid=True, quote='PRIVATE synthetic quote',
                    quote_start=0, quote_end=23,
                    review_text='PRIVATE synthetic quote, synthetic full review') for i in range(1, count + 1)]))
    result = dict(manifest=manifest, manifest_sha256=content_sha256(manifest),
        scope_sha256=content_sha256([APPROVED_SCOPE, PROFILE_KEY, cli.SPACE_ID]), searches=searches,
        inventory={}, encoder_metrics={}, query_vectors_sha256='synthetic', authorized_snapshot_unchanged=True)
    result['run_id'] = run_binding(result)
    return result


def human_labels(run, values=(0, 2, 1, 0, 2)):
    labels = label_template(run)
    labels.update(human_confirmed=True, reviewer='synthetic test reviewer')
    for q in labels['queries']:
        q['evidence_status'] = 'answerable'
    for j in labels['judgments']:
        j['grade'] = values[j['rank'] - 1]
    return labels


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.run = synthetic_run()
        self.labels = human_labels(self.run)

    def test_exact_precision_hit_mrr_partial_and_denominators(self):
        result = score_run(self.run, self.labels)
        r = result['query_details'][0]
        self.assertEqual([r[f'precision_at_{k}'] for k in (1, 3, 5)], [0, 1/3, 2/5])
        self.assertEqual([r[f'partial_at_{k}'] for k in (1, 3, 5)], [0, 1/3, 1/5])
        self.assertEqual((r['hit_rate_at_5'], r['mrr_at_5'], r['denominator_at_5']), (1, .5, 5))
        self.assertEqual(result['groups']['mode:filtered']['precision_at_5']['query_denominator'], 24)
        self.assertIsNone(r['recall_at_5'])

    def test_no_automatic_labels_or_metrics(self):
        template = label_template(self.run)
        self.assertTrue(all(j['grade'] is None for j in template['judgments']))
        result = score_run(self.run, template)
        self.assertEqual(result['status'], 'awaiting_human_review')
        self.assertIsNone(result['query_details'][0]['precision_at_1'])
        self.assertEqual(result['groups']['mode:unfiltered']['precision_at_5']['query_denominator'], 0)

    def test_labels_require_explicit_human_confirmation_and_reviewer(self):
        self.labels['human_confirmed'] = False
        self.assertIsNone(score_run(self.run, self.labels)['query_details'][0]['precision_at_5'])
        self.labels.update(human_confirmed=True, reviewer='')
        with self.assertRaisesRegex(EvaluationError, 'human_reviewer_required'):
            score_run(self.run, self.labels)

    def test_unjudged_prefix_is_na_without_hiding_judged_top1(self):
        self.labels['judgments'][3]['grade'] = None
        r = score_run(self.run, self.labels)['query_details'][0]
        self.assertEqual(r['precision_at_3'], 1/3)
        self.assertIsNone(r['precision_at_5'])
        self.assertIsNone(r['mrr_at_5'])

    def test_illegal_grade_types_ranges_and_nan_rejected(self):
        for grade in (-1, 3, '2', True, 1.0, float('nan'), [], {}):
            with self.subTest(grade_type=type(grade).__name__):
                labels = deepcopy(self.labels)
                labels['judgments'][0]['grade'] = grade
                with self.assertRaisesRegex(EvaluationError, 'invalid_human_grade'):
                    score_run(self.run, labels)

    def test_short_results_use_fixed_k_denominator(self):
        run = synthetic_run(2)
        r = score_run(run, human_labels(run, (2, 1)))['query_details'][0]
        self.assertEqual([r[f'precision_at_{k}'] for k in (1, 3, 5)], [1, 1/3, 1/5])
        self.assertEqual(r['partial_at_5'], 1/5)

    def test_empty_results_unreviewed_are_na_and_confirmed_answerable_are_zero(self):
        run = synthetic_run(0)
        self.assertIsNone(score_run(run, label_template(run))['query_details'][0]['precision_at_5'])
        r = score_run(run, human_labels(run))['query_details'][0]
        self.assertEqual((r['precision_at_1'], r['hit_rate_at_5'], r['mrr_at_5']), (0, 0, 0))

    def test_no_known_evidence_and_insufficient_sample_excluded(self):
        for q in self.labels['queries']:
            q['evidence_status'] = 'no_known_relevant'
        for j in self.labels['judgments']:
            j['grade'] = 0
        r = score_run(self.run, self.labels)['query_details'][0]
        self.assertIsNone(r['precision_at_5'])
        self.assertEqual((r['no_known_candidate_return_at_5'], r['no_known_irrelevant_count_at_5']), (1, 5))
        self.labels['queries'][0]['evidence_status'] = 'insufficient_sample'
        self.assertIsNone(score_run(self.run, self.labels)['query_details'][0]['no_known_candidate_return_at_5'])

    def test_no_known_evidence_cannot_contain_high_relevance(self):
        self.labels['queries'][0]['evidence_status'] = 'no_known_relevant'
        with self.assertRaisesRegex(EvaluationError, 'contradictory_evidence_status'):
            score_run(self.run, self.labels)

    def test_no_known_empty_return_rate_is_zero_without_quality_zero(self):
        run = synthetic_run(0)
        labels = human_labels(run)
        labels['queries'][0]['evidence_status'] = 'no_known_relevant'
        r = score_run(run, labels)['query_details'][0]
        self.assertIsNone(r['precision_at_1'])
        self.assertEqual(r['no_known_candidate_return_at_5'], 0)

    def test_paired_comparison_uses_same_judged_queries(self):
        for j in self.labels['judgments']:
            j['grade'] = 2 if j['mode'] == 'filtered' else 0
        self.labels['judgments'][-1]['grade'] = None
        metric = score_run(self.run, self.labels)['paired_comparison']['precision_at_5']
        self.assertEqual(metric, dict(filtered_minus_unfiltered=1, paired_query_denominator=23))

    def test_query_scope_run_and_label_versions_do_not_mix(self):
        for key in ('run_id', 'manifest_sha256', 'scope_sha256', 'label_version'):
            with self.subTest(key=key):
                labels = deepcopy(self.labels)
                labels[key] = 'wrong'
                with self.assertRaisesRegex(EvaluationError, 'label_binding_mismatch'):
                    score_run(self.run, labels)
        altered = deepcopy(self.run)
        altered['manifest']['queries'][0]['text'] = 'changed'
        with self.assertRaisesRegex(EvaluationError, 'query_hash_mismatch'):
            score_run(altered, self.labels)

    def test_manifest_rejects_scope_space_and_rule_changes(self):
        for field, value in (('scope', dict(APPROVED_SCOPE, product_ids=['other'])),
                             ('rules', {}), ('embedding_space_id', 'other'), ('version', 'other')):
            manifest = deepcopy(self.run['manifest'])
            manifest[field] = value
            with self.assertRaises(EvaluationError):
                validate_manifest(manifest)

    def test_unauthorized_scope_and_profile_rejected_before_db(self):
        for options in (dict(APPROVED_SCOPE, base_url='https://offline.invalid'),
                        dict(APPROVED_SCOPE, base_url='https://offline.invalid', product_ids=['other']),
                        dict(APPROVED_SCOPE, base_url='https://offline.invalid', as_of='2022-01-01')):
            with patch.object(cli, '_snapshot') as connect:
                with self.assertRaises(Exception):
                    cli.collect_run('not-used', options, self.run['manifest'], None)
                connect.assert_not_called()

    def test_changed_inventory_stops_before_model_encoding(self):
        encoder = Mock()
        with patch.object(cli, 'validate_options'), patch.object(cli, '_snapshot', return_value=nullcontext(None)):
            with patch.object(cli, 'inventory', return_value=({'snapshot_sha256': 'changed'}, None, None)):
                with self.assertRaisesRegex(EvaluationError, 'frozen_inventory_changed'):
                    cli.collect_run('synthetic', {}, self.run['manifest'], encoder)
        encoder.encode.assert_not_called()

    def test_index_expansion_or_invalidation_cannot_silently_change_evaluation(self):
        encoder = Mock()
        for changes in (dict(stored_count=201), dict(valid_count=199, invalid_count=1)):
            state = dict(snapshot_sha256=self.run['manifest']['inventory_snapshot_sha256'],
                         stored_count=200, valid_count=200, invalid_count=0) | changes
            with patch.object(cli, 'validate_options'), patch.object(cli, '_snapshot', return_value=nullcontext(None)):
                with patch.object(cli, 'inventory', return_value=(state, None, None)):
                    with self.assertRaisesRegex(EvaluationError, 'approved_200_index_changed'):
                        cli.collect_run('synthetic', {}, self.run['manifest'], encoder)
        encoder.encode.assert_not_called()

    def test_query_encoder_wrong_space_shape_and_overlength_rejected(self):
        state = dict(snapshot_sha256=self.run['manifest']['inventory_snapshot_sha256'],
                     stored_count=200, valid_count=200, invalid_count=0)
        for value in (dict(space_id='other', vectors=[None]*24),
                      dict(space_id=cli.SPACE_ID, vectors=[]),
                      dict(space_id=cli.SPACE_ID, vectors=[None]*24)):
            encoder = Mock()
            encoder.encode.return_value = value
            with patch.object(cli, 'validate_options'), patch.object(cli, '_snapshot', return_value=nullcontext(None)):
                with patch.object(cli, 'inventory', return_value=(state, None, None)):
                    with self.assertRaises(Exception):
                        cli.collect_run('synthetic', {}, self.run['manifest'], encoder)
            self.assertEqual(encoder.encode.call_args.args[0], [q['text'] for q in self.run['manifest']['queries']])

    def test_stale_evidence_ranks_order_and_topk_rejected(self):
        for field, value in (('evidence_valid', False), ('rank', 0), ('cosine_distance', float('inf'))):
            run = deepcopy(self.run)
            run['searches'][0]['hits'][0][field] = value
            run['run_id'] = run_binding(run) if field != 'cosine_distance' else 'invalid'
            with self.assertRaises((EvaluationError, ValueError)):
                validate_run(run)
        run = synthetic_run(6)
        with self.assertRaisesRegex(EvaluationError, 'invalid_search_filters_or_top_k'):
            validate_run(run)

    def test_missing_duplicate_or_wrong_judgments_rejected(self):
        for mutate in (lambda x: x.pop(), lambda x: x.append(x[0]), lambda x: x[0].update(key='wrong')):
            labels = deepcopy(self.labels)
            mutate(labels['judgments'])
            with self.assertRaisesRegex(EvaluationError, 'judgments_mismatch'):
                score_run(self.run, labels)

    def test_repeated_scores_and_labels_are_deterministic(self):
        self.assertEqual(label_template(self.run), label_template(self.run))
        self.assertEqual(score_run(self.run, self.labels), score_run(self.run, self.labels))

    def test_actual_result_tags_and_intent_groups_are_kept_separate(self):
        result = score_run(self.run, self.labels)
        r = result['query_details'][8]  # Q05 的无过滤合成结果是 service，查询意图是 logistics。
        self.assertEqual(r['aspect'], 'logistics')
        self.assertEqual(r['hit_details'][0]['aspect'], 'service')
        self.assertIn('sentiment:mixed:filtered', result['groups'])

    def test_private_notes_and_text_never_appear_in_metrics_or_safe_summary(self):
        self.labels['judgments'][0]['note'] = 'PRIVATE review note and secret'
        public = json.dumps([score_run(self.run, self.labels), cli.safe_summary(self.run)])
        self.assertNotIn('PRIVATE', public)
        self.assertNotIn('base_url', public)

    def test_errors_hide_underlying_private_exception(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'private_directory', side_effect=RuntimeError('PRIVATE password and review')):
            with redirect_stderr(err), redirect_stdout(out):
                code = cli.main(['inventory'])
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', out.getvalue() + err.getvalue())

    def test_output_outside_private_ignored_tree_is_rejected(self):
        with self.assertRaisesRegex(EvaluationError, 'local_private_output_required'):
            cli.private_directory(ROOT / 'docs')

    def test_review_html_escapes_script_in_real_text(self):
        run = deepcopy(self.run)
        run['searches'][0]['hits'][0]['quote'] = '</script><script>PRIVATE</script>'
        run['run_id'] = run_binding(run)
        with tempfile.TemporaryDirectory() as directory:
            cli.write_review(Path(directory), run)
            html = (Path(directory) / 'review.html').read_text(encoding='utf-8')
            self.assertNotIn('</script><script>PRIVATE', html)
            self.assertIn('\\u003c/script\\u003e', html)


if __name__ == '__main__':
    unittest.main()
