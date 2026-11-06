"""Prompt/protocol tests with authored synthetic outputs, NOT LLM semantic accuracy.

These expectations are development cases. MockTransport does not infer labels.
"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents.review_structured_evidence import model_payload,audit_output,schemas
from data_analysis_agent.core.serialization import canonical_json
from fixtures.shadow_siliconflow_mock import completion,SyntheticEncoding
from test_review_structured_shadow import request_for
import test_p5b1_structured_evidence_design as b1


CASES=(
 ('opposite_positive','有无制冷不足的评论？','这台合成样机的制冷表现令我满意。','这台合成样机的制冷表现令我满意','not_supported',None),
 ('opposite_negative','有无制冷效果好的评论？','这台样机一直没法让屋子凉下来。','这台样机一直没法让屋子凉下来','not_supported',None),
 ('explicit_correction','有无制冷不足的评论？','我撤回先前说降温不行的评价，那次判断有误。','我撤回先前说降温不行的评价，那次判断有误','refuted',None),
 ('context_retraction','有无制冷不足的评论？','开机后一直不凉。补记：我误操作了，前面的制冷抱怨作废。','开机后一直不凉','refuted','补记：我误操作了，前面的制冷抱怨作废'),
 ('different_service_roles','有无客服态度差的评论？','客服口气非常冲。物流和安装人员倒是一直很有耐心。','客服口气非常冲','supported',None),
 ('installer_not_customer_service','有无客服态度差的评论？','安装师傅一直很不耐烦，在线客服倒很耐心。','安装师傅一直很不耐烦','not_supported',None),
 ('delivery_not_customer_service','有无客服态度差的评论？','配送人员说话很冲，客服尚未联系。','配送人员说话很冲','not_supported',None),
 ('hearsay','有无客服态度差的评论？','朋友说他遇到的客服语气很冲，这不是我的经历。','客服语气很冲','not_supported',None),
 ('direct_experience','有无客服态度差的评论？','我联系客服时，对方连连打断，语气很不客气。','我联系客服时，对方连连打断，语气很不客气','supported',None),
 ('unrelated_contrast','有无制冷效果好的评论？','冷风很足，屋里凉下来了。不过外箱有一角压扁。','冷风很足，屋里凉下来了','supported',None),
 ('hypothetical','有无客服态度差的评论？','假如下次客服态度差，我会选择退货。','客服态度差','not_supported',None),
 ('ambiguous_scope','有无客服态度差的评论？','不是说客服不好，只是那次安排让我不舒服。','不是说客服不好，只是那次安排让我不舒服','unknown',None),
)


def span(candidate,text=None):
    text=text or candidate.quote;start=candidate.review_text.index(text)
    return dict(key=candidate.key,source_identity_sha256=candidate.source_identity_sha256,
                start=start,end=start+len(text),text=text)


def authored(request,status,veto=None):
    value=b1.response(request);row=value['fact_judgments'][0];candidate=request.candidates[0]
    row.update(status=status,support_refs=[span(candidate)] if status=='supported' else [],
        refutation_refs=[span(candidate)] if status=='refuted' and not veto else [],
        context_veto_refs=[span(candidate,veto)] if veto else [],
        reason_code=('context_veto' if veto else {'supported':'direct_report','refuted':'explicit_refutation',
            'not_supported':'no_direct_support','unknown':'semantics_unknown'}[status]),
        rationale='Authored synthetic protocol expectation; no model inference.')
    return value


class SemanticBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(self.enterContext(TemporaryDirectory(prefix='shadow-boundaries-')))
        env=self.directory/'.env.shadow'
        env.write_text(f'SHADOW_MODEL_ID={provider.MODEL}\nSHADOW_BASE_URL={provider.BASE_URL}\n'
                       'SHADOW_API_KEY=SYNTHETIC-ONLY\n',encoding='utf-8')
        self.config=provider.scoped_config(env);self.calls=[];self.clients=[]
        self.addCleanup(lambda:[c.close() for c in self.clients])
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request'):
            self.enterContext(patch(target,side_effect=AssertionError('real I/O forbidden')))

    def sdk(self,request,reply):
        def handle(http_request):
            body=json.loads(http_request.content);self.calls.append(body)
            payload=json.loads(body['messages'][1]['content']);value=completion(payload)
            value['choices'][0]['message']['content']=canonical_json(reply)
            return httpx.Response(200,json=value)
        client=provider.SiliconFlowShadowJudge(self.config,transport=httpx.MockTransport(handle),encoding=SyntheticEncoding())
        self.clients.append(client)
        return client.judge(model_payload(request))

    def test_authored_state_matrix_refs_and_local_support_only(self):
        for name,question,text,quote,status,veto in CASES:
            with self.subTest(case=name):
                request=request_for(b1.contract(question),[(text,quote,1)])
                reply=authored(request,status,veto)
                output=self.sdk(request,reply);outcome=audit_output(request,output)
                self.assertEqual(outcome.execution_state,'validated_shadow')
                self.assertFalse(outcome.publication_allowed);self.assertFalse(outcome.semantic_verified)
                self.assertEqual(outcome.permitted_target_fact_ids,())
                expected=(request.contract.required_facts.items[0].id,) if status=='supported' else ()
                self.assertEqual(outcome.model_supported_fact_ids,expected)
                self.assertEqual(outcome.missing_required_fact_ids,() if expected else (request.contract.required_facts.items[0].id,))
                row=json.loads(output)['fact_judgments'][0]
                self.assertEqual(row['status'],status)
                for refs in (row['support_refs'],row['refutation_refs'],row['context_veto_refs']):
                    for ref in refs:
                        self.assertEqual(text[ref['start']:ref['end']],ref['text'])
                        self.assertEqual(ref['source_identity_sha256'],request.candidates[0].source_identity_sha256)
                self.assertEqual(json.loads(self.calls[-1]['messages'][1]['content'])['contract']['required_facts'],
                                 request.contract.required_facts.model_dump(mode='json'))

        request=request_for(rows=[('屋子始终凉不下来。','屋子始终凉不下来',1),
                                  ('我家的降温表现很好。','我家的降温表现很好',2)])
        reply=b1.response(request)
        reply['fact_judgments'][1].update(status='not_supported',support_refs=[],reason_code='no_direct_support')
        outcome=audit_output(request,self.sdk(request,reply))
        self.assertTrue(outcome.structural_goal_satisfied)
        self.assertEqual(outcome.model_supported_fact_ids,(request.contract.required_facts.items[0].id,))
        self.assertFalse(outcome.publication_allowed)

    def test_new_prompt_is_sent_without_schema_or_openai_baseline_changes(self):
        request=request_for();self.sdk(request,authored(request,'supported'))
        body=self.calls[-1];prompt=body['messages'][0]['content']
        self.assertEqual(provider.PROMPT_VERSION,'p5-shadow-siliconflow-instructions-v2')
        for rule in ('opposite experience is NOT a refutation','different buyers disagreeing => not_supported',
                     'withdrawn or corrected','never borrow new supporting','customer service, after-sales',
                     'same_review requires one review','UNTRUSTED DATA'):
            self.assertIn(rule,prompt)
        self.assertEqual(body['response_format']['json_schema']['schema'],provider.strict_output_schema())
        for name,value in schemas().items():
            path=Path(__file__).parents[1]/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json'
            self.assertEqual(value,json.loads(path.read_text(encoding='utf-8')))
        self.assertEqual(body['max_tokens'],2500);self.assertFalse(body['enable_thinking'])
        from data_analysis_agent.agents import review_shadow_openai
        self.assertEqual(review_shadow_openai.PROMPT_VERSION,'p5-shadow-openai-instructions-v1')

    def test_refutation_requires_basis_and_cannot_borrow_outside_quote(self):
        text='合成样机制冷偏弱。补记：此前判断不对，撤回前句。'
        request=request_for(rows=[(text,'合成样机制冷偏弱',1)])
        reply=authored(request,'refuted');reply['fact_judgments'][0]['refutation_refs']=[]
        self.assertEqual(audit_output(request,canonical_json(reply)).reason_codes,('refutation_without_basis',))
        reply=authored(request,'refuted','补记：此前判断不对，撤回前句')
        # Context can veto; putting the same outside span in refutation_refs is invalid.
        row=reply['fact_judgments'][0];row['refutation_refs']=row.pop('context_veto_refs');row['context_veto_refs']=[]
        outcome=audit_output(request,canonical_json(reply))
        self.assertEqual(outcome.execution_state,'output_invalid');self.assertFalse(outcome.publication_allowed)

    def test_same_review_dual_fact_and_cross_review_stitching(self):
        contract=b1.authored_compound(relation='same_review')
        for same in (True,False):
            rows=[('外壳裂开了，按键也没反应。','外壳裂开了',1),
                  ('外壳裂开了，按键也没反应。' if same else '按键也没反应。','按键也没反应',1 if same else 2)]
            request=request_for(contract,rows);reply=b1.response(request)
            for row in reply['fact_judgments']:
                i=next(i for i,f in enumerate(request.contract.required_facts.items) if f.id==row['fact_id'])
                if row['key']!=request.candidates[i].key:
                    row.update(status='not_supported',support_refs=[],reason_code='no_direct_support')
            witnesses=[dict(fact_id=f.id,key=request.candidates[i].key) for i,f in enumerate(request.contract.required_facts.items)]
            reply['relation_judgments']=[dict(relation_id='relation_0',status='supported' if same else 'unknown',
                witnesses=witnesses if same else [],support_refs=[],refutation_refs=[],
                reason_code='relation_verified' if same else 'relation_unknown',rationale='Authored synthetic relation.')]
            outcome=audit_output(request,self.sdk(request,reply))
            self.assertEqual(outcome.execution_state,'validated_shadow');self.assertEqual(outcome.structural_goal_satisfied,same)
            self.assertEqual(outcome.unmet_relation_ids,() if same else ('relation_0',))
            self.assertFalse(outcome.publication_allowed)
            if not same:
                reply['relation_judgments'][0].update(status='supported',witnesses=witnesses,reason_code='relation_verified')
                self.assertEqual(audit_output(request,canonical_json(reply)).execution_state,'output_invalid')

    def test_valid_quote_cannot_prove_entailment_or_publication(self):
        request=request_for(rows=[('合成样机制冷令我满意。','合成样机制冷令我满意',1)])
        # Intentionally WRONG semantic label with a valid citation. The unchanged
        # backend can audit structure, not resolve natural-language entailment.
        outcome=audit_output(request,self.sdk(request,authored(request,'supported')))
        self.assertEqual(outcome.execution_state,'validated_shadow')
        self.assertFalse(outcome.semantic_verified);self.assertFalse(outcome.publication_allowed)

    def test_safe_transport_trace_timeout_stage_and_unknown_cost(self):
        attempts=[]
        def handle(request):
            attempts.append(1);trace=request.extensions['trace']
            trace('connection.connect_tcp.started',dict(host='DO-NOT-LOG-SECRET'))
            trace('connection.connect_tcp.complete',dict(return_value='DO-NOT-LOG-SECRET'))
            trace('http11.send_request_body.complete',dict(body='DO-NOT-LOG-COMMENT'))
            trace('http11.receive_response_headers.failed',dict(exception=httpx.ReadTimeout('DO-NOT-LOG-SECRET')))
            raise httpx.ReadTimeout('DO-NOT-LOG-SECRET')
        client=provider.SiliconFlowShadowJudge(self.config,transport=httpx.MockTransport(handle),encoding=SyntheticEncoding())
        self.clients.append(client)
        with self.assertRaises(provider.ShadowProviderError):client.judge(model_payload(request_for()))
        self.assertEqual(attempts,[1]);self.assertFalse(client.metrics['usage_known'])
        self.assertNotIn('cost_cny_at_peak_rates',client.metrics)
        self.assertEqual(client.metrics['failure_stage'],'sdk_request')
        self.assertIn('ReadTimeout',client.metrics['exception_types'])
        self.assertEqual(len(client.metrics['transport_trace']),4)
        for event in client.metrics['transport_trace']:self.assertGreaterEqual(event['elapsed_ms'],0)
        for word in ('DO-NOT-LOG-SECRET','DO-NOT-LOG-COMMENT','SYNTHETIC-ONLY'):
            self.assertNotIn(word,canonical_json(client.metrics))
        client.close();self.assertTrue(client.client.is_closed())


if __name__=='__main__':unittest.main()
