"""证据缺口与固定补查建议；不执行网络、SQL 或语义模型。"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

from scripts.insight_evidence_cases import cases
from data_analysis_agent.agents.review_evidence import assess_evidence,TOOL_MODELS
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator, AnswerAssembler
from data_analysis_agent.agents.review_insights import fact_catalog, aliases, parse_draft, validate_draft, InsightDraft, default_focus
from test_agent import SETTINGS


class EvidenceAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with redirect_stdout(io.StringIO()):cls.cases={row['case']:row for row in cases()}

    def context(self,key):return CompactAnalysisContext.model_validate(deepcopy(self.cases[key]['context']))

    def test_fixed_case_sufficiency_and_existing_tool_contracts(self):
        for row in self.cases.values():
            with self.subTest(case=row['case']):
                result=assess_evidence(CompactAnalysisContext.model_validate(row['context']))
                self.assertEqual(result['status'],row['expected_sufficiency'])
                self.assertTrue(set(row['expected_additional_tools'])<={q['tool'] for q in result['followup_queries']})
                self.assertLessEqual(len(result['followup_queries']),2)
                for query in result['followup_queries']:
                    TOOL_MODELS[query['tool']].model_validate(query['parameters'])
                    self.assertEqual(query['execution'],'proposal_only')
                    self.assertNotIn('sql',query)

    def test_missing_packaging_content_recommends_bound_single_product_drilldown(self):
        context=self.context('P01');assessment=assess_evidence(context)
        query=assessment['followup_queries'][0]
        self.assertEqual(query['tool'],'get_aspect_drilldown')
        self.assertEqual(query['parameters'],{'product_ids':['IE-PACK'],'aspect':'packaging','sentiment':'negative'})
        self.assertEqual(query['scope_ref'],context.scope_summary[0]['scope_ref'])
        self.assertFalse(query['requires_scope_confirmation'])
        with patch('openai.OpenAI',side_effect=AssertionError('no model needed')) as client:
            answer,diag=NaturalLanguageAnswerGenerator().generate(context.question,context.model_dump(),settings=SETTINGS,validated=True)
        client.assert_not_called()
        self.assertEqual(diag['evidence_assessment'],assessment)
        self.assertEqual(diag['attempts'],0)
        self.assertFalse(answer['insights'])

    def test_comparison_proposals_preserve_both_products_and_scope_mismatch_needs_confirmation(self):
        assessment=assess_evidence(self.context('L01'))
        self.assertEqual({q['parameters']['product_ids'][0] for q in assessment['followup_queries']},{'IE-LOG-A','IE-LOG-B'})
        mismatch=assess_evidence(self.context('L03'))
        self.assertEqual(mismatch['status'],'comparison_scope_mismatch')
        self.assertTrue(mismatch['followup_queries'][0]['requires_scope_confirmation'])
        self.assertIsNone(mismatch['followup_queries'][0]['scope_ref'])

    def test_causal_and_prediction_boundaries_are_not_declared_solved_by_more_comments(self):
        for key in ('E04','P04','C02','C04','T04'):
            assessment=assess_evidence(self.context(key))
            self.assertEqual(assessment['status'],'external_evidence_required')
            self.assertFalse(assessment['followup_queries'])
        self.assertNotIn('no_valid_annotations',assess_evidence(self.context('L04'))['gaps'])

    def test_sample_content_and_partial_do_not_become_complete_population_facts(self):
        result=assess_evidence(self.context('E05'))
        self.assertEqual(result['status'],'sample_descriptive_only')
        self.assertIn('nonrandom_sample_not_population',result['gaps'])
        result=assess_evidence(self.context('P05'))
        self.assertEqual(result['status'],'analysis_incomplete')
        self.assertFalse(result['followup_queries'])

    def test_existing_opinions_do_not_trigger_duplicate_queries_but_missing_model_content_is_recorded(self):
        result=assess_evidence(self.context('P03'))
        self.assertEqual(result['status'],'descriptive_evidence_available')
        self.assertFalse(result['followup_queries'])
        self.assertIn('verbatim_evidence_not_sent_to_answer_model',result['gaps'])

    def test_unresolved_topics_request_examples_instead_of_repeating_clustering(self):
        context=self.context('E02');filters=context.statistics[0]['filters']
        filters.update(analysis_status='insufficient_data',topics=[]);context.evidence=[]
        result=assess_evidence(context)
        self.assertEqual(result['status'],'content_not_resolved')
        self.assertEqual(result['followup_queries'][0]['tool'],'get_evidence')
        self.assertEqual(result['followup_queries'][0]['parameters']['page_size'],3)

    def test_unknown_aspect_and_large_product_scope_do_not_guess_or_silently_truncate(self):
        context=self.context('L01');context.question='这两款差异具体是什么？'
        result=assess_evidence(context)
        self.assertEqual(result['status'],'clarification_needed')
        self.assertFalse(result['followup_queries'])
        context=self.context('L01');context.scope_summary[0]['product_ids']=['A','B','C']
        result=assess_evidence(context)
        self.assertIn('followup_query_budget_exceeded',result['gaps'])
        self.assertFalse(result['followup_queries'])

    def test_pair_pronouns_are_allowed_only_for_two_referenced_products(self):
        def check(key,text):
            catalog,_=fact_catalog(self.context(key));refs=list(aliases(catalog))
            draft,_=parse_draft({'focus':refs,'insights':[{'kind':'interpretation','refs':refs,'text':text}]},catalog)
            return validate_draft(draft,catalog)
        for text in ('两款产品的评价侧重不同。','一款关注启动体验，另一款关注睡眠体验。','这两款的主观评价不能替代客观测试。'):
            with self.subTest(text=text):
                check('C01',text)
                with self.assertRaisesRegex(ValueError,'数字'):
                    check('E02',text)
        for text in ('十一款产品的体验相同。','三款产品的体验相同。','两款产品各有99条好评。','两款产品的好评占百分之五十。'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError,'数字'):
                check('C01',text)

    def test_empty_scope_does_not_invent_a_query(self):
        context=self.context('E01');context.scope_summary=[]
        result=assess_evidence(context)
        self.assertEqual(result['status'],'clarification_needed')
        self.assertFalse(result['followup_queries'])

    def test_merged_facts_keep_product_and_window_attribution_in_prose(self):
        for key in ('C01','T02'):
            context=self.context(key);catalog,_=fact_catalog(context)
            answer=AnswerAssembler().assemble_insights(context,InsightDraft(focus=default_focus(catalog)),catalog,generation_status='template')
            by_ref={row['fact_id']:row for row in catalog}
            for highlight in answer.highlights:
                scope=by_ref[highlight.finding_ref]['scope']
                if key=='C01':self.assertIn(scope['product_ids'][0],highlight.text)
                else:
                    self.assertIn(scope['date_from'],highlight.text)
                    self.assertIn(scope['date_to'],highlight.text)
            self.assertEqual(answer.evidence_refs,context.evidence)
            if key=='C01':self.assertEqual(answer.summary.count('睡眠安静体验'),1)
            else:self.assertIn('2023 年 1 月至 3 月',answer.summary)

    def test_trend_content_proposals_are_two_confirmed_subwindows_inside_original_range(self):
        context=self.context('T01');result=assess_evidence(context)
        self.assertEqual(len(result['followup_queries']),2)
        windows=[query['requested_subwindow'] for query in result['followup_queries']]
        self.assertEqual(windows,[{'date_from':'2023-01-01','date_to':'2023-01-31'},
            {'date_from':'2023-03-01','date_to':'2023-03-31'}])
        self.assertTrue(all(query['requires_scope_confirmation'] for query in result['followup_queries']))
        for row in self.cases['T01']['followup_validation']:
            self.assertEqual(row['status'],'verified_synthetic_only')
            self.assertEqual(row['validation'],'passed')
