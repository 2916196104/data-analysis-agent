"""P6-A 固定合成排名/产物；冻结与验收阶段禁止任何主题执行。"""
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import hashlib
import json
import unittest
from unittest.mock import patch

import test_p1a_independent_validation as independent
from data_analysis_agent.agents.review_acceptance import build_request_goal, build_task_goal
from data_analysis_agent.agents.review_multi_aspect import (
    GOAL_VERSION, SELECTION_RULE, P6RequestGoal, freeze_p6_goal, select_required_aspects,
    check_current_ranking, validate_p6_completion,
)
from data_analysis_agent.agents.review_plan import ReviewQueryPlan, build_query_plan
from data_analysis_agent.agents.review_topics import TOPIC_VERSION, recompute_topics
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs.analysis_state import restore_ranking
from data_analysis_agent.jobs.parameters import AnalysisParameters
from data_analysis_agent.jobs.store import analysis_result_integrity


class P6AContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = independent.IndependentStateValidation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.first
        self.scope = self.task['result']['scope']
        self.context = self.fixture.load()
        for target in ('data_analysis_agent.agents.review_tools.ReviewToolContext.execute',
                       'data_analysis_agent.agents.review_topics.analyze_aspect_topics',
                       'data_analysis_agent.agents.review_intent.resolve_review_request'):
            blocked = self.enterContext(patch(target, side_effect=AssertionError('P6-A contracts must not execute tools/router/models')))
            self.addCleanup(blocked.assert_not_called)
        self.goal = self.freeze().goal
        self.assertIsNotNone(self.goal)

    def freeze(self, question='具体体现在哪里？', **changes):
        return freeze_p6_goal(question, **{**self.arguments(), **changes})

    def arguments(self):
        return dict(scope=self.scope, conversation_id='independent-session',
                    agent_identity=self.fixture.agent_identity, ranking_task=self.task, context=self.context)

    def plan(self, aspects=None):
        return ReviewQueryPlan('aspect_drilldown', 'negative', deepcopy(self.scope), requirements=[
            {'tool': 'get_aspect_drilldown', 'parameters': {'product_ids': self.scope['product_ids'],
                'aspect': aspect, 'sentiment': 'negative'}} for aspect in (aspects or self.goal.required_aspects)])

    def snapshot(self):
        return {'scope': deepcopy(self.scope), 'denominator': {'field': 'successful_reviews', 'value': 6},
                'negative_review_counts': dict(independent.COUNTS)}

    def reseal(self, task):
        task['progress']['analysis_integrity'] = analysis_result_integrity(
            task['task_id'], task['status'], task['parameters'], task['result'])
        return task

    def ranked_task(self, counts):
        task = deepcopy(self.task)
        for result in task['result']['results'].values():
            summary = result['data'].get('current') or result['data'].get('summary')
            if summary:
                summary.update(negative_review_count=max(counts.values()), negative_rate=round(max(counts.values()) / 6, 6))
                for aspect, count in counts.items():
                    summary['aspects'][aspect].update(negative_review_count=count, negative_rate=round(count / 6, 6))
            if result['kind'] == 'issues':
                for row in result['data']['issues']:
                    row.update(negative_review_count=counts[row['aspect']], negative_rate=round(counts[row['aspect']] / 6, 6))
                result['data']['issues'].sort(key=lambda r: (-r['negative_review_count'], r['aspect']))
                for rank, row in enumerate(result['data']['issues'], 1):
                    row['rank'] = rank
        return self.reseal(task)

    def outcome(self, aspect, state='complete'):
        if state in ('failed', 'skipped_budget'):
            return {'aspect': aspect, 'status': state, 'results': []}
        assigned = state == 'complete'
        quote = independent.QUOTES[aspect]
        opinion = {'opinion_id': 'o-' + aspect, 'aspect': aspect, 'sentiment': 'negative',
                   'quote': quote, 'quote_start': 0, 'quote_end': len(quote)}
        item = {'review_id': 1, 'annotation_id': 1, 'revision_id': None, 'product_id': independent.A,
                'published_at': '2023-02-10T00:00:00+00:00', 'text': quote, 'file_sha256': 'a' * 64,
                'line_number': 1, 'evidence_ref': 'sha256:' + 'a' * 64 + '#line=1',
                'opinion_source': 'ai', 'opinions': [opinion]}
        data = {'scope': {k: v for k, v in self.scope.items() if not k.startswith('baseline_')},
            'summary': deepcopy(self.task['result']['results']['r1']['data']['current']),
            'aspect': aspect, 'sentiment': 'negative', 'total': independent.COUNTS[aspect],
            'total_opinions': independent.COUNTS[aspect],
            'analysis_type': 'aspect_drilldown', 'analysis_status': 'complete',
            'population_mode': 'sample' if independent.COUNTS[aspect] > 1 else 'full',
            'grouping_version': TOPIC_VERSION,
            'opinions': [{'unit_id': 'u1', 'review_id': 1, **opinion}],
            'items': [item], 'failures': [],
            'catalog': [{'key': 't1', 'name': quote, 'definition': quote, 'support_ids': ['u1']}] if assigned else [],
            'assignments': [{'opinion_id': 'u1', 'state': 'assigned' if assigned else 'unclassified', 'matches': [
                {'key': 't1', 'start': 0, 'end': len(quote)}] if assigned else []}],
            'discovery_unit_ids': ['u1']}
        data['topics'], data['coverage'] = recompute_topics(data)
        row = {'kind': 'aspect_drilldown', 'result_id': 'r-' + aspect,
               'parameters': {'product_ids': [independent.A], 'aspect': aspect, 'sentiment': 'negative'}, 'data': data}
        return {'aspect': aspect, 'status': state, 'results': [row]}

    def accept(self, outcomes=None, **changes):
        arguments = self.arguments()
        arguments.pop('scope')
        return validate_p6_completion(changes.pop('goal', self.goal), self.scope,
            changes.pop('plan', self.plan()), outcomes if outcomes is not None else [
                self.outcome(aspect) for aspect in self.goal.required_aspects],
            current_snapshot=changes.pop('current_snapshot', self.snapshot()), **{**arguments, **changes})

    def test_zero_to_six_nonzero_counts_and_ties_have_fixed_selection(self):
        names = sorted(independent.COUNTS)
        for size in range(7):
            with self.subTest(nonzero=size):
                counts = {name: int(i < size) for i, name in enumerate(names)}
                task = self.ranked_task(counts)
                basis = restore_ranking(task)
                self.assertIsNotNone(basis)
                self.assertEqual(select_required_aspects(basis), tuple(names[:min(size, 3)]))
                decision = self.freeze(ranking_task=task)
                self.assertEqual(decision.route, 'p6_contract' if size else 'reject')
                if size:
                    self.assertEqual(decision.goal.required_aspects, tuple(names[:min(size, 3)]))
                else:
                    self.assertEqual(decision.reason, 'no_negative_evidence')

    def test_selection_hash_independently_binds_full_ranking_source_scope_rule_and_target(self):
        value = self.goal.to_dict()
        basis = value['selection_basis']
        expected = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(self.goal.selection_basis_sha256, expected)
        self.assertEqual(basis['selection_rule_version'], SELECTION_RULE)
        self.assertEqual(len(basis['ranking_basis']['ranking']), 6)
        self.assertEqual(value['ranking_source']['task_id'], self.task['task_id'])
        self.assertEqual(value['ranking_source']['result_id'], 'r1')
        self.assertEqual(value['ranking_source']['scope_sha256'], content_sha256(self.scope))
        self.assertEqual(self.goal.required_aspects, ('service', 'effectiveness', 'logistics'))

    def test_client_like_ranking_dictionary_is_never_a_trusted_input(self):
        context = deepcopy(self.context)
        context['analysis_state']['ranking_basis']['source']['task_id'] = 'client-forged'
        context['analysis_state']['ranking_basis']['ranking'][0]['negative_review_count'] = 999
        decision = self.freeze(context=context)
        self.assertEqual(decision.goal, self.goal)
        self.assertEqual(decision.goal.ranking_source['task_id'], self.task['task_id'])
        self.assertEqual(self.freeze(context=context, ranking_task=None).route, 'clarify')

    def test_wrong_order_duplicate_missing_zero_and_invalid_counts_are_rejected(self):
        basis = restore_ranking(self.task)
        bad = []
        swapped = deepcopy(basis); swapped['ranking'][0], swapped['ranking'][1] = swapped['ranking'][1], swapped['ranking'][0]; bad.append(swapped)
        duplicate = deepcopy(basis); duplicate['ranking'][2] = deepcopy(duplicate['ranking'][0]); bad.append(duplicate)
        missing = deepcopy(basis); missing['ranking'].pop(); bad.append(missing)
        for count in (-1, 7, True, 1.5):
            changed = deepcopy(basis); changed['ranking'][0]['negative_review_count'] = count; bad.append(changed)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_required_aspects(value)

    def test_tampered_unsealed_filtered_positive_and_business_failed_sources_rejected(self):
        variants = []
        tampered = deepcopy(self.task); tampered['result']['answer'] = 'tampered'; variants.append(tampered)
        unsealed = deepcopy(self.task); unsealed['progress'] = {}; variants.append(unsealed)
        for mutate in (lambda t: t['result']['task_goal'].update(levels=['themes']),
                       lambda t: t['result']['query_plan']['requirements'][0]['parameters'].update(aspect='service'),
                       lambda t: t['result'].update(sentiment_intent='positive'),
                       lambda t: t['result']['results']['r1']['data']['issues'].reverse()):
            task = deepcopy(self.task); mutate(task); variants.append(self.reseal(task))
        for task in variants:
            with self.subTest(task=task['progress']):
                decision = self.freeze(ranking_task=task)
                self.assertEqual(decision.route, 'reject')
                self.assertIsNone(decision.goal)

    def test_cross_session_agent_identity_and_each_scope_change_do_not_trigger(self):
        self.assertEqual(self.freeze(conversation_id='different').route, 'reject')
        self.assertEqual(self.freeze(agent_identity={**self.fixture.agent_identity, 'model': 'different'}).route, 'reject')
        for key, value in [('product_ids', [independent.B]), ('date_from', '2023-02-02'),
                ('source', 'reviews_csv'), ('dataset_version', 'different'), ('model', 'different'),
                ('base_url', 'https://different.invalid'), ('prompt_version', 'different'),
                ('baseline_from', '2023-01-02'), ('as_of', '2023-03-01T00:00:00+00:00')]:
            with self.subTest(key=key):
                self.assertIsNone(self.freeze(scope={**self.scope, key: value}).goal)
        self.assertEqual(self.freeze(ranking_task=None).route, 'clarify')

    def test_explicit_single_dimension_and_other_goals_take_priority_before_source_lookup(self):
        with patch('data_analysis_agent.agents.review_multi_aspect.restore_ranking', side_effect=AssertionError('no lookup')):
            for question in ('客服方面呢？', '物流差评具体体现在哪里？', '有多少条？', '差评数量具体体现在哪里？',
                    '给我三条差评原文', '那好评呢？', '那正面的具体表现呢？', '那好评主要集中在哪些维度？',
                    '差评趋势如何？', '按月看', '比较这两款商品', '具体表现并给出数量', '具体表现和原文'):
                with self.subTest(question=question):
                    self.assertEqual(self.freeze(question).route, 'existing')
        for question in ('具体体现在哪里？', '这些差评具体有哪些问题？', '深入分析这些负面主题'):
            self.assertEqual(self.freeze(question).route, 'p6_contract')

    def test_goal_is_deeply_immutable_serializes_and_rejects_wrong_versions(self):
        before = self.goal.to_dict()
        with self.assertRaises(FrozenInstanceError):
            self.goal.required_aspects = ('price',)
        self.goal.selection_basis['ranking_basis']['ranking'].clear()
        self.goal.ranking_source['task_id'] = 'forged'
        exported = self.goal.to_dict(); exported['required_aspects'].clear()
        self.assertEqual(self.goal.to_dict(), before)
        self.assertEqual(P6RequestGoal.from_dict(before), self.goal)
        for change in ({'version': 'review-request-goal-v1'}, {'per_aspect_level': 'statistics'},
                       {'selection_basis_sha256': '0' * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                P6RequestGoal.from_dict({**before, **change})

    def test_plan_cannot_drop_duplicate_replace_reorder_or_use_statistics(self):
        frozen_before = self.goal.to_dict()
        variants = [self.plan(self.goal.required_aspects[:2]), self.plan(('service', 'service', 'logistics')),
                    self.plan(('service', 'effectiveness', 'quality')), self.plan(tuple(reversed(self.goal.required_aspects))),
                    build_query_plan('差评有多少条？', self.scope)]
        wrong_sentiment = self.plan(); wrong_sentiment.requirements[1]['parameters']['sentiment'] = 'positive'; variants.append(wrong_sentiment)
        wrong_scope = self.plan(); wrong_scope.scope['date_from'] = '2023-02-02'; variants.append(wrong_scope)
        for plan in variants:
            with self.subTest(plan=plan.to_dict()):
                result = self.accept(plan=plan)
                self.assertFalse(result['plan_check']['passed'])
                self.assertFalse(result['complete'])
                self.assertEqual(result['publishable_aspects'], [])
        forged_goal = replace(self.goal, required_aspects=('service', 'effectiveness', 'quality'))
        result = self.accept(goal=forged_goal, plan=self.plan(forged_goal.required_aspects))
        self.assertFalse(result['plan_check']['passed'])
        self.assertEqual(self.goal.to_dict(), frozen_before)

    def test_missing_duplicate_replaced_or_statistics_outcomes_cannot_complete(self):
        good = [self.outcome(a) for a in self.goal.required_aspects]
        variants = [good[:2], [good[0], good[0], good[2]], [good[0], good[1], self.outcome('quality')]]
        counts = deepcopy(good); counts[0]['results'][0]['kind'] = 'issues'; variants.append(counts)
        for outcomes in variants:
            with self.subTest(outcomes=[r['aspect'] for r in outcomes]):
                result = self.accept(outcomes)
                self.assertFalse(result['complete'])
                self.assertEqual(result['required_aspects'], list(self.goal.required_aspects))
                self.assertEqual(len(result['per_aspect']), 3)
                self.assertEqual(result['publishable_aspects'], [])

    def test_all_aspects_require_real_validated_evidence_to_complete(self):
        result = self.accept()
        self.assertEqual(result['status'], 'complete', result)
        self.assertTrue(result['complete'])
        self.assertEqual((result['agent_status'], result['task_status']), ('ok', 'succeeded'))
        self.assertEqual(result['publishable_aspects'], list(self.goal.required_aspects))
        for child in result['per_aspect']:
            self.assertTrue(child['acceptance']['complete'])

    def test_bad_quotes_denominators_and_partial_classification_cannot_publish_that_aspect(self):
        for damage in ('quote', 'denominator', 'classification'):
            outcomes = [self.outcome(a) for a in self.goal.required_aspects]
            data = outcomes[1]['results'][0]['data']
            if damage == 'quote':
                data['items'][0]['text'] = '不支持这段引用'
            elif damage == 'denominator':
                data['topics'][0]['review_denominator'] = 999
            else:
                data['analysis_status'] = 'partial'
            result = self.accept(outcomes)
            self.assertFalse(result['complete'])
            self.assertEqual(result['status'], 'partial')
            self.assertNotIn(self.goal.required_aspects[1], result['publishable_aspects'])

    def test_scope_or_sentiment_errors_are_global_hard_failures(self):
        for field, value in [('scope', {**self.outcome('effectiveness')['results'][0]['data']['scope'], 'model': 'wrong'}),
                             ('sentiment', 'positive')]:
            outcomes = [self.outcome(a) for a in self.goal.required_aspects]
            outcomes[1]['results'][0]['data'][field] = value
            result = self.accept(outcomes)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['publishable_aspects'], [])
        malformed = [self.outcome(a) for a in self.goal.required_aspects]
        malformed[1]['results'][0]['data'].pop('scope')
        self.assertEqual(self.accept(malformed)['status'], 'failed')

    def test_goal_freezes_without_planner_and_serialized_forgery_cannot_authorize_itself(self):
        with patch('data_analysis_agent.agents.review_multi_aspect.build_query_plan', side_effect=AssertionError('no Planner')):
            self.assertEqual(self.freeze().goal, self.goal)
        selection = self.goal.selection_basis
        selection['ranking_basis']['source']['task_id'] = 'forged'
        forged = replace(self.goal, selection_basis_json=json.dumps(selection, ensure_ascii=False),
                         selection_basis_sha256=content_sha256(selection))
        # 自洽重算选择哈希不等于历史来源通过认证。
        restored = P6RequestGoal.from_dict(forged.to_dict())
        result = self.accept(goal=restored)
        self.assertFalse(result['plan_check']['passed'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['publishable_aspects'], [])

    def test_unvalidated_or_invalid_outcome_claims_do_not_become_complete_child_states(self):
        for outcomes in ([], [self.outcome('service')], [
                {**self.outcome(a), 'status': 'partial'} for a in self.goal.required_aspects]):
            result = self.accept(outcomes)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(all(row['status'] != 'complete' for row in result['per_aspect']))
            self.assertFalse(result['complete'])
        duplicates = [self.outcome(a) for a in self.goal.required_aspects]
        duplicates[0]['results'].append(deepcopy(duplicates[0]['results'][0]))
        self.assertEqual(self.accept(duplicates)['status'], 'failed')

    def test_result_population_must_match_the_current_full_dimension_count(self):
        outcomes = [self.outcome(a) for a in self.goal.required_aspects]
        outcomes[0]['results'][0]['data']['total'] -= 1
        result = self.accept(outcomes)
        self.assertFalse(result['complete'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['publishable_aspects'], [])

    def test_internal_partial_states_map_to_existing_outer_statuses_without_losing_targets(self):
        for state in ('insufficient_evidence', 'failed', 'skipped_budget'):
            for index in range(3):
                with self.subTest(state=state, position=index):
                    outcomes = [self.outcome(a, state if i == index else 'complete')
                                for i, a in enumerate(self.goal.required_aspects)]
                    result = self.accept(outcomes)
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual((result['agent_status'], result['task_status']), ('partial', 'partial'))
                    self.assertFalse(result['complete'])
                    self.assertEqual(result['per_aspect'][index]['status'], state)
                    self.assertEqual(result['required_aspects'], list(self.goal.required_aspects))
        for state, agent, worker in [('insufficient_evidence', 'unsupported', 'succeeded'),
                                    ('failed', 'error', 'failed'), ('skipped_budget', 'error', 'failed')]:
            result = self.accept([self.outcome(a, state) for a in self.goal.required_aspects])
            self.assertEqual(result['status'], state)
            self.assertEqual((result['agent_status'], result['task_status']), (agent, worker))
            self.assertFalse(result['complete'])
            self.assertEqual(result['publishable_aspects'], [])

    def test_current_count_or_denominator_change_blocks_instead_of_reselecting(self):
        for key in independent.COUNTS:
            snapshot = self.snapshot(); snapshot['negative_review_counts'][key] += 1
            self.assertFalse(check_current_ranking(self.goal, snapshot))
            result = self.accept(current_snapshot=snapshot)
            self.assertFalse(result['complete'])
            self.assertEqual(result['publishable_aspects'], [])
            self.assertEqual(result['required_aspects'], list(self.goal.required_aspects))
        for snapshot in (None, {**self.snapshot(), 'denominator': {'field': 'successful_reviews', 'value': 7}},
                         {**self.snapshot(), 'scope': {**self.scope, 'date_from': '2023-02-02'}}):
            self.assertFalse(self.accept(current_snapshot=snapshot)['complete'])

    def test_v1_fields_serialization_and_public_api_input_are_unchanged(self):
        request = build_request_goal('物流差评具体体现在哪里？')
        self.assertEqual(set(asdict(request)), {'original_question', 'levels', 'aspect', 'sentiment',
                                              'topic', 'statistic_families', 'source'})
        goal = build_task_goal(request.original_question, self.scope,
            build_query_plan(request.original_question, self.scope), {}, request_goal=request)
        self.assertEqual(goal['version'], 'review-request-goal-v1')
        self.assertNotIn('analysis_mode', goal)
        self.assertEqual(self.goal.to_dict()['version'], GOAL_VERSION)
        for name in ('ranking_basis', 'required_aspects', 'analysis_mode', 'ranking_source'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                AnalysisParameters.model_validate({**self.fixture.scope, 'question': '具体体现在哪里？', name: {}})


if __name__ == '__main__':
    unittest.main()
