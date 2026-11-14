"""P0: frozen synthetic 27/10/3 case; test published answer, not only topics.

No production database or provider. Numbers reproduce the reported contract case;
product IDs, evidence and definitions below are synthetic test fixtures.
"""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
from data_analysis_agent.agents.review_answers import AnswerAssembler, NaturalLanguageAnswerGenerator, read_public_answer, template_draft
from data_analysis_agent.agents.review_answer_facts import build_answer_facts
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_compression import public_from_context
from data_analysis_agent.agents.review_insights import (
    InsightDraft, aliases, fact_catalog, parse_draft, support_payload, validate_draft,
)
from data_analysis_agent.agents.review_topics import topic_id
from data_analysis_agent.agents import review_topics as topics_module
from data_analysis_agent.agents.review_results import adapt_result
from data_analysis_agent.agents.review_compression import AnalysisResultCompressor
from data_analysis_agent.agents.review_plan import ReviewQueryPlan
from data_analysis_agent.core.serialization import canonical_json


def topic_context(counts=(27, 10, 3), *, aspect='service', result_id='r1', scope_ref='scope:p0'):
    specs = [('安装服务好', '安装师傅服务专业、细致', '安装师傅认真细致'),
             ('安装速度快', '安装师傅上门及安装及时', '安装及时'),
             ('客服响应快', '客服回复迅速、沟通耐心', '客服回复快且耐心')]
    scopes = [dict(scope_ref=scope_ref, product_ids=['P0-SYNTHETIC'], source='reviews_csv',
                   dataset_version='p0-synthetic', date_from='2021-01-01', date_to='2021-12-31', timezone='UTC')]
    topics, evidence = [], []
    for i, count in enumerate(counts):
        name, definition, quote = specs[i] if i < len(specs) else (f'候选主题{i}', f'具体体验侧面{i}', f'测试观点{i}')
        rid = 100 + i
        ref = f'sha256:synthetic#line={i + 1}'
        topics.append(dict(topic_id=topic_id(name, aspect, 'positive'), name=name, aspect=aspect, sentiment='positive',
                           definition=definition, review_count=count, opinion_count=count, review_denominator=37,
                           review_rate=round(count / 37, 6), review_id=rid, quote=quote, evidence_ref=ref))
        evidence.append(dict(source_result_id=result_id, review_id=rid, product_id='P0-SYNTHETIC',
                             published_at='2021-06-01T00:00:00+00:00', evidence_ref=ref, opinion_source='model',
                             opinions=[dict(aspect=aspect, sentiment='positive', quote=quote,
                                            quote_start=0, quote_end=len(quote))]))
    topics.sort(key=lambda row: (-row['review_count'], row['topic_id']))
    filters = dict(aspect=aspect, sentiment='positive', analysis_status='complete', population_mode='sample',
                   total=229, total_opinions=251, topics=topics,
                   coverage=dict(selected_reviews=37, selected_opinions=37, evaluated_opinions=37,
                                 classified_opinions=37, unclassified_opinions=0, not_evaluated_opinions=0))
    return CompactAnalysisContext(question='客服的好评具体体现在哪些方面？', analysis_type='aspect_drilldown',
                                  sentiment='positive', scope_summary=scopes, sample_summary=[], key_findings=[],
                                  statistics=[dict(source_result_id=result_id, analysis_type='aspect_drilldown',
                                                   scope_ref=scope_ref, filters=filters)], evidence=evidence,
                                  caveats=[], source_result_ids=[result_id])


def generate(context, *, focus=None, text=None, refs=None, reject=False, timeout=False, settings=SETTINGS,
             review_claim='definition', checked_groups=None):
    requests = []
    def handle(request):
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        requests.append(payload)
        if payload['stage'] == 'answer_draft':
            body = dict(focus=focus or payload['required_focus'], insights=[] if text is None else [
                dict(kind='interpretation', refs=refs or ['F1'], text=text)])
        else:
            if timeout:
                raise httpx.ReadTimeout('offline support-review timeout')
            body = dict(decisions=[dict(index=row['index'], supported=not reject,
                                       reason='unsupported_interpretation' if reject else 'supported',
                                       detail='离线审核固定响应', topic_claim_type=review_claim,
                                       checked_topic_groups=row.get('topic_group_ids', []) if checked_groups is None
                                       else checked_groups) for row in payload['insights']])
        return completion(dict(role='assistant', content=json.dumps(body, ensure_ascii=False)))
    def client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    with patch('openai.OpenAI', side_effect=client):
        answer, diagnostics = NaturalLanguageAnswerGenerator().generate(
            context.question, context.model_dump(), settings=settings, validated=True)
    return answer, diagnostics, requests


