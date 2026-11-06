"""Fixed N experiment orchestration with synthetic P0 sources and Mock HTTP only."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import run_rag_shadow_boundaries as entry
from scripts import run_rag_shadow_first as first
from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, JudgeFactory
from data_analysis_agent.agents.review_structured_evidence import ShadowOutcome
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.jobs.submission import configured_identity
from test_review_structured_shadow import verified
import test_p5b1_structured_evidence_design as b1


class BoundaryEntryTests(unittest.TestCase):
    OS_RECEIPTS = []

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='n123-synthetic-')))
        self.shadow = self.root/'.env.shadow'; self.business = self.root/'.env'
        self.shadow.write_text(f'SHADOW_MODEL_ID={provider.MODEL}\nSHADOW_BASE_URL={provider.BASE_URL}\n'
                               'SHADOW_API_KEY=SYNTHETIC-SHADOW-KEY\n', encoding='utf-8')
        self.business.write_text('LLM_MODEL_ID=SYNTHETIC\nLLM_BASE_URL=https://offline.invalid/v1\n'
            'LLM_API_KEY=SYNTHETIC-MAIN-KEY\nDATABASE_URL=postgresql://synthetic.invalid/synthetic\n'
            'RAG_PYTHON=SYNTHETIC-PYTHON\nRAG_MODEL_CACHE=SYNTHETIC-CACHE\n', encoding='utf-8')
        _, identity = configured_identity(self.business)
        self.scope = {**identity, **deepcopy(entry.BOUND)}
        self.enterContext(patch.object(entry, 'EXPECTED_PROFILE', content_sha256([identity[k]
            for k in ('model', 'base_url', 'prompt_version')])))
        self.enterContext(patch.object(b1, 'SCOPE', self.scope))
        self.enterContext(patch.object(entry, 'OUTPUT_DIR', self.root/'run'))
        self.events = []; self.factories = []; self.closes = []
        for target in ('psycopg.connect', 'socket.create_connection', 'httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))
        # Keep Windows system variables required by the child's asyncio/Winsock.
        self.enterContext(patch.dict('os.environ', {k:v for k,v in os.environ.items()
            if not k.startswith(('LLM_', 'RAG_', 'SHADOW_', 'OPENAI_')) and k != 'DATABASE_URL'}, clear=True))

    def preflight(self, **kwargs):
        return entry.preflight(self.shadow, self.business, **kwargs)

    def run_fixed(self, **kwargs):
        return entry.run_authorized(self.shadow, self.business,
            business_authorized=True, estimated_budget_risk_accepted=True, **kwargs)

    def install_ports(self, *, mode='actual_mock_http', changes=None, empty_case=None, source_error=False):
        def service(db, scope, settings):
            self.assertEqual(scope, self.scope)
            def search(query):
                self.events.append(query)
                if source_error:
                    raise ValueError('SYNTHETIC-PRIVATE-ERROR')
                contract = entry.build_query_plan(query.question, scope).task_contract
                rows = [] if query.question == empty_case else [('合成样机仅供测试。', '合成样机仅供测试', 1)]
                _, result, _ = verified(contract, rows)
                return result
            return SimpleNamespace(search=search)

        outer = self
        class Adapter:
            def __init__(self, **kwargs):
                outer.assertEqual(kwargs['settings'].timeout_seconds, 30.0)
                factory = kwargs['judge_factory']; outer.factories.append(factory)
                outer.assertEqual(factory.entrypoint,
                    'data_analysis_agent.agents.review_shadow_siliconflow:create_judge')
                self.config = json.loads(factory.config_json)
                self.settings = kwargs['settings']

            def run(self, contract, result, **kwargs):
                outer.assertTrue(self.config['estimated_budget_risk_accepted'])
                if mode == 'actual_mock_http':
                    run = StructuredEvidenceShadow(settings=self.settings,
                        judge_factory=JudgeFactory(entrypoint='fixtures.shadow_siliconflow_mock:create_mock_judge',
                                                   config_json=canonical_json(self.config))).run(contract, result, **kwargs)
                    if run.diagnostics['process_id'] is not None:
                        outer.OS_RECEIPTS.append(dict(test=outer.id(), **run.debug_record()))
                    return run
                cid = kwargs['request_id']; digest = content_sha256(cid)
                usage = dict(request_id=cid, input_sha256=digest, usage_known=True, state='response_received',
                    model_calls=1, reasoning_tokens=0, reasoning_content_present=False, finish_reason='stop',
                    response_model=provider.MODEL, prompt_version=provider.PROMPT_VERSION,
                    system_prompt_sha256=entry.EXPECTED_PROMPT, cost_cny_at_peak_rates='0.0004')
                usage.update(changes or {})
                if mode != 'missing_usage':
                    Path(self.config['usage_file']).write_text(canonical_json(usage), encoding='utf-8')
                diagnostics = dict(input_sha256=digest, total_ms=1.0, reaped=mode!='cleanup_failed', threads_released=True)
                outcome = ShadowOutcome('output_invalid' if mode=='invalid_output' else 'validated_shadow', ())
                return SimpleNamespace(outcome=outcome, diagnostics=diagnostics, judgments=None,
                    debug_record=lambda:dict(outcome=dict(publication_allowed=False), execution=diagnostics))

        self.enterContext(patch.object(entry, 'create_semantic_service', service))
        self.enterContext(patch.object(entry, 'StructuredEvidenceShadow', Adapter))
        self.enterContext(patch.object(entry, 'close_embedding_processes', lambda:self.closes.append(True)))

    def test_fixed_questions_scope_and_cli_do_not_allow_expansion(self):
        self.assertEqual(tuple(x[:2] for x in entry.CASES), (
            ('N1', '是否有安装师傅态度很差的评论？'), ('N2', '有无客服态度很好的反馈？'), ('N3', '有没有噪音不大的反馈？')))
        self.assertEqual(entry.BOUND, dict(source='reviews_csv', dataset_version='reviews-csv',
            product_ids=['100013588055'], date_from='2021-09-11', date_to='2022-08-26', timezone='UTC'))
        self.assertEqual(tuple(c[0] for c in first.CASES), ('S1', 'S2', 'S3'))
        for flag in ('--question', '--case', '--product-id', '--top-k', '--model', '--output-dir', '--shadow-env'):
            with self.subTest(flag=flag), patch.object(sys, 'argv', ['entry', flag, 'changed']), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit): entry.main()

    def test_offline_preflight_does_not_create_worker_encoder_repository_or_files(self):
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=AssertionError('no worker')), \
             patch.object(entry, 'create_semantic_service', side_effect=AssertionError('no repository')):
            check = self.preflight()
        self.assertTrue(check['shadow_key_configured']); self.assertTrue(check['strict_schema']=='verified_by_saved_probe')
        self.assertFalse(check['spending_authorized']); self.assertFalse(check['real_calls_executed'])
        self.assertEqual([c['required_fact_ids'] for c in check['cases']], [['installer_bad'], ['service_good'], ['noise_low']])
        self.assertEqual([c['query']['top_k'] for c in check['cases']], [5,5,5])
        self.assertIn('native_tokenizer_binding_unverified', check['missing'])
        self.assertIn('fixed_business_data_and_send_authorization_required', check['missing'])
        self.assertNotIn('SYNTHETIC-SHADOW-KEY', canonical_json(check)); self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_both_explicit_authorizations_required_before_any_side_effect(self):
        with patch.object(entry, 'create_semantic_service', side_effect=AssertionError('no source read')):
            for data, risk in ((False,False), (True,False), (False,True)):
                with self.subTest(data=data,risk=risk), self.assertRaisesRegex(ValueError, 'preflight_incomplete'):
                    entry.run_authorized(self.shadow,self.business,business_authorized=data,estimated_budget_risk_accepted=risk)
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_profile_prompt_budget_and_space_drift_stop_offline(self):
        for obj, attr, value in ((entry, 'EXPECTED_PROFILE', '0'*64), (provider, 'SYSTEM_PROMPT', 'changed'),
                                 (provider, 'OUTPUT_LIMIT', 2501), (entry, 'SPACE_ID', 'changed')):
            with self.subTest(attr=attr), patch.object(obj,attr,value):
                check=self.preflight(business_authorized=True,estimated_budget_risk_accepted=True)
                self.assertTrue(check['missing']); self.assertEqual(check['status'],'blocked')

    def test_three_real_local_workers_mock_http_keep_results_separate_and_claim_once(self):
        self.install_ports()
        result = self.run_fixed()
        self.assertEqual(result['status'], 'completed'); self.assertEqual(len(self.events), 3)
        self.assertEqual([q.question for q in self.events], [c[1] for c in entry.CASES])
        self.assertEqual([q.top_k for q in self.events], [5,5,5]); self.assertEqual(len(self.factories),3)
        self.assertFalse(result['publication_allowed']); self.assertFalse(result['semantic_verified'])
        self.assertEqual(result['unverified_usage_or_cost_cases'],0); self.assertEqual(len(self.closes),1)
        for cid, _, _ in entry.CASES:
            folder=entry.OUTPUT_DIR/cid
            self.assertTrue((folder/'v2-assessment.json').exists()); self.assertTrue((folder/'shadow-judgments.json').exists())
            usage=json.loads((folder/'provider-usage.json').read_text(encoding='utf-8'))
            self.assertEqual(usage['model_calls'],1)
            for field in ('request_preparation_ms','sdk_call_ms','response_validation_ms','provider_elapsed_ms'):
                self.assertGreaterEqual(usage[field],0)
            execution=json.loads((folder/'shadow-execution.json').read_text(encoding='utf-8'))
            self.assertTrue(execution['execution']['reaped']); self.assertTrue(execution['execution']['threads_released'])
            self.assertEqual(execution['outcome']['permitted_target_fact_ids'],[])
        before=(entry.OUTPUT_DIR/'execution-summary.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'preflight_incomplete'): self.run_fixed()
        self.assertEqual(before,(entry.OUTPUT_DIR/'execution-summary.json').read_bytes()); self.assertEqual(len(self.events),3)

    def test_empty_candidate_makes_no_call_and_does_not_replace_question(self):
        self.install_ports(empty_case=entry.CASES[0][1]); result=self.run_fixed()
        self.assertEqual(result['status'],'completed'); self.assertEqual(len(self.events),3)
        first_case=result['cases'][0]
        self.assertEqual(first_case['status'],'skipped_empty'); self.assertEqual(first_case['model_calls'],0)
        self.assertFalse((entry.OUTPUT_DIR/'N1/provider-usage.json').exists())
        self.assertEqual(first_case['risk_coverage'],'not_reviewed')

    def test_usage_identity_reasoning_cost_and_execution_errors_stop_without_retry(self):
        scenarios=[('missing_usage',None),('invalid_output',None),('cleanup_failed',None),
            ('scripted',dict(usage_known=False)),('scripted',dict(response_model='changed')),
            ('scripted',dict(reasoning_tokens=1)),('scripted',dict(finish_reason='length')),
            ('scripted',dict(input_sha256='changed')),('scripted',dict(system_prompt_sha256='changed')),
            ('scripted',dict(cost_cny_at_peak_rates='NaN')),('scripted',dict(cost_cny_at_peak_rates='0.109'))]
        for index,(mode,changes) in enumerate(scenarios):
            with self.subTest(mode=mode,changes=changes), patch.object(entry,'OUTPUT_DIR',self.root/f'failure-{index}'):
                self.events.clear(); self.install_ports(mode=mode,changes=changes)
                result=self.run_fixed()
                self.assertEqual(result['status'],'stopped'); self.assertEqual(len(self.events),1)
                self.assertEqual([r['status'] for r in result['cases'][1:]],['not_started','not_started'])
                self.assertFalse(result['publication_allowed']); self.assertFalse(result['actual_bill_known'])

    def test_source_failure_stops_before_any_judge_and_error_text_is_not_leaked(self):
        self.install_ports(source_error=True); result=self.run_fixed()
        self.assertEqual(result['status'],'stopped'); self.assertEqual(len(self.events),1)
        self.assertEqual(self.factories,[]); self.assertEqual(len(self.closes),1)
        self.assertNotIn('SYNTHETIC-PRIVATE-ERROR',canonical_json(result))
        self.assertEqual(result['cases'][0]['failure_stage'],'source_retrieval')
        with patch.object(entry, 'OUTPUT_DIR', self.root/'cancelled'), \
             patch.object(entry, 'create_semantic_service', side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_fixed()['stop_reason'], 'cancelled')
        with patch.object(entry, 'OUTPUT_DIR', self.root/'cleanup'), \
             patch.object(entry, 'close_embedding_processes', side_effect=OSError('SYNTHETIC-PRIVATE-ERROR')):
            self.assertEqual(self.run_fixed()['stop_reason'], 'embedding_cleanup_failed')


if __name__=='__main__': unittest.main()
