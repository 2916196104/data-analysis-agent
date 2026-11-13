"""二级主题、去重分母、来源及部分失败；使用真实只读 SQL 和隔离评论。"""
import csv
from copy import deepcopy
from dataclasses import replace
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
            if schema is TopicCatalog:
                draft = {'themes': [{'n': row['name'], 'd': row['definition'], 'q': row['support_ids'][:2]} for row in draft['themes']]}
            if schema is TopicBatch:
                states = {'assigned': 'a', 'uncertain': 'u', 'conflicting': 'c', 'unclassified': 'n'}
                draft = {'rows': [{'i': row['opinion_id'], 's': states[row['state']],
                                  'm': [[match['key'], next(unit['quote'] for unit in payload['opinions'] if unit['opinion_id'] == row['opinion_id'])[match['start']:match['end']]] for match in row['matches']]}
                                 for row in draft['assignments']]}
                self.assertIn('rows', payload['schema']['properties'])
            if bad_counts[0]:
                draft['review_count'] = 999999
            return completion({'role': 'assistant', 'content': json.dumps(draft)})
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0); self.assertLessEqual(kwargs['timeout'], SETTINGS.timeout_seconds)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with patch('openai.OpenAI', side_effect=client):
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(len(requests), 2)
        self.assertTrue(validate_topic_result(data))
        self.assertEqual(data['coverage']['classified_opinions'], 4)
        self.assertEqual(data['coverage']['unclassified_reasons']['uncertain'], 1)
        self.assertEqual(data['coverage']['unclassified_reasons']['conflicting'], 1)
        bad_counts[0] = True
        with patch('openai.OpenAI', side_effect=client):
            rejected = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(rejected['analysis_status'], 'failed')
        self.assertFalse(rejected['topics'])
        self.assertEqual(rejected['failures'][0]['code'], 'invalid_schema')
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

    def test_siliconflow_v32_topic_requests_disable_extra_thinking_only_for_that_provider(self):
        from data_analysis_agent.agents.review_topics import _call, CLASSIFY_PROMPT
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return completion({'role': 'assistant', 'content': '{"rows":[{"i":"u1","s":"u","m":[]}]}'})
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        for host, model, expected in [('https://api.siliconflow.cn/v1', 'Pro/deepseek-ai/DeepSeek-V3.2', False),
                                      ('https://offline.invalid/v1', 'Pro/deepseek-ai/DeepSeek-V3.2', None),
                                      ('https://api.siliconflow.cn/v1', 'another-model', None)]:
            with patch('openai.OpenAI', side_effect=client):
                result = _call(replace(SETTINGS, base_url=host, model=model), CLASSIFY_PROMPT,
                               {'stage': 'topic_classification', 'themes': [], 'opinions': [{'opinion_id': 'u1', 'quote': '合成观点'}]}, TopicBatch, 10)
            self.assertEqual(requests[-1].get('enable_thinking'), expected)
            self.assertEqual(result.assignments[0].state, 'uncertain')

    def test_support_spans_are_located_by_code_and_ambiguous_or_fabricated_text_is_rejected(self):
        from data_analysis_agent.agents.review_topics import _call, CLASSIFY_PROMPT
        support = ['耐心解答']
        def handle(request):
            return completion({'role': 'assistant', 'content': json.dumps({'rows': [{'i': 'u1', 's': 'a', 'm': [['t1', support[0]]]}]})})
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        payload = {'opinions': [{'opinion_id': 'u1', 'quote': '😊耐心解答，服务好好'}]}
        with patch('openai.OpenAI', side_effect=client):
            result = _call(SETTINGS, CLASSIFY_PROMPT, payload, TopicBatch, 10)
            match = result.assignments[0].matches[0]
            self.assertEqual((match.start, match.end), (1, 5))
            for value in ('好', '不存在的片段', ''):
                support[0] = value
                with self.assertRaisesRegex(ValueError, '片段'):
                    _call(SETTINGS, CLASSIFY_PROMPT, payload, TopicBatch, 10)

    def test_missing_assignments_report_the_exact_validation_reason(self):
        def missing(settings, prompt, payload, schema, timeout):
            return semantic_response(settings, prompt, payload, schema, timeout) if schema is TopicCatalog else TopicBatch(assignments=[])
        with topic_model(missing):
            result = run_review_agent(self.db, '使用效果具体好在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['error']['code'], 'topic_assignment_coverage_mismatch')
        self.assertIn('归属结果遗漏、重复或编造观点', result['error']['message'])
        self.assertEqual(result['scope']['product_ids'], ['TOPIC-TEST'])
        self.assertNotIn('answer', result)

    def test_positive_classification_splits_twenty_expressions_without_dropping_reviews(self):
        extracted = self.extracted()
        seed = extracted['items'][0]
        extracted.update(aspect='service', total=20, total_opinions=20, items=[])
        extracted['summary'].update(review_count=20, eligible_text_reviews=20, successful_reviews=20)
        for index in range(20):
            text = f'客服耐心解答第{index}次咨询'
            extracted['items'].append({**deepcopy(seed), 'review_id': 1000 + index, 'text': text,
                'opinions': [dict(aspect='service', sentiment='positive', quote=text,
                                  quote_start=0, quote_end=len(text))]})
        batches = []
        def bounded_model(settings, prompt, payload, schema, timeout):
            if schema is TopicCatalog:
                return TopicCatalog(themes=[dict(key='t1', name='解答耐心', definition='客服耐心解答咨询',
                    support_ids=[payload['opinions'][0]['opinion_id']])])
            batches.append(len(payload['opinions']))
            self.assertTrue(all('support_ids' not in theme for theme in payload['themes']))
            if len(payload['opinions']) > 10:
                raise TimeoutError('模拟过大的复核响应超过单次期限')
            return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'], state='assigned',
                matches=[dict(key='t1', start=0, end=len(row['quote']))]) for row in payload['opinions']])
        with topic_model(bounded_model):
            data = analyze_aspect_topics(extracted, settings=SETTINGS)
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(batches, [10, 10])
        self.assertEqual(data['model_calls'], 3)
        self.assertEqual(data['coverage']['evaluated_opinions'], 20)
        self.assertEqual(data['topics'][0]['review_count'], 20)
        self.assertTrue(validate_topic_result(data))

    def test_classification_timeout_preserves_stage_deadline_and_safe_public_reason(self):
        def timeout(settings, prompt, payload, schema, deadline):
            if schema is TopicBatch:
                raise TimeoutError('secret-provider-body-must-not-be-published')
            return semantic_response(settings, prompt, payload, schema, deadline)
        with topic_model(timeout):
            result = run_review_agent(self.db, '使用效果具体好在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['code'], 'topic_timeout')
        self.assertIn('主题归属复核第 1 批请求超时', result['error']['message'])
        failure = result['results']['r1']['data']['failures'][0]
        self.assertEqual(failure['stage'], 'topic_classification')
        self.assertGreater(failure['timeout_seconds'], 0)
        self.assertLessEqual(failure['timeout_seconds'], 30)
        self.assertNotIn('secret-provider-body', json.dumps(result))
        self.assertNotIn('answer', result)

    def test_exhausted_total_budget_does_not_send_another_classification_request(self):
        clock = [0]
        def slow(settings, prompt, payload, schema, timeout):
            if schema is TopicCatalog:
                clock[0] = 10
                return semantic_response(settings, prompt, payload, schema, timeout)
            clock[0] = 70
            raise TimeoutError('offline slow response')
        with patch('data_analysis_agent.agents.review_topics.perf_counter', side_effect=lambda: clock[0]), \
                patch('data_analysis_agent.agents.review_topics.POSITIVE_BATCH_SIZE', 2), topic_model(slow) as model:
            data = analyze_aspect_topics(self.extracted(), settings=SETTINGS)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(data['model_calls'], 2)
        self.assertEqual(data['failures'][0]['code'], 'timeout')
        self.assertEqual(data['failures'][1]['code'], 'budget_exhausted')
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertFalse(data['topics'])

    def test_provider_rate_limit_is_reported_without_its_response_body(self):
        from openai import RateLimitError
        request = httpx.Request('POST', 'https://offline.invalid/v1/chat/completions')
        response = httpx.Response(429, request=request)
        error = RateLimitError('secret-provider-body', response=response, body={'secret': 'must-not-publish'})
        with topic_model(lambda *args: (_ for _ in ()).throw(error)):
            result = run_review_agent(self.db, '使用效果具体好在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['error']['code'], 'topic_http_error')
        self.assertIn('主题发现模型服务限流（HTTP 429）', result['error']['message'])
        self.assertNotIn('secret-provider-body', json.dumps(result))

    def test_explicit_full_quote_reference_binds_exact_source_and_rejects_other_ids(self):
        from data_analysis_agent.agents.review_topics import _call, CLASSIFY_PROMPT, TopicResponseError
        support = [{'quote_id': 'u1'}]
        quote = '😊客服耐心解答；客服耐心解答。'
        def handle(request):
            return completion({'role': 'assistant', 'content': json.dumps({'rows': [
                {'i': 'u1', 's': 'a', 'm': [['t1', support[0]]]}]})})
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        payload = {'opinions': [{'opinion_id': 'u1', 'quote': quote}]}
        with patch('openai.OpenAI', side_effect=client):
            result = _call(SETTINGS, CLASSIFY_PROMPT, payload, TopicBatch, 10)
            match = result.assignments[0].matches[0]
            self.assertEqual((match.start, match.end), (0, len(quote)))
            self.assertEqual(quote[match.start:match.end], quote)
            for value, code in [({'quote_id': 'u2'}, 'source_reference_mismatch'),
                                ('模型改写的原文', 'support_text_mismatch')]:
                support[0] = value
                with self.assertRaises(TopicResponseError) as raised:
                    _call(SETTINGS, CLASSIFY_PROMPT, payload, TopicBatch, 10)
                self.assertEqual(raised.exception.code, code)

    def test_four_supported_themes_keep_all_associations_and_original_denominator(self):
        extracted = self.extracted()
        seed = deepcopy(extracted['items'][0])
        clauses = ['客服回应及时', '解答清楚', '态度热情', '售后负责']
        text = '，'.join(clauses)
        extracted.update(aspect='service', total=1, total_opinions=1,
            items=[{**seed, 'text': text, 'opinions': [dict(aspect='service', sentiment='positive',
                    quote=text, quote_start=0, quote_end=len(text))]}])
        def response(settings, prompt, payload, schema, timeout):
            if schema is TopicCatalog:
                return TopicCatalog(themes=[dict(key=f't{index+1}', name=clause,
                    definition=clause, support_ids=['u1']) for index, clause in enumerate(clauses)])
            return TopicBatch(assignments=[dict(opinion_id='u1', state='assigned', matches=[
                dict(key=f't{index+1}', start=text.index(clause), end=text.index(clause)+len(clause))
                for index, clause in enumerate(clauses)])])
        with topic_model(response):
            data = analyze_aspect_topics(extracted, settings=SETTINGS)
        self.assertTrue(validate_topic_result(data))
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(len(data['assignments'][0]['matches']), 4)
        self.assertEqual(len(data['topics']), 4)
        self.assertTrue(all(row['review_count']==row['review_denominator']==1 for row in data['topics']))
        self.assertEqual(data['coverage']['selected_reviews'], 1)
        for replacement in ('t1', 't8'):
            invalid = deepcopy(data)
            invalid['assignments'][0]['matches'][-1]['key'] = replacement
            self.assertFalse(validate_topic_result(invalid))

    def test_negative_latency_budget_completes_agent_validation_and_public_answer(self):
        clock = [0.0]
        def slow(settings, prompt, payload, schema, timeout):
            delay = 8.4 if schema is TopicCatalog else 25
            clock[0] += min(delay, timeout)
            if delay > timeout:
                raise TimeoutError('fixed slow classification')
            return semantic_response(settings, prompt, payload, schema, timeout)
        with patch('data_analysis_agent.agents.review_topics.perf_counter', side_effect=lambda: clock[0]), topic_model(slow):
            result = run_review_agent(self.db, '物流差评具体体现在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['task_acceptance']['status'], 'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['public_answer']['status'], 'ok')
        self.assertEqual(result['results']['r1']['data']['coverage']['not_evaluated_opinions'], 0)

    def test_negative_timeout_cannot_publish_partial_classification_as_complete(self):
        clock = [0.0]
        def partial(settings, prompt, payload, schema, timeout):
            if schema is TopicBatch and payload['opinions'][0]['opinion_id'] == 'u1':
                clock[0] += timeout
                raise TimeoutError('fixed first batch timeout')
            return semantic_response(settings, prompt, payload, schema, timeout)
        with patch('data_analysis_agent.agents.review_topics.perf_counter', side_effect=lambda: clock[0]), \
                patch('data_analysis_agent.agents.review_topics.BATCH_SIZE', 1), topic_model(partial) as model:
            result = run_review_agent(self.db, '物流差评具体体现在哪里？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(model.call_count, 3)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['task_acceptance']['status'], 'failed')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertEqual(result['public_answer']['status'], 'partial')
        self.assertFalse(result['public_answer']['highlights'])
        self.assertEqual(result['results']['r1']['data']['failures'][0]['code'], 'timeout')

    def test_topic_associations_remain_bounded_by_the_eight_candidate_limit(self):
        from pydantic import ValidationError
        from data_analysis_agent.agents.review_topics import TopicAssignment, TopicWireAssignment
        matches = [dict(key=f't{index+1}', start=0, end=1) for index in range(8)]
        wire = [[match['key'], {'quote_id':'u1'}] for match in matches]
        self.assertEqual(len(TopicAssignment(opinion_id='u1', state='assigned', matches=matches).matches), 8)
        self.assertEqual(len(TopicWireAssignment.model_validate_json(json.dumps(dict(i='u1', s='a', m=wire))).m), 8)
        with self.assertRaises(ValidationError):
            TopicAssignment(opinion_id='u1', state='assigned', matches=matches+[matches[0]])
        with self.assertRaises(ValidationError):
            TopicWireAssignment.model_validate_json(json.dumps(dict(i='u1', s='a', m=wire+[wire[0]])))

    def test_wire_count_violation_reports_the_specific_limit_without_truncation(self):
        from data_analysis_agent.agents.review_topics import _call, CLASSIFY_PROMPT, TopicResponseError
        def handle(request):
            return completion({'role':'assistant', 'content':json.dumps({'rows':[
                {'i':'u1', 's':'a', 'm':[['t1', {'quote_id':'u1'}]]*9}]})})
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with patch('openai.OpenAI', side_effect=client):
            with self.assertRaises(TopicResponseError) as raised:
                _call(SETTINGS, CLASSIFY_PROMPT, {'opinions':[{'opinion_id':'u1', 'quote':'合成评论'}]}, TopicBatch, 10)
        self.assertEqual(raised.exception.code, 'too_many_theme_associations')
        self.assertIn('上限 8 个', str(raised.exception))
