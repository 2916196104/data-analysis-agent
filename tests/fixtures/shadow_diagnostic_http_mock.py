"""Actual SiliconFlow SDK on Mock HTTP, with network/DB/model loading denied in child."""
import json
import os
from pathlib import Path
from time import sleep
from unittest.mock import patch

import httpx

from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.core.serialization import canonical_json
from fixtures.shadow_siliconflow_mock import completion, SyntheticEncoding


def create_mock_judge(reply=None, mode='valid', marker=None, **config):
    if mode == 'diagnostic_write_failure':
        patch('data_analysis_agent.agents.review_shadow_diagnostics._atomic_write',
              side_effect=OSError('DO-NOT-LOG-SECRET disk failure')).start()
    for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                   'httpx.AsyncHTTPTransport.handle_async_request',
                   'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
        patch(target, side_effect=AssertionError('real I/O forbidden in diagnostic test worker')).start()
    def handle(request):
        if marker:
            Path(marker).write_text(str(os.getpid()), encoding='ascii')
        if mode == 'hang':
            while True: sleep(.02)
        if mode == 'sdk_error':
            raise httpx.ReadTimeout('DO-NOT-LOG-SECRET', request=request)
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        value = completion(payload)
        if reply is not None:
            value['choices'][0]['message']['content'] = reply
        if mode == 'drift': value['model'] = 'wrong-model'
        if mode == 'oversize_http': return httpx.Response(200, content=b'x'*140000)
        if mode == 'oversize_content': value['choices'][0]['message']['content'] = 'x'*65537
        return httpx.Response(200, content=canonical_json(value).encode('utf-8'),
                              headers={'content-type':'application/json'})
    return provider.SiliconFlowShadowJudge(provider.SiliconFlowShadowConfig.model_validate_json(canonical_json(config)),
        transport=httpx.MockTransport(handle), encoding=SyntheticEncoding())
