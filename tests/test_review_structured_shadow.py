"""P5-B2 synthetic inputs and REAL local process cancellation. No real inference."""
from copy import deepcopy
from dataclasses import asdict, replace
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread, enumerate as threads
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_p5b1_structured_evidence_design as b1
import test_semantic_retrieval as sr
from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory, worker_executable
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_task_contract import RuntimeQueryContract
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.reviews.embedding_store import OpinionMatch, SPACE_ID
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalService, require_source_validated_result


FACTORY = 'fixtures.shadow_mock_judge:MockJudge'


def verified(c=None, rows=None):
    c = c or b1.contract()
    result,sources = b1.retrieval(c.original_question,rows)
    events=[]
    snapshot=SimpleNamespace(scope=None,info=result.snapshot,embedding_space_id=SPACE_ID,text_builder_version='quote-utf8-v1',
        search=lambda *args,**kw:tuple(OpinionMatch(row.document,row.cosine_distance) for row in result.evidence),
        load_sources=lambda keys:sources)
    service=SemanticRetrievalService(encoder=sr.FakeEncoder(events),repository=sr.FakeRepository(events,snapshot),
                                     scope=result.scope.model_dump(mode='json'))
    return c,service.search(result.query),events


def request_for(c=None, rows=None):
    c,result,_=verified(c,rows)
    return protocol.prepare_input(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                                   request_id='SYNTHETIC-REQUEST')


