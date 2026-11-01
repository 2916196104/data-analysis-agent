"""二级主题、去重分母、来源及部分失败；使用真实只读 SQL 和隔离评论。"""
import csv
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch
import unittest

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
import test_review_intent as intent_fixture
import test_assistant_semantics as semantics_fixture
from topic_fixture import topic_model
from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch, analyze_aspect_topics, validate_topic_result
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_compression import read_public_analysis
from data_analysis_agent.reviews.analytics import get_aspect_opinions
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


def semantic_response(settings, prompt, payload, schema, timeout):
    if schema is TopicCatalog:
        topics = []
        for key, name, quotes in [('t1', '制冷速度', {'降温很快', '制冷速度快'}), ('t2', '运行噪声', {'运行声音小'}),
                                   ('t3', '配送时效', {'物流速度慢', '物流发货慢'})]:
            supports = [row['opinion_id'] for row in payload['opinions'] if row['quote'] in quotes]
            if supports:
                topics.append(dict(key=key, name=name, definition=name + '的直接使用评价', support_ids=supports[:6]))
        return TopicCatalog(themes=topics)
    states = {'好像还行': 'uncertain', '时好时坏': 'conflicting'}
    mapping = {'降温很快': 't1', '制冷速度快': 't1', '运行声音小': 't2', '物流速度慢': 't3', '物流发货慢': 't3'}
    keys = {row['key'] for row in payload['themes']}
    return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'],
        state=states.get(row['quote'], 'assigned' if mapping.get(row['quote']) in keys else 'unclassified'),
        matches=[dict(key=mapping[row['quote']], start=0, end=len(row['quote']))] if mapping.get(row['quote']) in keys else [])
        for row in payload['opinions']])


