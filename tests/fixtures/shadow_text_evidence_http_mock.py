"""Actual SDK with Mock HTTP only, including disposable-worker failures."""
from pathlib import Path
from time import sleep
from unittest.mock import patch

import httpx

from experiments import text_evidence_shadow as port
from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from fixtures.shadow_siliconflow_mock import completion


def create_mock_judge(reply, capture_file, mode='valid', **config):
    for target in ('psycopg.connect','socket.create_connection','socket.socket.connect',
                   'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request',
                   'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
        patch(target,side_effect=AssertionError('real I/O forbidden in text worker')).start()
    if mode=='write_failure':
        patch.object(port.privacy,'_atomic_write',side_effect=OSError('SYNTHETIC disk failure')).start()
    diagnostic = config.pop('text_diagnostic',None)
    def handle(request):
        body = strict_json_loads(request.content.decode('utf-8'))
        with Path(capture_file).open('a',encoding='utf-8') as out: out.write(canonical_json(body)+'\n')
        if mode=='hang':
            while True: sleep(.02)
        if mode=='sdk_error': raise httpx.ReadTimeout('SYNTHETIC SDK failure',request=request)
        value = completion(strict_json_loads(body['messages'][1]['content']))
        value['choices'][0]['message']['content'] = reply
        if mode=='drift': value['model'] = 'wrong-model'
        if mode=='length': value['choices'][0]['finish_reason'] = 'length'
        return httpx.Response(200,json=value)
    return port.TextEvidenceJudge(port.provider.SiliconFlowShadowConfig.model_validate_json(canonical_json(config)),
        text_diagnostic=diagnostic,transport=httpx.MockTransport(handle))
