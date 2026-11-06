"""Private failure retention only; actual SDK/worker, Mock HTTP, no real calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents import review_shadow_diagnostics as diagnostics
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from test_review_structured_shadow import verified, request_for
import test_p5b1_structured_evidence_design as b1


class FailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        # Existing ignored outputs boundary, never an old experiment directory.
        self.directory = Path(self.enterContext(TemporaryDirectory(prefix='diagnostic-test-', dir=diagnostics.PRIVATE_ROOT))).resolve()
        self.env = self.directory/'synthetic.env'
        self.env.write_text(f'SHADOW_MODEL_ID={provider.MODEL}\nSHADOW_BASE_URL={provider.BASE_URL}\n'
                            'SHADOW_API_KEY=SYNTHETIC-INDEPENDENT-KEY\n', encoding='utf-8')
        self.config = provider.scoped_config(self.env)
        self.count = 0
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))

    def run_reply(self, reply, *, rows=None, enabled=True, mode='valid', timeout=5, cancel_event=None, marker=None):
        self.count += 1
        target = self.directory/f'failure-{self.count}'
        c, result, _ = verified(rows=rows)
        config = self.config.model_dump(mode='json') | dict(reply=reply, mode=mode,
            marker=str(marker) if marker else None)
        adapter = StructuredEvidenceShadow(settings=ShadowSettings(enabled=True, timeout_seconds=timeout),
            judge_factory=JudgeFactory(entrypoint='fixtures.shadow_diagnostic_http_mock:create_mock_judge',
                config_json=canonical_json(config)), development_diagnostic_dir=str(target) if enabled else None)
        run = adapter.run(c, result, trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                          request_id='SYNTHETIC-REQUEST', cancel_event=cancel_event)
        self.assertFalse(run.outcome.publication_allowed)
        self.assertFalse(run.outcome.semantic_verified)
        self.assertFalse(run.outcome.permitted_target_fact_ids)
        self.assertTrue(run.diagnostics['reaped'])
        self.assertTrue(run.diagnostics['threads_released'])
        for private in ('raw_model_text', str(target), 'SYNTHETIC-INDEPENDENT-KEY', 'DO-NOT-LOG-SECRET'):
            self.assertNotIn(private, canonical_json(run.debug_record()))
        return run, target

    def response(self, rows=None):
        return b1.response(request_for(rows=rows))

    def read_failure(self, target):
        path = target/'failure.json'
        self.assertLessEqual(path.stat().st_size, diagnostics.MAX_FILE_BYTES)
        self.assertEqual(list(target.iterdir()), [path])
        value = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(value['evidence_status'], 'unvalidated_diagnostic_only')
        self.assertFalse(value['publication_allowed'])
        return value

    def test_valid_chinese_emoji_absolute_codepoints_no_retention(self):
        rows = [('前言🙂中文。客服态度差😀。后续','客服态度差😀',1)]
        output = self.response(rows)
        run, target = self.run_reply(canonical_json(output), rows=rows)
        self.assertEqual(run.outcome.execution_state, 'validated_shadow')
        self.assertFalse(target.exists())

    def test_relative_out_of_bounds_and_text_mismatch_localized(self):
        rows = [('前言🙂中文。客服态度差😀。后续','客服态度差😀',1)]
        original = self.response(rows)
        for mode in ('relative','outside','text','utf8'):
            with self.subTest(mode=mode):
                output = deepcopy(original); ref = output['fact_judgments'][0]['support_refs'][0]
                if mode == 'relative': ref.update(start=0, end=len(rows[0][1]))
                if mode == 'outside': ref['end'] = 1000
                if mode == 'text': ref['text'] = '客服态度好😀'
                if mode == 'utf8':
                    start = rows[0][0].index(rows[0][1]); ref.update(
                        start=len(rows[0][0][:start].encode()), end=len(rows[0][0][:start+len(rows[0][1])].encode()))
                raw = canonical_json(output)
                run, target = self.run_reply(raw, rows=rows)
                self.assertEqual(run.outcome.reason_codes, ('citation_span_mismatch',))
                self.assertIsNone(run.judgments)
                saved = self.read_failure(target)
                self.assertEqual(saved['raw_model_text'], raw)
                item = saved['failure_details'][0]
                self.assertEqual(item['candidate_key'], ref['key'])
                self.assertEqual(item['reference_type'], 'support_refs')
                self.assertEqual(item['reference_index'], 0)
                self.assertEqual(item['start'], ref['start'])
                self.assertEqual(item['end'], ref['end'])
                self.assertEqual(item['coordinate_unit'], 'unicode_code_point')
                self.assertEqual(item['matches_if_quote_relative'], mode == 'relative')

    def test_context_veto_support_coordinates_and_multi_candidate(self):
        text = '客服态度差。更新：那句差评不实，我撤回。'
        rows = [(text,'客服态度差',1)]
        output = self.response(rows); request = request_for(rows=rows)
        veto = b1.span(request.candidates[0], '更新：那句差评不实，我撤回。')
        row = output['fact_judgments'][0]
        row.update(status='refuted', support_refs=[], context_veto_refs=[veto], reason_code='context_veto')
        run, target = self.run_reply(canonical_json(output), rows=rows)
        self.assertEqual(run.outcome.execution_state, 'validated_shadow'); self.assertFalse(target.exists())
        bad = deepcopy(output); bad['fact_judgments'][0]['context_veto_refs'][0].update(start=0,end=len(veto['text']))
        run, target = self.run_reply(canonical_json(bad), rows=rows)
        self.assertEqual(self.read_failure(target)['failure_details'][0]['reference_type'], 'context_veto_refs')
        bad = self.response(rows); bad['fact_judgments'][0]['support_refs'] = [veto]
        run, target = self.run_reply(canonical_json(bad), rows=rows)
        self.assertEqual(run.outcome.reason_codes, ('citation_span_mismatch',))
        rows = [('前缀。客服态度差。','客服态度差',i) for i in (1,2,3)]
        output = self.response(rows); output['fact_judgments'][1]['support_refs'][0]['end'] += 1
        run, target = self.run_reply(canonical_json(output), rows=rows)
        self.assertEqual(self.read_failure(target)['failure_details'][0]['judgment_index'], 1)

    def test_invalid_json_schema_identity_and_binding_remain_rejected(self):
        variants = ['{']
        output = self.response(); output['fact_judgments'][0]['support_refs'][0]['start'] = -1
        variants.append(canonical_json(output))
        output = self.response(); output['fact_judgments'][0]['support_refs'][0]['source_identity_sha256'] = '0'*64
        variants.append(canonical_json(output))
        output = self.response(); output['input_sha256'] = '0'*64
        variants.append(canonical_json(output))
        output = self.response(); output['diagnostic_dir'] = str(self.directory/'model-chosen')
        variants.append(canonical_json(output))
        for raw in variants:
            run, target = self.run_reply(raw)
            self.assertIn(run.outcome.execution_state, ('output_invalid','model_failed'))
            self.assertIsNone(run.judgments)
            self.assertTrue(self.read_failure(target)['error_codes'])
        self.assertFalse((self.directory/'model-chosen').exists())

    def test_provider_failures_and_size_limits_do_not_retain(self):
        for mode in ('drift','sdk_error','oversize_http','oversize_content'):
            run, target = self.run_reply(canonical_json(self.response()), mode=mode)
            self.assertEqual(run.outcome.execution_state, 'model_failed')
            self.assertFalse(target.exists())

    def test_default_off_does_not_save_invalid_raw(self):
        output = self.response(); output['fact_judgments'][0]['support_refs'][0]['end'] += 1
        run, target = self.run_reply(canonical_json(output), enabled=False)
        self.assertEqual(run.outcome.reason_codes, ('citation_span_mismatch',))
        self.assertFalse(target.exists())
        self.assertNotIn('failure_diagnostic_saved', run.diagnostics)

    def test_redaction_and_private_permissions_atomic_no_overwrite(self):
        output = self.response(); row = output['fact_judgments'][0]
        row['support_refs'][0]['end'] += 1
        row['rationale'] = 'Authorization: Bearer SYNTHETIC-INDEPENDENT-KEY; database_url=postgresql://user:pass@host/db'
        run, target = self.run_reply(canonical_json(output))
        saved = self.read_failure(target)
        self.assertNotIn('SYNTHETIC-INDEPENDENT-KEY', saved['raw_model_text'])
        self.assertNotIn('user:pass', saved['raw_model_text'])
        self.assertIn('[REDACTED]', saved['raw_model_text'])
        if os.name == 'nt':
            acl = subprocess.run(['icacls.exe',str(target/'failure.json')], capture_output=True, check=True)
            # No inherited broad groups: owner-only DACL is inherited from the private directory.
            for broad in (b'Everyone',b'BUILTIN\\Users',b'Authenticated Users'):
                self.assertNotIn(broad, acl.stdout)
        else:
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target/'failure.json').stat().st_mode), 0o600)
        before = (target/'failure.json').read_bytes()
        with self.assertRaises(FileExistsError): diagnostics._atomic_write(target/'failure.json', {'wrong':True})
        self.assertEqual(before, (target/'failure.json').read_bytes())

    def test_write_failure_cannot_change_invalid_outcome(self):
        request = request_for(); capture = diagnostics.FailureCapture(str(self.directory/'disk-fail'),
            request.model_dump(mode='json'), 65536)
        output = self.response(); output['fact_judgments'][0]['support_refs'][0]['end'] += 1
        capture.capture(canonical_json(output))
        with patch.object(diagnostics, '_atomic_write', side_effect=OSError('secret disk error')):
            with self.assertRaises(OSError): capture.finish()
        # Parent outcome is independent of retention; real worker catches retention errors.
        run, target = self.run_reply(canonical_json(output), mode='diagnostic_write_failure')
        self.assertEqual(run.outcome.reason_codes, ('citation_span_mismatch',))
        self.assertFalse((target/'failure.json').exists())
        self.assertFalse(run.diagnostics['failure_diagnostic_saved'])

    def test_disabled_default_never_prepares_or_writes(self):
        target = self.directory/'disabled'
        run = StructuredEvidenceShadow(development_diagnostic_dir=str(target)).run(
            None, None, trusted_contract_sha256='invalid', request_id='SYNTHETIC')
        self.assertEqual(run.outcome.execution_state, 'disabled')
        self.assertEqual(run.diagnostics['attempts'], 0)
        self.assertFalse(target.exists())

    def test_cancellation_after_mock_http_starts(self):
        marker = self.directory/'started'; event = Event()
        def cancel():
            end = monotonic()+5
            while not marker.exists() and monotonic()<end: sleep(.01)
            event.set()
        watcher = Thread(target=cancel); watcher.start()
        try:
            run, target = self.run_reply(None, mode='hang', marker=marker, cancel_event=event)
        finally: watcher.join(timeout=6)
        self.assertTrue(marker.exists())
        self.assertEqual(run.outcome.execution_state, 'cancelled'); self.assertFalse(target.exists())

    def test_path_must_be_new_inside_ignored_outputs_not_model_controlled(self):
        with self.assertRaises(ValueError): diagnostics.destination(str(self.env))
        with self.assertRaises(ValueError): diagnostics.destination(str(self.directory.parent.parent.parent/'outside'))
        with self.assertRaises(ValueError): diagnostics.destination('relative')


if __name__ == '__main__': unittest.main()
