"""New synthetic cases only. No provider, historical fixture/run, DB or Embedding I/O."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import shadow_text_evidence_prototype as experiment
from shadow_reference_locator_prototype import locate_fragment
from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from test_review_structured_shadow import verified
import test_p5b1_structured_evidence_design as b1

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = 'OFFLINE-TEXT-ONLY-NEW-CASES'
CASES = [
    ('F201', '🙂测试机墨竹：我咨询发票，客服当场讥笑我。', '我咨询发票，客服当场讥笑我。', 'supported', None),
    ('F202', '测试机墨竹：客服主动解释，我觉得对方很礼貌。', '客服主动解释，我觉得对方很礼貌。', 'not_supported', None),
    ('F203', '墨竹更正：先前说客服羞辱我是我写错了，那项指控我收回。', '先前说客服羞辱我是我写错了，那项指控我收回。', 'refuted', None),
    ('F204', '墨竹初稿：客服挂断了我的电话。核对号码后发现是快递站，我收回对客服的那段批评。', '客服挂断了我的电话。', 'refuted', '核对号码后发现是快递站，我收回对客服的那段批评。'),
    ('F205', '墨竹记录：那个人一直挖苦我。来电者未报单位，我不知道是客服还是运输站。', '那个人一直挖苦我。', 'unknown', None),
]


def text_ref(candidate, text=None):
    return dict(key=candidate.key, source_identity_sha256=candidate.source_identity_sha256,
                text=candidate.quote if text is None else text)


def text_reply(request, states=None, vetoes=None):
    states = states or ['supported']*len(request.candidates)
    vetoes = vetoes or [None]*len(request.candidates)
    rows = []
    for fact in request.contract.required_facts.items:
        for candidate, status, veto in zip(request.candidates, states, vetoes):
            rows.append(dict(fact_id=fact.id, key=candidate.key, status=status,
                support_refs=[text_ref(candidate)] if status=='supported' else [],
                refutation_refs=[text_ref(candidate)] if status=='refuted' and not veto else [],
                context_veto_refs=[text_ref(candidate,veto)] if veto else [],
                reason_code='context_veto' if veto else dict(supported='direct_report',refuted='explicit_refutation',
                    not_supported='no_direct_support',unknown='semantics_unknown')[status],
                rationale='全新合成案例的开发者预置判断，不是模型推理或正式许可'))
    return dict(version=experiment.OUTPUT_VERSION, request_id=request.request_id,
        input_sha256=content_sha256(request.model_dump(mode='json')), execution_status='completed',
        fact_judgments=rows, relation_judgments=[dict(relation_id=f'relation_{i}',status='unknown',witnesses=[],
            support_refs=[],refutation_refs=[],reason_code='relation_unknown',rationale='预置未知关系')
            for i,_ in enumerate(request.contract.required_facts.relations)])


class TextEvidenceTests(unittest.TestCase):
    def setUp(self):
        for target in ('psycopg.connect','socket.create_connection','socket.socket.connect',
                       'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request',
                       'openai.OpenAI','subprocess.Popen',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('offline prototype: real I/O forbidden')))

    def prepare(self, rows=None, contract=None):
        rows = rows or [('新造测试件：一直没有凉风。','一直没有凉风。',301)]
        contract,result,events = verified(contract,rows)
        request = protocol.prepare_input(contract,result,
            trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=REQUEST_ID)
        return contract,result,request,events

    def run_output(self, context, output):
        contract,result,_,_ = context
        run = experiment.evaluate(contract,result,canonical_json(output),
            trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=REQUEST_ID)
        self.assertFalse(run.outcome.publication_allowed)
        self.assertFalse(run.outcome.semantic_verified)
        self.assertEqual(run.outcome.permitted_target_fact_ids,())
        self.assertEqual(run.evidence_status,'offline_experiment_only')
        if run.outcome.execution_state!='validated_shadow':
            self.assertIsNone(run.resolved_output)
        return run

    def test_schema_is_new_version_without_coordinate_fields_and_b1_is_unchanged(self):
        saved = ROOT/'docs/schemas/p5-text-evidence-output-experiment-v1.schema.json'
        self.assertEqual(experiment.output_schema(),strict_json_loads(saved.read_text(encoding='utf-8')))
        ref = experiment.output_schema()['$defs']['TextReference']
        self.assertEqual(set(ref['properties']),{'key','source_identity_sha256','text'})
        self.assertFalse(ref['additionalProperties'])
        self.assertIn('version',experiment.output_schema()['required'])
        for name,schema in protocol.schemas().items():
            path = ROOT/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json'
            self.assertEqual(schema,strict_json_loads(path.read_text(encoding='utf-8')))

    def test_new_five_case_semantic_expectations_and_exact_references(self):
        context = self.prepare([(text,quote,int(cid[1:])) for cid,text,quote,_,_ in CASES],b1.contract('有无客服态度差的评论？'))
        request = context[2]
        output = text_reply(request,[c[3] for c in CASES],[c[4] for c in CASES])
        run = self.run_output(context,output)
        self.assertEqual(run.outcome.execution_state,'validated_shadow')
        self.assertEqual([r.status for r in run.resolved_output.fact_judgments],[c[3] for c in CASES])
        for row,candidate in zip(run.resolved_output.fact_judgments,request.candidates):
            for name in ('support_refs','refutation_refs','context_veto_refs'):
                for ref in getattr(row,name):
                    self.assertEqual(candidate.review_text[ref.start:ref.end],ref.text)
                    self.assertEqual(ref.end,ref.start+len(ref.text))
                    self.assertEqual(ref.source_identity_sha256,candidate.source_identity_sha256)
                    if name!='context_veto_refs':
                        self.assertTrue(candidate.quote_start<=ref.start<ref.end<=candidate.quote_end)
        withdrawal = run.resolved_output.fact_judgments[2]
        self.assertTrue(withdrawal.refutation_refs)
        veto = run.resolved_output.fact_judgments[3]
        self.assertEqual(veto.context_veto_refs[0].text,CASES[3][4])
        self.assertGreaterEqual(veto.context_veto_refs[0].start,request.candidates[3].quote_end)

    def test_legacy_outputs_and_coordinate_fields_are_rejected_not_repaired(self):
        context = self.prepare()
        request = context[2]
        for change in ('old_version','no_version','coordinates','licence'):
            output = text_reply(request)
            if change=='old_version': output['version'] = protocol.OUTPUT_VERSION
            if change=='no_version': output.pop('version')
            if change=='coordinates': output['fact_judgments'][0]['support_refs'][0].update(start=999,end=1001)
            if change=='licence': output['publication_allowed'] = True
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')
        old = b1.response(request)
        old['fact_judgments'][0]['support_refs'][0]['end'] = 999
        original = deepcopy(old)
        self.assertEqual(protocol.audit_output(request,canonical_json(old)).reason_codes,('citation_span_mismatch',))
        self.assertEqual(self.run_output(context,old).outcome.execution_state,'output_invalid')
        self.assertEqual(old,original)
        self.assertEqual(protocol.audit_output(request,canonical_json(old)).reason_codes,('citation_span_mismatch',))

    def test_request_and_input_digest_must_match_independently(self):
        context = self.prepare()
        for field,value in (('request_id','other'),('input_sha256','0'*64)):
            output = text_reply(context[2]) | {field:value}
            self.assertEqual(self.run_output(context,output).outcome.reason_codes,('input_binding_mismatch',))

    def test_source_identity_and_other_candidate_borrowing_fail_closed(self):
        context = self.prepare([('甲：没有凉风。','没有凉风。',311),('乙：一直很凉快。','一直很凉快。',312)])
        for change in ('key','identity','other_candidate'):
            output = text_reply(context[2])
            refs = output['fact_judgments'][0]['support_refs']
            if change=='key': refs[0]['key'] = 'unknown-key'
            if change=='identity': refs[0]['source_identity_sha256'] = '0'*64
            if change=='other_candidate': refs[0] = text_ref(context[2].candidates[1])
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')

    def test_p0_seal_hash_and_source_tampering_are_rejected_before_resolution(self):
        contract,result,request,events = self.prepare()
        row = result.evidence[0]
        variants = [replace(result,_source_validation=None),replace(result,_source_validation=True),
            replace(result,evidence=(replace(row,review_text=row.review_text+'篡改'),)),
            replace(result,evidence=(replace(row,review_text_sha256='0'*64),)),
            replace(result,evidence=(replace(row,document=replace(row.document,quote_end=999)),)),
            replace(result,evidence=(replace(row,document=replace(row.document,annotation_id=999)),)),
            replace(result,evidence=(replace(row,document=replace(row.document,file_sha256='0'*64)),))]
        with patch.object(experiment,'locate_fragment',side_effect=AssertionError('invalid sources must not reach locator')):
            for value in variants:
                run = self.run_output((contract,value,request,events),text_reply(request))
                self.assertEqual(run.outcome.execution_state,'input_invalid')

    def test_fixed_target_digest_and_unsupported_targets_do_not_self_authorize(self):
        contract,result,request,_ = self.prepare()
        run = experiment.evaluate(contract,result,canonical_json(text_reply(request)),
            trusted_contract_sha256='0'*64,request_id=REQUEST_ID)
        self.assertEqual(run.outcome.reason_codes,('target_binding_mismatch',))
        unsupported = b1.contract('为什么降温慢，是否由电机故障导致？')
        context = (unsupported,result,request,[])
        self.assertEqual(self.run_output(context,text_reply(request)).outcome.execution_state,'input_invalid')

    def test_locator_rechecks_full_hash_quote_bounds_and_text(self):
        request = self.prepare()[2]
        candidate = request.candidates[0]
        for change in ({'review_text_sha256':'0'*64},{'quote_end':999},{'quote':'not-original'},
                       {'review_text':candidate.review_text+'extra'}):
            mutated = request.model_copy(update={'candidates':(candidate.model_copy(update=change),)})
            result = locate_fragment(mutated,**text_ref(candidate),reference_type='support_refs')
            self.assertEqual(result['status'],'source_text_invalid')
            self.assertEqual(result['locations'],[])

    def test_support_and_refutation_cannot_search_outside_quote(self):
        context = self.prepare([('前文说外壳完整。补充：始终没有凉风。','前文说外壳完整。',321)])
        for field,status,reason in (('support_refs','supported','direct_report'),('refutation_refs','refuted','explicit_refutation')):
            output = text_reply(context[2])
            row = output['fact_judgments'][0]
            row.update(status=status,support_refs=[],refutation_refs=[],reason_code=reason)
            row[field] = [text_ref(context[2].candidates[0],'始终没有凉风。')]
            self.assertEqual(self.run_output(context,output).outcome.reason_codes,('text_reference_missing',))

    def test_context_veto_is_full_review_only_and_never_crosses_reviews(self):
        context = self.prepare([('记录：没有凉风。更新：原话写错了，我撤回。','没有凉风。',331),
                                ('另一件：隔壁评论才有的撤回原文。','隔壁评论才有的撤回原文。',332)])
        output = text_reply(context[2],['refuted','unknown'],['更新：原话写错了，我撤回。',None])
        self.assertEqual(self.run_output(context,output).outcome.execution_state,'validated_shadow')
        output['fact_judgments'][0]['context_veto_refs'][0]['text'] = '隔壁评论才有的撤回原文。'
        self.assertEqual(self.run_output(context,output).outcome.reason_codes,('text_reference_missing',))

    def test_repeated_and_overlapping_matches_are_ambiguous(self):
        for text,quote,selected in (('新样机：不凉不凉。','不凉不凉。','不凉'),('哈哈哈','哈哈哈','哈哈')):
            context = self.prepare([(text,quote,341)])
            output = text_reply(context[2])
            output['fact_judgments'][0]['support_refs'][0]['text'] = selected
            self.assertEqual(self.run_output(context,output).outcome.reason_codes,('text_reference_ambiguous',))

    def test_quote_can_disambiguate_support_but_not_full_review_veto(self):
        context = self.prepare([('初记：不凉。后记：不凉。','初记：不凉。',351)])
        output = text_reply(context[2])
        row = output['fact_judgments'][0]
        row['support_refs'] = [text_ref(context[2].candidates[0],'不凉。')]
        run = self.run_output(context,output)
        self.assertEqual(run.outcome.execution_state,'validated_shadow')
        self.assertEqual(run.resolved_output.fact_judgments[0].support_refs[0].start,3)
        row.update(status='refuted',support_refs=[],reason_code='context_veto',
                   context_veto_refs=[text_ref(context[2].candidates[0],'不凉。')])
        self.assertEqual(self.run_output(context,output).outcome.reason_codes,('text_reference_ambiguous',))

    def test_no_match_empty_normalized_or_translated_text_is_rejected(self):
        context = self.prepare([('样件：e\u0301 不凉。','e\u0301 不凉。',361)])
        for text in ('','é 不凉。','e\u0301不凉。','not cool','不存在的引文'):
            output = text_reply(context[2])
            output['fact_judgments'][0]['support_refs'][0]['text'] = text
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')

    def test_code_points_and_exact_whitespace_are_preserved(self):
        context = self.prepare([('🙂新件： 仍无凉风。',' 仍无凉风。',371)])
        run = self.run_output(context,text_reply(context[2]))
        ref = run.resolved_output.fact_judgments[0].support_refs[0]
        self.assertEqual((ref.start,ref.end),(4,10))
        self.assertEqual(ref.text,' 仍无凉风。')
        self.assertNotEqual(ref.start,len('🙂新件：'.encode('utf-16-le'))//2)
        self.assertNotEqual(ref.start,len('🙂新件：'.encode('utf-8')))

    def test_duplicate_references_and_missing_basis_still_fail_b2(self):
        context = self.prepare()
        for status in ('supported','refuted','unknown'):
            output = text_reply(context[2],[status])
            row = output['fact_judgments'][0]
            if status=='supported': row['support_refs'] *= 2
            if status=='refuted': row['refutation_refs'] = []
            if status=='unknown': row['support_refs'] = [text_ref(context[2].candidates[0])]
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')

    def test_fact_matrix_unknown_ids_missing_and_duplicate_rows_are_rejected(self):
        context = self.prepare()
        for change in ('missing','duplicate','unknown_fact','unknown_candidate'):
            output = text_reply(context[2])
            if change=='missing': output['fact_judgments'] = []
            if change=='duplicate': output['fact_judgments'] *= 2
            if change=='unknown_fact': output['fact_judgments'][0]['fact_id'] = 'invented_fact'
            if change=='unknown_candidate': output['fact_judgments'][0]['key'] = 'invented_candidate'
            self.assertEqual(self.run_output(context,output).outcome.reason_codes,('fact_matrix_mismatch',))

    def relation_case(self, kind, rows=None):
        context = self.prepare(rows or [('新造件：外壳先开裂，接着按键失灵。','外壳先开裂，接着按键失灵。',381)],
                               b1.authored_compound(relation=kind))
        request = context[2]
        output = text_reply(request)
        output['relation_judgments'][0].update(status='supported',reason_code='relation_verified',
            witnesses=[dict(fact_id=f.id,key=request.candidates[0].key) for f in request.contract.required_facts.items],
            support_refs=[text_ref(request.candidates[0])] if kind=='before' else [])
        return context,output

    def test_same_review_and_before_relations_keep_original_witness_checks(self):
        for kind in ('same_review','before'):
            context,output = self.relation_case(kind)
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'validated_shadow')
            if kind=='before':
                output['relation_judgments'][0]['support_refs'] = []
                self.assertEqual(self.run_output(context,output).outcome.reason_codes,('temporal_evidence_missing',))

    def test_cross_review_missing_and_unknown_relation_witnesses_fail(self):
        rows = [('甲样件：外壳开裂。','外壳开裂。',391),('乙样件：按键失灵。','按键失灵。',392)]
        for change in ('cross_review','missing_witness','unknown_witness','unknown_relation'):
            context,output = self.relation_case('same_review',rows)
            relation = output['relation_judgments'][0]
            if change=='cross_review': relation['witnesses'][1]['key'] = context[2].candidates[1].key
            if change=='missing_witness': relation['witnesses'].pop()
            if change=='unknown_witness': relation['witnesses'][0]['key'] = 'unknown'
            if change=='unknown_relation': relation['relation_id'] = 'relation_999'
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')

    def test_relation_reference_outside_quote_or_outside_witness_is_rejected(self):
        rows = [('甲件：外壳开裂。补记：随后按键失灵。','外壳开裂。',401),('乙件：其他观察。','其他观察。',402)]
        context,output = self.relation_case('before',rows)
        relation = output['relation_judgments'][0]
        relation['support_refs'] = [text_ref(context[2].candidates[0],'补记：随后按键失灵。')]
        self.assertEqual(self.run_output(context,output).outcome.reason_codes,('text_reference_missing',))
        relation['support_refs'] = [text_ref(context[2].candidates[1])]
        self.assertEqual(self.run_output(context,output).outcome.reason_codes,('relation_citation_mismatch',))

    def test_unique_locations_do_not_certify_wrong_semantic_status_or_basis(self):
        # Independently authored semantic expectation: unresolved object must be unknown.
        context = self.prepare([('墨竹另记：那人态度粗鲁，但我确实不知道他是不是客服。','那人态度粗鲁',411)],
                               b1.contract('有无客服态度差的评论？'))
        output = text_reply(context[2],['supported'])
        run = self.run_output(context,output)
        self.assertEqual(run.outcome.execution_state,'validated_shadow')
        self.assertNotEqual(run.resolved_output.fact_judgments[0].status,'unknown')
        self.assertFalse(run.outcome.semantic_verified)
        # Wrong semantic basis: a literal product identifier is not a denial/retraction.
        context = self.prepare([('新样件编号丙：没有凉风。','新样件编号丙：没有凉风。',412)])
        output = text_reply(context[2],['refuted'])
        output['fact_judgments'][0]['refutation_refs'][0]['text'] = '新样件编号丙'
        run = self.run_output(context,output)
        self.assertEqual(run.outcome.execution_state,'validated_shadow')
        self.assertFalse(run.outcome.semantic_verified)
        self.assertNotEqual(run.resolved_output.fact_judgments[0].status,'supported')

    def test_model_declared_failure_and_illegal_failed_judgments_remain_closed(self):
        context = self.prepare()
        output = text_reply(context[2])
        output['execution_status'] = 'failed'
        self.assertEqual(self.run_output(context,output).outcome.reason_codes,('failed_output_has_judgments',))
        output['fact_judgments'] = []
        self.assertEqual(self.run_output(context,output).outcome.execution_state,'model_failed')

    def test_invalid_json_duplicate_keys_types_and_size_fail_closed(self):
        context = self.prepare()
        contract,result,_,_ = context
        for raw in ('{','{"version":"x","version":"x"}',canonical_json(text_reply(context[2]))+' '*65537):
            run = experiment.evaluate(contract,result,raw,
                trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=REQUEST_ID)
            self.assertEqual(run.outcome.execution_state,'output_invalid')
            self.assertIsNone(run.resolved_output)
        for text in (123,True,None):
            output = text_reply(context[2])
            output['fact_judgments'][0]['support_refs'][0]['text'] = text
            self.assertEqual(self.run_output(context,output).outcome.execution_state,'output_invalid')

    def test_input_output_and_source_call_counts_are_unchanged(self):
        context = self.prepare()
        contract,result,request,events = context
        output = text_reply(request)
        before = canonical_json(output),canonical_json(result.to_dict()),list(events),request.model_dump(mode='json')
        self.run_output(context,output)
        self.assertEqual((canonical_json(output),canonical_json(result.to_dict()),events,request.model_dump(mode='json')),before)

    def test_agent_adapter_and_history_are_isolated(self):
        for folder in ('src','scripts'):
            for path in (ROOT/folder).rglob('*.py'):
                self.assertNotIn('shadow_text_evidence_prototype',path.read_text(encoding='utf-8-sig'))
        # Deliberately deny old experiment data and all outputs, including reads.
        real_open = Path.open
        def open_without_history(path,*args,**kwargs):
            path = Path(path)
            self.assertNotIn('outputs',path.parts)
            self.assertNotIn(path.name,('shadow_single_four_state_v1.json','shadow_v3_independent_cases_v1.json'))
            return real_open(path,*args,**kwargs)
        context = self.prepare()
        with patch.object(Path,'open',open_without_history):
            self.assertEqual(self.run_output(context,text_reply(context[2])).outcome.execution_state,'validated_shadow')
        self.assertEqual(StructuredEvidenceShadow().run(None,None,trusted_contract_sha256='',request_id='off').outcome.execution_state,'disabled')
        # The active B1 output parser cannot consume the new experimental version.
        self.assertEqual(protocol.audit_output(context[2],canonical_json(text_reply(context[2]))).execution_state,'output_invalid')


if __name__ == '__main__':
    unittest.main()
