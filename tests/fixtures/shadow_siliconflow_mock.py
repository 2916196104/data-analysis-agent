"""Synthetic HTTP only. Neither tokenizer nor completions represent real semantics."""
import json
import os
from pathlib import Path
from time import sleep

import httpx

from data_analysis_agent.agents import review_shadow_siliconflow as provider
from fixtures.shadow_openai_mock import completion as openai_completion


class SyntheticEncoding:
    def encode(self, text): return [0]*100  # Only tests the budget branch, not DeepSeek token counts.


def completion(payload):
    value=openai_completion(payload)
    value['model']=provider.MODEL
    value['usage'].update(prompt_cache_hit_tokens=20,prompt_cache_miss_tokens=80,
                          completion_tokens_details=dict(reasoning_tokens=0))
    return value


def create_mock_judge(mode='valid',marker=None,**config):
    def handle(request):
        if marker: Path(marker).write_text(str(os.getpid()),encoding='ascii')
        if mode=='hang':
            while True: sleep(.02)
        if mode=='exit': os._exit(17)
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        value=completion(payload)
        if mode=='illegal_json': value['choices'][0]['message']['content']='{'
        if mode=='huge': return httpx.Response(200,content=b'x'*140000)
        return httpx.Response(200,json=value)
    return provider.SiliconFlowShadowJudge(
        provider.SiliconFlowShadowConfig.model_validate_json(json.dumps(config)),
        transport=httpx.MockTransport(handle),encoding=SyntheticEncoding())
