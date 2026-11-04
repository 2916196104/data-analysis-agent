"""All fixtures are synthetic; no real comments, scores, database or predictions."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.exploratory_e1 import (
    EXPERIMENT_KIND, ExploratoryInput, prepare_exploratory_input, make_review_draft, blind_review_case,
)
from data_analysis_agent.reviews.exploratory_e1_metrics import summarize_e1
from data_analysis_agent.reviews.query_contract import ScopeRef
from data_analysis_agent.reviews.retrieval_evaluation import run_binding
from test_e1_readiness import fixture, review_fixture


def inputs(quotes=('合成设备陈述尚待人工判断',)):
    run,old,scope=fixture(quotes)
    manifest=run['manifest']
    run['scope_sha256']=content_sha256([manifest['scope'],manifest['label_profile_key'],manifest['embedding_space_id']])
    run['run_id']=run_binding(run)
    scope=ScopeRef(run_id=run['run_id'],manifest_sha256=run['manifest_sha256'],scope_sha256=run['scope_sha256'])
    sources=dict(version='e1-observed-sources-v1',experiment_kind=EXPERIMENT_KIND,data_kind='synthetic',
        scope_ref=scope.model_dump(mode='json'),inventory_snapshot_sha256=old['inventory_snapshot_sha256'],
        observations=[dict(role=role,observed_at=f'2031-01-0{n}T00:00:00Z',transaction_snapshot='SYNTHETIC-SAME-VISIBLE-XIDS',
            observation_file_sha256=content_sha256(role),receipt_file_sha256=content_sha256(role+'receipt'))
            for n,role in enumerate(('row_sources','complete_metadata'),2)],
        snapshots=list({r['review_id']:dict(review_id=r['review_id'],classification='history_unproven',
            computed_snapshot_sha256=r['effective_snapshot_sha256'],frozen_snapshot_sha256=r['effective_snapshot_sha256'])
            for r in old['records']}.values()),records=old['records'])
    return run,sources,scope


def prepare(run,sources,scope,mode='unfiltered'):
    return prepare_exploratory_input(run,sources,expected_scope=scope,expected_data_kind='synthetic',
        query_id=run['manifest']['queries'][0]['id'],mode=mode)


def draft(scope,qid,question):
    _,c,_,_=review_fixture()
    value=c.model_dump(mode='json')
    value['scope_ref']=scope.model_dump(mode='json')
    value['required_facts']['items'][0]['description']='合成原问题中的特殊必要事实，不能替换为规则句式'
    return dict(version='e1-query-contract-draft-v1',query_id=qid,query_sha256=content_sha256(question),contract=value,
        independent_target_fact_ids=[],human_confirmed=False)


def audited(qid='SYNTH-A',mode='unfiltered',ref='sufficient',out='sufficient',**changes):
    value=dict(version='e1-preaudited-case-v1',origin='independent_reference_and_prediction_audit',query_id=qid,mode=mode,
        contract_sha256='1'*64,observed_sources_sha256='2'*64,audit_record_sha256='3'*64,
        context_policy='quote_full_review_veto',context_version='1',source_valid=True,reference_confirmed=True,
        reference_state=ref,reference_licensed_fact_ids=['f0'] if ref!='insufficient' else [],output_execution='completed',
        output_state=out,output_licensed_fact_ids=['f0'] if out!='insufficient' else [],licensed_fact_audits=[],
        citation_count=1 if out!='insufficient' else 0,valid_citation_count=1 if out!='insufficient' else 0,
        semantic_coverage_unknown=False)
    value.update(changes)
    return value


class ExploratoryPreparationTests(unittest.TestCase):
    def test_separate_version_retains_history_and_two_actual_observation_times(self):
        run,sources,scope=inputs();before=deepcopy((run,sources))
        p=prepare(run,sources,scope)
        self.assertEqual(p.history_status,'history_unproven')
        self.assertFalse(p.independent_seal)
        self.assertNotIn('trusted_batch',p.model_dump())
        self.assertEqual(p.batch_sha256,content_sha256(p.candidate_batch.model_dump(mode='json')))
        self.assertEqual((run,sources),before)
        self.assertEqual(p,ExploratoryInput.model_validate_json(p.model_dump_json()))

    def test_missing_or_duplicate_global_source_rejected_even_for_empty_list(self):
        for change in (lambda s:s.update(records=[]),lambda s:s['records'].append(deepcopy(s['records'][0]))):
            run,sources,scope=inputs();change(sources)
            with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_valid_empty_lists_and_unchanged_frozen_identity(self):
        run,sources,scope=inputs(())
        self.assertEqual(prepare(run,sources,scope).candidate_batch.candidates,())
        self.assertEqual(prepare(run,sources,scope,'filtered').candidate_batch.candidates,())

    def test_scope_data_kind_and_snapshot_status_cannot_be_upgraded(self):
        mutations=(lambda s:s.update(data_kind='frozen_query'),lambda s:s['scope_ref'].update(run_id='0'*64),
            lambda s:s['snapshots'][0].update(classification='rebuilt_match'),
            lambda s:s['snapshots'][0].update(computed_snapshot_sha256='0'*64))
        for change in mutations:
            run,sources,scope=inputs();change(sources)
            with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_scope_digest_uses_project_profile_space_binding_and_inventory_is_checked(self):
        for change in (lambda r:r.update(scope_sha256=content_sha256(r['manifest']['scope'])),
                       lambda r:r['inventory'].update(embedding_space_id='WRONG-SYNTHETIC-SPACE'),
                       lambda r:r['inventory'].update(invalid_count=1)):
            run,sources,scope=inputs();change(run);run['run_id']=run_binding(run)
            scope=ScopeRef(run_id=run['run_id'],manifest_sha256=run['manifest_sha256'],scope_sha256=run['scope_sha256'])
            sources['scope_ref']=scope.model_dump(mode='json')
            with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_two_roles_timezone_and_missing_provenance_are_required(self):
        for change in (lambda s:s['observations'].pop(),lambda s:s['observations'][0].update(role='complete_metadata'),
            lambda s:s['observations'][0].update(observed_at='2031-01-02T00:00:00')):
            run,sources,scope=inputs();change(sources)
            with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_quote_span_hash_and_source_conflict_are_not_silently_accepted(self):
        for change in (lambda s:s['records'][0].update(quote_start=100),lambda s:s['records'][0].update(embedding_input_sha256='0'*64),
            lambda s:s['records'][0].update(evidence_ref='wrong-synthetic-ref'),lambda s:s['records'][0].update(published_at='2032-01-01T00:00:00Z')):
            run,sources,scope=inputs();change(sources)
            with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_original_rank_cannot_be_reordered_even_with_rebound_run(self):
        run,sources,scope=inputs(('合成甲','合成乙'))
        run['searches'][0]['hits'].reverse();run['run_id']=run_binding(run)
        scope=ScopeRef(run_id=run['run_id'],manifest_sha256=scope.manifest_sha256,scope_sha256=scope.scope_sha256)
        sources['scope_ref']=scope.model_dump(mode='json')
        with self.assertRaises(ValueError):prepare(run,sources,scope)

    def test_reference_is_unscored_unconfirmed_with_original_goal_preserved(self):
        run,sources,scope=inputs();p=prepare(run,sources,scope);q=run['manifest']['queries'][0]
        d=draft(scope,q['id'],q['text']);ref=make_review_draft(p,d,question=q['text'],expected_scope=scope)
        self.assertFalse(ref['human_confirmed']);self.assertFalse(ref['contract_confirmed'])
        self.assertIsNone(ref['reference_state'])
        self.assertEqual(ref['fact_findings'][0]['support'],'unknown')
        self.assertEqual(ref['reviewer_decisions'],[])
        self.assertEqual(d['contract']['required_facts']['items'][0]['description'],'合成原问题中的特殊必要事实，不能替换为规则句式')

    def test_blind_projection_has_no_scores_predictions_mode_or_scheme(self):
        run,sources,scope=inputs();p=prepare(run,sources,scope);q=run['manifest']['queries'][0]
        view=blind_review_case(p,draft(scope,q['id'],q['text']),question=q['text'],case_id='CASE-1',expected_scope=scope)
        forbidden={'mode','filters','grade','cosine_distance','reason_codes','experiment_kind','reference_prediction','criterion'}
        def walk(value):
            if isinstance(value,dict):
                self.assertFalse(set(value)&forbidden)
                for v in value.values():walk(v)
            elif isinstance(value,list):
                for v in value:walk(v)
        walk(view)
        self.assertEqual(view['reviewer_count'],1);self.assertFalse(view['human_confirmed'])
        self.assertIsNone(view['prior_relevance_exposure'])

    def test_invalid_draft_query_independent_ids_or_confirmation_rejected(self):
        for change in (lambda d:d.update(query_sha256='0'*64),lambda d:d.update(independent_target_fact_ids=['unknown']),
                       lambda d:d.update(human_confirmed=True)):
            run,sources,scope=inputs();p=prepare(run,sources,scope);q=run['manifest']['queries'][0];d=draft(scope,q['id'],q['text']);change(d)
            with self.assertRaises(ValueError):make_review_draft(p,d,question=q['text'],expected_scope=scope)

    def test_no_database_judge_strict_preparation_score_or_network_calls(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')), \
             patch('psycopg.connect',side_effect=AssertionError('database forbidden')), \
             patch('data_analysis_agent.reviews.evidence_judge.judge_evidence',side_effect=AssertionError('judge forbidden')), \
             patch('data_analysis_agent.reviews.e1_preparation.prepare_trusted_batch',side_effect=AssertionError('strict preparation forbidden')), \
             patch('data_analysis_agent.reviews.retrieval_evaluation.score_run',side_effect=AssertionError('scores forbidden')):
            run,sources,scope=inputs();p=prepare(run,sources,scope);q=run['manifest']['queries'][0]
            self.assertIsNone(make_review_draft(p,draft(scope,q['id'],q['text']),question=q['text'],expected_scope=scope)['reference_state'])


class ExploratoryMetricTests(unittest.TestCase):
    def test_all_refusal_exposes_over_refusal_and_empty_permission_denominators(self):
        rows=[audited(qid=q,mode=m,ref=ref,out='insufficient') for q,ref in (('SYNTH-A','sufficient'),('SYNTH-B','insufficient'))
              for m in ('unfiltered','filtered')]
        result=summarize_e1(rows,expected_pairs=[(r['query_id'],r['mode']) for r in rows])
        g=result['overall']
        self.assertEqual(g['over_refusal_rate'],dict(numerator=2,denominator=2,value=1))
        self.assertEqual(g['unsupported_answer_permission_rate']['value'],0)
        self.assertIsNone(g['licensed_fact_support_rate']['value'])
        self.assertIsNone(g['citation_validity_rate']['value'])
        self.assertEqual(result['paired']['over_refusal_rate']['paired_query_denominator'],1)

    def test_limited_is_both_an_answer_permission_and_answerable_reference(self):
        rows=[audited(ref='insufficient',out='limited'),audited(qid='SYNTH-B',ref='limited',out='insufficient')]
        g=summarize_e1(rows,expected_pairs=[(r['query_id'],r['mode']) for r in rows])['overall']
        self.assertEqual(g['unsupported_answer_permission_rate']['value'],1)
        self.assertEqual(g['over_refusal_rate']['value'],1)
        self.assertEqual(g['confusion_matrix']['limited']['insufficient'],1)

    def test_missing_and_failures_do_not_count_as_correct_insufficient(self):
        rows=[audited(source_valid=False,valid_citation_count=0),audited(qid='SYNTH-B',reference_confirmed=False),
              audited(qid='SYNTH-C',output_execution='execution_failed',output_state=None,output_licensed_fact_ids=[])]
        pairs=[(r['query_id'],r['mode']) for r in rows]+[('SYNTH-D','filtered')]
        g=summarize_e1(rows,expected_pairs=pairs)['overall']
        self.assertEqual((g['source_failures'],g['missing_references'],g['execution_failures'],g['missing_rows']),(1,1,1,1))
        self.assertEqual(g['valid_comparisons'],0);self.assertIsNone(g['state_agreement']['value'])

    def test_fact_support_requires_independent_mapping_not_just_fact_ids(self):
        row=audited(licensed_fact_audits=[dict(fact_id='f0',supported_by_reference_and_citations=None)])
        g=summarize_e1([row],expected_pairs=[('SYNTH-A','unfiltered')])['overall']
        self.assertIsNone(g['licensed_fact_support_rate']['value']);self.assertEqual(g['unassessed_licensed_fact_count'],1)
        row['licensed_fact_audits'][0]['supported_by_reference_and_citations']=True
        self.assertEqual(summarize_e1([row],expected_pairs=[('SYNTH-A','unfiltered')])['overall']['licensed_fact_support_rate']['value'],1)
        row.update(reference_state='insufficient',reference_licensed_fact_ids=[])
        with self.assertRaises(ValueError):summarize_e1([row],expected_pairs=[('SYNTH-A','unfiltered')])

    def test_pair_and_group_denominators_and_coverage_are_explicit(self):
        rows=[audited(mode='unfiltered',out='insufficient',semantic_coverage_unknown=True),audited(mode='filtered')]
        result=summarize_e1(rows,expected_pairs=[(r['query_id'],r['mode']) for r in rows],groups={'SYNTH-TOPIC':['SYNTH-A'],'EMPTY':[]})
        self.assertEqual(result['overall']['coverage_unknown_over_refusals'],1)
        self.assertEqual(result['paired']['state_agreement']['filtered_minus_unfiltered'],1)
        self.assertIsNone(result['by_group']['EMPTY']['state_agreement']['value'])
        rows[1]['contract_sha256']='4'*64
        result=summarize_e1(rows,expected_pairs=[(r['query_id'],r['mode']) for r in rows])
        self.assertEqual(result['paired']['state_agreement']['paired_query_denominator'],0)

    def test_unassessed_facts_cannot_disappear_from_primary_support_denominator(self):
        row=audited(reference_licensed_fact_ids=['f0','f1'],output_licensed_fact_ids=['f0','f1'],
            licensed_fact_audits=[dict(fact_id='f0',supported_by_reference_and_citations=True)])
        g=summarize_e1([row],expected_pairs=[('SYNTH-A','unfiltered')])['overall']
        self.assertEqual(g['licensed_fact_support_rate'],dict(numerator=1,denominator=2,value=None))
        self.assertEqual(g['licensed_fact_audit_coverage']['value'],0.5)
        self.assertEqual(g['assessed_fact_support_rate']['value'],1)

    def test_context_mismatch_duplicate_cases_and_failure_states_are_rejected(self):
        for changes in (dict(context_policy='quote_only'),dict(valid_citation_count=2),
                        dict(source_valid=False),
                        dict(output_execution='missing',output_state='insufficient')):
            with self.assertRaises(ValueError):summarize_e1([audited(**changes)],expected_pairs=[('SYNTH-A','unfiltered')])
        with self.assertRaises(ValueError):summarize_e1([audited(),audited()],expected_pairs=[('SYNTH-A','unfiltered')])

    def test_unassessed_citations_are_not_reported_as_valid_or_invalid(self):
        row=audited(citation_count=2,valid_citation_count=1,unassessed_citation_count=1)
        g=summarize_e1([row],expected_pairs=[('SYNTH-A','unfiltered')])['overall']
        self.assertEqual(g['citation_validity_rate'],dict(numerator=1,denominator=2,value=None))
        self.assertEqual(g['citation_audit_coverage']['value'],0.5)


if __name__=='__main__':
    unittest.main()