def visible(answer):
    return answer['summary'] + '\n' + '\n'.join(row['text'] for row in answer['highlights'])


class TopicAnswerIntegrityTests(unittest.TestCase):
    def assert_ranking(self, answer, names=('安装服务好', '安装速度快', '客服响应快')):
        self.assertEqual(answer['status'], 'ok')
        self.assertEqual(len(answer['highlights']), len(names))
        for row, name in zip(answer['highlights'], names):
            self.assertIn(name, row['text'])
        text = visible(answer)
        for name in names:
            self.assertIn(name, text)
        for before, after in zip(names, names[1:]):
            self.assertLess(answer['summary'].index(before), answer['summary'].index(after))

    def test_A1_all_three_topics_in_final_answer(self):
        answer, _, _ = generate(topic_context())
        self.assert_ranking(answer)
        for row, count in zip(answer['highlights'], (27, 10, 3)):
            self.assertIn(f'{count} 条去重评论', row['text'])
            self.assertIn('分母 37 条', row['text'])

    def test_A2_minority_focus_cannot_hide_major_topics(self):
        answer, _, _ = generate(topic_context(), focus=['F3'])
        self.assert_ranking(answer)

    def test_A3_reversed_model_focus_cannot_reorder_topics(self):
        answer, _, _ = generate(topic_context(), focus=['F3', 'F2', 'F1'])
        self.assert_ranking(answer)

    def test_A4_equal_counts_use_backend_stable_topic_id(self):
        context = topic_context((10, 10, 10))
        names = [row['name'] for row in context.statistics[0]['filters']['topics']]
        for order in (['F3', 'F1', 'F2'], ['F2', 'F3', 'F1']):
            answer, _, _ = generate(context, focus=order)
            self.assert_ranking(answer, names)

    def test_A5_no_topics_or_unknown_focus_cannot_invent_theme(self):
        context = topic_context(())
        answer, _, requests = generate(context)
        self.assertFalse(requests)
        self.assertNotIn('安装服务好', visible(answer))
        answer, diag, _ = generate(topic_context(), focus=['F999'])
        self.assert_ranking(answer)
        self.assertEqual(diag['failure_reason'], 'unknown_reference')

    def test_A6_top_k_discloses_omitted_count(self):
        context = topic_context(tuple(range(12, 0, -1)))
        answer, _, _ = generate(context, focus=['F8', 'F7'])
        self.assertEqual(len(answer['highlights']), 8)
        self.assertIn('未展示 4 个', ''.join(answer['caveats']))
        for row, topic in zip(answer['highlights'], context.statistics[0]['filters']['topics'][:8]):
            self.assertIn(topic['name'], row['text'])

    def test_A_template_and_direct_assembler_follow_same_policy(self):
        context = topic_context()
        facts, _ = build_answer_facts(context)
        answer = AnswerAssembler().assemble(context, template_draft(context, facts)).model_dump()
        self.assert_ranking(answer)
        catalog, _ = fact_catalog(context)
        draft = InsightDraft(focus=[row['fact_id'] for row in reversed(catalog)])
        answer = AnswerAssembler().assemble_insights(context, draft, catalog, generation_status='llm').model_dump()
        self.assert_ranking(answer)

    def test_A_recomputed_37_opinions_reach_visible_answer_unchanged(self):
        specs = [('安装师傅认真细致', ['t1'])] * 24 + [('安装认真细致而且及时', ['t1', 't2'])] * 3
        specs += [('安装及时', ['t2'])] * 7 + [('客服回复快且耐心', ['t3'])] * 3
        source = frozen_input(specs)
        definitions = [('t1', '安装服务好', '安装师傅服务专业、细致'),
                       ('t2', '安装速度快', '安装师傅上门及安装及时'),
                       ('t3', '客服响应快', '客服回复迅速、沟通耐心')]
        keys = {quote: matches for quote, matches in specs}
        def respond(settings, prompt, payload, schema, timeout):
            if schema is topics_module.TopicCatalog:
                return schema(themes=[dict(key=key, name=name, definition=definition,
                    support_ids=[next(row['opinion_id'] for row in payload['opinions'] if key in keys[row['quote']])])
                    for key, name, definition in definitions])
            return schema(assignments=[dict(opinion_id=row['opinion_id'], state='assigned',
                matches=[dict(key=key, start=0, end=len(row['quote'])) for key in keys[row['quote']]])
                for row in payload['opinions']])
        with patch.object(topics_module, '_call', side_effect=respond):
            data = topics_module.analyze_aspect_topics(source, settings=SETTINGS)
        self.assertTrue(topics_module.validate_topic_result(data))
        before = canonical_json(data)
        item = dict(result_id='r1', kind='aspect_drilldown', parameters={}, data=data)
        plan = ReviewQueryPlan(analysis_type='aspect_drilldown', sentiment='positive', scope=source['scope'],
                               requires_evidence=True, max_evidence_items=10)
        context = AnalysisResultCompressor().compress('客服的好评体现在哪里？', plan, [adapt_result(item, source['scope'])])
        answer, _, _ = generate(context, focus=['F3'])
        self.assert_ranking(answer)
        self.assertEqual([row['review_count'] for row in data['topics']], [27, 10, 3])
        self.assertEqual(data['coverage']['evaluated_opinions'], 37)
        self.assertEqual(before, canonical_json(data))

    def test_A_history_rebuilds_old_incomplete_focus_without_model_calls(self):
        context = topic_context()
        catalog, _ = fact_catalog(context)
        saved = dict(schema_version='3.0', focus=[catalog[-1]['fact_id']], insights=[dict(kind='interpretation',
            fact_ids=[catalog[-1]['fact_id']], text='用户对客服的好评主要集中在其响应速度和沟通态度上。')])
        task = dict(status='succeeded', result=dict(analysis_intent='aspect_drilldown', sentiment_intent='positive',
            question=context.question, answer_generation=dict(validated_draft=saved, generation_status='llm')))
        with patch('openai.OpenAI', side_effect=AssertionError('history must not call provider')):
            answer = read_public_answer(task, public_analysis=public_from_context(context).model_dump())
        self.assert_ranking(answer)
        self.assertFalse(answer['insights'])

    def test_B1_minority_dominance_rejected_even_if_mock_reviewer_would_accept(self):
        answer, diag, _ = generate(topic_context(), focus=['F3'], refs=['F3'],
            text='用户对客服的好评主要集中在其响应速度和沟通态度上。')
        self.assert_ranking(answer)
        self.assertEqual(answer['insights'], [])
        self.assertEqual(answer['generation_status'], 'fallback')

    def test_B2_major_topic_description_can_pass_semantic_review(self):
        answer, _, requests = generate(topic_context(), text='安装服务好关注师傅服务是否专业细致。')
        self.assert_ranking(answer)
        self.assertEqual(len(answer['insights']), 1)
        self.assertEqual(len(requests), 2)

    def test_B3_unproven_all_reviews_claim_rejected(self):
        for text in ('所有评论均关注安装。', '每位买家都在评价安装服务。', '这已覆盖全部主题。'):
            with self.subTest(text=text):
                answer, _, _ = generate(topic_context(), text=text)
                self.assertEqual(answer['insights'], [])
                self.assert_ranking(answer)

    def test_B4_minority_description_is_not_rejected_for_low_count(self):
        answer, _, requests = generate(topic_context(), focus=['F3'], refs=['F3'],
                                      text='客服响应快关注回复的及时性和沟通的耐心。')
        self.assertEqual(len(answer['insights']), 1)
        self.assertEqual(len(requests), 2)
        self.assert_ranking(answer)

    def test_B5_sample_extrapolation_overlap_and_human_label_rejected(self):
        for text in ('全部消费者的关注点是安装服务。', '所有买家均认可安装服务。',
                     '这些主题合计构成互斥的评价比例。', '这些属于人工确认的标签。',
                     '客服响应快是占比最高的主题。'):
            with self.subTest(text=text):
                answer, _, _ = generate(topic_context(), refs=['F3'], text=text)
                self.assertEqual(answer['insights'], [])
                self.assert_ranking(answer)

    def test_B6_reviewer_receives_complete_same_group_ranking(self):
        context = topic_context()
        catalog, _ = fact_catalog(context)
        draft, _ = parse_draft(dict(focus=['F3'], insights=[dict(kind='interpretation', refs=['F3'],
                                         text='客服响应快关注回复是否及时。')]), catalog)
        payload = support_payload(context.question, draft, catalog, [0], ['非随机样本，不可外推'])
        self.assertEqual([row['metrics'][0]['review_count'] for row in payload['facts']], [27, 10, 3])
        self.assertEqual(payload['insights'][0]['refs'], ['F3'])
        sent = canonical_json(payload)
        self.assertIn('2021-01-01', sent)
        self.assertIn('service', sent)
        self.assertIn('positive', sent)
        self.assertIn('evaluated_opinions', sent)
        self.assertNotIn('安装师傅认真细致', sent)

    def test_B6_unrelated_scope_and_aspect_not_sent(self):
        context = topic_context()
        other = topic_context((9, 8), aspect='logistics', result_id='r2', scope_ref='scope:other')
        other.scope_summary[0]['product_ids'] = ['OTHER-PRODUCT']
        context.scope_summary += other.scope_summary
        context.statistics += other.statistics
        context.source_result_ids += other.source_result_ids
        context.evidence += other.evidence
        catalog, _ = fact_catalog(context)
        draft, _ = parse_draft(dict(focus=['F3'], insights=[dict(kind='interpretation', refs=['F3'],
                                         text='客服响应快关注回复是否及时。')]), catalog)
        payload = support_payload(context.question, draft, catalog, [0], [])
        self.assertEqual(len(payload['facts']), 3)
        self.assertNotIn('OTHER-PRODUCT', canonical_json(payload))

    def test_B7_timeout_rejection_and_no_insight_keep_statistics(self):
        for options, status in ((dict(timeout=True), 'fallback'), (dict(reject=True), 'fallback'), ({}, 'llm')):
            with self.subTest(options=options):
                answer, diag, _ = generate(topic_context(), text=None if not options else '安装服务好关注专业细致。', **options)
                self.assert_ranking(answer)
                self.assertEqual(answer['generation_status'], status)
                self.assertFalse(answer['insights'])
                self.assertLessEqual(diag['attempts'], 2)

    def test_B_budget_overflow_does_not_send_oversized_review(self):
        answer, diag, requests = generate(topic_context(), text='安装服务好关注专业细致。',
                                          settings=replace(SETTINGS, answer_input_max_bytes=100))
        self.assert_ranking(answer)
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertFalse(requests)

    def test_B_structured_review_cannot_verify_unproven_distribution(self):
        # 不依赖“主要/最多”等关键词：语义复核必须明确声明断言类型与已审组。
        for claim in ('distribution', 'exhaustiveness', 'population', 'aggregation', 'other', None):
            with self.subTest(claim=claim):
                answer, diag, _ = generate(topic_context(), text='客服响应快体现交流体验。', refs=['F3'],
                                           review_claim=claim)
                self.assert_ranking(answer)
                self.assertFalse(answer['insights'])
                self.assertEqual(diag['generation_status'], 'fallback')

    def test_B_structured_review_must_cover_the_actual_topic_group(self):
        for groups in ([], ['G999']):
            with self.subTest(groups=groups):
                answer, _, _ = generate(topic_context(), text='客服响应快关注回复及时性。', refs=['F3'],
                                        checked_groups=groups)
                self.assert_ranking(answer)
                self.assertFalse(answer['insights'])

    def test_facts_are_not_mutated_by_answer_generation(self):
        context = topic_context()
        before = canonical_json(context.model_dump())
        generate(context, focus=['F3'])
        self.assertEqual(before, canonical_json(context.model_dump()))

    def test_P1_service_boundary_is_visible_without_relabeling(self):
        context = topic_context()
        answer, _, _ = generate(context)
        self.assertIn('安装、售后与客服沟通', ''.join(answer['caveats']))
        self.assertEqual(context.statistics[0]['filters']['aspect'], 'service')


