"""T01–T16 synthetic-only acceptance. No private reviews, source seals or live I/O."""
from contextlib import ExitStack
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments import text_evidence_runtime_contract as compiler
from experiments import text_evidence_protocol as text_protocol
from experiments.text_evidence_locator import locate_fragment
from data_analysis_agent.agents import review_structured_evidence as b1
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_reported_facts import POLICY, assess
from data_analysis_agent.agents.review_task_contract import RuntimeQueryContract, matches_saved_task_contract
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticResult, SemanticQuery, SnapshotInfo, _scope, require_source_validated_result, SemanticRetrievalError,
)


SCOPE = dict(source='reviews_csv', dataset_version='SYNTHETIC', product_ids=['SYNTHETIC-PRODUCT'],
    date_from='2032-01-01', date_to='2032-01-31', timezone='UTC', model='SYNTHETIC-MODEL',
    base_url='https://synthetic.invalid/v1', prompt_version='SYNTHETIC-ANNOTATION')


def synthetic_records():
    """Invented confirmation metadata, explicitly not historical human records."""
    ref = {k:content_sha256(['SYNTHETIC',k]) for k in ('run_id','manifest_sha256','scope_sha256')}
    records = {}
    for qid, d in compiler.DEFINITIONS.items():
        contract = dict(version='rag-query-contract-v1', task_type='evidence', target_entity=d.entity,
            subtopic=d.subtopic, sentiment_intent='positive' if qid in ('Q05','Q11') else 'negative',
            required_facts=dict(operator='all_of', items=[dict(id=i,description=t,sentiment=s) for i,t,s,_ in d.facts],
                relations=[dict(kind=k,fact_ids=['f0','f1']) for k in ('same_review','before')] if qid=='Q18' else []),
            polarity_structure=dict(negations=[dict(fact_id=i,cue=c,scope=s) for i,c,s,_ in d.negations],contrast_fact_ids=[]),
            proposed_filters=dict(aspect=None,sentiment=None), aspect_candidates=[], uncertainty=[],clarification=[],scope_ref=ref)
        for name,index,_ in d.notes:
            assert index==len(contract[name])
            contract[name].append('SYNTHETIC NOTE '+qid+' '+name)
        records[qid] = dict(question=d.question,contract=contract,revision=1,event_id='SYNTHETIC-E-'+qid,
            rationale='SYNTHETIC authored goal fixture, no human adjudication or P0 source assertion: '+qid,
            independent_target_fact_ids=[])
    return records


def synthetic_origins():
    # Private test factory cannot load/publish human data, create P0 or execute a model.
    return compiler._make_origins(synthetic_records(), SCOPE, kind='synthetic_fixture')


def nodes(expression, path=()):
    yield path, expression
    for i, child in enumerate(expression['children']):
        yield from nodes(child,path+(i,))


def at(expression, path):
    for i in path:
        expression=expression['children'][i]
    return expression


def offline_runtime(target):
    # Only checks existing Runtime schema with an explicitly untrusted synthetic scope.
    value=target.runtime_projection.model_dump(mode='json')
    scope=compiler.RuntimeScope.model_validate_json(canonical_json(SCOPE)).model_dump(mode='json')
    return RuntimeQueryContract.model_validate_json(canonical_json(dict(value,scope=scope,scope_sha256=content_sha256(scope))))


def envelope(target, rows):
    """Direct test-only B1 envelope, never a result of P0 or a model-send entry."""
    candidates=[]
    for rank, row in enumerate(rows,1):
        quote, full, review_id = row
        start=full.index(quote)
        candidates.append(b1.Candidate(rank=rank,key='SYNTHETIC-KEY-'+str(rank),
            source_identity_sha256=content_sha256(['SYNTHETIC-IDENTITY',rank,full]),review_id=review_id,
            annotation_id=100+review_id,product_id='SYNTHETIC-PRODUCT',published_at='2032-01-02',
            quote=quote,quote_start=start,quote_end=start+len(quote),review_text=full,
            review_text_sha256=sha256(full.encode()).hexdigest(),
            effective_snapshot_sha256=content_sha256(['SYNTHETIC-SNAPSHOT',review_id]),
            review_content_sha256=content_sha256(['SYNTHETIC-CONTENT',full]),
            label_profile_key=content_sha256([SCOPE[k] for k in ('model','base_url','prompt_version')]),
            file_sha256=content_sha256('SYNTHETIC-FILE'),line_number=rank))
    contract=offline_runtime(target)
    return b1.JudgeInput(request_id='SYNTHETIC-REQUEST',contract=contract,
        contract_sha256=content_sha256(contract.model_dump(mode='json')),retrieval_sha256=content_sha256('NO-RETRIEVAL'),
        candidates=tuple(candidates))


