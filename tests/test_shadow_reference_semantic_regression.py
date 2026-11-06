"""Offline frozen failure replay and exact-location proposal, never model accuracy."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from fixtures.shadow_siliconflow_mock import completion, SyntheticEncoding
from shadow_reference_locator_prototype import locate_fragment
from test_review_structured_shadow import request_for
from test_shadow_quality_plan import authored_output

ROOT = Path(__file__).resolve().parents[1]
PINS = strict_json_loads((ROOT/'tests/fixtures/shadow_single_failure_regression_v1.json').read_text(encoding='utf-8'))
CANDIDATE = ROOT/'tests/fixtures/shadow_siliconflow_prompt_v3_candidate.txt'


class OfflineTests(unittest.TestCase):
    def setUp(self):
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))

    def assert_closed(self, outcome):
        self.assertFalse(outcome.publication_allowed)
        self.assertFalse(outcome.semantic_verified)
        self.assertEqual(outcome.permitted_target_fact_ids, ())


class LocatorAndSemanticsTests(OfflineTests):
    def locate(self, request, text, field='support_refs', index=0, **changes):
        c = request.candidates[index]
        args = dict(key=c.key,source_identity_sha256=c.source_identity_sha256,reference_type=field,text=text)
        return locate_fragment(request, **(args | changes))

    def test_unique_absolute_codepoints_and_source_binding(self):
        request = request_for(rows=[('前缀🙂客服态度差。','客服态度差',71),('配送人员不耐烦。','配送人员不耐烦',72)])
        proposal = self.locate(request, '客服态度差')
        self.assertEqual(proposal['status'],'unique')
        self.assertEqual(proposal['locations'],[(3,8)])
        self.assertEqual(proposal['review_id'],71)
        self.assertFalse(proposal['publication_allowed']); self.assertFalse(proposal['semantic_verified'])
        self.assertEqual(self.locate(request,'客服态度差',source_identity_sha256='0'*64)['status'],'identity_mismatch')
        self.assertEqual(self.locate(request,'客服态度差',key=request.candidates[1].key)['status'],'identity_mismatch')
        self.assertEqual(self.locate(request,'配送人员不耐烦')['status'],'missing')

    def test_duplicates_overlaps_missing_and_role_boundaries_fail_closed(self):
        request = request_for(rows=[('哈哈哈。其他内容。哈哈哈。','哈哈哈',71)])
        self.assertEqual(self.locate(request,'哈哈哈')['status'],'unique')
        self.assertEqual(self.locate(request,'哈哈哈','context_veto_refs')['status'],'ambiguous')
        self.assertEqual(self.locate(request,'哈哈')['locations'],[(0,2),(1,3)])
        self.assertEqual(self.locate(request,'其他内容')['status'],'missing')
        self.assertEqual(self.locate(request,'其他内容','refutation_refs')['status'],'missing')
        self.assertEqual(self.locate(request,'不存在')['status'],'missing')
        self.assertEqual(self.locate(request,'')['status'],'missing_text')
        self.assertEqual(self.locate(request,'哈哈哈','made_up')['status'],'unsupported_reference_type')
        self.assertEqual(self.locate(request,'哈哈 哈')['status'],'missing')
        changed = request.model_copy(update={'candidates':(request.candidates[0].model_copy(update={'review_text':'changed'}),)})
        self.assertEqual(self.locate(changed,'changed')['status'],'source_text_invalid')

    def test_v3_candidate_is_offline_and_request_schema_budget_unchanged(self):
        prompt = CANDIDATE.read_text(encoding='utf-8').rstrip('\n')
        self.assertIn('BEFORE lack of direct support',prompt)
        self.assertIn('NOT not_supported',prompt)
        self.assertIn('Later improvement does not erase',prompt)
        self.assertIn('rather than fabricate coordinates',prompt)
        self.assertIn('different buyers disagreeing',prompt)
        self.assertEqual(provider.PROMPT_VERSION,'p5-shadow-siliconflow-instructions-v2')
        self.assertEqual(sha256(provider.SYSTEM_PROMPT.encode()).hexdigest(),
                         '6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d')
        data = strict_json_loads((ROOT/'tests/fixtures/shadow_single_four_state_v1.json').read_text(encoding='utf-8'))
        from scripts.run_rag_shadow_quality import prepare_batch
        request = prepare_batch(data['cases'])[2]
        body = provider.request_body(protocol.model_payload(request))
        baseline = deepcopy(body); body['messages'][0]['content'] = prompt
        self.assertLessEqual(provider.estimated_input_tokens(body),8000)
        body['messages'][0]['content'] = baseline['messages'][0]['content']
        self.assertEqual(body,baseline)

    def test_authored_semantics_retraction_opposites_and_later_improvement(self):
        cases = [
            ('原句不凉。补记：我说不凉不属实，我撤回。','我说不凉不属实，我撤回。','refuted',None),
            ('开机不凉。补记：先前关于不凉的抱怨不实，我撤回。','开机不凉','refuted','补记：先前关于不凉的抱怨不实，我撤回。'),
            ('制冷很好，一直很满意。','制冷很好，一直很满意','not_supported',None),
            ('昨晚不凉，今天维修好了；昨晚的体验确实发生，我没有撤回。','昨晚不凉','supported',None),
        ]
        for review,quote,status,veto in cases:
            request = request_for(rows=[(review,quote,71)])
            output = self.authored(request,status,veto)
            outcome = protocol.audit_output(request,canonical_json(output))
            self.assertEqual(outcome.execution_state,'validated_shadow'); self.assert_closed(outcome)
            if status == 'refuted':
                row = output['fact_judgments'][0]
                row.update(refutation_refs=[],context_veto_refs=[])
                self.assertEqual(protocol.audit_output(request,canonical_json(output)).reason_codes,('refutation_without_basis',))
        request = request_for(rows=[('我家昨晚不凉。','我家昨晚不凉',71),('我家降温很好。','我家降温很好',72)])
        output = self.authored(request,'supported')
        output['fact_judgments'][1].update(status='not_supported',support_refs=[],reason_code='no_direct_support')
        outcome = protocol.audit_output(request,canonical_json(output))
        self.assertTrue(outcome.structural_goal_satisfied); self.assert_closed(outcome)

    @staticmethod
    def authored(request,status,veto=None):
        # Test expectations are explicitly authored, not inferred by Mock HTTP or by the locator.
        from test_shadow_semantic_boundaries import authored
        return authored(request,status,veto)


class FrozenRealFailureTests(OfflineTests):
    @classmethod
    def setUpClass(cls):
        folder = ROOT/PINS['artifact_dir']
        try:
            raw_file = (folder/'diagnostics/failure.json').read_bytes()
        except OSError:
            raise unittest.SkipTest('private frozen failure not accessible; it is not checked into Git')
        if sha256(raw_file).hexdigest() != PINS['failure_file_sha256']:
            raise AssertionError('frozen failure file changed')
        value = strict_json_loads(raw_file.decode('utf-8'))
        cls.raw = value['raw_model_text']
        if value['redaction_applied'] or sha256(cls.raw.encode()).hexdigest()!=PINS['raw_sha256'] or len(cls.raw.encode())!=PINS['raw_bytes']:
            raise AssertionError('frozen raw output changed')
        wire = strict_json_loads((folder/'model-input.json').read_text(encoding='utf-8'))
        wire.pop('input_sha256')
        wire['contract'] = strict_json_loads((folder/'contract.json').read_text(encoding='utf-8'))
        cls.request = protocol.parse(wire,protocol.JudgeInput)  # Offline envelope only, NOT a new P0 proof.
        if content_sha256(cls.request.model_dump(mode='json')) != PINS['input_sha256']:
            raise AssertionError('frozen input changed')
        cls.output = protocol.parse(cls.raw,protocol.JudgeOutput)
        cls.cases = strict_json_loads((ROOT/'tests/fixtures/shadow_single_four_state_v1.json').read_text(encoding='utf-8'))['cases']

    def test_raw_failure_and_labels_stay_frozen(self):
        details = []
        outcome = protocol.audit_output(self.request,self.raw,failure_details=details)
        self.assertEqual(outcome.execution_state,'output_invalid')
        self.assertEqual(outcome.reason_codes,(PINS['original_error'],)); self.assert_closed(outcome)
        self.assertFalse(details[0]['bounds_valid']); self.assertTrue(details[0]['text_matches'])
        self.assertEqual([j.status for j in self.output.fact_judgments],PINS['observed_statuses'])
        self.assertEqual([c['expected_status'] for c in self.cases],PINS['expected_statuses'])

    def test_d74_unique_location_is_not_an_automatic_repair(self):
        c = self.request.candidates[3];ref = self.output.fact_judgments[3].context_veto_refs[0]
        proposal = locate_fragment(self.request,key=ref.key,source_identity_sha256=ref.source_identity_sha256,
            reference_type='context_veto_refs',text=ref.text)
        self.assertEqual(proposal['locations'],[tuple(PINS['trusted_veto_range'])])
        self.assertEqual([ref.start,ref.end],PINS['original_veto_range'])
        self.assertEqual(proposal['evidence_status'],'unvalidated_diagnostic_only')
        self.assertEqual(protocol.audit_output(self.request,self.raw).execution_state,'output_invalid')
        self.assertEqual(sha256(self.raw.encode()).hexdigest(),PINS['raw_sha256'])

    def test_separately_authored_legal_reply_and_unreliable_basis_rejected(self):
        legal = authored_output(self.request,self.cases)
        outcome = protocol.audit_output(self.request,canonical_json(legal))
        self.assertEqual(outcome.execution_state,'validated_shadow'); self.assert_closed(outcome)
        self.assertEqual(legal['fact_judgments'][2]['status'],'refuted')
        self.assertTrue(legal['fact_judgments'][2]['refutation_refs'])
        self.assertEqual(legal['fact_judgments'][3]['context_veto_refs'][0]['end'],62)
        for variant in ('missing','identity','range'):
            wrong = deepcopy(legal);row = wrong['fact_judgments'][3]
            if variant == 'missing': row['context_veto_refs'] = []
            if variant == 'identity': row['context_veto_refs'][0]['source_identity_sha256'] = '0'*64
            if variant == 'range': row['context_veto_refs'][0]['end'] = 79
            rejected = protocol.audit_output(self.request,canonical_json(wrong))
            self.assertEqual(rejected.execution_state,'output_invalid'); self.assert_closed(rejected)

    def test_mock_sdk_candidate_prompt_compatibility_is_not_semantic_inference(self):
        prompt = CANDIDATE.read_text(encoding='utf-8').rstrip('\n')
        reply = authored_output(self.request,self.cases)
        calls = []
        def handle(request):
            body = strict_json_loads(request.content.decode('utf-8'));calls.append(body)
            value = completion(strict_json_loads(body['messages'][1]['content']))
            value['choices'][0]['message']['content'] = canonical_json(reply)
            return httpx.Response(200,json=value)
        with TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.env'
            path.write_text(f'SHADOW_MODEL_ID={provider.MODEL}\nSHADOW_BASE_URL={provider.BASE_URL}\nSHADOW_API_KEY=SYNTHETIC-ONLY\n',encoding='utf-8')
            with patch.object(provider,'SYSTEM_PROMPT',prompt):
                judge = provider.SiliconFlowShadowJudge(provider.scoped_config(path),
                    transport=httpx.MockTransport(handle),encoding=SyntheticEncoding())
                try: raw = judge.judge(protocol.model_payload(self.request))
                finally: judge.close()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['messages'][0]['content'],prompt)
        self.assertEqual(calls[0]['response_format'],provider.request_body(protocol.model_payload(self.request))['response_format'])
        self.assertEqual(protocol.audit_output(self.request,raw).execution_state,'validated_shadow')
        self.assertEqual(protocol.audit_output(self.request,self.raw).execution_state,'output_invalid')


if __name__ == '__main__': unittest.main()
