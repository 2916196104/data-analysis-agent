"""Independent v3 SDK/worker tests: actual SDK, Mock HTTP, no real I/O."""
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts import run_rag_shadow_v3_independent as entry
from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from fixtures.shadow_siliconflow_mock import completion


def create_mock_judge(reply, capture_file, mode='valid', **config):
    for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                   'httpx.AsyncHTTPTransport.handle_async_request',
                   'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
        patch(target, side_effect=AssertionError('real I/O forbidden in v3 worker test')).start()

    def handle(request):
        body = strict_json_loads(request.content.decode('utf-8'))
        with Path(capture_file).open('a', encoding='utf-8') as out:
            out.write(canonical_json(body)+'\n')
        if mode == 'sdk_error':
            raise httpx.ReadTimeout('SYNTHETIC-ERROR', request=request)
        value = completion(strict_json_loads(body['messages'][1]['content']))
        value['choices'][0]['message']['content'] = reply
        return httpx.Response(200, json=value)

    return entry.IndependentV3Judge(entry.provider.SiliconFlowShadowConfig.model_validate_json(canonical_json(config)),
                                    transport=httpx.MockTransport(handle))
