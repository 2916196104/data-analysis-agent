"""P5-B1 Mock/structure acceptance; no production policy or semantic model is added."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import p5b1_contract_prototype as design
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_task_contract import RuntimeQueryContract
from data_analysis_agent.agents.review_reported_facts import assess
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SemanticEvidence, SemanticResult, SemanticQuery, SnapshotInfo, _scope


SCOPE = dict(model='SYNTHETIC',base_url='https://offline.invalid/v1',prompt_version='SYNTHETIC-v1',
    source='SYNTHETIC',dataset_version='synthetic-v1',product_ids=['SYNTHETIC-P1'],
    date_from='2031-01-01',date_to='2031-01-02')
QUESTION = '有无制冷不足的评论？'


def retrieval(question=QUESTION, rows=None):
    rows = [('房间迟迟凉不下来。','房间迟迟凉不下来',1)] if rows is None else rows
    profile = content_sha256([SCOPE[k] for k in ('model','base_url','prompt_version')])
    grouped = {}
    ordered = []
    for index,(text,quote,rid) in enumerate(rows):
        row = dict(source=SCOPE['source'],dataset_version=SCOPE['dataset_version'],product_id=SCOPE['product_ids'][0],
            review_id=rid,annotation_id=100+rid,human_revision_id=None,processing_status='success',human_status='unreviewed',
            error_code=None,opinion_source='model',published_at=SCOPE['date_from']+'T00:00:00+00:00',review_text=text,
            review_content_sha256=content_sha256(['SYNTHETIC-ROW',text]),import_run_id=1,line_number=rid,
            file_sha256=content_sha256('SYNTHETIC-FILE'),model_opinion_id=index+1,human_opinion_id=None,
            quote=quote,quote_start=text.index(quote),quote_end=text.index(quote)+len(quote),aspect='effectiveness',sentiment='negative')
        doc = _document(row,tuple(SCOPE[k] for k in ('model','base_url','prompt_version')),profile)
        if rid in grouped:assert grouped[rid][0]['review_text']==text
        grouped.setdefault(rid,(row,[]))[1].append(doc)
        ordered.append(doc)
    sources = {rid:ReviewSource(_review_state(row,docs,profile),tuple(docs),row['review_text']) for rid,(row,docs) in grouped.items()}
    evidence = tuple(SemanticEvidence(i,0.1,doc,sources[doc.review_id].state,sources[doc.review_id].review_text,
        sha256(sources[doc.review_id].review_text.encode()).hexdigest()) for i,doc in enumerate(ordered,1))
    result = SemanticResult(SemanticQuery(question=question),_scope(SCOPE),
        SnapshotInfo('SYNTHETIC-SNAPSHOT','2031-01-03T00:00:00+00:00'),evidence)
    return result,tuple(sources.values())


def contract(question=QUESTION):
    return build_query_plan(question,deepcopy(SCOPE)).task_contract


def authored_compound(*, operator='all_of', relation=None):
    """Trusted producer fixture only, NOT an upgrade of a production unsupported plan."""
    value = contract().model_dump(mode='json')
    value.update(original_question='合成协议问题：外壳开裂与按键失灵是否满足指定关系？',
        evidence_question='合成协议问题：外壳开裂与按键失灵是否满足指定关系？',subtopic='casing_and_buttons',
        required_facts=dict(operator=operator,items=[dict(id='f0',description='该设备外壳开裂'),dict(id='f1',description='该设备按键失灵')],
            relations=[dict(kind=relation,fact_ids=['f0','f1'])] if relation else []))
    return RuntimeQueryContract.model_validate_json(canonical_json(value))


def prepared(c=None, rows=None):
    c = c or contract()
    result,sources = retrieval(c.original_question,rows)
    return design.prepare_input(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
        trusted_sources=sources,request_id='SYNTHETIC-REQUEST')


def span(candidate, text=None):
    text = text or candidate.quote
    start = candidate.review_text.index(text)
    return dict(key=candidate.key,source_identity_sha256=candidate.source_identity_sha256,start=start,end=start+len(text),text=text)


def response(request, statuses=None):
    facts = []
    for fact in request.contract.required_facts.items:
        for candidate in request.candidates:
            status = (statuses or {}).get((fact.id,candidate.key),'supported')
            facts.append(dict(fact_id=fact.id,key=candidate.key,status=status,
                support_refs=[span(candidate)] if status=='supported' else [],
                refutation_refs=[span(candidate)] if status=='refuted' else [],context_veto_refs=[],
                reason_code={'supported':'direct_report','refuted':'explicit_refutation','not_supported':'no_direct_support','unknown':'semantics_unknown'}[status],
                rationale='开发者提供的 Mock 判断，不是模型或人工金标准'))
    return dict(version=design.OUTPUT_VERSION,request_id=request.request_id,
        input_sha256=content_sha256(request.model_dump(mode='json')),execution_status='completed',
        fact_judgments=facts,relation_judgments=[dict(relation_id=f'relation_{i}',status='unknown',witnesses=[],
            support_refs=[],reason_code='relation_unknown',rationale='Mock 未判断关系') for i,_ in enumerate(request.contract.required_facts.relations)])


def relation_response(request, value, *, status='supported', temporal=False):
    witnesses = [dict(fact_id=f.id,key=request.candidates[i].key) for i,f in enumerate(request.contract.required_facts.items)]
    value['relation_judgments']=[dict(relation_id='relation_0',status=status,witnesses=witnesses,
        support_refs=[span(request.candidates[0])] if temporal and status=='supported' else [],
        reason_code='relation_verified' if status=='supported' else 'relation_unknown',rationale='Mock 关系说明')]
    return value


class StructuredDesignTests(unittest.TestCase):
    def setUp(self):
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request','openai.OpenAI',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('real model/network/database forbidden')))

    def assert_closed(self, outcome):
        self.assertFalse(outcome.publication_allowed)
        self.assertFalse(outcome.semantic_verified)
        self.assertEqual(outcome.permitted_target_fact_ids,())

    def test_input_reuses_runtime_contract_and_json_round_trip(self):
        request=prepared()
        self.assertIs(type(request.contract),RuntimeQueryContract)
        self.assertEqual(design.parse(request.model_dump(mode='json'),design.JudgeInput),request)
        self.assertEqual(request.contract,contract())
        with self.assertRaises(ValueError):request.contract.scope.product_ids=('OTHER',)

    def test_model_whitelist_excludes_reference_distance_and_label_endpoint(self):
        request=prepared();payload=json.loads(design.model_payload(request))
        self.assertEqual(payload['contract']['required_facts'],request.contract.required_facts.model_dump(mode='json'))
        wire=canonical_json(payload)
        for word in ('base_url','offline.invalid','cosine_distance','assessment','reference_state','human_confirmed','human_labels'):
            self.assertNotIn(word,wire)
        self.assertIn('review_text',wire)

    def test_schema_projection_preserves_goal_and_has_no_extra_output_fields(self):
        values=design.schemas()
        self.assertIn('base_url',values['backend_input']['$defs']['RuntimeScope']['properties'])
        self.assertNotIn('base_url',values['model_input']['$defs']['RuntimeScope']['properties'])
        self.assertIn('input_sha256',values['model_input']['required'])
        self.assertEqual(values['backend_input']['$defs']['RequiredFacts'],values['model_input']['$defs']['RequiredFacts'])
        self.assertFalse(values['model_output']['additionalProperties'])
        self.assertEqual(values['model_output']['$defs']['FactJudgment']['properties']['status']['enum'],
                         ['supported','refuted','not_supported','unknown'])

    def test_source_is_independent_and_cannot_be_rebuilt_from_mutant_candidate(self):
        result,sources=retrieval();c=contract()
        for change in (dict(quote='篡改观点'),dict(annotation_id=777),dict(product_id='OTHER'),
                       dict(published_at='2032-01-01T00:00:00+00:00'),dict(prompt_version='OTHER'),dict(file_sha256='f'*64)):
            with self.subTest(change=change),self.assertRaises((ValueError,RuntimeError)):
                row=result.evidence[0]
                mutant=replace(result,evidence=(replace(row,document=replace(row.document,**change)),))
                design.prepare_input(c,mutant,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                    trusted_sources=sources,request_id='SYNTHETIC')

    def test_context_and_quote_hash_drift_are_execution_errors(self):
        result,sources=retrieval();c=contract();row=result.evidence[0]
        for mutant in (replace(row,review_text='篡改上下文'),replace(row,review_text_sha256='f'*64),
                       replace(row,document=replace(row.document,quote_end=row.document.quote_end+1))):
            with self.subTest(mutant=mutant.key),self.assertRaises((ValueError,RuntimeError)):
                design.prepare_input(c,replace(result,evidence=(mutant,)),trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                    trusted_sources=sources,request_id='SYNTHETIC')

    def test_scope_profile_source_policy_and_target_binding_are_required(self):
        result,sources=retrieval();c=contract();digest=content_sha256(c.model_dump(mode='json'))
        variants=[replace(result,source_policy='historical_trusted'),replace(result,context_version='2'),
                  replace(result,embedding_space_id='other'),replace(result,query=SemanticQuery(question='另一个目标')),
                  replace(result,scope=_scope(SCOPE | {'product_ids':['OTHER']})),
                  replace(result,scope=_scope(SCOPE | {'date_to':'2031-01-03'})),
                  replace(result,scope=_scope(SCOPE | {'prompt_version':'OTHER'}))]
        for mutant in variants:
            with self.subTest(mutant=mutant.source_policy),self.assertRaises((ValueError,RuntimeError)):
                design.prepare_input(c,mutant,trusted_contract_sha256=digest,trusted_sources=sources,request_id='SYNTHETIC')
        changed=c.model_copy(update={'target_entity':'other'})
        with self.assertRaisesRegex(design.PrototypeError,'target_binding_mismatch'):
            design.prepare_input(changed,result,trusted_contract_sha256=digest,trusted_sources=sources,request_id='SYNTHETIC')

    def test_paraphrase_mock_support_is_not_claimed_as_semantic_validation(self):
        request=prepared();judge=Mock();judge.judge.return_value=canonical_json(response(request))
        outcome=design.run_mock_shadow(request,judge)
        self.assertEqual(outcome.execution_state,'validated_shadow');self.assertTrue(outcome.structural_goal_satisfied)
        self.assertEqual(outcome.model_supported_fact_ids,('cooling_poor',));self.assert_closed(outcome)
        judge.judge.assert_called_once()
        self.assertEqual(assess(QUESTION,[dict(quote='房间迟迟凉不下来',review_text='房间迟迟凉不下来。',quote_start=0,quote_end=9,key='SYNTHETIC',review_id=1)])['status'],'insufficient')

    def test_new_expressions_are_mock_protocol_cases_not_real_model_scores(self):
        cases=json.loads((Path(__file__).parent/'fixtures/p5b1_new_expressions.json').read_text(encoding='utf-8'))['cases']
        self.assertEqual(len(cases),12)
        for case in cases:
            with self.subTest(case=case['id']):
                c=contract(case['question'])
                if case['mock_status']=='blocked_unresolved_target':
                    with self.assertRaisesRegex(design.PrototypeError,'not_eligible|unresolved_target'):prepared(c)
                    continue
                rows=[] if case['mock_status']=='empty' else [(case['review_text'],case['quote'],1)]
                request=prepared(c,rows)
                if not rows:
                    judge=Mock();outcome=design.run_mock_shadow(request,judge);judge.judge.assert_not_called()
                    self.assertEqual(outcome.execution_state,'skipped_empty');self.assert_closed(outcome);continue
                value=response(request,{(c.required_facts.items[0].id,request.candidates[0].key):case['mock_status']})
                if 'context_veto' in case:
                    judgment=value['fact_judgments'][0];judgment.update(refutation_refs=[],
                        context_veto_refs=[span(request.candidates[0],case['context_veto'])],reason_code='context_veto')
                outcome=design.audit_output(request,value)
                self.assertEqual(outcome.execution_state,'validated_shadow',outcome)
                self.assertEqual(outcome.structural_goal_satisfied,case['mock_status']=='supported');self.assert_closed(outcome)

    def test_context_cannot_add_support_and_unicode_indices_are_code_points(self):
        text='🎈外形顺眼。屋里一直热。';request=prepared(rows=[(text,'外形顺眼',1)])
        value=response(request);value['fact_judgments'][0]['support_refs']=[span(request.candidates[0],'屋里一直热')]
        self.assertEqual(design.audit_output(request,value).reason_codes,('citation_span_mismatch',))
        valid=response(request);outcome=design.audit_output(request,valid)
        self.assertEqual(outcome.execution_state,'validated_shadow');self.assert_closed(outcome)
        valid['fact_judgments'][0]['support_refs'][0]['start']+=1
        self.assertEqual(design.audit_output(request,valid).execution_state,'output_invalid')

    def test_unknown_ids_sources_digests_spans_or_duplicate_rows_fail_closed(self):
        request=prepared();base=response(request)
        mutations=[lambda v:v['fact_judgments'][0].update(fact_id='made_up'),
            lambda v:v['fact_judgments'][0].update(key='OTHER'),
            lambda v:v['fact_judgments'][0]['support_refs'][0].update(key='OTHER'),
            lambda v:v['fact_judgments'][0]['support_refs'][0].update(source_identity_sha256='f'*64),
            lambda v:v['fact_judgments'][0]['support_refs'][0].update(end=999),
            lambda v:v['fact_judgments'][0]['support_refs'][0].update(text='非原文'),
            lambda v:v['fact_judgments'].append(deepcopy(v['fact_judgments'][0])),
            lambda v:v.update(input_sha256='f'*64),lambda v:v.update(request_id='OTHER'),
            lambda v:v['fact_judgments'].clear()]
        for mutate in mutations:
            value=deepcopy(base);mutate(value);outcome=design.audit_output(request,value)
            self.assertEqual(outcome.execution_state,'output_invalid');self.assert_closed(outcome)

    def test_unsupported_or_unknown_status_cannot_carry_positive_licence(self):
        request=prepared()
        for status in ('unknown','not_supported','refuted','invented'):
            value=response(request);value['fact_judgments'][0]['status']=status
            self.assertEqual(design.audit_output(request,value).execution_state,'output_invalid')
        value=response(request);value['permitted_target_fact_ids']=['cooling_poor']
        self.assertEqual(design.audit_output(request,value).execution_state,'output_invalid')

    def test_any_of_and_all_of_have_different_missing_sets_without_limited(self):
        rows=[('外壳有裂纹。','外壳有裂纹',1)]
        for operator in ('all_of','any_of'):
            request=prepared(authored_compound(operator=operator),rows)
            outcome=design.audit_output(request,response(request,{('f1',request.candidates[0].key):'unknown'}))
            self.assertEqual(outcome.structural_goal_satisfied,operator=='any_of')
            self.assertEqual(outcome.unestablished_fact_ids,('f1',))
            self.assertEqual(outcome.missing_required_fact_ids,() if operator=='any_of' else ('f1',));self.assert_closed(outcome)

    def test_same_review_witnesses_pass_structure_and_cross_review_fails(self):
        text='外壳有裂纹；按键按下没有反应。';c=authored_compound(relation='same_review')
        rows=[(text,'外壳有裂纹',1),(text,'按键按下没有反应',1)]
        request=prepared(c,rows);value=relation_response(request,response(request))
        self.assertTrue(design.audit_output(request,value).structural_goal_satisfied)
        request=prepared(c,[(rows[0][0],rows[0][1],1),(rows[1][0],rows[1][1],2)])
        outcome=design.audit_output(request,relation_response(request,response(request)))
        self.assertEqual(outcome.reason_codes,('cross_review_relation',));self.assert_closed(outcome)

    def test_before_requires_quote_relation_evidence_not_just_positions(self):
        text='先发现外壳有裂纹，第二天按键按下没有反应。'
        c=authored_compound(relation='before');request=prepared(c,[(text,text,1),(text,'按键按下没有反应',1)])
        self.assertEqual(design.audit_output(request,relation_response(request,response(request))).reason_codes,('temporal_evidence_missing',))
        outcome=design.audit_output(request,relation_response(request,response(request),temporal=True))
        self.assertTrue(outcome.structural_goal_satisfied);self.assertFalse(outcome.semantic_verified);self.assert_closed(outcome)
        unresolved=relation_response(request,response(request),status='unknown')
        outcome=design.audit_output(request,unresolved)
        self.assertFalse(outcome.structural_goal_satisfied);self.assertEqual(outcome.unmet_relation_ids,('relation_0',))

    def test_unknown_or_missing_relation_and_witness_cannot_pass(self):
        text='外壳有裂纹；按键失灵。';request=prepared(authored_compound(relation='same_review'),[(text,text,1),(text,'按键失灵',1)])
        for mutate in (lambda v:v['relation_judgments'].clear(),lambda v:v['relation_judgments'][0].update(relation_id='relation_99'),
                       lambda v:v['relation_judgments'][0]['witnesses'][0].update(fact_id='invented')):
            value=relation_response(request,response(request));mutate(value)
            outcome=design.audit_output(request,value);self.assertEqual(outcome.execution_state,'output_invalid');self.assert_closed(outcome)

    def test_same_review_counterevidence_vetoes_support_but_other_buyer_does_not_disprove_existence(self):
        for other_review in (False,True):
            text='降温乏力。更正前述评价。';rows=[(text,'降温乏力',1),(text,'更正前述评价',2 if other_review else 1)]
            request=prepared(rows=rows);value=response(request,{('cooling_poor',request.candidates[1].key):'refuted'})
            outcome=design.audit_output(request,value)
            self.assertEqual(outcome.structural_goal_satisfied,other_review);self.assert_closed(outcome)

    def test_timeout_exception_declared_failure_invalid_json_and_extra_fields(self):
        request=prepared()
        for error in (TimeoutError(),RuntimeError('DO NOT EXPOSE SECRET')):
            judge=Mock();judge.judge.side_effect=error;outcome=design.run_mock_shadow(request,judge)
            self.assertEqual(outcome.execution_state,'model_failed');self.assertNotIn('SECRET',repr(outcome));self.assert_closed(outcome)
            judge.judge.assert_called_once()
        for raw in ('not JSON','{"version":1,"version":2}','{"score":NaN}',canonical_json(response(request) | {'assessment':{}})):
            judge=Mock();judge.judge.return_value=raw;outcome=design.run_mock_shadow(request,judge)
            self.assertEqual(outcome.execution_state,'output_invalid');self.assert_closed(outcome)
        value=response(request);value.update(execution_status='failed',fact_judgments=[])
        self.assertEqual(design.audit_output(request,value).execution_state,'model_failed')

    def test_shadow_cannot_replace_v2_or_change_current_and_historical_answer(self):
        import test_review_semantic_integration as fixtures
        from data_analysis_agent.agents.review_compression import read_public_analysis
        from data_analysis_agent.agents.review_answers import read_public_answer
        from data_analysis_agent.agents.review_retrieval import validate_retrieval
        fixture=fixtures.SemanticAgentTests('test_real_agent_registry_p0_encoder_repository_validation_and_answer')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        result=fixture.run_question();fixture.assert_success(result)
        task=dict(status='succeeded',result=result)
        before=(read_public_analysis(task),read_public_answer(task))
        request=prepared();model_value=response(request)
        outcome=design.audit_output(request,model_value);self.assert_closed(outcome)
        result['p5b1_shadow']=dict(raw_output=model_value,outcome=asdict(outcome))
        self.assertEqual((read_public_analysis(task),read_public_answer(task)),before)
        data=deepcopy(result['results']['r1']['data']);data['assessment']=model_value
        self.assertFalse(validate_retrieval(data,result['question']))

    def test_empty_and_context_budget_do_not_call_or_licence_judge(self):
        request=prepared(rows=[]);judge=Mock();outcome=design.run_mock_shadow(request,judge)
        judge.judge.assert_not_called();self.assertEqual(outcome.execution_state,'skipped_empty');self.assert_closed(outcome)
        text='🎈'*35000+'制冷不足';request=prepared(rows=[(text,'制冷不足',1)])
        outcome=design.run_mock_shadow(request,judge)
        judge.judge.assert_not_called();self.assertEqual(outcome.reason_codes,('context_budget_exceeded',));self.assert_closed(outcome)

    def test_original_p5_fifteen_are_seen_route_and_eligibility_regressions(self):
        seen=[('有没有制冷效果好的反馈？',True),('有没有降温不给力的反馈？',False),
            ('不是制冷慢，而是完全不降温，有没有类似抱怨？',False),('是否有制冷不足的评论？',True),
            ('这款商品有哪些优点和缺点？',False),('有没有制冷好或物流很快的反馈？',False),
            ('有没有同一评论既说制冷好又抱怨噪音大的反馈？',True),('有没有先制冷正常、后来不制冷的反馈？',False),
            ('好评主要集中在哪些方面？',False),('制冷不足的反馈占全部评论多少？',False),
            ('2023-01-02至2023-01-04有没有制冷不足的反馈？',True),
            ('为什么这款空调制冷不足，是否由压缩机故障导致？',False),('有没有空调使用十年仍然耐用的反馈？',False),
            ('是否有客服态度差的反馈？',True),('有没有配送很慢的反馈？',True)]
        self.assertEqual(len(seen),15)
        for i,(question,eligible) in enumerate(seen):
            dates={'date_from':'2023-01-02','date_to':'2023-01-04'} if i==10 else {}
            with self.subTest(question=question),patch.dict(SCOPE,dates):
                c=contract(question)
                if not eligible:
                    with self.assertRaises(design.PrototypeError):prepared(c)
                else:
                    request=prepared(c);statuses={(f.id,candidate.key):'unknown' for f in c.required_facts.items for candidate in request.candidates}
                    outcome=design.audit_output(request,response(request,statuses));self.assert_closed(outcome)
                    self.assertEqual(outcome.execution_state,'validated_shadow');self.assertFalse(outcome.structural_goal_satisfied)


if __name__=='__main__':unittest.main()
