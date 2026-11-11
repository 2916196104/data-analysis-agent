"""F01 A–H：真实 Worker 的目标继承、显式覆盖、范围隔离及授权检查。"""
from dataclasses import fields
import json
import unittest
from unittest.mock import patch

import test_p1a_independent_validation as independent
from data_analysis_agent.agents.review_plan import ReviewQueryPlan, TOOL_KINDS, build_query_plan
from data_analysis_agent.agents.review_tools import ReviewToolContext
from data_analysis_agent.jobs.analysis_state import has_saved_integrity, restore_pending_goal
from data_analysis_agent.jobs.store import enqueue, claim_task, get_task


def fixed_topics(settings, prompt, payload, schema, timeout):
    from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch
    if schema is TopicCatalog:
        return TopicCatalog(themes=[dict(key='t1', name='客服响应慢', definition='买家明确反映客服回复缓慢',
                                        support_ids=[row['opinion_id'] for row in payload['opinions']])])
    return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'], state='assigned',
        matches=[dict(key='t1', start=0, end=len(row['quote']))]) for row in payload['opinions']])


def fixed_positive_topics(settings, prompt, payload, schema, timeout):
    from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch
    if schema is TopicCatalog:
        return TopicCatalog(themes=[dict(key='t1', name='客服响应及时', definition='买家明确反映客服回复及时',
            support_ids=[row['opinion_id'] for row in payload['opinions']])])
    return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'], state='assigned',
        matches=[dict(key='t1', start=0, end=len(row['quote']))]) for row in payload['opinions']])


