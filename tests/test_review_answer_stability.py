"""阶段 3.5 短引用、误拒绝、逐条复核和可诊断降级；全部为隔离合成数据。"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

import test_review_insights as fixture
from test_agent import SETTINGS, completion
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_insights import fact_catalog, parse_draft, render_insights, validate_draft


class AnswerStabilityTests(fixture.answer_fixture.WorkbenchFixture, unittest.TestCase):
    topic_context = fixture.InsightTests.topic_context
    context = fixture.InsightTests.context
    result = fixture.InsightTests.result
    scope = fixture.InsightTests.scope
    strong_trend_context = fixture.InsightTests.strong_trend_context

    def run_draft(self, context, transform=None, review=None, fence=False):
        requests = []
        def handle(request):
            payload = json.loads(json.loads(request.content)['messages'][-1]['content']); requests.append(payload)
            if payload['stage'] == 'answer_draft':
                draft = {'focus': payload['required_focus'], 'insights': [{'kind': 'interpretation',
                    'refs': ['F1', 'F2'], 'text': '制冷速度关注降温快慢，运行噪声关注安静程度。'}]}
                if transform: draft = transform(draft, payload)
                content = draft if isinstance(draft, str) else json.dumps(draft, ensure_ascii=False)
                if fence: content = '```json\n' + content + '\n```'
            else:
                content = json.dumps({'decisions': review or [{'index': item['index'], 'supported': True,
                    'reason': 'supported', 'detail': '对候选名称的描述性释义，未新增性能或因果事实。'} for item in payload['insights']]})
            return completion({'role': 'assistant', 'content': content})
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with patch('openai.OpenAI', side_effect=client):
            answer, diag = NaturalLanguageAnswerGenerator().generate(context['question'], context, settings=SETTINGS, validated=True)
        return answer, diag, requests

    def test_short_refs_bind_names_and_review_receives_visible_meaning(self):
        _, context = self.topic_context()
        answer, diag, requests = self.run_draft(context, fence=True)
        self.assertEqual(answer['generation_status'], 'llm')
        self.assertEqual(diag['protocol'], 'compact_refs_v3')
        self.assertEqual(diag['attempts'], 2)
        self.assertIn('制冷速度关注降温快慢', answer['insights'][0]['text'])
        self.assertTrue(all(ref.startswith('finding:') for ref in answer['insights'][0]['finding_refs']))
        review_text = requests[1]['insights'][0]['text']
        self.assertNotIn('[[', review_text)
        self.assertIn('运行噪声', review_text)
        sent = json.dumps(requests, ensure_ascii=False)
        self.assertNotIn('降温很快', sent)
        self.assertNotIn('annotation_profile_ref', sent)
        self.assertNotIn('finding:', sent)

    def test_focus_completes_known_groups_without_accepting_unknown_entities(self):
        _, context = self.topic_context()
        answer, diag, _ = self.run_draft(context, lambda draft, _: {**draft, 'focus': ['f2', 'F2']})
        self.assertEqual(answer['generation_status'], 'llm')
        self.assertTrue(diag['focus_augmented'])
        self.assertEqual(len(answer['highlights']), 2)
        answer, diag, _ = self.run_draft(context, lambda draft, _: {**draft, 'focus': ['F999']})
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual(diag['failure_reason'], 'unknown_reference')
        self.assertEqual(diag['attempts'], 1)

    def test_trusted_statement_needs_no_semantic_review_and_is_not_repeated(self):
        _, context = self.topic_context()
        def exact(draft, payload):
            draft['insights'] = [{'kind': 'interpretation', 'refs': ['F1'], 'text': payload['facts'][0]['claim']}]
            return draft
        answer, diag, _ = self.run_draft(context, exact)
        self.assertEqual(diag['attempts'], 1)
        self.assertEqual(diag['insight_decisions'][0]['reason'], 'program_verified')
        self.assertEqual(answer['insights'], [])
        self.assertEqual(diag['answer_outcome'], 'facts_only')

    def test_rejected_explanation_does_not_discard_accepted_explanation(self):
        _, context = self.topic_context()
        def two(draft, _):
            draft['insights'].append({'kind':'suggestion','refs':['F1'], 'text':'可以更换供应商以改善体验。'})
            return draft
        review = [{'index':0,'supported':True,'reason':'supported','detail':'描述已有体验主题。'},
                  {'index':1,'supported':False,'reason':'new_fact','detail':'没有供应商或更换后效果的证据。'}]
        answer, diag, _ = self.run_draft(context, two, review)
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual(len(answer['insights']), 1)
        self.assertNotIn('更换供应商', answer['insights'][0]['text'])
        self.assertEqual(diag['failure_reason'], 'support_rejected')
        self.assertEqual(diag['insight_decisions'][1]['detail'], review[1]['detail'])

    def test_failure_reasons_distinguish_json_schema_reference_and_text(self):
        _, context = self.topic_context()
        for transform, code in [
            (lambda d,p: 'not JSON', 'json_decode'),
            (lambda d,p: {**d, 'invented':123}, 'schema_validation'),
            (lambda d,p: {**d, 'insights':[{'kind':'interpretation','refs':['F123'],'text':'这些主题相关。'}]}, 'unknown_reference'),
            (lambda d,p: {**d, 'insights':[{'kind':'interpretation','refs':['F1'],'text':'有９９条评价。'}]}, 'unbound_number_or_quote'),
            (lambda d,p: {**d, 'insights':[{'kind':'interpretation','refs':['F1'],'text':'原文是“从未有过的体验”。'}]}, 'unbound_number_or_quote')]:
            with self.subTest(code=code):
                answer, diag, _ = self.run_draft(context, transform)
                self.assertEqual(answer['generation_status'], 'fallback')
                self.assertEqual(diag['failure_reason'], code)
                self.assertFalse(answer['insights'])
                self.assertEqual(diag['attempts'], 1)
        answer, diag, _ = self.run_draft(context, review=[{'index':1,'supported':True,'reason':'supported'}])
        self.assertEqual(diag['failure_reason'], 'review_coverage')
        self.assertFalse(answer['insights'])

    def test_unverified_single_quoted_text_and_unproven_significance_are_blocked(self):
        _,context=self.topic_context()
        for text in ("代表原文是'编造评价'。",'差异源于运行时长。','这些主题的评价显著更好。'):
            answer,diag,_=self.run_draft(context,lambda d,p:{**d,'insights':[{'kind':'interpretation','refs':['F1'],'text':text}]})
            self.assertEqual(answer['generation_status'],'fallback')
            self.assertEqual(diag['attempts'],1)
            self.assertFalse(answer['insights'])

    def test_exact_name_normalization_does_not_accept_forged_quotes_or_stats(self):
        _, context = self.topic_context()
        answer, _, _ = self.run_draft(context, lambda d,p: {**d, 'insights':[{'kind':'interpretation',
            'refs':['F1','F2'], 'text':'“制冷速度”和「运行噪声」描述不同的使用体验。'}]})
        self.assertEqual(answer['generation_status'], 'llm')
        self.assertNotIn('“', answer['insights'][0]['text'])
        for text in ('制冷速度达到百分之百。', '来自 OTHER-PRODUCT 的反馈更好。', '参考 https://invented.invalid 。'):
            answer, diag, _ = self.run_draft(context, lambda d,p: {**d, 'insights':[{'kind':'interpretation','refs':['F1'],'text':text}]},
                review=[{'index':0,'supported':False,'reason':'entity_error','detail':'目录没有这个商品或来源。'}])
            self.assertEqual(answer['generation_status'], 'fallback')
            self.assertFalse(answer['insights'])

    def test_product_identifiers_with_digits_bind_before_number_validation(self):
        _, context = self.context('比较两个商品的主要负面问题', product_ids=['P1','P2'])
        def mention(draft,payload):
            draft['insights']=[{'kind':'interpretation','refs':payload['required_focus'],
                'text':'P1与P2的评价差异不等于产品性能差异。'}]
            return draft
        answer, diag, requests = self.run_draft(context,mention)
        self.assertEqual(answer['generation_status'],'llm')
        self.assertFalse(answer['insights'])
        catalog,_=fact_catalog(CompactAnalysisContext.model_validate(context))
        draft,_=parse_draft({'focus':['F1'],'insights':[{'kind':'interpretation','refs':['F1'],'text':'P1与P2的评价存在差异。'}]},catalog)
        validate_draft(draft,catalog)
        self.assertIn('P1与P2',render_insights(draft,catalog)[0]['text'])
        self.assertIn('.product_',draft.insights[0].text)
        answer, diag, _ = self.run_draft(context,lambda d,p:{**d,'insights':[{'kind':'interpretation','refs':p['required_focus'],
            'text':'P99999的表现更好。'}]})
        self.assertEqual(diag['failure_reason'],'unbound_number_or_quote')
        self.assertFalse(answer['insights'])

    def test_long_review_diagnostic_is_truncated_without_changing_decision(self):
        _, context = self.topic_context()
        answer,diag,_ = self.run_draft(context,review=[{'index':0,'supported':True,'reason':'supported','detail':'有目录依据。'*100}])
        self.assertEqual(answer['generation_status'],'llm')
        self.assertEqual(len(diag['insight_decisions'][0]['detail']),180)
        answer,diag,_ = self.run_draft(context,review=[{'index':0,'supported':False,'reason':'new_fact','detail':'没有证据。'*100}])
        self.assertEqual(answer['generation_status'],'fallback')
        self.assertFalse(answer['insights'])

    def test_dimension_only_explanation_request_reports_content_gap_without_a_model(self):
        _,context=self.context('好评集中在哪些方面？请解释。')
        def no_free(draft,payload):return {'focus':payload['required_focus'],'insights':[]}
        answer,diag,_=self.run_draft(context,no_free)
        self.assertEqual(diag['attempts'],0)
        self.assertEqual(answer['generation_status'],'template')
        self.assertIn('使用效果',answer['summary'])
        self.assertIn('不能判断具体问题类型',''.join(answer['caveats']))

    def test_redundant_explanation_is_omitted_without_pretending_it_was_published(self):
        _,context=self.topic_context()
        answer,diag,_=self.run_draft(context,review=[{'index':0,'supported':False,'reason':'redundant',
            'detail':'重复候选主题名单，没有增加证据支持的信息。'}])
        self.assertEqual(answer['generation_status'],'llm')
        self.assertFalse(answer['insights'])
        self.assertEqual(diag['published_insights'],0)
        self.assertEqual(diag['answer_outcome'],'facts_only')
        self.assertEqual(diag['insight_decisions'][0]['reason'],'redundant')

    def test_boilerplate_topic_definition_cannot_create_extra_insight(self):
        _,context=self.topic_context()
        for row in context['statistics'][0]['filters']['topics']:
            row['definition']=row['name']+'的直接使用评价'
        answer,diag,_=self.run_draft(context)
        self.assertEqual(answer['generation_status'],'llm')
        self.assertFalse(answer['insights'])
        self.assertEqual(diag['attempts'],1)
        self.assertEqual(diag['insight_decisions'][0]['reason'],'no_incremental_evidence')

    def test_single_filtered_dimension_is_not_presented_as_global_ranking(self):
        _,context=self.context('包装差评反映了什么？请解释。')
        answer,diag,_=self.run_draft(context)
        self.assertEqual(diag['attempts'],0)
        self.assertNotIn('主要负面问题',answer['summary'])
        self.assertIn('有 1 条包含包装相关负面观点',answer['summary'])
        self.assertFalse(answer['highlights'])
        self.assertTrue(answer['metrics'])

    def test_topic_small_matching_set_does_not_claim_low_annotation_coverage(self):
        _,context=self.topic_context()
        answer,_,_=self.run_draft(context,lambda d,p:{'focus':p['required_focus'],'insights':[]})
        text=''.join(answer['caveats'])
        self.assertIn('4 条匹配评论、6 条观点，样本量较少',text)
        self.assertNotIn('覆盖率不足',text)
        self.assertNotIn('非随机',text)
        context['statistics'][0]['filters']['population_mode']='sample'
        answer,_,_=self.run_draft(context,lambda d,p:{'focus':p['required_focus'],'insights':[]})
        self.assertIn('非随机样本评论',''.join(answer['caveats']))

    def test_three_month_summary_is_bounded_and_collection_completeness_stays_unknown(self):
        self.strong_trend_context()
        _,context=self.context('按月查看包装和物流问题变化',dataset_version='phase4-controlled',
            product_ids=['TREND-CONTROL'],date_from='2023-04-01',date_to='2023-06-30')
        answer,_,_=self.run_draft(context,lambda d,p:{'focus':p['required_focus'],'insights':[]})
        self.assertIn('2023 年 4 月至 6 月',answer['summary'])
        self.assertIn('包装负面占比先降后升',answer['summary'])
        self.assertIn('物流负面占比逐月下降',answer['summary'])
        self.assertEqual(answer['summary'].count('已标注样本'),1)
        self.assertIn('不能直接推断长期稳定改善',answer['summary'])
        notes=''.join(answer['caveats'])
        self.assertIn('未核验',notes)
        self.assertNotIn('未覆盖完整自然月',notes)
        for stat in context['statistics']:
            for series in stat.get('trends',[]):
                series.update(original_point_count=4,omitted_point_count=1)
        answer,_,_=self.run_draft(context,lambda d,p:{'focus':p['required_focus'],'insights':[]})
        self.assertNotIn('先降后升',answer['summary'])
        self.assertNotIn('逐月下降',answer['summary'])