class ShadowTests(unittest.TestCase):
    OS_RECEIPTS = []

    def setUp(self):
        self.directory=self.enterContext(TemporaryDirectory(prefix='shadow-synthetic-'))
        self.addTypeEqualityFunc(protocol.ShadowOutcome,lambda a,b,msg=None:self.assertEqual(asdict(a),asdict(b),msg))
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request','openai.OpenAI',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('real model/network/database forbidden')))

    def adapter(self, mode='valid', *, timeout=5.0, enabled=True, **config):
        return StructuredEvidenceShadow(settings=ShadowSettings(enabled=enabled,timeout_seconds=timeout),
            judge_factory=JudgeFactory(entrypoint=FACTORY,config_json=canonical_json(dict(mode=mode,**config))))

    def run_adapter(self, mode='valid', *, c=None, rows=None, **kwargs):
        c,result,_=verified(c,rows)
        return self.adapter(mode,**kwargs).run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                                               request_id='SYNTHETIC-REQUEST')

    def assert_closed(self, run):
        self.assertFalse(run.outcome.publication_allowed)
        self.assertFalse(run.outcome.semantic_verified)
        self.assertEqual(run.outcome.permitted_target_fact_ids,())
        self.assertTrue(run.diagnostics['reaped'])
        self.assertTrue(run.diagnostics['threads_released'])
        self.assertFalse(any(t.name.startswith('shadow-') for t in threads()))
        if run.diagnostics['process_id'] is not None:
            self.OS_RECEIPTS.append(dict(test=self.id(),**run.debug_record()))

    def test_schema_is_exactly_b1_not_a_new_protocol(self):
        for name,value in protocol.schemas().items():
            path=Path(__file__).parents[1]/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json'
            self.assertEqual(value,json.loads(path.read_text(encoding='utf-8')))

    def test_descriptions_come_from_original_clause_not_id(self):
        for question in ('有无制冷不足的评论？','有没有同一评论既说制冷好又抱怨噪音大的反馈？'):
            c=b1.contract(question)
            for fact in c.required_facts.items:
                self.assertNotEqual(fact.description,fact.id)
                self.assertIn(fact.description.split('：')[1],c.evidence_question)
        request=request_for()
        wire=protocol.model_payload(request)
        for field in ('base_url','offline.invalid','cosine_distance','assessment','reference_state','human_confirmed'):
            self.assertNotIn(field,wire)

    def test_p0_binding_reused_and_no_extra_repository_or_encoder_work(self):
        c,result,events=verified();before=list(events)
        request=protocol.prepare_input(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC')
        self.assertEqual(events,before)
        self.assertIs(require_source_validated_result(result),result)
        self.assertIsInstance(request.contract,RuntimeQueryContract)
        self.assertNotIn('_source_validation',canonical_json(result.to_dict()))

    def test_forged_or_modified_p0_result_cannot_self_verify(self):
        c,result,_=verified()
        variants=[replace(result,_source_validation=True),replace(result,_source_validation=None),
                  replace(result,evidence=(replace(result.evidence[0],document=replace(result.evidence[0].document,base_url='OTHER')),)),
                  replace(result,evidence=(replace(result.evidence[0],rank=2),))]
        for value in variants:
            run=self.adapter().run(c,value,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC')
            self.assertEqual(run.outcome.execution_state,'input_invalid');self.assertEqual(run.diagnostics['attempts'],0);self.assert_closed(run)

    def test_cross_scope_version_source_and_quote_tampering_fail_without_process(self):
        c,result,_=verified();row=result.evidence[0]
        mutants=[replace(result,scope=sr.RetrievalScope.model_validate_json(canonical_json(b1.SCOPE | change)))
                 for change in ({'product_ids':['OTHER']},{'date_to':'2031-01-03'},{'prompt_version':'OTHER'})]
        mutants += [replace(result,evidence=(replace(row,document=replace(row.document,**change)),))
                    for change in ({'annotation_id':999},{'file_sha256':'f'*64},{'quote':'篡改'},
                                   {'quote_end':999},{'review_content_sha256':'f'*64})]
        for mutant in mutants:
            run=self.adapter().run(c,mutant,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC')
            self.assertEqual(run.outcome.execution_state,'input_invalid');self.assertEqual(run.diagnostics['attempts'],0);self.assert_closed(run)

    def test_target_digest_scope_and_description_fail_before_process(self):
        c,result,_=verified();original=content_sha256(c.model_dump(mode='json'))
        for description in ('cooling_poor','unknown','未知','待确认'):
            items=(c.required_facts.items[0].model_copy(update={'description':description}),)
            mutant=c.model_copy(update={'required_facts':c.required_facts.model_copy(update={'items':items})})
            run=self.adapter().run(mutant,result,trusted_contract_sha256=content_sha256(mutant.model_dump(mode='json')),request_id='SYNTHETIC')
            self.assertEqual(run.outcome.execution_state,'not_eligible');self.assertEqual(run.diagnostics['attempts'],0)
        run=self.adapter().run(c,result,trusted_contract_sha256='f'*64,request_id='SYNTHETIC')
        self.assertEqual(run.outcome.reason_codes,('target_binding_mismatch',));self.assert_closed(run)

    def test_unsupported_unknown_cause_and_sql_never_run(self):
        for q in ('有没有降温不给力的反馈？','有没有制冷好或物流很快的反馈？',
                  '为什么这款空调制冷不足，是否由压缩机故障导致？','好评主要集中在哪些方面？',
                  '制冷不足的反馈占全部评论多少？'):
            c=b1.contract(q);_,result,_=verified()
            run=self.adapter().run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC')
            self.assertEqual(run.outcome.execution_state,'not_eligible');self.assertEqual(run.diagnostics['attempts'],0);self.assert_closed(run)

    def test_default_off_never_validates_input_or_launches_worker(self):
        with patch('data_analysis_agent.agents.review_shadow_adapter.prepare_input',side_effect=AssertionError('must not prepare')),\
             patch('data_analysis_agent.agents.review_shadow_adapter.subprocess.Popen',side_effect=AssertionError('must not launch')):
            run=StructuredEvidenceShadow().run(None,None,trusted_contract_sha256='invalid',request_id='SYNTHETIC')
        self.assertEqual(run.outcome.execution_state,'disabled');self.assertEqual(run.diagnostics['attempts'],0);self.assert_closed(run)

    def test_actual_worker_returns_normal_structured_shadow_and_cleans_client(self):
        marker=Path(self.directory)/'closed'
        run=self.run_adapter('support',closed_marker=str(marker))
        self.assertEqual(run.outcome.execution_state,'validated_shadow',run.debug_record())
        self.assertTrue(run.outcome.structural_goal_satisfied);self.assertIsNotNone(run.judgments)
        self.assertEqual(marker.read_text(),'closed');self.assertEqual(run.diagnostics['exit_code'],0)
        self.assert_closed(run)

    def test_actual_worker_errors_invalid_json_unknown_id_incomplete_and_span(self):
        for mode,state in [('error','model_failed'),('invalid_type','model_failed'),('invalid_utf8','model_failed'),
                           ('invalid_json','output_invalid'),('unknown_fact','output_invalid'),('incomplete','output_invalid')]:
            with self.subTest(mode=mode):
                run=self.run_adapter(mode);self.assertEqual(run.outcome.execution_state,state,run.debug_record());self.assert_closed(run)
        request=request_for();value=b1.response(request);value['fact_judgments'][0]['support_refs'][0]['end']+=1
        run=self.run_adapter(reply=canonical_json(value))
        self.assertEqual(run.outcome.reason_codes,('citation_span_mismatch',));self.assert_closed(run)

    def test_actual_deadline_kills_infinite_wait_and_no_late_activity(self):
        marker=Path(self.directory)/'started';closed=Path(self.directory)/'closed'
        started=monotonic();run=self.run_adapter('hang',timeout=1.5,marker=str(marker),closed_marker=str(closed))
        self.assertEqual(run.outcome.reason_codes,('deadline_exceeded',),run.debug_record())
        self.assertTrue(marker.exists(),'must reach judge, not only time out at interpreter startup')
        self.assertEqual(run.diagnostics['process_id'],int(marker.read_text()))
        self.assertTrue(run.diagnostics['termination_requested']);self.assertIsNotNone(run.diagnostics['exit_code'])
        self.assertLess(monotonic()-started,5);self.assertFalse(closed.exists())
        self.assert_closed(run)

    def test_actual_external_cancellation_reaps_running_worker(self):
        marker=Path(self.directory)/'cancel-started';event=Event()
        def cancel_when_started():
            end=monotonic()+4
            while not marker.exists() and monotonic()<end:sleep(.01)
            event.set()
        watcher=Thread(target=cancel_when_started);watcher.start()
        try:
            c,result,_=verified()
            run=self.adapter('hang',marker=str(marker)).run(c,result,
                trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC',cancel_event=event)
        finally:watcher.join(timeout=5)
        self.assertTrue(marker.exists());self.assertEqual(run.outcome.execution_state,'cancelled',run.debug_record())
        self.assertEqual(run.diagnostics['process_id'],int(marker.read_text()))
        self.assertTrue(run.diagnostics['termination_requested']);self.assert_closed(run)
        self.assertFalse(watcher.is_alive())

    def test_pre_cancel_no_process_and_clean_crash_or_missing_output(self):
        c,result,_=verified();event=Event();event.set()
        run=self.adapter().run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                              request_id='SYNTHETIC',cancel_event=event)
        self.assertEqual(run.diagnostics['attempts'],0);self.assertEqual(run.outcome.execution_state,'cancelled')
        for mode,code in [('exit','worker_exited'),('missing','missing_output')]:
            run=self.run_adapter(mode);self.assertEqual(run.outcome.reason_codes,(code,));self.assert_closed(run)

    def test_completed_output_with_hanging_client_thread_is_not_success(self):
        marker=Path(self.directory)/'linger-started'
        run=self.run_adapter('linger',timeout=1.5,marker=str(marker))
        self.assertEqual(run.outcome.reason_codes,('deadline_exceeded',));self.assertTrue(run.diagnostics['termination_requested'])
        self.assertEqual(run.diagnostics['process_id'],int(marker.read_text()))
        self.assertIsNone(run.judgments);self.assert_closed(run)

    def test_child_and_parent_output_byte_budgets(self):
        run=self.run_adapter('oversize');self.assertEqual(run.outcome.reason_codes,('output_too_large',));self.assert_closed(run)
        real_popen=subprocess.Popen
        script=Path(__file__).parent/'fixtures/shadow_pipe_flood.py'
        def flood(args,**kwargs):return real_popen([worker_executable(),'-B',str(script)],**kwargs)
        with patch('data_analysis_agent.agents.review_shadow_adapter.subprocess.Popen',side_effect=flood):
            run=self.run_adapter()
        self.assertEqual(run.outcome.reason_codes,('output_too_large',));self.assertTrue(run.diagnostics['termination_requested']);self.assert_closed(run)

    def test_cleanup_failure_is_explicit_not_reported_as_reaped(self):
        class FakeProcess:
            pid=123
            stdin=BytesIO()
            stdout=BytesIO()
            def poll(self):return None
            def wait(self,timeout):raise subprocess.TimeoutExpired('synthetic-only',timeout)
            def kill(self):raise PermissionError('synthetic cleanup denial')
        with patch('data_analysis_agent.agents.review_shadow_adapter.subprocess.Popen',return_value=FakeProcess()):
            run=self.run_adapter(timeout=.05)
        self.assertEqual(run.outcome.reason_codes,('cleanup_failed',))
        self.assertFalse(run.diagnostics['reaped']);self.assertTrue(run.diagnostics['threads_released'])
        self.assertFalse(run.outcome.publication_allowed)

    def test_full_context_over_budget_is_not_truncated_or_sent(self):
        c,result,_=verified(rows=[('🎈'*35000+'制冷不足','制冷不足',1)])
        run=self.adapter().run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id='SYNTHETIC')
        self.assertEqual(run.outcome.reason_codes,('context_budget_exceeded',));self.assertEqual(run.diagnostics['attempts'],0);self.assert_closed(run)
        run=self.run_adapter(rows=[])
        self.assertEqual(run.outcome.execution_state,'skipped_empty');self.assertEqual(run.diagnostics['attempts'],0)

    def test_safe_debug_records_do_not_include_config_raw_comment_or_model_error(self):
        for mode in ('noisy','error','invalid_json'):
            run=self.run_adapter(mode,secret='SYNTHETIC-PRIVATE-TOKEN')
            record=canonical_json(run.debug_record())
            for text in ('SYNTHETIC-PRIVATE-TOKEN','房间迟迟凉不下来','offline.invalid','config_json','rationale'):
                self.assertNotIn(text,record);self.assertNotIn(text,repr(run))
            self.assert_closed(run)

    def test_all_any_same_review_before_and_unknown_relation_checks(self):
        for operator in ('all_of','any_of'):
            c=b1.authored_compound(operator=operator);request=request_for(c,rows=[('外壳有裂纹。','外壳有裂纹',1)])
            output=b1.response(request,{('f1',request.candidates[0].key):'unknown'})
            run=self.run_adapter(c=c,rows=[('外壳有裂纹。','外壳有裂纹',1)],reply=canonical_json(output))
            self.assertEqual(run.outcome.structural_goal_satisfied,operator=='any_of');self.assert_closed(run)
        text='先发现外壳开裂，后来按键失灵。';rows=[(text,text,1),(text,'按键失灵',1)]
        for relation in ('same_review','before'):
            c=b1.authored_compound(relation=relation);request=request_for(c,rows)
            output=b1.relation_response(request,b1.response(request),temporal=relation=='before')
            run=self.run_adapter(c=c,rows=rows,reply=canonical_json(output))
            self.assertTrue(run.outcome.structural_goal_satisfied,run.debug_record());self.assert_closed(run)
        c=b1.authored_compound(relation='same_review');rows=[(text,text,1),(text,'按键失灵',2)]
        request=request_for(c,rows);output=b1.relation_response(request,b1.response(request))
        run=self.run_adapter(c=c,rows=rows,reply=canonical_json(output));self.assertEqual(run.outcome.reason_codes,('cross_review_relation',))
        c=b1.authored_compound(relation='before');rows=[(text,text,1),(text,'按键失灵',1)]
        request=request_for(c,rows);output=b1.relation_response(request,b1.response(request))
        self.assertEqual(protocol.audit_output(request,output).reason_codes,('temporal_evidence_missing',))
        output['relation_judgments'][0].update(status='unknown',reason_code='relation_unknown')
        outcome=protocol.audit_output(request,output)
        self.assertFalse(outcome.structural_goal_satisfied);self.assertEqual(outcome.unmet_relation_ids,('relation_0',))

    def test_concurrent_requests_use_separate_workers_and_bindings(self):
        c,result,_=verified();adapter=self.adapter('support');runs={}
        def run_request(name):
            runs[name]=adapter.run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),request_id=name)
        callers=[Thread(target=run_request,args=(name,)) for name in ('SYNTHETIC-A','SYNTHETIC-B')]
        for t in callers:t.start()
        for t in callers:t.join(timeout=10);self.assertFalse(t.is_alive())
        self.assertEqual(set(runs),{'SYNTHETIC-A','SYNTHETIC-B'})
        self.assertNotEqual(runs['SYNTHETIC-A'].diagnostics['process_id'],runs['SYNTHETIC-B'].diagnostics['process_id'])
        self.assertNotEqual(runs['SYNTHETIC-A'].diagnostics['input_sha256'],runs['SYNTHETIC-B'].diagnostics['input_sha256'])
        for name,run in runs.items():
            self.assertEqual(run.judgments.request_id,name);self.assert_closed(run)

    def test_seen_b1_expressions_and_negation_conflicts_use_existing_semantics(self):
        cases=json.loads((Path(__file__).parent/'fixtures/p5b1_new_expressions.json').read_text(encoding='utf-8'))['cases']
        for case in cases:
            c=b1.contract(case['question'])
            rows=[] if case['mock_status']=='empty' else [(case['review_text'],case['quote'],1)]
            if case['mock_status']=='blocked_unresolved_target':
                run=self.run_adapter(c=c);self.assertEqual(run.outcome.execution_state,'not_eligible');continue
            request=request_for(c,rows)
            if not rows:
                self.assertEqual(self.run_adapter(c=c,rows=[]).outcome.execution_state,'skipped_empty');continue
            value=b1.response(request,{(c.required_facts.items[0].id,request.candidates[0].key):case['mock_status']})
            if case.get('context_veto'):
                value['fact_judgments'][0].update(refutation_refs=[],context_veto_refs=[b1.span(request.candidates[0],case['context_veto'])],reason_code='context_veto')
            outcome=protocol.audit_output(request,value)
            self.assertEqual(outcome.execution_state,'validated_shadow');self.assertEqual(outcome.structural_goal_satisfied,case['mock_status']=='supported')

    def test_default_off_agent_sql_rag_and_legacy_history_stay_compatible(self):
        import test_review_semantic_integration as fixtures
        from data_analysis_agent.agents.review_compression import read_public_analysis
        from data_analysis_agent.agents.review_answers import read_public_answer
        fixture=fixtures.SemanticAgentTests('test_real_agent_registry_p0_encoder_repository_validation_and_answer')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        for question in (fixtures.QUESTION,'好评主要集中在哪些方面？'):
            result=fixture.run_question(question);fixture.assert_success(result)
            task=dict(status='succeeded',result=result);before=(read_public_analysis(task),read_public_answer(task))
            self.assertEqual(StructuredEvidenceShadow().run(None,None,trusted_contract_sha256='',request_id='unused').outcome.execution_state,'disabled')
            self.assertEqual((read_public_analysis(task),read_public_answer(task)),before)
            if question==fixtures.QUESTION:
                old=deepcopy(result)
                for field in (old['query_contract'],old['query_plan']['task_contract']):
                    for fact in field['required_facts']['items']:fact['description']=fact['id']
                self.assertEqual((read_public_analysis(dict(status='succeeded',result=old)),read_public_answer(dict(status='succeeded',result=old))),before)
                old['query_contract']['required_facts']['items'][0]['description']='随意修改目标'
                old['query_plan']['task_contract']=deepcopy(old['query_contract'])
                self.assertEqual(read_public_analysis(dict(status='succeeded',result=old))['status'],'failed')


if __name__=='__main__':unittest.main()