class F01WorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = independent.IndependentStateValidation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pending = self.fixture.pending_task()
        self.unauthorized_calls = []
        execute = ReviewToolContext.execute

        def checked_execute(context, name, parameters):
            try:
                context.query_plan.check_call(name, parameters, default_products=context.allowed_products)
            except Exception as exc:
                self.unauthorized_calls.append((name, parameters, str(exc)))
                raise
            return execute(context, name, parameters)

        self.enterContext(patch.object(ReviewToolContext, 'execute', checked_execute))
        self.authorized(self.fixture.first)
        self.authorized(self.pending)

    def authorized(self, task, *, goal_matches=True):
        self.assertEqual(self.unauthorized_calls, [])
        result = task['result']
        if result['user_intent'].get('needs_clarification'):
            self.assertEqual(result['query_executions'], 0)
            self.assertEqual(result['total_model_calls'], 0)
            self.assertEqual(result['trace'], [])
            self.assertEqual(result['results'], {})
        saved = result.get('query_plan')
        if not saved or not saved['requirements']:
            self.assertEqual(result['query_executions'], 0)
            self.assertEqual(result['trace'], [])
            return
        plan = ReviewQueryPlan(**{f.name: saved[f.name] for f in fields(ReviewQueryPlan)
                                  if f.name in saved and f.name != 'task_contract'})
        if goal_matches:
            self.assertTrue(result['task_plan_check']['passed'])
            self.assertTrue(result['task_acceptance']['plan_check']['passed'])
        for event in result['trace']:
            if event['event'] == 'tool_call':
                self.assertIn(event['tool'], ['get_analysis_context', *plan.required_tools, *plan.optional_tools])
        for row in result['results'].values():
            if row['kind'] == 'context':
                continue
            tool = next(name for name, kind in TOOL_KINDS.items() if kind == row['kind'])
            plan.check_call(tool, row['parameters'], default_products=result['scope']['product_ids'])

    def assert_positive_clarification(self, result, *, task=None, structured=True):
        # 正式人工 A / Oracle v2：统计与未完成主题竞争时，证据有无都不能替用户选层次。
        self.assertIsNone(result['request_goal'])
        self.assertTrue(result['user_intent']['needs_clarification'])
        self.assertEqual(result['user_intent']['sentiment'], 'positive')
        self.assertIsNone(result['user_intent']['query_type'])
        self.assertFalse((result.get('query_plan') or {}).get('requirements'))
        self.assertEqual((result.get('query_plan') or {}).get('required_tools', []), [])
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse((result.get('task_acceptance') or {}).get('complete'))
        for key in ('query_executions', 'tool_calls', 'topic_model_calls', 'total_model_calls'):
            self.assertEqual(result[key], 0, key)
        self.assertEqual(result['results'], {}); self.assertEqual(result['result_ids'], [])
        self.assertEqual(result['scope']['product_ids'], [independent.A])
        self.assertIn('好评统计', result['answer']); self.assertIn('主题具体表现', result['answer'])
        expected = restore_pending_goal(self.pending)
        self.assertIsNotNone(expected); self.assertTrue(has_saved_integrity(self.pending))
        self.assertEqual(expected['goal'], self.pending['result']['request_goal'])
        self.assertEqual(expected['scope'], self.pending['result']['scope'])
        self.assertEqual(expected['scope_sha256'], independent.independent_digest(expected['scope']))
        self.assertEqual(expected['source']['task_sha256'], self.pending['progress']['analysis_integrity']['sha256'])
        self.assertEqual((expected['goal']['levels'], expected['goal']['sentiment']), (['themes'], 'negative'))
        self.assertFalse(expected['completed'])
        if structured:
            self.assertEqual(result['analysis_state']['pending_goal'], expected)
        else:
            # 旧调用未提供结构化来源，不能从文字捏造可信状态；原任务库中的待办不变。
            self.assertIsNone(result['analysis_state']['pending_goal'])
            self.assertIsNone(result['analysis_state']['ranking_basis'])
        if task is not None:
            self.assertEqual(task['status'], 'succeeded', task.get('error'))
            self.assertEqual(self.fixture.load(task)['analysis_state']['pending_goal'], expected)
        self.assertEqual(self.fixture.load(self.pending)['analysis_state']['pending_goal'], expected)
        self.authorized(task or {'result': result})

    def test_a_short_sentiment_keeps_themes_with_correct_empty_positive_result(self):
        # 保留历史测试 ID；旧“短句直接承接主题”已由正式人工 A 取代，先澄清。
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=AssertionError('clarify first')) as model:
            task = self.fixture.work('那好评呢？', self.pending['task_id'])
        model.assert_not_called()
        self.assert_positive_clarification(task['result'], task=task)

    def test_b_explicit_dimension_continues_pending_negative_theme(self):
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=fixed_topics):
            task = self.fixture.work('客服方面呢？', self.pending['task_id'])
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        result = task['result']
        self.assertEqual(result['request_goal']['levels'], ['themes'])
        self.assertEqual((result['user_intent']['aspect'], result['user_intent']['sentiment']), ('service', 'negative'))
        self.assertEqual(result['query_plan']['required_tools'], ['get_aspect_drilldown'])
        self.assertTrue(result['task_acceptance']['complete'])
        self.authorized(task)

    def test_c_explicit_positive_manifestation_keeps_theme_level(self):
        task = self.fixture.work('那正面的具体表现呢？', self.pending['task_id'])
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        result = task['result']
        self.assertEqual((result['request_goal']['levels'], result['request_goal']['sentiment']), (['themes'], 'positive'))
        self.assertEqual(result['analysis_intent'], 'aspect_drilldown')
        self.assertEqual(result['task_acceptance']['status'], 'insufficient_evidence')
        self.authorized(task)

    def test_d_explicit_positive_dimension_statistics_replace_pending_themes(self):
        for question in ('那好评主要集中在哪些维度？', '那总体好评呢？', '那整体好评统计呢？'):
            with self.subTest(question=question):
                task = self.fixture.work(question, self.pending['task_id'])
                self.assertEqual(task['status'], 'succeeded', task.get('error'))
                self.assertEqual(task['result']['request_goal']['levels'], ['statistics'])
                self.assertEqual(task['result']['request_goal']['sentiment'], 'positive')
                self.assertEqual(task['result']['query_plan']['required_tools'], ['get_positive_aspects'])
                self.assertTrue(task['result']['task_acceptance']['complete'])
                self.authorized(task)

    def test_e_explicit_count_replaces_pending_themes(self):
        task = self.fixture.work('有多少条？', self.pending['task_id'])
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        self.assertEqual(task['result']['request_goal']['levels'], ['statistics'])
        self.assertEqual(task['result']['query_plan']['required_tools'], ['get_top_issues'])
        self.assertTrue(task['result']['task_acceptance']['complete'])
        self.authorized(task)

    def test_f_scope_changes_do_not_reanimate_pending_from_old_prose(self):
        for change in ({'date_from': '2023-02-02'}, {'product_ids': [independent.B]}):
            with self.subTest(change=change):
                task = self.fixture.work('那好评呢？', self.pending['task_id'], **change)
                self.assertEqual(task['status'], 'succeeded', task.get('error'))
                result = task['result']
                self.assertEqual(result['request_goal']['levels'], ['statistics'])
                self.assertIsNone(result['analysis_state']['pending_goal'])
                self.assertIsNone(result['analysis_state']['ranking_basis'])
                if result['user_intent'].get('needs_clarification'):
                    self.assertEqual(result['query_executions'], 0)
                else:
                    self.assertEqual(result['query_plan']['required_tools'], ['get_positive_aspects'])
                self.authorized(task)

    def test_g_cross_session_does_not_inherit_pending_theme(self):
        for question in ('具体体现在哪里？', '那好评呢？'):
            with self.subTest(question=question):
                params = {**self.fixture.scope, 'selection_scope': self.fixture.scope, 'rules': {},
                          'question': question, 'conversation_id': 'other-session',
                          'context_task_id': self.pending['task_id'], 'model_identity': self.fixture.agent_identity}
                task_id = enqueue(self.fixture.tasks, 'analysis', params)['task']['task_id']
                self.fixture.worker._execute(claim_task(self.fixture.tasks, self.fixture.worker.worker_id))
                task = get_task(self.fixture.tasks, task_id)
                self.assertEqual(task['status'], 'succeeded')
                self.assertIsNone(task['result']['analysis_state']['pending_goal'])
                self.assertIsNone(task['result']['analysis_state']['ranking_basis'])
                if question == '具体体现在哪里？':
                    self.assertEqual(task['result']['status'], 'unsupported')
                    self.assertEqual(task['result']['query_executions'], 0)
                else:
                    self.assertEqual(task['result']['request_goal']['levels'], ['statistics'])
                    self.assertEqual(task['result']['query_plan']['required_tools'], ['get_positive_aspects'])
                self.authorized(task)

    def test_h_injected_statistics_plan_still_fails_independent_theme_goal(self):
        def inject(question, scope, products, **kwargs):
            bound = {**scope, 'product_ids': [independent.A]}
            return bound, build_query_plan('客服差评有多少条？', bound), True
        with patch('data_analysis_agent.agents.reviews.resolve_review_request', side_effect=inject):
            task = self.fixture.work('客服差评具体体现在哪里？', self.pending['task_id'])
        self.assertEqual(task['status'], 'failed')
        result = task['result']
        self.assertEqual(result['request_goal']['levels'], ['themes'])
        self.assertFalse(result['task_plan_check']['passed'])
        self.assertFalse(result['task_acceptance']['plan_check']['passed'])
        self.assertFalse(result['task_acceptance']['complete'])
        self.authorized(task, goal_matches=False)

    def test_ambiguous_history_clarifies_before_any_tool_execution(self):
        from data_analysis_agent.agents.reviews import run_review_agent
        # 隔离注入多查询历史，验证歧义分支；A–H 另经真实持久化 Worker 验证。
        context = self.fixture.load(self.pending)
        context['validated']['queries'].append({'kind': 'evidence', 'parameters': {
            'product_ids': [independent.A], 'sentiment': 'negative', 'page_size': 3}})
        result = run_review_agent(self.fixture.db, '那好评呢？',
            scope=self.fixture.first['result']['scope'], settings=independent.SETTINGS,
            conversation_context=context)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['request_goal']['levels'], ['themes'])
        self.assertEqual(result['request_goal']['sentiment'], 'positive')
        self.assertTrue(result['user_intent']['needs_clarification'])
        self.assertIsNotNone(result['analysis_state']['pending_goal'])
        self.assertEqual(result['query_executions'], 0)
        self.authorized({'result': result})

    def test_explicit_scope_in_question_invalidates_pending_goal(self):
        for question in ('100013588055的好评主要集中在哪些维度？',
                         '2023-02-02到2023-02-28好评主要集中在哪些维度？'):
            with self.subTest(question=question):
                task = self.fixture.work(question, self.pending['task_id'])
                self.assertEqual(task['status'], 'succeeded', task.get('error'))
                result = task['result']
                self.assertEqual(result['request_goal']['levels'], ['statistics'])
                self.assertIsNone(result['analysis_state']['pending_goal'])
                self.assertIsNone(result['analysis_state']['ranking_basis'])
                if '100013588055' in question:
                    self.assertEqual(result['scope']['product_ids'], [independent.B])
                else:
                    self.assertEqual(result['scope']['date_from'], '2023-02-02')
                self.authorized(task)

    def test_explicit_replacement_is_used_by_the_next_short_reference(self):
        replacement = self.fixture.work('那好评主要集中在哪些维度？', self.pending['task_id'])
        following = self.fixture.work('客服方面呢？', replacement['task_id'])
        self.assertEqual(following['status'], 'succeeded', following.get('error'))
        result = following['result']
        self.assertEqual((result['request_goal']['levels'], result['request_goal']['sentiment']),
                         (['statistics'], 'positive'))
        self.assertEqual(result['query_plan']['required_tools'], ['get_positive_aspects'])
        self.assertIsNone(result['analysis_state']['pending_goal'])
        self.authorized(replacement)
        self.authorized(following)

    def test_legacy_user_conditions_without_structured_state_keep_theme_goal(self):
        from data_analysis_agent.agents.reviews import run_review_agent
        context = self.fixture.load(self.pending)
        context.pop('analysis_state')
        # 正式人工 A 同样约束旧用户条件入口；缺少结构化状态不代表可以消除层次歧义。
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=AssertionError('clarify first')) as model:
            result = run_review_agent(self.fixture.db, '那好评呢？',
                scope=self.fixture.first['result']['scope'], settings=independent.SETTINGS,
                conversation_context=context)
        model.assert_not_called()
        self.assert_positive_clarification(result, structured=False)
        self.assertNotIn('analysis_state', context)

    def add_positive_evidence(self):
        # 仅向隔离业务夹具补充正向证据，历史负面排名仍是原保存快照。
        path = self.fixture.root / 'positive.jsonl'
        path.write_text(json.dumps({'parent_asin': independent.A, 'user_id': 'positive',
            'text': '客服回复及时', 'timestamp': 1675987200000}, ensure_ascii=False), encoding='utf-8')
        independent.import_amazon_jsonl(self.fixture.db, reviews_path=path, dataset_version='independent-state')
        review = next(row for row in independent.pending_reviews(self.fixture.db, **self.fixture.identity,
            dataset_version='independent-state', limit=100) if row['text'] == '客服回复及时')
        independent.save_annotation(self.fixture.db, review, **self.fixture.identity, status='success',
            opinions=independent.validate_opinions(json.dumps({'opinions': [dict(aspect='service',
                sentiment='positive', quote='客服回复及时')]}), review['text']))

    def assert_positive_theme(self, task, model, *, aspect=None):
        from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch, validate_topic_result
        result = task['result']
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual((result['request_goal']['levels'], result['request_goal']['sentiment']), (['themes'], 'positive'))
        self.assertEqual(result['request_goal']['aspect'], aspect)
        self.assertTrue(result['task_plan_check']['passed'])
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['query_plan']['required_tools'], ['get_aspect_drilldown'])
        self.assertEqual(model.call_count, 2)
        self.assertEqual([call.args[3] for call in model.call_args_list], [TopicCatalog, TopicBatch])
        self.assertEqual(result['topic_model_calls'], 2)
        data = next(row['data'] for row in result['results'].values() if row['kind'] == 'aspect_drilldown')
        self.assertTrue(validate_topic_result(data)); self.assertEqual(data['sentiment'], 'positive')
        self.assertEqual(data['scope']['product_ids'], [independent.A])
        self.assertEqual(len(data['topics']), 1)
        topic = data['topics'][0]
        self.assertEqual((topic['aspect'], topic['review_count'], topic['review_denominator']), ('service', 1, 1))
        self.assertEqual(topic['semantic_status'], 'model_reviewed_not_human_confirmed')
        self.assertEqual(topic['review_ids'], sorted({row['review_id'] for row in data['opinions']}))
        self.assertTrue(topic['evidence_opinion_ids'])
        for item in data['items']:
            self.assertEqual(item['product_id'], independent.A)
            for opinion in item['opinions']:
                self.assertEqual((opinion['aspect'], opinion['sentiment'], opinion['quote']), ('service', 'positive', '客服回复及时'))
                self.assertEqual(item['text'][opinion['quote_start']:opinion['quote_end']], opinion['quote'])
        self.authorized(task)

    def test_positive_theme_with_matching_evidence_completes_without_paid_model(self):
        self.add_positive_evidence()
        # 保留历史 ID 的有证据边界；正式 A 先澄清，正向完成能力由下方明确主题用例保留。
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=fixed_positive_topics) as model:
            task = self.fixture.work('那好评呢？', self.pending['task_id'])
        model.assert_not_called(); self.assert_positive_clarification(task['result'], task=task)

    def test_explicit_positive_theme_with_matching_evidence_completes_without_paid_model(self):
        self.add_positive_evidence()
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=fixed_positive_topics) as model:
            task = self.fixture.work('那好评具体体现在哪里？', self.pending['task_id'])
        self.assert_positive_theme(task, model)

    def test_explicit_positive_theme_without_evidence_is_insufficient_not_clarification(self):
        task = self.fixture.work('那好评具体体现在哪里？', self.pending['task_id'])
        result = task['result']
        self.assertEqual(task['status'], 'succeeded'); self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['user_intent'].get('needs_clarification'))
        self.assertEqual((result['request_goal']['levels'], result['request_goal']['sentiment']), (['themes'], 'positive'))
        self.assertEqual(result['task_acceptance']['status'], 'insufficient_evidence')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertGreater(result['query_executions'], 0); self.assertEqual(result['topic_model_calls'], 0)
        self.authorized(task)

    def test_unique_single_theme_context_allows_short_positive_switch(self):
        self.add_positive_evidence()
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=fixed_topics):
            negative = self.fixture.work('客服差评具体体现在哪里？', self.pending['task_id'])
        self.assertTrue(negative['result']['task_acceptance']['complete']); self.authorized(negative)
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=fixed_positive_topics) as model:
            task = self.fixture.work('那好评呢？', negative['task_id'])
        self.assert_positive_theme(task, model, aspect='service')

    def test_short_positive_after_only_statistics_uses_positive_counts(self):
        self.add_positive_evidence()
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=AssertionError('statistics is not classification')) as model:
            task = self.fixture.work('那好评呢？', self.fixture.first['task_id'])
        model.assert_not_called()
        result = task['result']; self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual((result['request_goal']['levels'], result['request_goal']['sentiment']), (['statistics'], 'positive'))
        self.assertEqual(result['query_plan']['required_tools'], ['get_positive_aspects'])
        data = next(row['data'] for row in result['results'].values() if row['kind'] == 'positive_aspects')
        self.assertEqual(data['summary']['positive_review_count'], 1)
        self.authorized(task)


if __name__ == '__main__':
    unittest.main()