class ReviewTopicTests(semantics_fixture.AssistantSemanticsFixture, unittest.TestCase):
    queue = intent_fixture.ReviewIntentTests.queue
    enqueue = intent_fixture.ReviewIntentTests.enqueue
    save = intent_fixture.ReviewIntentTests.save
    work = intent_fixture.ReviewIntentTests.work
    def setUp(self):
        super().setUp()
        path = self.root / 'topics.csv'
        rows = [dict(product_id='TOPIC-TEST', product_name='主题测试空调', brand='测试', text=text,
                     published_at=day, user_id=str(index)) for index, (text, day) in enumerate([
            ('降温很快，制冷速度快，运行声音小', '2021-06-09'), ('制冷速度快', '2021-06-08'),
            ('物流速度慢', '2021-06-07'), ('物流发货慢', '2021-06-06'), ('物流送货很快', '2021-06-05'),
            ('好像还行', '2021-06-04'), ('时好时坏', '2021-06-03')])]
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **self.identity, source='reviews_csv', product_ids=['TOPIC-TEST'], limit=100):
            values = [dict(aspect='logistics' if '物流' in quote else 'effectiveness',
                sentiment='negative' if quote in ('物流速度慢', '物流发货慢') else 'positive', quote=quote)
                for quote in review['text'].split('，')]
            save_annotation(self.db, review, **self.identity, status='success', opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))

    def scope(self, **changes):
        return super().scope(**{'product_ids': ['TOPIC-TEST'], **changes})

    def extracted(self, aspect='effectiveness', sentiment='positive'):
        scope = {key: value for key, value in self.scope().items() if not key.startswith('baseline_')}
        return get_aspect_opinions(self.db, **scope, aspect=aspect, sentiment=sentiment)

    def test_synonyms_dedup_multiple_topics_uncertainty_and_no_writes(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with topic_model(semantic_response):
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertTrue(validate_topic_result(data))
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(data['population_mode'], 'full')
        topics = {row['name']: row for row in data['topics']}
        self.assertEqual(set(topics), {'制冷速度', '运行噪声'})
        self.assertEqual((topics['制冷速度']['review_count'], topics['制冷速度']['opinion_count']), (2, 3))
        self.assertEqual((topics['制冷速度']['review_denominator'], topics['制冷速度']['review_rate']), (4, .5))
        self.assertEqual(topics['运行噪声']['review_count'], 1)
        self.assertTrue(set(topics['运行噪声']['review_ids']) <= set(topics['制冷速度']['review_ids']))
        self.assertEqual(data['coverage']['classified_opinions'], 4)
        self.assertEqual(data['coverage']['unclassified_reasons'], {'uncertain': 1, 'conflicting': 1, 'unclassified': 0})
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        with topic_model(semantic_response):
            repeated = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual([row['topic_id'] for row in data['topics']], [row['topic_id'] for row in repeated['topics']])
        for mutate in (lambda d: d['topics'][0].update(review_count=999),
                       lambda d: d['opinions'][0].update(quote='编造原文'),
                       lambda d: d['assignments'][0]['matches'][0].update(key='invented'),
                       lambda d: d['catalog'][0].update(support_ids=['invented'])):
            corrupt = deepcopy(data); mutate(corrupt); self.assertFalse(validate_topic_result(corrupt))

    def test_negative_logistics_never_contains_positive_or_other_aspects(self):
        with topic_model(semantic_response):
            result = run_review_agent(self.db, '物流方面主要有哪些问题？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['analysis_intent'], 'aspect_drilldown')
        data = result['results']['r1']['data']
        self.assertEqual((data['total'], data['total_opinions']), (2, 2))
        self.assertEqual([row['name'] for row in data['topics']], ['配送时效'])
        self.assertEqual(data['topics'][0]['review_rate'], 1.0)
        self.assertTrue(all(row['aspect'] == 'logistics' and row['sentiment'] == 'negative' for row in data['opinions']))
        self.assertEqual(result['validation']['status'], 'passed')

    def test_batches_partial_failure_retains_verified_facts_and_failed_discovery_publishes_none(self):
        def partial(settings, prompt, payload, schema, timeout):
            if schema is TopicBatch and payload['opinions'][0]['opinion_id'] == 'u3':
                raise TimeoutError('fixture timeout')
            return semantic_response(settings, prompt, payload, schema, timeout)
        with patch('data_analysis_agent.agents.review_topics.BATCH_SIZE', 2), topic_model(partial):
            result = run_review_agent(self.db, '使用效果的好评主要集中在哪些地方？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'partial', result.get('error'))
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['public_answer']['status'], 'partial')
        self.assertGreater(result['results']['r1']['data']['coverage']['not_evaluated_opinions'], 0)
        self.assertTrue(result['results']['r1']['data']['topics'])
        with topic_model(lambda *args: (_ for _ in ()).throw(TimeoutError('fixture'))):
            failed = run_review_agent(self.db, '使用效果的好评主要集中在哪些地方？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(failed['status'], 'error')
        self.assertNotIn('answer', failed)
        self.assertFalse(failed['results']['r1']['data']['topics'])
        self.assertEqual(failed['results']['r1']['data']['analysis_status'], 'failed')

    def test_sampling_empty_and_hallucinated_membership(self):
        with patch('data_analysis_agent.agents.review_topics.MAX_OPINIONS', 2), topic_model(semantic_response):
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(data['population_mode'], 'sample')
        self.assertEqual(data['coverage']['selected_reviews'], 1)
        self.assertTrue(validate_topic_result(data))
        with topic_model() as model:
            empty = analyze_aspect_topics(self.extracted('packaging'), settings=SETTINGS)
        model.assert_not_called()
        self.assertEqual(empty['analysis_status'], 'insufficient_data')
        def invented(settings, prompt, payload, schema, timeout):
            if schema is TopicCatalog:
                return TopicCatalog(themes=[dict(key='t1', name='虚构主题', definition='虚构主题', support_ids=['not-present'])])
            raise AssertionError('must not classify fabricated catalog')
        with topic_model(invented):
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertEqual(data['topics'], [])

    def test_actual_sdk_schema_boundary_rejects_model_counts_and_bounded_requests(self):
        requests = []
        bad_counts = [False]
        def handle(request):
            body = json.loads(request.content); payload = json.loads(body['messages'][-1]['content']); requests.append(body)
            schema = TopicCatalog if payload['stage'] == 'topic_discovery' else TopicBatch
            draft = semantic_response(SETTINGS, '', payload, schema, 10).model_dump()
            if bad_counts[0]:
                draft['review_count'] = 999999
            return completion({'role': 'assistant', 'content': json.dumps(draft)})
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0); self.assertLessEqual(kwargs['timeout'], 20)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with patch('openai.OpenAI', side_effect=client):
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(len(requests), 2)
        self.assertTrue(validate_topic_result(data))
        bad_counts[0] = True
        with patch('openai.OpenAI', side_effect=client):
            rejected = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(rejected['analysis_status'], 'failed')
        self.assertFalse(rejected['topics'])
        for body in requests:
            self.assertNotIn('tools', body)
            self.assertLessEqual(len(body['messages'][-1]['content'].encode()), 16000)

    def test_worker_followup_reaches_positive_topics_and_partial_is_not_trusted_context(self):
        self.queue()
        first = self.run_sync('这款商品好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        with topic_model(semantic_response):
            task = self.work('使用效果具体好在哪里？', parent=parent)
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        self.assertEqual(task['result']['analysis_intent'], 'aspect_drilldown')
        self.assertEqual(task['result']['sentiment_intent'], 'positive')
        self.assertEqual(task['result']['validation']['status'], 'passed')
        with topic_model(semantic_response):
            standalone = run_review_agent(self.db, '使用效果具体好在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(standalone['analysis_intent'], 'aspect_drilldown')
        self.assertEqual(standalone['status'], 'ok')
        def partial(settings, prompt, payload, schema, timeout):
            if schema is TopicBatch and payload['opinions'][0]['opinion_id'] == 'u3':
                raise TimeoutError('fixture')
            return semantic_response(settings, prompt, payload, schema, timeout)
        with patch('data_analysis_agent.agents.review_topics.BATCH_SIZE', 2), topic_model(partial):
            incomplete = self.work('使用效果具体好在哪里？', parent=task['task_id'])
        self.assertEqual(incomplete['status'], 'partial')
        self.assertEqual(incomplete['result']['validation']['status'], 'passed')
        public = read_public_analysis(incomplete)
        self.assertEqual(public['status'], 'partial')
        self.assertEqual(public['statistics'], [])
        context = intent_fixture.load_analysis_context(self.tasks, {**self.scope(), 'context_task_id': incomplete['task_id'], 'conversation_id': 'conversation-A'})
        self.assertEqual(context['validated']['task_id'], task['task_id'])
