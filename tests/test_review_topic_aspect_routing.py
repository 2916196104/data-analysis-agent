"""Whole-positive aspect constraints and bounded recovery; synthetic input, no DB or network."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
from data_analysis_agent.agents import review_topics as topics
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import opinion_request
from data_analysis_agent.agents.review_acceptance import build_task_goal, validate_goal_plan
from data_analysis_agent.reviews.opinions import ASPECTS


def source(count=12):
    items = []
    for index in range(count):
        aspect = tuple(ASPECTS)[index % len(ASPECTS)]
        quote = ('商品质量和物流送货没问题' if index in (0, 4) else
                 '安装满意' if index == 1 else f'合成体验{index}')
        items.append(dict(review_id=index + 1, annotation_id=index + 101, revision_id=None,
            product_id='ASPECT-SYNTHETIC', text=quote, opinions=[dict(aspect=aspect, sentiment='positive',
                quote=quote, quote_start=0, quote_end=len(quote))]))
    return dict(scope={'product_ids': ['ASPECT-SYNTHETIC']}, aspect=None, sentiment='positive',
        summary={'successful_reviews': count, 'aspects': dict.fromkeys(ASPECTS, {})},
        total=count, total_opinions=count, items=items)


def valid_response(payload, schema):
    if schema is topics.TopicCatalog:
        themes = []
        for aspect in ASPECTS:
            support = [r['opinion_id'] for r in payload['opinions'] if r['aspect'] == aspect]
            # The synthetic effectiveness input includes a questionable installation label.
            if support and aspect != 'effectiveness':
                themes.append(dict(key=f't{len(themes) + 1}', name=ASPECTS[aspect] + '体验',
                    definition=ASPECTS[aspect] + '的直接评价', aspect=aspect, support_ids=support[:2]))
        return topics.TopicCatalog(themes=themes)
    rows = []
    for row in payload['opinions']:
        keys = row['allowed_theme_keys']
        rows.append(dict(opinion_id=row['opinion_id'], state='assigned' if keys else 'unclassified',
            matches=[dict(key=keys[0], start=0, end=len(row['quote']))] if keys else []))
    return topics.TopicBatch(assignments=rows)


def cross_assignment(payload):
    response = valid_response(payload, topics.TopicBatch)
    row = payload['opinions'][0]
    foreign = next(t for t in payload['themes'] if t['aspect'] != row['aspect'])
    response.assignments[0] = topics.TopicAssignment(opinion_id=row['opinion_id'], state='assigned',
        matches=[topics.TopicMatch(key=foreign['key'], start=0, end=len(row['quote']))])
    return response


def cross_support(payload):
    ids = [next(r['opinion_id'] for r in payload['opinions'] if r['aspect'] == aspect)
           for aspect in ('quality', 'logistics')]
    return topics.TopicCatalog(themes=[dict(key='t1', name='综合满意', definition='合成测试跨维度依据', support_ids=ids)])


class TopicAspectRoutingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('offline only')))

    def analyze(self, responder=valid_response, *, delays=()):
        original, clock, calls = source(), [0.0], []
        before = deepcopy(original)
        def respond(settings, prompt, payload, schema, timeout):
            index = len(calls)
            calls.append(dict(prompt=prompt, payload=deepcopy(payload), schema=schema, timeout=timeout))
            delay = delays[index] if index < len(delays) else 0
            clock[0] += min(delay, timeout)
            if delay > timeout:
                raise TimeoutError('synthetic timeout')
            return responder(payload, schema)
        with patch.object(topics, '_call', side_effect=respond), \
                patch.object(topics, 'perf_counter', side_effect=lambda: clock[0]):
            data = topics.analyze_aspect_topics(original, settings=SETTINGS)
        self.assertEqual(original, before)
        self.assertTrue(topics.validate_topic_result(data))
        return data, calls

    def test_each_opinion_has_only_same_aspect_candidates_and_keeps_original_labels(self):
        data, calls = self.analyze()
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(data['model_calls'], 3)
        for call in calls[1:]:
            payload = call['payload']
            self.assertTrue(all('support_ids' not in t for t in payload['themes']))
            for row in payload['opinions']:
                self.assertEqual(row['allowed_theme_keys'],
                    [t['key'] for t in payload['themes'] if t['aspect'] == row['aspect']])
        installation = next(r for r in data['opinions'] if r['quote'] == '安装满意')
        self.assertEqual(installation['aspect'], 'effectiveness')
        assignment = next(r for r in data['assignments'] if r['opinion_id'] == installation['unit_id'])
        self.assertEqual(assignment['state'], 'unclassified')
        duplicate = [r for r in data['opinions'] if r['quote'] == '商品质量和物流送货没问题']
        self.assertEqual(len({r['unit_id'] for r in duplicate}), 2)
        self.assertEqual(data['coverage']['not_evaluated_opinions'], 0)

    def test_failed_second_batch_is_corrected_without_repeating_successful_first_batch(self):
        def respond(payload, schema):
            if schema is topics.TopicBatch and len(payload['opinions']) == 2 and 'correction' not in payload:
                return cross_assignment(payload)
            return valid_response(payload, schema)
        data, calls = self.analyze(respond)
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(data['model_calls'], 4)
        self.assertEqual([len(c['payload']['opinions']) for c in calls[1:]], [10, 2, 2])
        self.assertEqual(calls[-1]['payload']['correction']['code'], 'cross_aspect_assignment')
        self.assertEqual(data['coverage']['evaluated_opinions'], 12)
        self.assertEqual(len({r['opinion_id'] for r in data['assignments']}), 12)

    def test_repeated_cross_assignment_stays_failed_after_one_correction(self):
        data, calls = self.analyze(lambda p, s: valid_response(p, s) if s is topics.TopicCatalog else cross_assignment(p))
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertEqual(len(calls), 4)  # discovery, first batch twice, second batch once
        self.assertFalse(data['assignments'])
        self.assertTrue(all(f['code'] == 'cross_aspect_assignment' for f in data['failures']))

    def test_cross_support_can_be_corrected_without_combining_equal_quotes(self):
        def respond(payload, schema):
            if schema is topics.TopicCatalog and 'correction' not in payload:
                return cross_support(payload)
            return valid_response(payload, schema)
        data, calls = self.analyze(respond)
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1]['payload']['correction']['code'], 'cross_aspect_support')

    def test_discovery_and_classification_share_one_correction_opportunity(self):
        def respond(payload, schema):
            if schema is topics.TopicCatalog:
                return valid_response(payload, schema) if 'correction' in payload else cross_support(payload)
            return cross_assignment(payload)
        data, calls = self.analyze(respond)
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertEqual(sum('correction' in c['payload'] for c in calls), 1)
        self.assertEqual(len(calls), 4)

    def test_correction_respects_original_deadline_and_does_not_start_after_exhaustion(self):
        def respond(payload, schema):
            return valid_response(payload, schema) if schema is topics.TopicCatalog else cross_assignment(payload)
        data, calls = self.analyze(respond, delays=(30, 28, 5))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]['timeout'], 2)
        self.assertEqual(data['duration_ms'], 60000)
        self.assertEqual([f['code'] for f in data['failures']], ['timeout', 'budget_exhausted'])
        self.assertEqual(data['analysis_status'], 'failed')

    def test_other_invalid_results_are_rejected_without_correction(self):
        def respond(payload, schema):
            response = valid_response(payload, schema)
            if schema is topics.TopicBatch:
                response.assignments[0].opinion_id = 'invented'
            return response
        data, calls = self.analyze(respond)
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(f['code'] == 'assignment_coverage_mismatch' for f in data['failures']))
        self.assertFalse(any('correction' in c['payload'] for c in calls))

    def test_wire_request_contains_aspect_constraints_and_whitelist(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return completion({'role': 'assistant', 'content': json.dumps({'rows': [dict(i='u1', s='n', m=[])]})})
        def client(**options):
            return OpenAI(**options, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        payload = dict(stage='topic_classification', themes=[], opinions=[dict(opinion_id='u1',
            aspect='effectiveness', quote='安装满意', allowed_theme_keys=[])])
        with patch('openai.OpenAI', side_effect=client):
            response = topics._call(SETTINGS, topics.CLASSIFY_PROMPT, payload, topics.TopicBatch, 30)
        self.assertEqual(response.assignments[0].state, 'unclassified')
        self.assertIn('allowed_theme_keys', requests[0]['messages'][0]['content'])
        self.assertEqual(json.loads(requests[0]['messages'][1]['content'])['opinions'], payload['opinions'])


class PositiveEnumerationRoutingTests(unittest.TestCase):
    def test_dimension_enumeration_routes_to_content_while_metrics_and_quotes_keep_their_tools(self):
        scope = dict(product_ids=['100013588053'], source='reviews_csv', dataset_version='aspect-routing-fixture',
            model=SETTINGS.model, base_url=SETTINGS.base_url, prompt_version='aspect-routing-fixture',
            date_from='2021-01-01', date_to='2021-12-31')
        for question in ('美的空调的物流的好评有哪些？', '物流方面有哪些好评？', '客服的好评主要有哪些？'):
            with self.subTest(question=question):
                self.assertTrue(opinion_request(question)['positive_summary'])
                plan = build_query_plan(question, scope)
                self.assertEqual(plan.required_tools, ['get_aspect_drilldown'])
                self.assertEqual(plan.requirements[0]['parameters']['sentiment'], 'positive')
                self.assertIsNotNone(plan.requirements[0]['parameters']['aspect'])
                goal = build_task_goal(question, scope, plan, {})
                self.assertEqual(goal['levels'], ['themes'])
                self.assertTrue(validate_goal_plan(goal, plan)['passed'])
                counts = build_query_plan('物流好评有多少条？', scope)
                self.assertFalse(validate_goal_plan(goal, counts)['passed'])
        for question, tool in (
                ('物流好评有多少条？', 'get_positive_aspects'),
                ('物流好评占比是多少？', 'get_positive_aspects'),
                ('好评有哪些维度？', 'get_positive_aspects'),
                ('物流方面的好评主要是什么？', 'get_positive_aspects'),
                ('物流的好评有哪些原文？', 'get_evidence'),
                ('给我3条物流好评', 'get_evidence')):
            with self.subTest(question=question):
                self.assertFalse(opinion_request(question)['positive_summary'])
                self.assertEqual(build_query_plan(question, scope).required_tools, [tool])

    def test_full_agent_publishes_topic_content_and_preserves_database(self):
        import hashlib
        import test_review_dimension_content as dimensions
        fixture = dimensions.DimensionContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        before = hashlib.sha256(fixture.db.read_bytes()).hexdigest()
        for question in ('DIM-A的物流的好评有哪些？', 'DIM-A的好评体现在哪里？'):
            with self.subTest(question=question):
                result = fixture.run_local(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(result['query_plan']['required_tools'], ['get_aspect_drilldown'])
                self.assertTrue(result['task_acceptance']['complete'])
                self.assertEqual(result['public_answer']['status'], 'ok')
                self.assertIn('送货准时', result['public_answer']['summary'])
        self.assertEqual(hashlib.sha256(fixture.db.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