def output(request, *, status='supported', relations=False):
    facts=[]
    for fact in request.contract.required_facts.items:
        for candidate in request.candidates:
            ref=dict(key=candidate.key,source_identity_sha256=candidate.source_identity_sha256,text=candidate.quote)
            facts.append(dict(fact_id=fact.id,key=candidate.key,status=status,
                support_refs=[ref] if status=='supported' else [],refutation_refs=[ref] if status=='refuted' else [],
                context_veto_refs=[],reason_code=dict(supported='direct_report',refuted='explicit_refutation',
                not_supported='no_direct_support',unknown='semantics_unknown')[status],rationale='SYNTHETIC mock, no real semantic score'))
    relation_rows=[]
    for i,relation in enumerate(request.contract.required_facts.relations):
        candidate=request.candidates[0]
        relation_rows.append(dict(relation_id='relation_'+str(i),status='supported' if relations else 'unknown',
            witnesses=[dict(fact_id=f,key=candidate.key) for f in relation.fact_ids] if relations else [],
            support_refs=[dict(key=candidate.key,source_identity_sha256=candidate.source_identity_sha256,text=candidate.quote)] if relations else [],
            refutation_refs=[],reason_code='relation_verified' if relations else 'relation_unknown',rationale='SYNTHETIC relation oracle'))
    return dict(version=text_protocol.OUTPUT_VERSION,request_id=request.request_id,
        input_sha256=content_sha256(request.model_dump(mode='json')),execution_status='completed',
        fact_judgments=facts,relation_judgments=relation_rows)


class RuntimeEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.guards=ExitStack()
        self.addCleanup(self.guards.close)
        for target in ('socket.socket.connect','socket.create_connection','psycopg.connect',
                       'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request',
                       'openai.OpenAI','data_analysis_agent.reviews.embedding.LocalBgeM3Embedder',
                       'data_analysis_agent.reviews.semantic_retrieval.SemanticRetrievalService.search'):
            self.guards.enter_context(patch(target,side_effect=AssertionError('synthetic_offline_only')))
        self.origins=synthetic_origins()

    def target(self,qid='Q02'):
        return compiler.compile_target(self.origins,compiler.propose_spec(self.origins,qid))

    def rejected(self,value,code=None):
        with self.assertRaises(compiler.EquivalenceError) as caught:
            compiler.compile_target(self.origins,value)
        if code:self.assertEqual(str(caught.exception),code)

    def test_t01_all_eight_specs_roundtrip_without_execution(self):
        expected_counts={'Q02':4,'Q04':3,'Q05':3,'Q06':4,'Q09':8,'Q11':6,'Q12':4,'Q18':5}
        for qid in compiler.DEFINITIONS:
            with self.subTest(qid=qid):
                target=self.target(qid);report=target.report()
                self.assertTrue(report['semantic_coverage_complete'])
                self.assertTrue(report['projection_lossless'])
                self.assertEqual(report['condition_count'],expected_counts[qid])
                self.assertFalse(report['p0_binding_present'])
                self.assertFalse(report['executable'])
                self.assertFalse(report['mapping_human_confirmed'])
                self.assertFalse(report['original_confirmation_verified'])
                self.assertEqual(target.runtime_projection.required_facts.operator,'all_of')
                self.assertEqual(len(target.spec.facts),2 if qid=='Q18' else 1)
                self.assertEqual(compiler.validate_projection(target,target.runtime_projection),target.runtime_projection)
                self.assertEqual(compiler.compile_target(self.origins,target.spec.model_dump_json()).report(),report)
                self.assertEqual(offline_runtime(target).support_status,'unsupported')
                for f in target.runtime_projection.required_facts.items:
                    self.assertLessEqual(len(f.description),1000)
                with self.assertRaisesRegex(compiler.EquivalenceError,'p0_binding_and_execution_not_implemented'):
                    compiler.require_executable(target)

    def test_t01_every_condition_deleted_and_replaced_is_rejected(self):
        count=0
        for qid in compiler.DEFINITIONS:
            raw=compiler.propose_spec(self.origins,qid).model_dump(mode='json')
            for fi,fact in enumerate(raw['facts']):
                for path,node in nodes(fact['expression']):
                    if node['kind']!='atom':continue
                    with self.subTest(qid=qid,fact=fi,condition=node['predicate']):
                        deleted=deepcopy(raw)
                        if not path:deleted['facts'].pop(fi)
                        else:at(deleted['facts'][fi]['expression'],path[:-1])['children'].pop(path[-1])
                        self.rejected(deleted)
                        substituted=deepcopy(raw)
                        at(substituted['facts'][fi]['expression'],path)['predicate']='damage' if node['predicate']!='damage' else 'main_device'
                        self.rejected(substituted)
                        count+=2
        self.assertEqual(count,74)

    def test_t01_every_condition_requires_original_confirmation_basis(self):
        count=0
        for qid in compiler.DEFINITIONS:
            raw=compiler.propose_spec(self.origins,qid).model_dump(mode='json')
            for i in range(len(raw['coverage'])):
                deleted=deepcopy(raw);deleted['coverage'].pop(i)
                self.rejected(deleted,'constraint_coverage_mismatch')
                wrong=deepcopy(raw);wrong['coverage'][i]['confirmation_sha256']='0'*64
                self.rejected(wrong,'constraint_coverage_mismatch')
                count+=2
        self.assertEqual(count,74)

    def test_t01_extra_conditions_duplicate_coverage_and_unknown_predicates_rejected(self):
        raw=self.target('Q11').spec.model_dump(mode='json')
        extra=deepcopy(raw);extra['facts'][0]['expression']['children'].append(dict(kind='atom',predicate='damage',children=[]))
        self.rejected(extra,'semantic_graph_mismatch')
        extra=deepcopy(raw);extra['coverage'].append(extra['coverage'][0]);self.rejected(extra,'constraint_coverage_mismatch')
        extra=deepcopy(raw);extra['facts'][0]['expression']['children'][0]['predicate']='arbitrary_new_rule';self.rejected(extra)

    def test_t02_roles_and_unknown_targets_do_not_get_guessed(self):
        for entity in ('installer','delivery_service','unknown'):
            raw=self.target().spec.model_dump(mode='json');raw['original_contract']['target_entity']=entity
            self.rejected(raw,'original_identity_or_contract_changed')
        quote='合成对象甲很客气。'
        request=envelope(self.target(),[(quote,'🧪'+quote,1)])
        for status in ('unknown','not_supported'):
            result=text_protocol.resolve_output(request,canonical_json(output(request,status=status)))
            self.assertEqual(result.outcome.execution_state,'validated_shadow')
            self.assertFalse(result.outcome.structural_goal_satisfied)
            self.assertEqual(result.outcome.permitted_target_fact_ids,())

    def test_t03_all_internal_connective_reversals_rejected(self):
        seen=set()
        for qid in compiler.DEFINITIONS:
            raw=self.target(qid).spec.model_dump(mode='json')
            for fi,fact in enumerate(raw['facts']):
                for path,node in nodes(fact['expression']):
                    if node['kind']=='atom':continue
                    changed=deepcopy(raw);at(changed['facts'][fi]['expression'],path)['kind']='or' if node['kind']=='and' else 'and'
                    self.rejected(changed,'semantic_graph_mismatch');seen.add((qid,node['kind']))
        self.assertTrue({('Q02','or'),('Q06','or'),('Q09','or')}<=seen)

    def test_t04_t07_critical_boundaries_have_independent_expected_predicates(self):
        required={'Q04':{'post_purchase_issue','continuous_no_followup','unresolved_not_unfollowed'},
            'Q05':{'order_to_receipt_fast','direct_receipt','no_installation_inference'},
            'Q06':{'prolonged_nonreceipt','clear_delivery_delay','receipt_not_dispatch'},
            'Q09':{'main_device','use_started','early_after_use','damage','malfunction','no_accessories','no_preuse_damage','no_generic_quality'},
            'Q11':{'hot_environment','startup','cooling_mode','room_cooling','rapid_cooling','same_episode'},
            'Q12':{'long_running','room_not_cool','cooling_mode','no_short_run_or_wind'}}
        for qid,predicates in required.items():
            actual={c.predicate for c in self.target(qid).spec.coverage}
            self.assertTrue(predicates<=actual)
        self.assertIn('不设固定',compiler.LABELS['early_after_use'])
        self.assertIn('不能仅由夏季',compiler.LABELS['hot_environment'])
        self.assertIn('主机',compiler.LABELS['main_device'])
        for qid in compiler.DEFINITIONS:
            raw=self.target(qid).spec.model_dump(mode='json')
            raw['facts'][0]['expression']['threshold_hours']=24
            self.rejected(raw)

    def test_t08_fact_identity_polarities_top_level_all_and_relations(self):
        for qid in compiler.DEFINITIONS:
            raw=self.target(qid).spec.model_dump(mode='json')
            for fi in range(len(raw['facts'])):
                for field,value in [('id','other_fact'),('sentiment','positive' if raw['facts'][fi]['original']['sentiment']!='positive' else 'negative')]:
                    changed=deepcopy(raw);changed['facts'][fi]['original'][field]=value
                    self.rejected(changed,'semantic_graph_mismatch')
            changed=deepcopy(raw);changed['original_contract']['required_facts']['operator']='any_of'
            self.rejected(changed)
        raw=self.target('Q18').spec.model_dump(mode='json')
        self.assertEqual([(f.original.id,f.original.sentiment) for f in self.target('Q18').spec.facts],[('f0','neutral'),('f1','negative')])
        for ri in range(2):
            changed=deepcopy(raw);changed['original_contract']['required_facts']['relations'].pop(ri)
            self.rejected(changed)
        changed=deepcopy(raw);changed['original_contract']['required_facts']['relations'][1]['fact_ids'].reverse()
        self.rejected(changed)
        changed=deepcopy(raw);changed['facts'].reverse();self.rejected(changed)

    def test_t08_scoped_negation_not_global_negative_label(self):
        raw=self.target('Q06').spec.model_dump(mode='json')
        self.assertEqual(raw['scoped_negations'][0]['predicate'],'prolonged_nonreceipt')
        raw['scoped_negations'][0]['predicate']='clear_delivery_delay'
        self.rejected(raw,'semantic_graph_mismatch')
        for qid in ('Q04','Q06','Q12','Q18'):
            raw=self.target(qid).spec.model_dump(mode='json');raw['scoped_negations']=[]
            self.rejected(raw,'semantic_graph_mismatch')

    def test_t09_b2_same_review_before_and_partial_fact_coverage(self):
        target=self.target('Q18');quote='合成买家甲：货已到达，等了好久仍没人安装。'
        request=envelope(target,[(quote,'🧪'+quote,1)])
        result=text_protocol.resolve_output(request,canonical_json(output(request,relations=True)))
        self.assertEqual(result.outcome.execution_state,'validated_shadow')
        self.assertTrue(result.outcome.structural_goal_satisfied)
        self.assertFalse(result.outcome.semantic_verified)
        raw=output(request,relations=True);raw['relation_judgments'][1]['support_refs']=[]
        result=text_protocol.resolve_output(request,canonical_json(raw))
        self.assertEqual(result.outcome.reason_codes,('temporal_evidence_missing',))
        raw=output(request,relations=True);row=raw['fact_judgments'][1]
        row.update(status='not_supported',support_refs=[],reason_code='no_direct_support')
        raw['relation_judgments']=output(request)['relation_judgments']
        result=text_protocol.resolve_output(request,canonical_json(raw))
        self.assertFalse(result.outcome.structural_goal_satisfied)
        request=envelope(target,[(quote,quote,1),(quote,quote,2)])
        raw=output(request,relations=True)
        for relation in raw['relation_judgments']:
            relation['witnesses'][1]['key']=request.candidates[1].key
        result=text_protocol.resolve_output(request,canonical_json(raw))
        self.assertEqual(result.outcome.reason_codes,('cross_review_relation',))

    def test_t10_legal_quote_does_not_prove_semantics_or_event_order(self):
        target=self.target('Q05');quote='合成安装员说当天安装完毕。'
        request=envelope(target,[(quote,quote,1)])
        result=text_protocol.resolve_output(request,canonical_json(output(request)))
        self.assertEqual(result.outcome.execution_state,'validated_shadow')
        self.assertFalse(result.outcome.semantic_verified)
        toy_events=dict(actual_receipt_reported=False,installation_completed=True)
        self.assertFalse(toy_events['actual_receipt_reported'])
        quote='合成叙述先写安装等待，随后说明货物之前已经到达。'
        request=envelope(self.target('Q18'),[(quote,quote,1)])
        result=text_protocol.resolve_output(request,canonical_json(output(request,relations=True)))
        self.assertEqual(result.outcome.execution_state,'validated_shadow')
        self.assertFalse(result.outcome.semantic_verified)
        self.assertLess(quote.index('安装等待'),quote.index('到达'))
        self.assertEqual(result.outcome.permitted_target_fact_ids,())

    def test_t11_resolved_notes_kept_and_unresolved_or_invented_resolution_rejected(self):
        for qid in ('Q05','Q09','Q11','Q12','Q18'):
            raw=self.target(qid).spec.model_dump(mode='json')
            self.assertTrue(raw['resolutions'])
            for i in range(len(raw['resolutions'])):
                unresolved=deepcopy(raw);unresolved['resolutions'][i]['state']='unresolved'
                self.rejected(unresolved,'unresolved_clarification')
                for key,value in [('confirmation_pointer','/invented'),('confirmation_sha256','0'*64),('original_text','invented note')]:
                    changed=deepcopy(raw);changed['resolutions'][i][key]=value
                    self.rejected(changed,'resolution_without_original_basis')
            raw['resolutions']=[];self.rejected(raw,'resolution_without_original_basis')
        raw=self.target().spec.model_dump(mode='json');raw['reviewed']=True;self.rejected(raw)

    def test_t12_scope_profile_and_origin_identity_drift_rejected(self):
        for field,value in [('source','amazon_reviews'),('dataset_version','OTHER'),('product_ids',['OTHER']),
            ('date_from','2032-01-02'),('date_to','2032-01-30'),('as_of','2032-02-01T00:00:00Z'),
            ('model','OTHER'),('base_url','https://other.invalid/v1'),('prompt_version','OTHER')]:
            raw=self.target().spec.model_dump(mode='json');raw['historical_scope_hint'][field]=value
            self.rejected(raw)
        for field,value in [('label_profile_key','0'*64),('original_question','different question')]:
            raw=self.target().spec.model_dump(mode='json');raw[field]=value;self.rejected(raw)
        for field,value in [('query_id','Q15'),('revision',2),('event_id','invented'),('contract_sha256','0'*64),('confirmation_sha256','0'*64)]:
            raw=self.target().spec.model_dump(mode='json');raw['origin'][field]=value;self.rejected(raw)
        raw=self.target().spec.model_dump(mode='json');raw['original_contract']['scope_ref']['run_id']='0'*64;self.rejected(raw)
        raw=self.target().spec.model_dump(mode='json');raw['historical_scope_hint'].update(baseline_from='2031-01-01',baseline_to='2031-01-31');self.rejected(raw)

    def test_t13_checked_objects_json_hashes_and_fake_seals_do_not_create_trust(self):
        target=self.target()
        with self.assertRaises(TypeError):compiler.CheckedExperimentTarget()
        for value in (target.report(),target._spec_json,dict(issuer='trusted',sha256=content_sha256(target._spec_json)),
                      SimpleNamespace(_source_validation=True)):
            with self.assertRaises(compiler.EquivalenceError):compiler.require_checked(value)
        changed=copy(target);object.__setattr__(changed,'_projection_json','{}')
        with self.assertRaisesRegex(compiler.EquivalenceError,'checked_target_changed'):compiler.require_checked(changed)
        with self.assertRaises(FrozenInstanceError):target._digest='0'*64
        with self.assertRaises(compiler.EquivalenceError):compiler.propose_spec({'trusted':True},'Q02')
        with self.assertRaisesRegex(compiler.EquivalenceError,'pinned_loader_required'):
            compiler._make_origins(synthetic_records(),SCOPE,kind='historical_pinned')
        for key,value in [('trusted_contract_sha256','0'*64),('source_identity_sha256','0'*64),('_source_validation',True)]:
            raw=target.spec.model_dump(mode='json');raw[key]=value;self.rejected(raw)
        unsealed=SemanticResult(SemanticQuery(question=target.spec.original_question),_scope(SCOPE),
            SnapshotInfo('SYNTHETIC-UNVERIFIED','2032-02-01T00:00:00Z'),())
        for value in (unsealed,replace(unsealed,_source_validation=SimpleNamespace(issuer='fake',result_sha256='0'*64))):
            with self.assertRaises(SemanticRetrievalError):require_source_validated_result(value)
        runtime=offline_runtime(target)
        with self.assertRaisesRegex(b1.StructuredEvidenceError,'not_eligible'):
            b1.prepare_input(runtime,unsealed,trusted_contract_sha256=content_sha256(runtime.model_dump(mode='json')),request_id='SYNTHETIC')
        # Deliberately forged eligibility still cannot mint P0 using a self-reported hash.
        forged=runtime.model_copy(update=dict(support_status='supported',unsupported_reason=None,uncertainty=()))
        with self.assertRaises(SemanticRetrievalError):
            b1.prepare_input(forged,unsealed,trusted_contract_sha256=content_sha256(forged.model_dump(mode='json')),request_id='SYNTHETIC')

    def test_t13_original_loader_pin_failure_never_creates_a_checked_origin(self):
        with patch.object(Path,'read_bytes',return_value=b'{}'):
            with self.assertRaisesRegex(compiler.EquivalenceError,'original_file_pin_mismatch'):
                compiler.load_confirmed_origins()
        records=synthetic_records();records['Q02']['revision']=2
        with self.assertRaisesRegex(compiler.EquivalenceError,'origin_identity_mismatch'):
            compiler._make_origins(records,SCOPE,kind='synthetic_fixture')
        records=synthetic_records();records['Q02']['independent_target_fact_ids']=['f0']
        with self.assertRaisesRegex(compiler.EquivalenceError,'origin_permission_or_filter_mismatch'):
            compiler._make_origins(records,SCOPE,kind='synthetic_fixture')

    def test_t14_quote_context_identity_and_ambiguity_fail_closed(self):
        quote='合成客服不耐烦。';veto='合成作者明确撤回该投诉。';full='🧪'+quote+veto
        request=envelope(self.target(),[(quote,full,1)])
        c=request.candidates[0]
        raw=output(request);raw['fact_judgments'][0]['support_refs'][0]['text']=veto
        self.assertEqual(text_protocol.resolve_output(request,canonical_json(raw)).outcome.execution_state,'output_invalid')
        raw=output(request,status='refuted');row=raw['fact_judgments'][0]
        row.update(refutation_refs=[],context_veto_refs=[dict(key=c.key,source_identity_sha256=c.source_identity_sha256,text=veto)],reason_code='context_veto')
        self.assertEqual(text_protocol.resolve_output(request,canonical_json(raw)).outcome.execution_state,'validated_shadow')
        raw['fact_judgments'][0]['context_veto_refs'][0]['source_identity_sha256']='0'*64
        self.assertEqual(text_protocol.resolve_output(request,canonical_json(raw)).failure_stage,'source_binding')
        for field,value in [('context_can_add_support',True),('support_range','full_text'),('refutation_range','full_text'),('context_veto_range','other_review')]:
            changed=self.target().spec.model_dump(mode='json');changed[field]=value;self.rejected(changed)
        quote='测试甲测试甲';request=envelope(self.target(),[(quote,'🧪'+quote,1)])
        result=locate_fragment(request,key=request.candidates[0].key,source_identity_sha256=request.candidates[0].source_identity_sha256,
            reference_type='support_refs',text='测试甲')
        self.assertEqual(result['status'],'ambiguous')
        bad=request.candidates[0].model_copy(update=dict(review_text_sha256='0'*64))
        broken=request.model_copy(update=dict(candidates=(bad,)))
        result=locate_fragment(broken,key=bad.key,source_identity_sha256=bad.source_identity_sha256,reference_type='support_refs',text=quote)
        self.assertNotEqual(result['status'],'unique')

    def test_t14_publication_and_independent_permission_forbidden(self):
        for qid in compiler.DEFINITIONS:
            for field,value in [('publication_allowed',True),('semantic_verified',True),('independent_target_fact_ids',['f0'])]:
                raw=self.target(qid).spec.model_dump(mode='json');raw[field]=value;self.rejected(raw)
        for fields in (dict(publication_allowed=True),dict(semantic_verified=True),dict(permitted_target_fact_ids=('f0',))):
            with self.assertRaises(ValueError):b1.ShadowOutcome('validated_shadow',(),**fields)

    def test_t15_formal_routes_v2_and_saved_runtime_compatibility_unchanged(self):
        expected={q:('unsupported','statistics','unsupported') for q in compiler.DEFINITIONS}
        expected.update(Q06=('fact_search','evidence','retrieval_only'),Q18=('opinion_summary','evidence','supported'))
        for qid,d in compiler.DEFINITIONS.items():
            old=build_query_plan(d.question,deepcopy(SCOPE)).task_contract
            self.assertEqual((old.operation,old.task_type,old.support_status),expected[qid])
            self.assertEqual(old.required_facts.items,())
            self.target(qid)
            self.assertEqual(build_query_plan(d.question,deepcopy(SCOPE)).task_contract,old)
        for question,status in [('有没有制冷效果好的反馈？','supported'),('有没有降温不给力的反馈？','retrieval_only'),
            ('有没有制冷好或物流很快的反馈？','unsupported')]:
            contract=build_query_plan(question,deepcopy(SCOPE)).task_contract
            self.assertEqual(contract.support_status,status)
            self.assertTrue(matches_saved_task_contract(contract.model_dump(mode='json'),contract))
        self.assertEqual(POLICY,'reported_experience_v2')
        self.assertEqual(assess('有没有制冷效果好的反馈？',[])['policy'],POLICY)
        for path in Path('src').rglob('*.py'):
            self.assertNotIn('text_evidence_runtime_contract',path.read_text(encoding='utf-8'))

    def test_t16_description_overflow_extra_fields_and_lossy_projection_rejected(self):
        target=self.target();fact=target.spec.facts[0]
        long=fact.model_copy(update=dict(original=fact.original.model_copy(update=dict(description='测'*1000))))
        with self.assertRaisesRegex(compiler.EquivalenceError,'description_too_long'):compiler.render_fact(long)
        for field,value in [('required_facts',dict(operator='any_of',items=[],relations=[])),('support_status','supported'),
            ('scope',SCOPE),('trusted_contract_sha256','0'*64)]:
            raw=target.runtime_projection.model_dump(mode='json');raw[field]=value
            with self.assertRaises(compiler.EquivalenceError):compiler.validate_projection(target,raw)
        raw=target.runtime_projection.model_dump(mode='json');raw['required_facts']['items'][0]['description']='测'*1001
        with self.assertRaises(compiler.EquivalenceError):compiler.validate_projection(target,raw)
        raw=target.runtime_projection.model_dump(mode='json');raw['required_facts']['items'][0]['description']='客服态度差'
        with self.assertRaises(compiler.EquivalenceError):compiler.validate_projection(target,raw)
        self.assertFalse(hasattr(compiler,'model_payload'))
        self.assertFalse(hasattr(compiler,'_SOURCE_VALIDATOR'))

    def test_t16_codec_negation_conflicts_and_duplicate_json_are_rejected(self):
        target=self.target('Q04');fact=target.spec.facts[0]
        negation=target.spec.scoped_negations[0].model_copy(update=dict(predicate='damage'))
        with self.assertRaisesRegex(compiler.EquivalenceError,'invalid_scoped_negation'):
            compiler.render_fact(fact,(negation,))
        description=target.runtime_projection.required_facts.items[0].description
        with self.assertRaises(compiler.EquivalenceError):compiler.decode_description(description+' trailing')
        with self.assertRaises(compiler.EquivalenceError):
            compiler.decode_description('P5实验语义v1 {"原事实":"a","原事实":"b","必要语义":{},"否定作用域":[]}')


if __name__ == '__main__':
    unittest.main()
