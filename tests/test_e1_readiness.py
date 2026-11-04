"""E1 准备与人工协议的完全合成测试；不调用判断器、不生成真实参考。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from test_evidence_judge import batch, contract
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.e1_preparation import (
    PreparationError, SourceCollection, prepare_trusted_batch,
)
from data_analysis_agent.reviews.e1_review_protocol import review_context_view, validate_human_reference
from data_analysis_agent.reviews.query_contract import ScopeRef, validate_query_contract
from data_analysis_agent.reviews.retrieval_evaluation import run_binding


def fixture(quotes=('客服回复及时',), **batch_options):
    # Independently generated complete source rows first; frozen projection second.
    original=batch(quotes,**batch_options)
    records=[{**{k:v for k,v in h.items() if k not in ('query_id','mode','rank')},
              'published_at':'2031-01-01T00:00:00Z'} for h in original['candidates']]
    scope=dict(source='SYNTHETIC',dataset_version='synthetic-v1',product_ids=['SYNTHETIC-DEVICE-Z'],
               date_from='2031-01-01',date_to='2031-01-01')
    snapshot=content_sha256('synthetic-source-snapshot')
    profile=records[0]['label_profile_key'] if records else content_sha256('synthetic-profile')
    manifest=dict(version='synthetic-evaluation-v1',scope=scope,label_profile_key=profile,
        text_builder_version='quote-utf8-v1',embedding_space_id='SYNTHETIC-SPACE',inventory_snapshot_sha256=snapshot,
        queries=[dict(id=original['query_id'],text='[合成] 需要核对的事实与证据。',filters={})])
    inventory=dict(scope=scope,label_profile_key=profile,text_builder_version='quote-utf8-v1',
        embedding_space_id='SYNTHETIC-SPACE',snapshot_sha256=snapshot,
        stored_count=len(records),valid_count=len(records),invalid_count=0)
    omitted={'source','dataset_version','product_id','label_profile_key','text_builder_version',
             'review_content_sha256','file_sha256','line_number','query_id','mode'}
    hits=[{**{k:v for k,v in h.items() if k not in omitted},'cosine_distance':(i+1)/10}
          for i,h in enumerate(original['candidates'])]
    run=dict(manifest=manifest,manifest_sha256=content_sha256(manifest),scope_sha256=content_sha256(scope),
             inventory=inventory,authorized_snapshot_unchanged=True,
             searches=[dict(query_id=original['query_id'],mode=m,filters={},hits=deepcopy(hits))
                       for m in ('unfiltered','filtered')])
    run['run_id']=run_binding(run)
    ref=ScopeRef(run_id=run['run_id'],manifest_sha256=run['manifest_sha256'],scope_sha256=run['scope_sha256'])
    collection=dict(version='e1-source-collection-v1',data_kind='synthetic',basis='independent_source_snapshot',
        collector_id='SYNTHETIC-COLLECTOR',collection_receipt_sha256=content_sha256('synthetic-receipt'),
        scope_ref=ref.model_dump(mode='json'),inventory_snapshot_sha256=snapshot,records=records)
    return run,collection,ref


def source_pin(collection):
    return content_sha256(SourceCollection.model_validate_json(json.dumps(collection)).model_dump(mode='json'))


def prepare(run,collection,scope,**updates):
    data=json.loads(run) if isinstance(run,str) else run
    args=dict(expected_scope=scope,expected_source_collection_sha256=source_pin(collection),
        expected_receipt_sha256=content_sha256('synthetic-receipt'),expected_data_kind='synthetic',
        query_id=data['manifest']['queries'][0]['id'],mode='unfiltered')
    args.update(updates)
    return prepare_trusted_batch(run,collection,**args)


def review_fixture(quotes=('客服回复及时',),*,descriptions=None,policy='quote_full_review_veto',**batch_options):
    run,collection,scope=fixture(quotes,**batch_options)
    prepared=prepare(run,collection,scope)
    value=contract(descriptions or quotes)
    value['scope_ref']=scope.model_dump(mode='json')
    c=validate_query_contract(value,expected_scope=scope)
    b=prepared.trusted_batch
    record=dict(version='e1-human-reference-v1',origin='independent_human_reference',judge_output_seen=False,
        review_id='SYNTHETIC-REVIEW',revision=1,parent_reference_sha256=None,
        scope_ref=scope.model_dump(mode='json'),query_id=b.query_id,mode=b.mode,
        contract_version=c.version,contract_sha256=content_sha256(c.model_dump(mode='json')),contract_confirmed=False,
        required_fact_ids=[f.id for f in c.required_facts.items],independent_target_fact_ids=[],
        batch_sha256=prepared.batch_sha256,context_policy=policy,context_version='1',
        context_input_sha256=content_sha256(review_context_view(b,policy)),
        review_status='draft',human_confirmed=False,reference_state=None,supported_target_fact_ids=[],
        missing_required_fact_ids=[],fact_findings=[],relation_findings=[],context_observations=[],
        reviewer_decisions=[],disagreements=[],adjudication=None)
    return record,c,prepared,scope


def witness(hit,fid):
    names=('query_id','mode','rank','key','review_id','annotation_id','quote_start','quote_end',
           'effective_snapshot_sha256','embedding_input_sha256','evidence_ref')
    return dict(fact_id=fid,**{n:getattr(hit,n) for n in names})


def confirmed(record,prepared,*,direct=('f0',),independent=(),state='sufficient',missing=()):
    r=deepcopy(record)
    r.update(contract_confirmed=True,human_confirmed=True,review_status='confirmed',reference_state=state,
        supported_target_fact_ids=list(direct if state=='sufficient' else independent if state=='limited' else ()),
        independent_target_fact_ids=list(independent),missing_required_fact_ids=list(missing))
    for i,fid in enumerate(r['required_fact_ids']):
        r['fact_findings'].append(dict(fact_id=fid,support='direct' if fid in direct else 'not_supported',
            evidence_refs=[witness(prepared.trusted_batch.candidates[min(i,len(prepared.trusted_batch.candidates)-1)],fid)]
                          if fid in direct else [],rationale='合成人工说明，非判断器输出'))
    r['reviewer_decisions']=[dict(reviewer_id='SYNTHETIC-HUMAN-A',reference_state=state,
        supported_target_fact_ids=r['supported_target_fact_ids'],rationale='合成人工独立确认')]
    return r


class E1PreparationTests(unittest.TestCase):
    def test_pinned_source_projection_preserves_all_candidate_fields(self):
        run,sources,scope=fixture()
        before=deepcopy((run,sources))
        p=prepare(run,sources,scope)
        self.assertEqual(p.trusted_batch.candidates[0].review_content_sha256,sources['records'][0]['review_content_sha256'])
        self.assertEqual(p.batch_sha256,content_sha256(p.trusted_batch.model_dump(mode='json')))
        self.assertEqual((run,sources),before)
        self.assertEqual(p,prepare(json.dumps(run),sources,scope,query_id='SYNTH-JUDGE-Z'))

    def test_external_pins_and_data_kind_are_mandatory(self):
        run,sources,scope=fixture()
        for updates in (dict(expected_source_collection_sha256='0'*64),dict(expected_receipt_sha256='0'*64),
                        dict(expected_data_kind='frozen_query')):
            with self.assertRaises(PreparationError):prepare(run,sources,scope,**updates)

    def test_source_collection_cannot_be_a_copied_candidate_batch(self):
        run,sources,scope=fixture()
        bad=deepcopy(sources);bad['basis']='candidate_copy'
        with self.assertRaises(PreparationError):
            prepare_trusted_batch(run,bad,expected_scope=scope,expected_source_collection_sha256='0'*64,
                expected_receipt_sha256='0'*64,expected_data_kind='synthetic',query_id='SYNTH-JUDGE-Z',mode='unfiltered')

    def test_missing_record_is_blocked_without_filling(self):
        run,sources,scope=fixture();sources['records']=[]
        with self.assertRaisesRegex(PreparationError,'missing_independent_source_record'):prepare(run,sources,scope)

    def test_missing_content_hash_is_not_derived_from_quote_or_text(self):
        run,sources,scope=fixture();pin=source_pin(sources);del sources['records'][0]['review_content_sha256']
        with self.assertRaises(PreparationError):
            prepare_trusted_batch(run,sources,expected_scope=scope,expected_source_collection_sha256=pin,
                expected_receipt_sha256=content_sha256('synthetic-receipt'),expected_data_kind='synthetic',
                query_id='SYNTH-JUDGE-Z',mode='unfiltered')

    def test_duplicate_and_extra_records_are_rejected(self):
        run,sources,scope=fixture();sources['records']*=2
        with self.assertRaisesRegex(PreparationError,'duplicate_source_key'):prepare(run,sources,scope)
        run,sources,scope=fixture();extra=deepcopy(sources['records'][0]);extra['key']='rag-opinion-v1:'+'0'*64
        sources['records'].append(extra)
        with self.assertRaisesRegex(PreparationError,'unexpected_source_record'):prepare(run,sources,scope)

    def test_frozen_run_and_manifest_mutations_cannot_borrow_old_scope(self):
        for mutate in (lambda r:r['manifest'].update(version='changed'),
                       lambda r:r['searches'][0]['hits'].reverse(),
                       lambda r:r['searches'].pop()):
            run,sources,scope=fixture(('客服回复及时','设备制冷良好'));mutate(run)
            with self.assertRaises(PreparationError):prepare(run,sources,scope)

    def test_snapshot_scope_and_range_identity_are_checked(self):
        mutations=[lambda s:s.update(inventory_snapshot_sha256='0'*64),
            lambda s:s['scope_ref'].update(scope_sha256='0'*64),
            lambda s:s['records'][0].update(product_id='OTHER-SYNTHETIC')]
        for mutate in mutations:
            run,sources,scope=fixture();mutate(sources)
            with self.assertRaises(PreparationError):prepare(run,sources,scope)

    def test_source_date_requires_timezone_and_frozen_utc_range(self):
        for timestamp in ('2031-01-02T00:00:00Z','2031-01-01T00:00:00','not-a-date'):
            run,sources,scope=fixture();sources['records'][0]['published_at']=timestamp
            with self.assertRaises(PreparationError):prepare(run,sources,scope)
        run,sources,scope=fixture();sources['records'][0]['published_at']='2031-01-02T01:00:00+02:00'
        self.assertEqual(prepare(run,sources,scope).status,'validated_against_pinned_sources')

    def test_frozen_field_conflicts_and_corrupt_source_hash_fail(self):
        for mutate in (lambda s:s['records'][0].update(quote='其他合成文字'),
                       lambda s:s['records'][0].update(review_content_sha256='0'*64),
                       lambda s:s['records'][0].update(evidence_valid=False)):
            run,sources,scope=fixture();mutate(sources)
            with self.assertRaises(PreparationError):prepare(run,sources,scope)

    def test_mode_binding_uses_frozen_list_without_reranking(self):
        run,sources,scope=fixture()
        u=prepare(run,sources,scope);f=prepare(run,sources,scope,mode='filtered')
        self.assertEqual([h.key for h in u.trusted_batch.candidates],[h.key for h in f.trusted_batch.candidates])
        self.assertNotEqual(u.batch_sha256,f.batch_sha256)
        with self.assertRaises(PreparationError):prepare(run,sources,scope,query_id='OTHER')

    def test_empty_frozen_list_is_preserved(self):
        run,sources,scope=fixture(())
        self.assertFalse(prepare(run,sources,scope).trusted_batch.candidates)

    def test_preparation_is_offline_and_never_calls_judge_or_scorer(self):
        with patch('socket.socket.connect',side_effect=AssertionError('no network')), \
             patch('psycopg.connect',side_effect=AssertionError('no database')), \
             patch('data_analysis_agent.reviews.evidence_judge.judge_evidence',side_effect=AssertionError('no E1')), \
             patch('data_analysis_agent.reviews.retrieval_evaluation.score_run',side_effect=AssertionError('no label conversion')):
            run,sources,scope=fixture();prepare(run,sources,scope)


class HumanReferenceTests(unittest.TestCase):
    def validate(self,record,c,p,scope,**updates):
        args=dict(contract=c,trusted_batch=p.trusted_batch,expected_scope=scope,
            expected_batch_sha256=p.batch_sha256,expected_context_policy=record['context_policy'],for_evaluation=True)
        args.update(updates)
        return validate_human_reference(record,**args)

    def test_draft_has_no_generated_state_and_cannot_enter_evaluation(self):
        r,c,p,s=review_fixture()
        draft=self.validate(r,c,p,s,for_evaluation=False)
        self.assertIsNone(draft.reference_state);self.assertFalse(draft.human_confirmed)
        with self.assertRaisesRegex(PreparationError,'not_confirmed'):self.validate(r,c,p,s)

    def test_independently_confirmed_reference_roundtrip(self):
        r,c,p,s=review_fixture();r=confirmed(r,p)
        a=self.validate(r,c,p,s)
        self.assertEqual(a.supported_target_fact_ids,('f0',))
        self.assertEqual(a.fact_findings[0].evidence_refs[0].key,p.trusted_batch.candidates[0].key)
        self.assertEqual(a,validate_human_reference(a.model_dump_json(),contract=c,trusted_batch=p.trusted_batch,
            expected_scope=s,expected_batch_sha256=p.batch_sha256,expected_context_policy='quote_full_review_veto',for_evaluation=True))

    def test_limited_requires_direct_independent_target_not_half_core(self):
        r,c,p,s=review_fixture(descriptions=('客服回复及时','设备制热良好'))
        r=confirmed(r,p,direct=('f0',),independent=('f0',),state='limited',missing=('f1',))
        self.assertEqual(self.validate(r,c,p,s).reference_state,'limited')
        r['independent_target_fact_ids']=[]
        with self.assertRaisesRegex(PreparationError,'limited_inconsistent'):self.validate(r,c,p,s)

    def test_compound_partial_core_can_be_insufficient_without_relabeling_direct_fact(self):
        r,c,p,s=review_fixture(descriptions=('客服回复及时','设备制热良好'))
        r=confirmed(r,p,direct=('f0',),state='insufficient',missing=('f1',))
        out=self.validate(r,c,p,s)
        self.assertFalse(out.supported_target_fact_ids);self.assertEqual(out.fact_findings[0].support,'direct')

    def test_partial_reference_is_not_derived_from_grade_and_not_direct(self):
        r,c,p,s=review_fixture();r=confirmed(r,p,direct=(),state='insufficient',missing=('f0',))
        r['fact_findings'][0]['support']='partial'
        self.assertEqual(self.validate(r,c,p,s).reference_state,'insufficient')
        r['supported_target_fact_ids']=['f0']
        with self.assertRaisesRegex(PreparationError,'not_direct'):self.validate(r,c,p,s)

    def test_all_insufficient_cannot_override_complete_direct_support(self):
        r,c,p,s=review_fixture();r=confirmed(r,p,state='insufficient')
        with self.assertRaisesRegex(PreparationError,'insufficient_inconsistent'):self.validate(r,c,p,s)

    def test_unreviewed_facts_or_contract_cannot_be_confirmed(self):
        for mutate in (lambda r:r.update(contract_confirmed=False),lambda r:r.update(fact_findings=[]),
                       lambda r:r['fact_findings'][0].update(support='unknown'),lambda r:r.update(reviewer_decisions=[])):
            r,c,p,s=review_fixture();r=confirmed(r,p);mutate(r)
            with self.assertRaises(PreparationError):self.validate(r,c,p,s)

    def test_duplicate_or_external_fact_sources_are_rejected(self):
        for mutate in (lambda r:r['fact_findings'][0]['evidence_refs'].append(deepcopy(r['fact_findings'][0]['evidence_refs'][0])),
                       lambda r:r['fact_findings'][0]['evidence_refs'][0].update(key='EXTERNAL'),
                       lambda r:r['fact_findings'][0]['evidence_refs'][0].update(rank=9)):
            r,c,p,s=review_fixture();r=confirmed(r,p);mutate(r)
            with self.assertRaises(PreparationError):self.validate(r,c,p,s)

    def test_context_a_hides_original_b_exposes_it_without_borrowed_fact_support(self):
        r,c,p,s=review_fixture(originals=['客服回复及时；额外合成上下文'])
        a=review_context_view(p.trusted_batch,'quote_only');b=review_context_view(p.trusted_batch,'quote_full_review_veto')
        self.assertNotIn('review_text',a['batch']['candidates'][0]);self.assertIn('review_text',b['batch']['candidates'][0])
        self.assertNotEqual(content_sha256(a),content_sha256(b))
        r=confirmed(r,p);hit=p.trusted_batch.candidates[0]
        r['context_observations']=[dict(key=hit.key,quote_start=hit.quote_end+1,quote_end=len(hit.review_text),rationale='合成上下文否决记录')]
        self.validate(r,c,p,s)
        r['fact_findings'][0]['evidence_refs'][0]['quote_end']=len(hit.review_text)
        with self.assertRaisesRegex(PreparationError,'outside_quote'):self.validate(r,c,p,s)

    def test_quote_only_cannot_review_outside_quote_or_reuse_b_reference(self):
        r,c,p,s=review_fixture(policy='quote_only',originals=['客服回复及时；额外合成上下文'])
        r=confirmed(r,p);h=p.trusted_batch.candidates[0]
        r['context_observations']=[dict(key=h.key,quote_start=h.quote_end+1,quote_end=len(h.review_text),rationale='合成外部观察')]
        with self.assertRaisesRegex(PreparationError,'quote_only_context_outside_quote'):self.validate(r,c,p,s)
        r['context_observations']=[]
        with self.assertRaisesRegex(PreparationError,'context_mismatch'):
            self.validate(r,c,p,s,expected_context_policy='quote_full_review_veto')

    def test_identity_hashes_context_and_fact_ids_are_bound(self):
        mutations=[lambda r:r.update(query_id='OTHER'),lambda r:r.update(mode='filtered'),
            lambda r:r.update(batch_sha256='0'*64),lambda r:r.update(contract_sha256='0'*64),
            lambda r:r.update(context_input_sha256='0'*64),lambda r:r.update(required_fact_ids=['unknown'])]
        for mutate in mutations:
            r,c,p,s=review_fixture();r=confirmed(r,p);mutate(r)
            with self.assertRaises(PreparationError):self.validate(r,c,p,s)

    def test_judge_exposure_predictions_grades_and_other_versions_are_rejected(self):
        for key,value in [('judge_output_seen',True),('origin','judge_generated'),('grade',2),
                          ('judge_result',{}),('version','other-version')]:
            r,c,p,s=review_fixture();r[key]=value
            with self.assertRaises(PreparationError):self.validate(r,c,p,s,for_evaluation=False)

    def test_disagreement_needs_explicit_matching_adjudication(self):
        r,c,p,s=review_fixture();r=confirmed(r,p)
        r['reviewer_decisions'].append(dict(reviewer_id='SYNTHETIC-HUMAN-B',reference_state='insufficient',
            supported_target_fact_ids=[],rationale='合成分歧'))
        with self.assertRaisesRegex(PreparationError,'unresolved_human_disagreement'):self.validate(r,c,p,s)
        r.update(review_status='adjudicated',disagreements=['reference_state'],
            adjudication=dict(adjudicator_id='SYNTHETIC-HUMAN-C',reference_state='sufficient',
                              supported_target_fact_ids=['f0'],rationale='合成裁决说明'))
        self.assertEqual(self.validate(r,c,p,s).review_status,'adjudicated')
        r['adjudication']['supported_target_fact_ids']=[]
        with self.assertRaisesRegex(PreparationError,'adjudication_final_mismatch'):self.validate(r,c,p,s)

    def test_any_of_does_not_count_unused_alternatives_as_required_missing(self):
        r,c,p,s=review_fixture(descriptions=('客服回复及时','设备制热良好'))
        data=c.model_dump(mode='json');data['required_facts']['operator']='any_of'
        c=validate_query_contract(data,expected_scope=s);r['contract_sha256']=content_sha256(c.model_dump(mode='json'))
        r=confirmed(r,p,direct=('f0',),missing=())
        self.assertEqual(self.validate(r,c,p,s).missing_required_fact_ids,())

    def test_missing_relation_review_and_cross_review_cooccurrence_are_rejected(self):
        r,c,p,s=review_fixture(('客服回复及时','设备制冷良好'))
        data=c.model_dump(mode='json');data['required_facts']['relations']=[dict(kind='same_review',fact_ids=['f0','f1'])]
        c=validate_query_contract(data,expected_scope=s);r['contract_sha256']=content_sha256(c.model_dump(mode='json'))
        r=confirmed(r,p,direct=('f0','f1'))
        with self.assertRaisesRegex(PreparationError,'relations_unreviewed'):self.validate(r,c,p,s)
        r['relation_findings']=[dict(relation_id='relation_0',support='supported',
            evidence_keys=[h.key for h in p.trusted_batch.candidates],rationale='合成共现声明')]
        with self.assertRaisesRegex(PreparationError,'same_review_splicing'):self.validate(r,c,p,s)

    def test_protocol_validation_never_runs_judge_or_io(self):
        with patch('data_analysis_agent.reviews.evidence_judge.judge_evidence',side_effect=AssertionError('no E1')), \
             patch('socket.socket.connect',side_effect=AssertionError('no network')), \
             patch('psycopg.connect',side_effect=AssertionError('no database')):
            r,c,p,s=review_fixture();self.validate(confirmed(r,p),c,p,s)


if __name__=='__main__':unittest.main()
