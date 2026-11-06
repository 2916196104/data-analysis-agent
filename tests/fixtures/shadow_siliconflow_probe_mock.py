"""Fixed synthetic probe through the real SDK, using only MockTransport."""
import json
import os
from pathlib import Path
from time import sleep

import httpx

from scripts import run_siliconflow_schema_probe as probe
from fixtures.shadow_siliconflow_mock import completion


def response(payload,mode='supported'):
    value=completion(payload)
    value['choices'][0]['message']['reasoning_content']=''
    if mode=='supported':
        answer=json.loads(value['choices'][0]['message']['content'])
        candidate=payload['candidates'][0]
        span=dict(key=candidate['key'],source_identity_sha256=candidate['source_identity_sha256'],
            start=candidate['quote_start'],end=candidate['quote_end'],text=candidate['quote'])
        answer['fact_judgments'][0].update(status='supported',support_refs=[span],reason_code='direct_report')
        value['choices'][0]['message']['content']=json.dumps(answer)
    if mode=='unknown_fact':
        answer=json.loads(value['choices'][0]['message']['content'])
        answer['fact_judgments'][0]['fact_id']='fabricated'
        value['choices'][0]['message']['content']=json.dumps(answer)
    if mode=='bad_span':
        value=response(payload)
        answer=json.loads(value['choices'][0]['message']['content'])
        answer['fact_judgments'][0]['support_refs'][0]['start']+=1
        value['choices'][0]['message']['content']=json.dumps(answer)
    if mode=='reasoning':
        value['choices'][0]['message']['reasoning_content']='SYNTHETIC private reasoning'
        value['usage']['completion_tokens_details']['reasoning_tokens']=5
    if mode=='invalid_json':value['choices'][0]['message']['content']='{'
    if mode=='length':value['choices'][0]['finish_reason']='length'
    if mode=='model_drift':value['model']='other-model'
    return value


def create_mock_probe_judge(mode='supported',marker=None,**config):
    def handle(request):
        if marker:Path(marker).write_text(str(os.getpid()),encoding='ascii')
        if mode=='hang':
            while True:sleep(.02)
        if mode=='exit':os._exit(17)
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(400 if mode=='http400' else 200,json=response(payload,mode))
    settings=probe.provider.SiliconFlowShadowConfig.model_validate_json(json.dumps(config))
    return probe.ProbeJudge(settings,transport=httpx.MockTransport(handle))
