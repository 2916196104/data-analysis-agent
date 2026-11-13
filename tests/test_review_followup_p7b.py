"""P7-B: synthetic CSV/SQLite, native Worker/context/tools; no PostgreSQL or paid calls."""
from contextlib import closing
from copy import deepcopy
import csv
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator, AnswerGenerationStrategy
from data_analysis_agent.agents.review_answer_facts import build_answer_facts
from data_analysis_agent.agents.review_compression import CompactAnalysisContext, build_analysis_artifacts
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_acceptance import validate_task_completion
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.analysis_state import restore_ranking
from data_analysis_agent.jobs.parameters import AnalysisParameters
from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, get_task, connect_task_database
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions

FIRST = '商品100013588055的差评主要集中在哪些方面？'
FOLLOWUP = '客服方面具体有什么问题？'


class FollowupP7BTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        texts = ['客服回复慢，推诿问题。', '客服回复慢。', '售后处理拖延。', '安装收费高。',
                 '客服态度很好。', '物流很慢。', '包装破损。', '客服回复慢，物流很慢。']
        rows = [dict(product_id='100013588055', user_id=f'p7b-{i}', text=text,
                     published_at=f'2022-0{i % 7 + 1}-01', product_name='合成客服测试商品')
                for i,text in enumerate(texts)]
        rows += [dict(product_id=p, user_id=f'control-{i}', text='安装不及时。',
                      published_at=d, product_name='合成范围对照') for i,(p,d) in enumerate([
                          ('100013588053','2022-01-01'), ('100013588055','2020-01-01')])]
        path = self.root / 'p7b.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', limit=1000):
            values = [opinion(aspect, direction, quote) for quote,aspect,direction in [
                ('客服回复慢','service','negative'), ('推诿问题','service','negative'),
                ('售后处理拖延','service','negative'), ('安装收费高','service','negative'),
                ('安装不及时','service','negative'), ('客服态度很好','service','positive'),
                ('物流很慢','logistics','negative'), ('包装破损','packaging','negative')]
                if quote in review['text']]
            save_annotation(self.db, review, **IDENTITY, status='success',
                opinions=validate_opinions(json.dumps({'opinions':values}), review['text']))
        self.scope = AnalysisParameters.model_validate({**IDENTITY, 'source':'reviews_csv', 'dataset_version':'reviews-csv',
            'product_ids':['100013588055'], 'date_from':'2021-09-11', 'date_to':'2022-08-26',
            'baseline_from':'2021-09-10', 'baseline_to':'2021-09-10'}).model_dump()
        self.tasks = self.root / 'p7b-tasks.sqlite3'; init_tasks(self.tasks, self.db)
        self.worker = TaskWorker(self.db, self.tasks, import_root=self.root, env_file=self.root/'unused.env')
        worker_started(self.tasks, self.worker.worker_id)
        self.agent_identity = dict(model=SETTINGS.model, base_url=SETTINGS.base_url, prompt_version='p7b-offline')
        self.enterContext(patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(SETTINGS,self.agent_identity)))
        self.model = self.enterContext(patch('openai.OpenAI', side_effect=AssertionError('P7-B: no paid LLM')))
        self.enterContext(patch('openai.AsyncOpenAI', side_effect=AssertionError('P7-B: no paid LLM')))
        self.enterContext(patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planning model')))
        self.enterContext(patch('data_analysis_agent.agents.review_retrieval.create_semantic_service', side_effect=AssertionError('no query embedding')))
        self.enterContext(patch('psycopg.connect', side_effect=AssertionError('no production database')))

    def work(self, question, parent=None, conversation='p7b-A', **changes):
        parameters = {**self.scope, **changes, 'question':question, 'conversation_id':conversation,
                      'model_identity':self.agent_identity}
        if parent: parameters['context_task_id'] = parent
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        task_id = enqueue(self.tasks, 'analysis', parameters)['task']['task_id']
        self.worker._execute(claim_task(self.tasks,self.worker.worker_id))
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
        return get_task(self.tasks,task_id)

    def anchor(self, question=FIRST):
        task = self.work(question)
        self.assertEqual(task['status'],'succeeded',task.get('error'))
        self.assertEqual(task['result']['task_acceptance']['status'],'passed')
        return task

    def context(self, parent, **changes):
        return load_analysis_context(self.tasks,{**self.scope,'conversation_id':'p7b-A',
            'context_task_id':parent['task_id'], **changes})

    def second(self, **changes):
        first = self.anchor(); second = self.work(FOLLOWUP,first['task_id'],**changes)
        self.assertEqual(second['status'],'succeeded',second.get('error'))
        result = second['result']
        self.assertEqual(result['status'],'ok',result.get('error'))
        self.assertEqual(result['user_intent']['query_type'],'opinion_breakdown')
        self.assertEqual(result['user_intent']['aspect'],'service')
        self.assertEqual(result['user_intent']['sentiment'],'negative')
        self.assertEqual(result['user_intent']['sub_intent'],'drill_down')
        self.assertEqual(result['query_plan']['required_tools'],['get_opinion_breakdown'])
        self.assertEqual(result['request_goal']['levels'],['opinion_content'])
        self.assertEqual(result['validation']['status'],'passed')
        self.assertEqual(result['task_acceptance']['status'],'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['total_model_calls'],0)
        self.assertEqual(result['public_answer']['generation_status'],'template')
        self.model.assert_not_called()
        return first,second

    def test_normal_followup_native_worker_inherits_trusted_scope_ranking_and_goal(self):
        first,second = self.second()
        result = second['result']; basis = result['analysis_state']['ranking_basis']
        self.assertEqual(basis['source']['task_id'],first['task_id'])
        self.assertEqual(basis['scope'],first['result']['scope'])
        self.assertIsNone(result['analysis_state']['pending_goal'])
        self.assertEqual(result['scope'],first['result']['scope'])
        self.assertEqual(result['results']['r1']['data']['total'],5)
        self.assertEqual(result['results']['r1']['data']['total_opinions'],6)
        self.assertIn('客服回复慢',result['public_answer']['summary'])
        self.assertNotIn('客服态度很好',result['public_answer']['summary'])

    def test_published_quotes_bind_annotation_review_source_and_exact_spans(self):
        _,second = self.second(); result = second['result']; data = result['results']['r1']['data']
        for ref in result['public_answer']['evidence_refs']:
            raw = next(row for row in data['items'] if row['review_id']==ref['review_id'])
            self.assertEqual(ref['evidence_ref'],raw['evidence_ref'])
            self.assertEqual(raw['source'],'reviews_csv'); self.assertEqual(raw['dataset_version'],'reviews-csv')
            for quote in ref['opinions']:
                self.assertEqual(quote['annotation_id'],raw['annotation_id'])
                self.assertEqual(raw['text'][quote['quote_start']:quote['quote_end']],quote['quote'])
                self.assertEqual(quote['sentiment'],'negative')

    def test_raw_groups_and_examples_are_not_presented_as_full_themes(self):
        _,task = self.second(); result = task['result']; data = result['results']['r1']['data']
        self.assertEqual(data['grouping'],'exact_opinion_text_and_sentiment')
        self.assertEqual(len(data['groups']),4)
        self.assertEqual(len(result['public_answer']['highlights']),3)
        self.assertNotIn('topics',data)
        self.assertIn('同义表述未合并',''.join(result['public_answer']['caveats']))
        self.assertEqual(result['task_acceptance']['classification_status'],'not_requested')

    def test_positive_history_does_not_become_negative_ranking(self):
        first = self.anchor(FIRST.replace('差评','好评'))
        context = self.context(first)
        self.assertIsNone(context['analysis_state']['ranking_basis'])
        intent = {}; _,plan,_=resolve_review_request('客服方面呢？',first['result']['scope'],
            [{'product_id':'100013588055','title':'合成客服测试商品','brand':None}],
            settings=SETTINGS,context=context,intent_state=intent,diagnostics={})
        self.assertEqual(intent['sentiment'],'positive')
        self.assertTrue(all(row['tool']=='get_positive_aspects' or row['parameters'].get('sentiment')=='positive'
                            for row in plan.requirements))
        self.model.assert_not_called()

    def test_explicit_negative_after_positive_history_is_isolated(self):
        first=self.anchor(FIRST.replace('差评','好评'))
        task=self.work('客服方面具体有什么差评？',first['task_id'])
        self.assertEqual(task['status'],'succeeded',task.get('error'))
        self.assertEqual(task['result']['sentiment_intent'],'negative')
        self.assertNotIn('客服态度很好',task['result']['public_answer']['summary'])

    def test_missing_trusted_ranking_is_not_fabricated_from_old_numbers(self):
        first=self.anchor()
        with closing(connect_task_database(self.tasks)) as connection,connection:
            connection.execute('UPDATE tasks SET progress_json=? WHERE task_id=?',('{}',first['task_id']))
        context=self.context(first)
        self.assertIsNone(context['analysis_state']['ranking_basis'])
        self.assertIsNone(restore_ranking(get_task(self.tasks,first['task_id'])))

    def test_tampered_history_is_not_restored(self):
        first=self.anchor(); changed=deepcopy(first['result']);changed['results']['r1']['data']['issues'][0]['negative_review_count']=999
        with closing(connect_task_database(self.tasks)) as connection,connection:
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?',(json.dumps(changed),first['task_id']))
        self.assertIsNone(self.context(first))

    def test_cross_conversation_history_is_not_restored(self):
        first=self.anchor();self.assertIsNone(self.context(first,conversation_id='p7b-B'))

    def test_cross_source_history_is_not_restored_and_worker_refuses_it(self):
        first=self.anchor();self.assertIsNone(self.context(first,source='amazon_reviews_2023'))
        task=self.work(FOLLOWUP,first['task_id'],source='amazon_reviews_2023')
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['error']['code'],'business_error')

    def test_explicit_product_switch_clears_old_ranking(self):
        first=self.anchor(); selection={key:value for key,value in self.scope.items() if key in first['result']['scope']}
        selection['product_ids']=['100013588053']
        task=self.work('商品100013588053的客服负面观点内容是什么？',first['task_id'],
                       product_ids=['100013588053'],selection_scope=selection)
        self.assertEqual(task['status'],'succeeded',task.get('error'))
        result=task['result'];self.assertEqual(result['scope']['product_ids'],['100013588053'])
        self.assertIsNone(result['analysis_state']['ranking_basis'])
        self.assertIn('安装不及时',result['public_answer']['summary'])
        self.assertNotIn('客服回复慢',result['public_answer']['summary'])

    def test_explicit_date_switch_clears_old_ranking(self):
        first=self.anchor()
        task=self.work('2022-03-01到2022-08-26 客服方面具体有什么问题？',first['task_id'])
        self.assertEqual(task['status'],'succeeded',task.get('error'))
        self.assertEqual(task['result']['scope']['date_from'],'2022-03-01')
        self.assertIsNone(task['result']['analysis_state']['ranking_basis'])

    def test_mismatched_quote_and_wrong_product_or_date_fail_native_validation(self):
        _,task=self.second(); result=task['result'];plan=build_query_plan('执行本轮已确认的只读查询\n'+json.dumps({
            'tool':'get_opinion_breakdown','parameters':{'aspect':'service','sentiment':'negative'}}),result['scope'])
        mutations=[lambda row:row['opinions'][0].update(quote='编造原文'),
                   lambda row:row.update(product_id='100013588053'),
                   lambda row:row.update(published_at='2020-01-01T00:00:00+00:00')]
        for mutate in mutations:
            changed=deepcopy(result['results']);mutate(changed['r1']['data']['items'][0])
            self.assertEqual(validate_report(changed,result['result_ids'],expected_scope=result['scope'],query_plan=plan)['status'],'failed')

    def test_pure_opinions_with_detailed_request_do_not_attempt_model(self):
        _,task=self.second(); context=deepcopy(task['result']['compact_context'])
        context['question'] += '请详细分析。'
        answer,diagnostics=NaturalLanguageAnswerGenerator().generate(context['question'],context,settings=SETTINGS,validated=True)
        self.assertEqual(answer['generation_status'],'template')
        self.assertEqual(diagnostics['attempts'],0)
        self.model.assert_not_called()

    def test_complete_theme_goal_cannot_be_satisfied_by_opinion_groups(self):
        _,task=self.second(); result=task['result']; goal=deepcopy(result['task_goal']);goal['levels']=['themes']
        plan=build_query_plan('执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_opinion_breakdown',
            'parameters':{'aspect':'service','sentiment':'negative'}}),result['scope'])
        acceptance=validate_task_completion(goal,result['scope'],plan,[result['results']['r1']])
        self.assertEqual(acceptance['status'],'failed');self.assertFalse(acceptance['complete'])

    def topic_context(self, aspect, sentiment):
        from test_review_topics import ReviewTopicTests, semantic_response
        from topic_fixture import topic_model
        from data_analysis_agent.agents.review_topics import analyze_aspect_topics, validate_topic_result
        fixture=ReviewTopicTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with topic_model(semantic_response):
            data=analyze_aspect_topics(fixture.extracted(aspect,sentiment),settings=SETTINGS)
        self.assertTrue(validate_topic_result(data))
        question='执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_aspect_drilldown',
            'parameters':{'aspect':aspect,'sentiment':sentiment}})
        scope=fixture.scope();plan=build_query_plan(question,scope)
        item=dict(result_id='r1',kind='aspect_drilldown',parameters=plan.requirements[0]['parameters'],data=data)
        checked=validate_report({'r1':item},['r1'],expected_scope=scope,query_plan=plan)
        self.assertEqual(checked['status'],'passed')
        compact=build_analysis_artifacts(dict(status='ok',question=question,scope=scope,
            validation=checked,results={'r1':item},result_ids=['r1']),plan)['compact_context']
        self.topic_item=item
        return CompactAnalysisContext.model_validate(compact)

    def test_single_verified_topic_keeps_existing_template_policy(self):
        context=self.topic_context('logistics','negative');facts,_=build_answer_facts(context)
        self.assertEqual(sum(f.kind=='topic' for f in facts),1)
        self.assertEqual(AnswerGenerationStrategy.choose(context,facts),'template')
        answer,diagnostics=NaturalLanguageAnswerGenerator().generate(context.question,context.model_dump(),
            settings=SETTINGS,validated=True)
        self.assertEqual(answer['generation_status'],'template');self.assertEqual(diagnostics['attempts'],0)
        self.model.assert_not_called()

    def test_multi_topic_model_strategy_is_detected_before_prohibited_execution(self):
        context=self.topic_context('effectiveness','positive');facts,_=build_answer_facts(context)
        self.assertEqual(sum(f.kind=='topic' for f in facts),2)
        self.assertEqual(AnswerGenerationStrategy.choose(context,facts),'llm')
        # This is a preflight refusal: no generator invocation or provider request.
        self.model.assert_not_called()

    def test_incomplete_theme_state_cannot_publish_even_with_validated_numbers(self):
        context=self.topic_context('effectiveness','positive')
        for status in ('partial','failed'):
            raw=context.model_dump();raw['statistics'][0]['filters']['analysis_status']=status
            answer,diagnostics=NaturalLanguageAnswerGenerator().generate(context.question,raw,settings=SETTINGS,validated=True)
            self.assertNotEqual(answer['status'],'ok');self.assertFalse(answer['highlights'])
            self.assertEqual(diagnostics['attempts'],0)
        self.model.assert_not_called()

    def test_inconsistent_insufficient_theme_data_is_rejected_before_answer(self):
        from data_analysis_agent.agents.review_topics import validate_topic_result
        self.topic_context('effectiveness','positive')
        broken=deepcopy(self.topic_item['data']);broken['analysis_status']='insufficient_data'
        self.assertFalse(validate_topic_result(broken))

    def test_no_matching_theme_data_keeps_controlled_insufficient_evidence(self):
        from test_review_topics import ReviewTopicTests, semantic_response
        from topic_fixture import topic_model
        fixture=ReviewTopicTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with topic_model(semantic_response) as classifier:
            result=run_review_agent(fixture.db,'包装差评具体体现在哪里？',scope=fixture.scope(),settings=SETTINGS)
        classifier.assert_not_called()
        self.assertEqual(result['status'],'unsupported')
        self.assertEqual(result['task_acceptance']['status'],'insufficient_evidence')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertFalse(result['public_answer']['highlights'])
        self.assertEqual(result['total_model_calls'],0)

    def test_explicit_theme_goal_is_not_downgraded_by_content_fix(self):
        from data_analysis_agent.agents.review_acceptance import build_request_goal
        from data_analysis_agent.agents.review_semantics import opinion_request
        for question in ('客服差评具体体现在哪里？','客服差评归纳成哪些子主题？'):
            self.assertEqual(build_request_goal(question).levels,('themes',))
            self.assertTrue(opinion_request(question)['theme_summary'])


if __name__=='__main__':
    unittest.main()
