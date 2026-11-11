"""P6-B 真实 Worker/只读 SQL 准备链；禁止任何主题分类和外部模型。"""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, replace, FrozenInstanceError
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import test_p1a_independent_validation as independent
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.agents import review_preparation
from data_analysis_agent.agents.review_plan import build_p6_query_plan
from data_analysis_agent.agents.review_multi_aspect import freeze_p6_goal, validate_p6_plan
from data_analysis_agent.core.errors import ReviewDataError


class P6BPlannerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = independent.IndependentStateValidation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        settings = SimpleNamespace(**{**asdict(independent.SETTINGS), 'p6_preparation_enabled': True})
        self.enterContext(patch('data_analysis_agent.jobs.worker.configured_agent_identity',
            return_value=(settings, self.fixture.agent_identity)))
        self.classifier = self.enterContext(patch('data_analysis_agent.agents.review_topics.analyze_aspect_topics',
            side_effect=AssertionError('P6-B must not classify')))
        self.addCleanup(self.classifier.assert_not_called)
        for target in ('data_analysis_agent.agents.review_intent._llm_intent',
                       'data_analysis_agent.agents.reviews.create_llm',
                       'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            blocked = self.enterContext(patch(target, side_effect=AssertionError('P6-B forbids model/embedding entry')))
            self.addCleanup(blocked.assert_not_called)

    def work(self, question='具体体现在哪里？', **changes):
        task = self.fixture.work(question, self.fixture.first['task_id'], **changes)
        if task['result'].get('p6_preparation'):
            self.assertEqual(task['result']['total_model_calls'], 0)
            self.assertEqual(task['result']['tool_calls'], 0)
            self.assertEqual(task['result']['trace'], [])
        return task

    def capture(self, question='具体体现在哪里？'):
        prepared = []
        original = review_preparation.prepare_p6_analysis
        def prepare(*args, **kwargs):
            value = original(*args, **kwargs)
            prepared.append(value)
            return value
        with patch.object(review_preparation, 'prepare_p6_analysis', side_effect=prepare):
            task = self.work(question)
        return task, prepared

    def add_review(self):
        path = self.fixture.root / 'new-review.jsonl'
        path.write_text(json.dumps({'parent_asin': independent.A, 'user_id': 'new',
            'text': '包装破损', 'timestamp': 1676073600000}, ensure_ascii=False), encoding='utf-8')
        independent.import_amazon_jsonl(self.fixture.db, reviews_path=path, dataset_version='independent-state')
        review = next(r for r in independent.pending_reviews(self.fixture.db, **self.fixture.identity,
            dataset_version='independent-state', limit=100) if r['text'] == '包装破损')
        independent.save_annotation(self.fixture.db, review, **self.fixture.identity, status='success',
            opinions=independent.validate_opinions(json.dumps({'opinions': [dict(aspect='packaging',
                sentiment='negative', quote='包装破损')]}), review['text']))

    def wal(self):
        with closing(connect_review_database(self.fixture.db)) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')

    def arguments(self):
        return {'scope': self.fixture.first['result']['scope'], 'conversation_id': 'independent-session',
                'ranking_task': self.fixture.first, 'agent_identity': self.fixture.agent_identity,
                'context': self.fixture.load()}

    def test_actual_worker_prepares_exact_plan_without_classifying_or_completing(self):
        task = self.work()
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        result = task['result']
        self.assertEqual(result['request_goal']['version'], 'review-request-goal-p6-v1')
        self.assertEqual(result['request_goal']['required_aspects'], ['service', 'effectiveness', 'logistics'])
        self.assertTrue(result['task_plan_check']['passed'])
        self.assertEqual(result['p6_preparation']['status'], 'ready')
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['p6_preparation']['query_executions'], 4)
        self.assertNotIn('items', json.dumps(result['p6_preparation']))

    def test_missing_trusted_ranking_clarifies_without_queries(self):
        task = self.fixture.work('具体体现在哪里？')
        result = task['result']
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['p6_preparation']['reason'], 'missing_trusted_ranking')
        self.assertEqual(result['query_executions'], 0)

    def test_single_aspect_and_count_stay_on_original_routes(self):
        task = self.work('物流差评有多少条？')
        self.assertEqual(task['result']['query_plan']['required_tools'], ['get_top_issues'])
        self.assertNotIn('p6_preparation', task['result'])

    def test_precise_plan_faults_are_rejected_before_statistics_or_opinion_reads(self):
        original = build_p6_query_plan
        def remove(p): p.requirements.pop()
        def duplicate(p): p.requirements[2] = deepcopy(p.requirements[0])
        def replace_fourth(p): p.requirements[2]['parameters']['aspect'] = 'quality'
        def reverse(p): p.requirements.reverse()
        def statistics(p): p.requirements[0]['tool'] = 'get_top_issues'
        def positive(p): p.requirements[0]['parameters']['sentiment'] = 'positive'
        def product(p): p.requirements[0]['parameters']['product_ids'] = [independent.B]
        def date(p): p.scope['date_from'] = '2023-02-02'
        def dataset(p): p.scope['dataset_version'] = 'different'
        def identity(p): p.scope['model'] = 'different'
        def extra(p): p.requirements.append(deepcopy(p.requirements[0]))
        def metadata(p): p.required_aspects = ('price',)
        for change in (remove, duplicate, replace_fourth, reverse, statistics, positive, product, date, dataset, identity, extra, metadata):
            def faulty(goal):
                self.assertEqual(goal.required_aspects, ('service', 'effectiveness', 'logistics'))
                plan = original(goal); change(plan); return plan
            with self.subTest(change=change.__name__), \
                 patch('data_analysis_agent.agents.review_plan.build_p6_query_plan', side_effect=faulty), \
                 patch.object(analytics, 'get_aspect_opinions', side_effect=AssertionError('unplanned read')):
                task = self.work()
                self.assertEqual(task['status'], 'failed')
                self.assertFalse(task['result']['task_plan_check']['passed'])
                self.assertEqual(task['result']['query_executions'], 0)
                self.assertEqual(task['result']['request_goal']['required_aspects'], ['service', 'effectiveness', 'logistics'])

    def test_data_changed_since_ranking_blocks_without_reselection(self):
        self.add_review()
        with patch.object(analytics, 'get_aspect_opinions', side_effect=AssertionError('stale ranking must stop')):
            task = self.work()
        value = task['result']['p6_preparation']
        self.assertEqual(value['reason'], 'stale_ranking')
        self.assertEqual(value['current_snapshot']['denominator']['value'], 7)
        self.assertEqual(value['current_snapshot']['negative_review_counts']['packaging'], 1)
        self.assertEqual(task['result']['request_goal']['required_aspects'], ['service', 'effectiveness', 'logistics'])
        self.assertEqual(value['query_executions'], 1)
        self.assertFalse(task['result']['task_acceptance']['complete'])

    def test_unselected_count_missing_dimension_and_definition_faults_block(self):
        original = analytics.get_top_issues
        for change in ('unselected_count', 'missing_aspect', 'missing_issue', 'denominator', 'count_unit', 'missing_summary'):
            def changed(*args, **kwargs):
                value = original(*args, **kwargs)
                if change == 'unselected_count':
                    value['current']['aspects']['packaging'].update(negative_review_count=1, negative_rate=0.166667)
                    next(r for r in value['issues'] if r['aspect']=='packaging').update(negative_review_count=1, negative_rate=0.166667)
                elif change == 'missing_aspect': value['current']['aspects'].pop('packaging')
                elif change == 'missing_issue': value['issues'].pop()
                elif change == 'denominator': value['definitions']['negative_rate_denominator'] = 'all_reviews'
                elif change == 'count_unit': value['definitions']['count_unit'] = 'opinions'
                else: value.pop('current')
                return value
            with self.subTest(change=change), patch.object(analytics, 'get_top_issues', side_effect=changed), \
                 patch.object(analytics, 'get_aspect_opinions', side_effect=AssertionError('must stop before prefetch')):
                task = self.work()
                self.assertEqual(task['result']['p6_preparation']['status'], 'blocked')
                self.assertEqual(task['result']['p6_preparation']['query_executions'], 1)

    def test_concurrent_committed_insert_does_not_mix_statistics_and_inputs(self):
        self.wal()
        original = analytics.get_top_issues
        connections = []
        extract = analytics.get_aspect_opinions
        def statistics(connection, **kwargs):
            value = original(connection, **kwargs)
            connections.append(connection)
            self.add_review()  # 另一连接真实提交，当前读事务继续保留原快照。
            return value
        def opinions(connection, **kwargs):
            self.assertIs(connection, connections[0])
            return extract(connection, **kwargs)
        with patch.object(analytics, 'get_top_issues', side_effect=statistics), \
             patch.object(analytics, 'get_aspect_opinions', side_effect=opinions):
            task, values = self.capture()
        self.assertEqual(task['result']['p6_preparation']['status'], 'ready')
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].validate()['current_snapshot']['denominator']['value'], 6)
        self.assertTrue(all(r['input']['total'] == independent.COUNTS[r['input']['aspect']] for r in values[0].validate()['inputs']))
        scope = {k:v for k,v in self.arguments()['scope'].items() if not k.startswith('baseline_')}
        self.assertEqual(original(self.fixture.db, **scope)['current']['successful_reviews'], 7)
        with self.assertRaises(Exception): connections[0].execute('SELECT 1')
        values[0].validate()  # 事务和连接已释放，仍可独立检查。

    def test_concurrent_opinion_edit_is_not_visible_mid_prefetch(self):
        self.wal()
        original = analytics.get_aspect_opinions
        first_quote = []
        def opinions(connection, **kwargs):
            data = original(connection, **kwargs)
            if not first_quote:
                first_quote.append(data['items'][0]['text'])
                with closing(connect_review_database(self.fixture.db)) as writer, writer:
                    writer.execute("UPDATE review_opinions SET sentiment='positive' WHERE aspect='effectiveness'")
            return data
        with patch.object(analytics, 'get_aspect_opinions', side_effect=opinions):
            task, values = self.capture()
        self.assertEqual(task['result']['p6_preparation']['status'], 'ready')
        self.assertTrue(all(r['sentiment']=='negative' for r in values[0].get_input('effectiveness')['opinions']))
        scope = {k:v for k,v in self.arguments()['scope'].items() if not k.startswith('baseline_')}
        self.assertEqual(analytics.get_top_issues(self.fixture.db, **scope)['current']['aspects']['effectiveness']['negative_review_count'], 0)

    def test_frozen_input_survives_later_changes_and_detects_corruption_without_reads(self):
        task, values = self.capture()
        prepared = values[0]
        self.assertNotIn(independent.QUOTES['service'], repr(prepared))
        before = prepared.get_input('service')
        self.add_review()
        self.assertEqual(prepared.get_input('service'), before)
        before['items'][0]['text'] = 'modified returned copy'
        self.assertNotEqual(prepared.get_input('service'), before)
        with self.assertRaises(FrozenInstanceError): prepared.payload_json = '{}'
        payload = json.loads(prepared.payload_json); payload['inputs'][0]['input']['items'].clear()
        damaged = replace(prepared, payload_json=json.dumps(payload))
        with patch.object(analytics, 'get_aspect_opinions', side_effect=AssertionError('no database fallback')), \
             patch.object(analytics, 'get_top_issues', side_effect=AssertionError('no database fallback')):
            prepared.get_input('service')
            with self.assertRaises(ReviewDataError): damaged.get_input('service')
            with self.assertRaises(ReviewDataError): prepared.get_input('price')
        serialized = json.dumps(task['result'], ensure_ascii=False)
        self.assertNotIn(independent.QUOTES['service'], serialized)
        self.assertNotIn('selected_inputs', serialized)

    def test_prefetch_failure_or_malformed_evidence_never_yields_complete_artifact(self):
        original = analytics.get_aspect_opinions
        for mode in ('exception', 'missing_opinion', 'bad_quote'):
            def broken(connection, **kwargs):
                if kwargs['aspect'] != 'effectiveness': return original(connection, **kwargs)
                if mode == 'exception': raise RuntimeError('injected prefetch failure')
                data = original(connection, **kwargs)
                if mode == 'missing_opinion': data['items'][0]['opinions'].clear()
                else: data['items'][0]['text'] = 'invalid citation'
                return data
            with self.subTest(mode=mode), patch.object(analytics, 'get_aspect_opinions', side_effect=broken):
                task, values = self.capture()
                self.assertEqual(task['result']['p6_preparation']['status'], 'blocked')
                self.assertFalse(values)
                self.assertFalse(task['result']['task_acceptance']['complete'])

    def test_cross_session_and_explicit_scope_changes_do_not_reuse_ranking(self):
        scope = self.fixture.scope
        context = load_analysis_context(self.fixture.tasks, {**scope, 'selection_scope':scope,
            'conversation_id':'another-session', 'context_task_id':self.fixture.first['task_id']})
        self.assertIsNone(context)
        for change in ({'product_ids':[independent.B]}, {'date_from':'2023-02-02'}):
            task = self.work(**change)
            self.assertEqual(task['result']['p6_preparation']['status'], 'blocked')
            self.assertEqual(task['result']['query_executions'], 0)
        for q in ('100013588055的差评具体体现在哪里？', '2023-02-02到2023-02-28差评具体体现在哪里？'):
            task = self.work(q)
            self.assertEqual(task['result']['p6_preparation']['status'], 'blocked')
            self.assertEqual(task['result']['query_executions'], 0)

    def test_explicit_other_requests_and_f01_short_sentiment_do_not_trigger_p6(self):
        for q, tool in [('物流方面呢？','get_top_issues'), ('物流差评有多少条？','get_top_issues'),
                ('给我三条差评原文','get_evidence'), ('那好评呢？','get_positive_aspects')]:
            with self.subTest(question=q):
                task = self.work(q)
                self.assertNotIn('p6_preparation', task['result'])
                self.assertIn(tool, task['result']['query_plan']['required_tools'])
                self.assertEqual(task['result']['total_model_calls'], 0)
        prepared = self.work()
        # 新准备轮后核对真实 Router 与独立目标；不执行旧正向主题分类函数。
        from data_analysis_agent.agents.review_intent import resolve_review_request
        from data_analysis_agent.agents.review_acceptance import build_request_goal
        from data_analysis_agent.agents.review_semantics import QuestionScopeError
        from data_analysis_agent.jobs.analysis_state import has_saved_integrity
        from data_analysis_agent.jobs.store import get_task
        context = self.fixture.load(prepared)
        scope = prepared['result']['scope']
        settings = SimpleNamespace(**{**asdict(independent.SETTINGS), 'p6_preparation_enabled':True})
        products = [{'product_id':independent.A, 'title':None, 'brand':None}]
        _, trend, _ = resolve_review_request('按月查看差评变化', scope, products,
            settings=settings, context=self.fixture.load())
        self.assertEqual(trend.required_tools, ['get_trend'])
        # 正式人工 A / Oracle v2 取代旧短句直入主题；保留上方合法统计、趋势覆盖。
        self.assertIsNone(build_request_goal('那好评呢？', context))
        intent = {}
        with self.assertRaises(QuestionScopeError):
            resolve_review_request('那好评呢？', scope, products, settings=settings, context=context, intent_state=intent)
        self.assertEqual(intent['sentiment'], 'positive')
        self.assertIsNone(intent['query_type'])
        task = self.fixture.work('那好评呢？', prepared['task_id'])
        result = task['result']
        self.assertEqual((task['status'], result['status']), ('succeeded', 'unsupported'))
        self.assertIsNone(result['request_goal']); self.assertTrue(result['user_intent']['needs_clarification'])
        self.assertEqual(result['user_intent']['sentiment'], 'positive')
        self.assertFalse((result.get('query_plan') or {}).get('requirements'))
        self.assertEqual((result.get('query_plan') or {}).get('required_tools', []), [])
        self.assertFalse((result.get('task_acceptance') or {}).get('complete'))
        self.assertEqual(result['query_executions'], 0); self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['results'], {}); self.assertEqual(result['result_ids'], [])
        self.assertNotIn('p6_preparation', result); self.assertNotIn('p6_execution', result)
        self.assertIn('好评统计', result['answer']); self.assertIn('主题具体表现', result['answer'])
        # 准备任务本身已冻结三维目标；不能将它伪造成 awaiting_aspect 或已完成任务。
        saved = get_task(self.fixture.tasks, prepared['task_id'])
        self.assertTrue(has_saved_integrity(saved)); self.assertEqual(saved, prepared)
        self.assertEqual(saved['result']['request_goal']['required_aspects'], ['service', 'effectiveness', 'logistics'])

    def test_explicit_positive_theme_keeps_ordinary_plan_after_p6_preparation(self):
        from data_analysis_agent.agents.review_intent import resolve_review_request
        from data_analysis_agent.agents.review_acceptance import build_request_goal, build_task_goal, validate_goal_plan
        prepared = self.work(); context = self.fixture.load(prepared); scope = prepared['result']['scope']
        settings = SimpleNamespace(**{**asdict(independent.SETTINGS), 'p6_preparation_enabled': True})
        products = [{'product_id': independent.A, 'title': None, 'brand': None}]
        question = '那好评具体体现在哪里？'
        _, plan, _ = resolve_review_request(question, scope, products, settings=settings, context=context)
        request = build_request_goal(question, context)
        self.assertEqual((request.levels, request.sentiment), (('themes',), 'positive'))
        self.assertEqual(plan.required_tools, ['get_aspect_drilldown'])
        self.assertTrue(validate_goal_plan(build_task_goal(question, scope, plan, {}, request_goal=request), plan)['passed'])
        decision = freeze_p6_goal(question, scope=scope, conversation_id='independent-session',
            agent_identity=self.fixture.agent_identity, ranking_task=self.fixture.first, context=context)
        self.assertEqual(decision.route, 'existing'); self.assertIsNone(decision.goal)

    def test_worker_rejects_cross_session_and_tampered_source_without_reads(self):
        from data_analysis_agent.jobs.store import enqueue, claim_task, get_task, connect_task_database
        params = {**self.fixture.scope, 'selection_scope':self.fixture.scope, 'rules':{}, 'question':'具体体现在哪里？',
            'conversation_id':'another-session', 'context_task_id':self.fixture.first['task_id'],
            'model_identity':self.fixture.agent_identity}
        task_id = enqueue(self.fixture.tasks,'analysis',params)['task']['task_id']
        self.fixture.worker._execute(claim_task(self.fixture.tasks,self.fixture.worker.worker_id))
        result = get_task(self.fixture.tasks,task_id)['result']
        self.assertEqual(result['p6_preparation']['reason'],'missing_trusted_ranking')
        self.assertEqual(result['query_executions'],0)
        raw = deepcopy(self.fixture.first['result']);raw['answer'] = 'tampered'
        with closing(connect_task_database(self.fixture.tasks)) as connection, connection:
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?', (json.dumps(raw),self.fixture.first['task_id']))
        self.assertEqual(self.work()['result']['query_executions'],0)

    def test_zero_one_two_three_and_more_nonzero_ranks_compile_via_worker(self):
        from test_p6a_contract import P6AContractTests
        from data_analysis_agent.jobs.store import connect_task_database
        producer = P6AContractTests();producer.task = deepcopy(self.fixture.first)
        names = sorted(independent.COUNTS)
        for size in range(7):
            with self.subTest(size=size):
                source = producer.ranked_task({name:int(i<size) for i,name in enumerate(names)})
                with closing(connect_task_database(self.fixture.tasks)) as connection, connection:
                    connection.execute('UPDATE tasks SET result_json=?,progress_json=? WHERE task_id=?',
                        (json.dumps(source['result']),json.dumps(source['progress']),source['task_id']))
                task = self.work()
                result = task['result']
                if size:
                    self.assertEqual(result['request_goal']['required_aspects'], names[:min(size,3)])
                    self.assertEqual([r['parameters']['aspect'] for r in result['query_plan']['requirements']],names[:min(size,3)])
                    self.assertTrue(result['task_plan_check']['passed'])
                else:
                    self.assertEqual(result['p6_preparation']['reason'],'no_negative_evidence')
                    self.assertEqual(result['query_executions'],0)

    def test_closed_runtime_artifact_rejects_recomputed_input_hash_and_missing_dimensions(self):
        from data_analysis_agent.core.serialization import content_sha256
        _, values = self.capture();prepared=values[0]
        for damage in ('missing_aspect','missing_opinion','wrong_scope'):
            payload = json.loads(prepared.payload_json)
            if damage == 'missing_aspect':payload['inputs'].pop()
            elif damage == 'missing_opinion':payload['inputs'][0]['input']['opinions'].clear()
            else:payload['inputs'][0]['input']['scope']['model']='different'
            for row in payload['inputs']:row['input_sha256']=content_sha256(row['input'])
            broken=replace(prepared,payload_json=json.dumps(payload),payload_sha256=content_sha256(payload))
            with self.subTest(damage=damage), self.assertRaises(ReviewDataError):broken.get_input('service')

    def test_p6_tool_dispatch_blocks_before_cache_or_database_fallback(self):
        from data_analysis_agent.agents.review_tools import ReviewToolContext
        goal=freeze_p6_goal('具体体现在哪里？',**self.arguments()).goal
        context=ReviewToolContext.__new__(ReviewToolContext)
        context.query_plan=build_p6_query_plan(goal)
        context.cache={}
        with patch.object(analytics,'get_aspect_opinions',side_effect=AssertionError('no fallback')):
            with self.assertRaises(ReviewDataError):
                context.execute('get_aspect_drilldown',context.query_plan.requirements[0]['parameters'])

    def test_transaction_is_required_and_postgres_protocol_requires_repeatable_read_only(self):
        from unittest.mock import MagicMock
        from psycopg.pq import TransactionStatus
        from data_analysis_agent.core.database import PostgresConnection
        args=self.arguments();scope=args.pop('scope');goal=freeze_p6_goal('具体体现在哪里？',scope=scope,**args).goal
        plan=build_p6_query_plan(goal)
        with closing(connect_review_database(self.fixture.db,readonly=True)) as connection:
            with self.assertRaises(review_preparation.P6PreparationError):
                review_preparation.prepare_p6_analysis(connection,goal,plan,**args)
        raw=MagicMock();raw.info.transaction_status=TransactionStatus.IDLE
        with patch('psycopg.connect',return_value=raw):
            connection=PostgresConnection('postgresql://offline/test',readonly=True)
        connection.execute('BEGIN')
        self.assertIn('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',[c.args[0] for c in raw.execute.call_args_list])
        raw.info.transaction_status=TransactionStatus.INTRANS
        for isolation,readonly in [('read committed','on'),('repeatable read','off')]:
            def execute(query,*params):
                cursor=MagicMock();cursor.fetchone.return_value=(isolation if query=='SHOW transaction_isolation' else readonly,);return cursor
            with patch.object(connection,'execute',side_effect=execute), \
                 patch.object(analytics,'get_top_issues',side_effect=AssertionError('invalid transaction')):
                with self.assertRaises(review_preparation.P6PreparationError) as caught:
                    review_preparation.prepare_p6_analysis(connection,goal,plan,**args)
                self.assertEqual(caught.exception.diagnostics['reason'],'invalid_transaction')
        connection.close()

    def test_truncation_is_explicit_and_candidate_ids_are_stable(self):
        records=[{'parent_asin':independent.A,'user_id':f'extra{i}','text':f'客服反馈{i}',
                  'timestamp':1676073600000} for i in range(25)]
        path=self.fixture.root/'many.jsonl';path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in records),encoding='utf-8')
        independent.import_amazon_jsonl(self.fixture.db,reviews_path=path,dataset_version='independent-state')
        for review in independent.pending_reviews(self.fixture.db,**self.fixture.identity,dataset_version='independent-state',limit=100):
            independent.save_annotation(self.fixture.db,review,**self.fixture.identity,status='success',
                opinions=independent.validate_opinions(json.dumps({'opinions':[dict(aspect='service',sentiment='negative',quote=review['text'])]}),review['text']))
        self.fixture.first=self.fixture.work(independent.A+'的差评主要集中在哪些方面？')
        task,values=self.capture()
        self.assertEqual(task['result']['p6_preparation']['status'],'ready')
        data=values[0].get_input('service')
        self.assertEqual(data['total'],30)
        self.assertEqual(data['coverage']['unique_expressions'],20)
        self.assertTrue(data['coverage']['truncated'])
        self.assertEqual(len({r['opinion_id'] for r in data['opinions']}),len(data['opinions']))
        self.assertEqual(data,values[0].get_input('service'))

    def test_frontend_cannot_supply_p6_state_or_enable_server_preparation(self):
        from data_analysis_agent.jobs.parameters import AnalysisParameters
        from data_analysis_agent.core.config import AgentSettings
        for key in ('ranking_task','ranking_basis','p6_preparation_enabled','prepared_analysis','required_aspects'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                AnalysisParameters.model_validate({**self.fixture.scope, 'question':'具体体现在哪里？', key:{}})
        config = {'LLM_MODEL_ID':'m','LLM_API_KEY':'offline','LLM_BASE_URL':'https://offline.invalid/v1'}
        self.assertFalse(AgentSettings.from_values(config).p6_preparation_enabled)
        self.assertTrue(AgentSettings.from_values({**config,'AGENT_P6_PREPARATION_ENABLED':'1'}).p6_preparation_enabled)


if __name__ == '__main__':
    unittest.main()
