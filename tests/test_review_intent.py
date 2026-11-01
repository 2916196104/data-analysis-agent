"""意图补全、失败指代恢复和受约束模型的离线验收；沿用真实工具和临时数据库。"""

import csv
import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from openai import OpenAI

import test_assistant_semantics as fixture
from test_agent import SETTINGS, completion
from data_analysis_agent.agents.review_intent import ReviewIntent
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, finish_task, get_task
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.store import import_amazon_jsonl


class ReviewIntentTests(fixture.AssistantSemanticsFixture, unittest.TestCase):

    def queue(self):
        self.tasks = self.root / 'intent-tasks.sqlite3'
        init_tasks(self.tasks, self.db)
        self.worker = TaskWorker(self.db, self.tasks, import_root=self.root, env_file=self.root / 'unused.env')
        worker_started(self.tasks, self.worker.worker_id)
        self.agent_identity = dict(model=SETTINGS.model, base_url=SETTINGS.base_url, prompt_version='intent-fixture')

    def enqueue(self, question, *, parent=None, **scope):
        params = {**self.scope(**scope), 'rules': {}, 'question': question, 'conversation_id': 'conversation-A',
                  'model_identity': self.agent_identity}
        if parent:
            params['context_task_id'] = parent
        return enqueue(self.tasks, 'analysis', params)['task']['task_id']

    def save(self, question, result, *, status='succeeded', parent=None):
        task_id = self.enqueue(question, parent=parent)
        claim_task(self.tasks, self.worker.worker_id)
        finish_task(self.tasks, task_id, self.worker.worker_id, status=status, result=result,
                    error={'code': 'timeout', 'message': 'fixture timeout'} if status == 'failed' else None)
        return task_id

    def work(self, question, *, parent):
        task_id = self.enqueue(question, parent=parent)
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(SETTINGS, self.agent_identity)), \
             patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planning model')), \
             patch('openai.OpenAI', side_effect=AssertionError('no model required')):
            self.worker._execute(claim_task(self.tasks, self.worker.worker_id))
        return get_task(self.tasks, task_id)

    def test_a_b_c_worker_inherits_positive_and_drills_into_effectiveness(self):
        self.queue()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        first = self.run_sync('美的空调的好评主要是什么？')
        self.assertEqual(first['status'], 'ok', first.get('error'))
        self.assertEqual(first['query_plan']['required_tools'], ['get_positive_aspects'])
        parent = self.save(first['question'], first)
        second = self.work('使用效果主要集中在哪些地方？', parent=parent)
        self.assertEqual(second['status'], 'succeeded', second.get('error'))
        result = second['result']
        self.assertEqual(result['sentiment_intent'], 'positive')
        self.assertEqual(result['validation']['status'], 'passed')
        query = result['results']['r1']
        self.assertEqual(query['parameters']['aspect'], 'effectiveness')
        self.assertEqual(query['parameters']['sentiment'], 'positive')
        self.assertEqual(query['kind'], 'opinion_breakdown')
        self.assertEqual(query['data']['total'], 2)
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['user_intent']['sub_intent'], 'drill_down')
        third = self.work('那物流方面呢？', parent=second['task_id'])
        self.assertEqual(third['status'], 'succeeded', third.get('error'))
        self.assertEqual(third['result']['results']['r1']['parameters']['sentiment'], 'positive')
        self.assertEqual(third['result']['results']['r1']['parameters']['aspect'], 'logistics')
        self.assertEqual(third['result']['results']['r1']['kind'], 'opinion_breakdown')
        self.assertEqual(third['result']['user_intent']['sub_intent'], 'drill_down')
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_c_ambiguous_query_type_asks_specific_question_without_queries(self):
        first = self.run_sync('好评主要方面，并给出3条证据')
        self.assertEqual(first['status'], 'ok', first.get('error'))
        self.queue(); parent = self.save(first['question'], first)
        task = self.work('那物流方面呢？', parent=parent)
        self.assertEqual(task['result']['status'], 'unsupported')
        self.assertIn('正向维度汇总', task['result']['answer'])
        self.assertIn('评论原文', task['result']['answer'])
        self.assertEqual(task['result']['query_executions'], 0)
        scope = self.scope(product_ids=['100013588053', 'XIAOMI-TEST'])
        context = {'user': {'task_id': 'good', 'question': '美的空调与小米空调的不同原文'},
            'validated': {'task_id': 'good', 'scope': scope, 'sentiment': 'negative', 'queries': [
                {'kind': 'evidence', 'parameters': {'product_ids': ['100013588053'], 'aspect': 'packaging', 'sentiment': 'negative', 'page_size': 3}},
                {'kind': 'evidence', 'parameters': {'product_ids': ['XIAOMI-TEST'], 'aspect': 'logistics', 'sentiment': 'negative', 'page_size': 3}}]}}
        selected = run_review_agent(self.db, '那物流方面呢？', scope=scope, settings=SETTINGS, conversation_context=context)
        self.assertEqual(selected['status'], 'ok', selected.get('error'))
        self.assertEqual(selected['scope']['product_ids'], ['XIAOMI-TEST'])

    def test_d_failed_task_recovers_explicit_objects_but_never_its_statistics(self):
        self.queue()
        failed = {'agent': True, 'status': 'error', 'results': {'untrusted': {'data': {'review_count': 999999}}},
                  'scope': self.scope(product_ids=['UNTRUSTED'])}
        parent = self.save('美的空调与小米空调的优缺点都有哪些？', failed, status='failed')
        context = load_analysis_context(self.tasks, {**self.scope(), 'conversation_id': 'conversation-A', 'context_task_id': parent})
        self.assertIsNone(context['validated'])
        self.assertNotIn('999999', json.dumps(context))
        task = self.work('对比这两款商品的差别。', parent=parent)
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        self.assertEqual(task['result']['scope']['product_ids'], ['100013588053', 'XIAOMI-TEST'])
        self.assertEqual(task['result']['query_plan']['required_tools'], ['compare_products'])
        self.assertNotIn('untrusted', task['result']['results'])
        with patch('openai.OpenAI', side_effect=AssertionError('no provider needed')):
            selected = run_review_agent(self.db, '对比这两款商品的差别',
                scope=self.scope(product_ids=['100013588053', 'XIAOMI-TEST']), settings=SETTINGS)
        self.assertEqual(selected['status'], 'ok', selected.get('error'))
        self.assertEqual(selected['query_plan']['required_tools'], ['compare_products'])

    def test_d_multiple_models_requests_concrete_ids_for_both_brands(self):
        self.queue()
        parent = self.save('美的空调与小米空调的优缺点都有哪些？', {'agent': True, 'status': 'error'}, status='failed')
        path = self.root / 'ambiguous-models.csv'
        rows = [dict(product_id='M2', product_name='美的 3匹空调', brand='美的', text='模拟未标注', published_at='2021-06-03'),
                dict(product_id='X2', product_name='小米 3匹空调', brand='小米', text='模拟未标注', published_at='2021-06-03')]
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        task = self.work('对比这两款商品的差别。', parent=parent)
        self.assertEqual(task['result']['status'], 'unsupported')
        for value in ('美的空调', '小米空调', 'M2', 'X2', '商品 ID'):
            self.assertIn(value, task['result']['answer'])
        self.assertEqual(task['result']['query_executions'], 0)
        self.assertTrue(task['result']['user_intent']['needs_clarification'])

    def test_e_missing_product_never_uses_current_selected_product(self):
        with patch('openai.OpenAI') as model:
            result = run_review_agent(self.db, '外星牌空调的好评主要是什么？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('未找到', result['answer'])
        self.assertEqual(result['query_executions'], 0)
        model.assert_not_called()
        result = run_review_agent(self.db, '100013588053 与外星牌空调的好评主要是什么？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('外星牌空调', result['answer'])
        self.assertEqual(result['query_executions'], 0)

    def test_failed_explicit_product_and_sentiment_override_older_success_conditions(self):
        self.queue()
        first = self.run_sync('美的空调好评主要集中在哪些方面？')
        old = self.save(first['question'], first)
        failed = self.save('小米空调的差评主要是什么？', {'agent': True, 'status': 'error'}, status='failed', parent=old)
        task = self.work('使用效果主要集中在哪些地方？', parent=failed)
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        query = task['result']['results']['r1']
        self.assertEqual(query['parameters']['sentiment'], 'negative')
        self.assertEqual(query['parameters']['product_ids'], ['XIAOMI-TEST'])
        self.assertEqual(task['result']['validation']['status'], 'passed')
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': failed, 'conversation_id': 'conversation-A'})
        with self.semantic_model(ReviewIntent(query_type='aspect_summary', sentiment='negative').model_dump()):
            result = run_review_agent(self.db, '到底哪块让人不爽呀', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['XIAOMI-TEST'])
        payload = json.loads(self.requests[0]['messages'][-1]['content'])
        self.assertEqual(payload['compact_context']['explicit_user_intent']['sentiment'], 'negative')
        self.assertEqual(payload['compact_context']['explicit_user_intent']['query_type'], 'aspect_summary')
        switched = self.work('那好评呢？', parent=failed)
        self.assertEqual(switched['result']['results']['r1']['kind'], 'positive_aspects')
        self.assertEqual(switched['result']['scope']['product_ids'], ['XIAOMI-TEST'])
        trend_failed = self.save('按日查看小米空调物流问题趋势', {'agent': True, 'status': 'error'}, status='failed', parent=old)
        monthly = self.work('换成按月', parent=trend_failed)
        self.assertEqual(monthly['result']['results']['r1']['kind'], 'aspect_trend')
        self.assertEqual(monthly['result']['results']['r1']['parameters']['period'], 'month')
        self.assertEqual(monthly['result']['scope']['product_ids'], ['XIAOMI-TEST'])

    def test_raw_logistics_followup_keeps_baseline_window_and_evidence_page_size(self):
        self.queue()
        question = '执行本轮已确认的只读查询\n' + json.dumps({'tool': 'get_evidence', 'parameters': {
            'product_ids': ['100013588053'], 'aspect': 'packaging', 'sentiment': 'negative',
            'window': 'baseline', 'page': 1, 'page_size': 5}})
        first = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
        self.assertEqual(first['status'], 'ok', first.get('error'))
        parent = self.save(first['question'], first)
        task = self.work('那物流方面呢？', parent=parent)
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        query = task['result']['results']['r1']
        self.assertEqual(query['parameters']['window'], 'baseline')
        self.assertEqual(query['parameters']['page_size'], 5)
        self.assertEqual(query['data']['scope']['date_from'], self.scope()['baseline_from'])
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        with self.semantic_model(ReviewIntent(query_type='evidence', sentiment='negative', aspect='logistics').model_dump()):
            oral = run_review_agent(self.db, '给咱瞅瞅物流那块的原话呗', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(oral['status'], 'ok', oral.get('error'))
        self.assertEqual(oral['results']['r1']['parameters']['window'], 'baseline')
        self.assertEqual(oral['results']['r1']['parameters']['page_size'], 5)

    def test_multiple_failed_turns_merge_explicit_slots_without_trusting_results(self):
        self.queue()
        first = self.run_sync('美的空调好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        unsafe = {'agent': True, 'status': 'error', 'sentiment_intent': 'positive',
                  'results': {'untrusted': {'data': {'review_count': 999999}}}}
        failed = self.save('小米空调的差评主要是什么？', unsafe, status='failed', parent=parent)
        failed_again = self.save('使用效果主要集中在哪些地方？', unsafe, status='failed', parent=failed)
        task = self.work('那物流方面呢？', parent=failed_again)
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        self.assertEqual(task['result']['results']['r1']['parameters'], {
            'product_ids': ['XIAOMI-TEST'], 'aspect': 'logistics', 'sentiment': 'negative', 'topic': None})
        self.assertEqual(task['result']['validation']['status'], 'passed')
        self.assertEqual(task['result']['total_model_calls'], 0)
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': failed_again, 'conversation_id': 'conversation-A'})
        self.assertNotIn('999999', json.dumps(context))
        with self.semantic_model(ReviewIntent(query_type='opinion_breakdown', aspect='effectiveness').model_dump()):
            oral = run_review_agent(self.db, '再往里挖挖呗', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(oral['status'], 'ok', oral.get('error'))
        self.assertEqual(oral['results']['r1']['parameters']['sentiment'], 'negative')
        payload = json.loads(self.requests[0]['messages'][-1]['content'])
        self.assertEqual(payload['compact_context']['explicit_user_intent']['sentiment'], 'negative')
        self.assertEqual(payload['compact_context']['explicit_user_intent']['aspect'], 'effectiveness')

    def test_summary_reference_preserves_effective_product_sentiment_and_detail(self):
        self.queue()
        first = self.run_sync('小米空调好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        repeated = self.work('主要集中在哪些地方？', parent=parent)
        self.assertEqual(repeated['result']['query_plan']['required_tools'], ['get_positive_aspects'])
        self.assertEqual(repeated['result']['scope']['product_ids'], ['XIAOMI-TEST'])
        detail = self.work('使用效果主要集中在哪些地方？', parent=repeated['task_id'])
        repeated_detail = self.work('那主要集中在哪些地方呢？', parent=detail['task_id'])
        self.assertEqual(repeated_detail['result']['results']['r1']['parameters'], {
            'product_ids': ['XIAOMI-TEST'], 'aspect': 'effectiveness', 'sentiment': 'positive', 'topic': None})
        # 本轮明确的负向方向覆盖历史正向条件。
        switched = self.work('使用效果的差评主要集中在哪些地方？', parent=detail['task_id'])
        self.assertEqual(switched['result']['results']['r1']['parameters']['sentiment'], 'negative')
        trend = self.run_sync('按月查看负面趋势')
        parent = self.save(trend['question'], trend)
        unclear = self.work('主要集中在哪些地方？', parent=parent)
        self.assertEqual(unclear['result']['status'], 'unsupported')
        self.assertIn('还是查看评价维度', unclear['result']['answer'])
        self.assertEqual(unclear['result']['query_executions'], 0)

    def test_named_missing_non_air_conditioner_is_not_replaced_by_selection(self):
        for question in ('外星牌冰箱的好评主要是什么？', '请看看外星牌耳机的好评主要是什么？',
                         '小米手机的好评主要是什么？',
                         '100013588053 与外星牌冰箱的好评主要是什么？'):
            with self.subTest(question=question), patch('openai.OpenAI') as model:
                result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
            self.assertEqual(result['status'], 'unsupported')
            self.assertIn('未找到', result['answer'])
            self.assertEqual(result['query_executions'], 0)
            model.assert_not_called()
        for question in ('这个商品的好评主要是什么？', '物流方面的好评主要是什么？', '它们的好评主要是什么？'):
            with self.subTest(question=question):
                result = self.run_sync(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))

    def test_semantic_ambiguity_is_specific_and_does_not_query(self):
        self.queue()
        first = self.run_sync('美的空调好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        draft = ReviewIntent(needs_clarification=True, clarification_question='“它”指当前空调，还是要换另一款商品？').model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '那它呢？', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('“它”指当前空调', result['answer'])
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['intent_model_calls'], 1)

    def test_model_can_inherit_only_the_grounded_topic_for_its_aspect(self):
        self.queue()
        first = self.run_sync('它的制冷效果怎么样？')
        parent = self.save(first['question'], first)
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        draft = ReviewIntent(query_type='opinion_breakdown', aspect='effectiveness', sub_intent='drill_down', topic='制冷').model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '那再往里挖挖呢？', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['results']['r1']['parameters']['topic'], '制冷')
        payload = json.loads(self.requests[0]['messages'][-1]['content'])
        self.assertEqual(payload['compact_context']['explicit_user_intent']['topic'], '制冷')
        with self.semantic_model({**draft, 'topic': '安装'}):
            result = run_review_agent(self.db, '那再往里挖挖呢？', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['query_executions'], 0)

    def semantic_model(self, draft, *, error=None):
        self.requests = []
        def handler(request):
            payload = json.loads(request.content); self.requests.append(payload)
            if error:
                raise error
            return completion({'role': 'assistant', 'content': json.dumps(draft)})
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            self.assertLessEqual(kwargs['timeout'], min(SETTINGS.timeout_seconds, 30))
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        return patch('openai.OpenAI', side_effect=client)

    def test_colloquial_fallback_has_one_schema_call_and_no_tools_or_history_data(self):
        draft = ReviewIntent(query_type='aspect_summary', sentiment='positive').model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '这款机器让买家买账的点是啥？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['query_plan']['required_tools'], ['get_positive_aspects'])
        self.assertEqual(result['intent_model_calls'], 1)
        self.assertEqual(result['total_model_calls'], 1)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('tools', self.requests[0])
        prompt = self.requests[0]['messages'][-1]['content']
        for field in ('file_sha256', 'base_url', 'api_key', 'raw_json', 'results', 'trace'):
            self.assertNotIn('"' + field + '"', prompt)
        self.assertLessEqual(len(prompt.encode()), 16000)

    def test_model_cannot_inject_sql_entities_dimensions_sentiment_or_time(self):
        base = ReviewIntent(query_type='aspect_summary', sentiment='positive').model_dump()
        invalid = [{}, {**base, 'sql': 'DELETE FROM reviews'}, {**base, 'aspect': 'installation'},
                   {**base, 'product_mentions': ['虚构商品']},
                   {**base, 'time_range': {'date_from': '2030-01-01', 'date_to': '2030-12-31'}}]
        for draft in invalid:
            with self.subTest(draft=draft), self.semantic_model(draft):
                result = run_review_agent(self.db, '这款机器让买家买账的点是啥？', scope=self.scope(), settings=SETTINGS)
            self.assertIn(result['status'], ('error', 'unsupported'))
            self.assertEqual(result['query_executions'], 0)
            self.assertNotEqual(result['public_answer']['status'], 'ok')

    def test_model_recognized_pros_cons_reuses_existing_readonly_capability(self):
        draft = ReviewIntent(query_type='pros_cons').model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '说说这款机器好的坏的呗', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['analysis_intent'], 'pros_cons')
        self.assertEqual(result['query_executions'], 3)
        self.assertEqual(result['intent_model_calls'], 1)

    def test_model_may_echo_frozen_dates_without_causing_false_clarification(self):
        self.queue()
        first = self.run_sync('美的空调好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        draft = ReviewIntent(query_type='opinion_breakdown', product_mentions=['美的空调'], sentiment='positive',
            time_range={'date_from': self.scope()['date_from'], 'date_to': self.scope()['date_to']}).model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '那大家到底是夸它哪些点呀？', scope=self.scope(), settings=SETTINGS,
                                      conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['date_from'], self.scope()['date_from'])
        self.assertEqual(result['results']['r1']['parameters']['sentiment'], 'positive')

    def test_explicit_current_product_wins_even_when_model_omits_or_replaces_entity(self):
        self.queue()
        first = self.run_sync('美的空调好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        context = load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        draft = ReviewIntent(query_type='aspect_summary', sentiment='positive').model_dump()
        with self.semantic_model(draft):
            result = run_review_agent(self.db, '小米空调哪些点让人买账啊', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['XIAOMI-TEST'])
        with self.semantic_model({**draft, 'product_mentions': ['美的空调']}):
            result = run_review_agent(self.db, '小米空调哪些点让人买账啊', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['query_executions'], 0)
        with self.semantic_model(ReviewIntent(query_type='pros_cons', product_mentions=['美的空调']).model_dump()):
            result = run_review_agent(self.db, '小米空调好的坏的都说说', scope=self.scope(), settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['XIAOMI-TEST'])
        self.assertEqual(result['user_intent']['product_mentions'], ['小米空调'])

    def test_rules_only_and_semantic_timeout_never_publish_unverified_answer(self):
        question = '这款机器让买家买账的点是啥？'
        with patch('openai.OpenAI') as model:
            result = run_review_agent(self.db, question, scope=self.scope(), settings=replace(SETTINGS, semantic_parser_enabled=False))
        self.assertEqual(result['status'], 'unsupported'); model.assert_not_called()
        with self.semantic_model({}, error=httpx.ReadTimeout('simulated semantic timeout')):
            result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['intent_resolution']['error_code'], 'timeout')
        self.assertEqual(result['intent_model_calls'], 1)
        self.assertEqual(result['query_executions'], 0)

    def test_context_is_bounded_and_does_not_cross_conversation_or_selection(self):
        self.queue()
        parent = None
        for index in range(6):
            parent = self.save(f'美的空调请求 {index}', {'agent': True, 'status': 'error'}, status='failed', parent=parent)
        params = {**self.scope(), 'conversation_id': 'conversation-A', 'context_task_id': parent}
        context = load_analysis_context(self.tasks, params)
        self.assertEqual(context['turns_read'], 4)
        self.assertIsNone(context['validated'])
        self.assertIsNone(load_analysis_context(self.tasks, {**params, 'conversation_id': 'other'}))
        self.assertIsNone(load_analysis_context(self.tasks, {**params, 'dataset_version': 'other'}))
        self.assertIsNone(load_analysis_context(self.tasks, {**params, 'product_ids': ['OTHER']}))