def frozen_input(specs):
    scope = topic_context().scope_summary[0].copy()
    scope.pop('scope_ref')
    items = []
    for i, (quote, _) in enumerate(specs):
        items.append(dict(review_id=1000 + i, annotation_id=2000 + i, revision_id=None,
            product_id='P0-SYNTHETIC', published_at='2021-06-01', text=quote,
            evidence_ref=f'sha256:synthetic#line={i + 1}', opinion_source='model',
            opinions=[dict(aspect='service', sentiment='positive', quote=quote, quote_start=0, quote_end=len(quote))]))
    return dict(scope=scope, aspect='service', sentiment='positive', total=229, total_opinions=251, items=items,
        summary=dict(review_count=1699, eligible_text_reviews=1699, successful_reviews=1660,
                     failed_reviews=39, unprocessed_reviews=0, labeling_coverage=round(1660 / 1699, 6),
                     aspects={'service': {}}))


class TopicSourceSafetyTests(unittest.TestCase):
    def test_C1_C2_C3_exact_support_full_reference_and_foreign_reference(self):
        quote = '客服耐心解答；客服耐心解答。'
        payload = {'opinions': [{'opinion_id': 'u1', 'quote': quote}]}
        def parse(support):
            response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                tool_calls=None, content=json.dumps({'rows': [{'i': 'u1', 's': 'a', 'm': [['t1', support]]}]})))])
            return topics_module._parse_topic_response(response, topics_module.TopicWireBatch,
                                                       topics_module.TopicBatch, payload)
        match = parse({'quote_id': 'u1'}).assignments[0].matches[0]
        self.assertEqual((match.start, match.end), (0, len(quote)))
        for support, code in (('改写的支持文本', 'support_text_mismatch'),
                              ({'quote_id': 'u2'}, 'source_reference_mismatch'),
                              ('客服耐心解答', 'ambiguous_support')):
            with self.subTest(code=code), self.assertRaises(topics_module.TopicResponseError) as error:
                parse(support)
            self.assertEqual(error.exception.code, code)

    def test_C_source_coverage_and_span_stay_strict(self):
        units = [dict(opinion_id='u1', quote='客服耐心', aspect='service')]
        catalog = topics_module.TopicCatalog(themes=[dict(key='t1', name='耐心解答',
            definition='客服耐心解答咨询', support_ids=['u1'], aspect='service')])
        for batch, code in ((topics_module.TopicBatch(assignments=[]), 'assignment_coverage_mismatch'),
                            (topics_module.TopicBatch(assignments=[dict(opinion_id='u1', state='assigned',
                                matches=[dict(key='t1', start=0, end=99)])]), 'invalid_citation_span')):
            with self.subTest(code=code), self.assertRaises(topics_module.TopicResponseError) as error:
                topics_module._checked_batch(batch, units, catalog)
            self.assertEqual(error.exception.code, code)

    def test_C8_failed_second_batch_keeps_25_of_37_not_complete_and_never_repeats_success(self):
        # 20 unique expressions; u1..u10 expand to 25 opinions, u11..u20 to 12.
        counts = [3] * 5 + [2] * 5 + [2] * 2 + [1] * 8
        expressions = [f'客服耐心解答第{i}项咨询' for i in range(20)]
        specs = [(quote, ['t1']) for quote in expressions]
        specs += [(quote, ['t1']) for quote, count in zip(expressions, counts) for _ in range(count - 1)]
        source, batches = frozen_input(specs), []
        def respond(settings, prompt, payload, schema, timeout):
            if schema is topics_module.TopicCatalog:
                return schema(themes=[dict(key='t1', name='耐心解答', definition='客服耐心解答咨询', support_ids=['u1'])])
            batches.append([row['opinion_id'] for row in payload['opinions']])
            if payload['opinions'][0]['opinion_id'] == 'u11':
                raise topics_module.TopicResponseError('support_text_mismatch', '归属支持片段与原始观点不匹配')
            return schema(assignments=[dict(opinion_id=row['opinion_id'], state='assigned',
                matches=[dict(key='t1', start=0, end=len(row['quote']))]) for row in payload['opinions']])
        with patch.object(topics_module, '_call', side_effect=respond) as calls:
            data = topics_module.analyze_aspect_topics(source, settings=SETTINGS)
        self.assertTrue(topics_module.validate_topic_result(data))
        self.assertEqual(data['analysis_status'], 'partial')
        self.assertEqual(data['coverage']['evaluated_opinions'], 25)
        self.assertEqual(data['coverage']['not_evaluated_opinions'], 12)
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len({tuple(batch) for batch in batches}), 2)


if __name__ == '__main__':
    unittest.main()
