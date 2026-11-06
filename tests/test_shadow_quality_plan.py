"""Expectations independent of the tested model; checks do not score an LLM."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.core.serialization import canonical_json
from test_review_structured_shadow import request_for
import test_p5b1_structured_evidence_design as b1

FIXTURE = Path(__file__).parent/'fixtures/shadow_quality_cases_v1.json'


def load_cases():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def prepare_cases(cases):
    contract=b1.contract(cases[0]['question'])
    assert all(c['question']==cases[0]['question'] for c in cases)
    rows=[(c['review_text'],c['quote'],int(c['id'][1:])) for c in cases]
    request=request_for(contract,rows)
    return request.model_copy(update={'request_id':'SYNTHETIC-QUALITY-'+'-'.join(c['id'] for c in cases)})


def authored_output(request,cases):
    output=b1.response(request)
    for row,case,candidate in zip(output['fact_judgments'],cases,request.candidates):
        status=case['expected_status'];veto=case.get('veto_text')
        row.update(status=status,support_refs=[b1.span(candidate)] if status=='supported' else [],
            refutation_refs=[b1.span(candidate)] if status=='refuted' and not veto else [],
            context_veto_refs=[b1.span(candidate,veto)] if veto else [],
            reason_code='context_veto' if veto else dict(supported='direct_report',refuted='explicit_refutation',
                not_supported='no_direct_support',unknown='semantics_unknown')[status],
            rationale=case['expected_reason'])
    return output


class QualityPlanTests(unittest.TestCase):
    def setUp(self):
        self.data=load_cases();self.cases=self.data['cases']
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request','openai.OpenAI',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('real I/O forbidden')))

    def test_fixed_cases_have_targets_context_reasons_and_distinct_ids(self):
        self.assertEqual(len(self.cases),15)
        self.assertEqual(len({c['id'] for c in self.cases}),15)
        self.assertEqual(Counter(c['expected_status'] for c in self.cases),
                         dict(supported=3,refuted=2,not_supported=8,unknown=2))
        self.assertEqual([cid for group in self.data['batches'] for cid in group],[c['id'] for c in self.cases])
        for case in self.cases:
            self.assertTrue(case['target_fact']);self.assertTrue(case['expected_reason'])
            self.assertIn(case['quote'],case['review_text'])
            request=prepare_cases([case])
            self.assertEqual(request.contract.required_facts.items[0].id,case['fact_id'])
            self.assertEqual(request.contract.scope.product_ids,('SYNTHETIC-P1',))
            wire=protocol.model_payload(request)
            for field in ('expected_status','expected_reason','target_fact','provenance'):
                self.assertNotIn(field,wire)
            self.assertNotIn(case['expected_reason'],wire)
            self.assertNotIn(self.data['purpose'],wire)

    def test_authored_single_cases_preserve_four_states_and_exact_refs(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                request=prepare_cases([case]);output=authored_output(request,[case])
                result=protocol.audit_output(request,canonical_json(output))
                self.assertEqual(result.execution_state,'validated_shadow')
                self.assertEqual(result.structural_goal_satisfied,case['expected_status']=='supported')
                self.assertEqual(result.permitted_target_fact_ids,())
                self.assertFalse(result.publication_allowed);self.assertFalse(result.semantic_verified)
                item=output['fact_judgments'][0]
                self.assertEqual(item['status'],case['expected_status'])
                for kind in ('support_refs','refutation_refs','context_veto_refs'):
                    for ref in item[kind]:
                        self.assertEqual(case['review_text'][ref['start']:ref['end']],ref['text'])
                        if kind!='context_veto_refs':
                            self.assertGreaterEqual(ref['start'],request.candidates[0].quote_start)
                            self.assertLessEqual(ref['end'],request.candidates[0].quote_end)
                        self.assertEqual(ref['source_identity_sha256'],request.candidates[0].source_identity_sha256)

    def test_three_batches_fit_current_estimate_without_truncation_or_schema_change(self):
        lookup={c['id']:c for c in self.cases}
        for group in self.data['batches']:
            cases=[lookup[i] for i in group];request=prepare_cases(cases)
            self.assertEqual(len(request.candidates),5)
            payload=protocol.model_payload(request);body=provider.request_body(payload)
            self.assertLessEqual(provider.estimated_input_tokens(body),8000)
            self.assertEqual(body['max_tokens'],2500);self.assertFalse(body['enable_thinking'])
            self.assertEqual(len(json.loads(payload)['candidates']),5)
            self.assertEqual([c.rank for c in request.candidates],[1,2,3,4,5])
            for candidate,case in zip(request.candidates,cases):
                self.assertEqual(candidate.review_text,case['review_text'])
            output=authored_output(request,cases)
            self.assertEqual(len(output['fact_judgments']),5)
            result=protocol.audit_output(request,canonical_json(output))
            self.assertEqual(result.execution_state,'validated_shadow')
            self.assertFalse(result.publication_allowed)
        for name,value in protocol.schemas().items():
            path=Path(__file__).parents[1]/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json'
            self.assertEqual(value,json.loads(path.read_text(encoding='utf-8')))

    def test_bad_basis_identity_goal_and_quote_outside_support_fail_closed(self):
        request=prepare_cases([self.cases[11]]);output=authored_output(request,[self.cases[11]])
        bad=[]
        changed=deepcopy(output);changed['fact_judgments'][0]['fact_id']='made_up';bad.append(changed)
        changed=deepcopy(output);changed['fact_judgments'][0]['support_refs'][0]['source_identity_sha256']='0'*64;bad.append(changed)
        changed=deepcopy(output);span=changed['fact_judgments'][0]['support_refs'][0]
        text='配送人员很耐心';start=request.candidates[0].review_text.index(text)
        span.update(start=start,end=start+len(text),text=text);bad.append(changed)
        for value in bad:
            result=protocol.audit_output(request,canonical_json(value))
            self.assertEqual(result.execution_state,'output_invalid');self.assertFalse(result.publication_allowed)
        for case in (self.cases[2],self.cases[3]):
            request=prepare_cases([case]);value=authored_output(request,[case])
            value['fact_judgments'][0].update(refutation_refs=[],context_veto_refs=[])
            self.assertEqual(protocol.audit_output(request,canonical_json(value)).execution_state,'output_invalid')

    def test_structure_does_not_validate_a_deliberately_wrong_semantic_label(self):
        case=self.cases[1];request=prepare_cases([case]);wrong=dict(case,expected_status='supported')
        result=protocol.audit_output(request,canonical_json(authored_output(request,[wrong])))
        self.assertEqual(result.execution_state,'validated_shadow')
        self.assertFalse(result.semantic_verified);self.assertFalse(result.publication_allowed)

    def test_identical_quote_needs_full_context_and_does_not_import_other_buyer_veto(self):
        lookup={c['id']:c for c in self.cases}
        cases=[lookup[cid] for cid in ('C12','C13','C14')]
        self.assertEqual(len({c['quote'] for c in cases}),1)
        self.assertEqual([c['expected_status'] for c in cases],
                         ['supported','not_supported','not_supported'])
        # A different buyer's retraction cannot remove the first buyer's support.
        cases=[lookup['C12'],lookup['C03']]
        request=prepare_cases(cases)
        result=protocol.audit_output(request,canonical_json(authored_output(request,cases)))
        self.assertTrue(result.structural_goal_satisfied)
        self.assertFalse(result.publication_allowed)


if __name__=='__main__':unittest.main()
