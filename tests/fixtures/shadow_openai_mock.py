"""Real OpenAI SDK with a synthetic HTTP transport, including an infinite request."""
import json
import os
from pathlib import Path
from time import sleep

import httpx

from data_analysis_agent.agents.review_shadow_openai import OpenAIShadowConfig, OpenAIShadowJudge, MODEL


def completion(payload,content=None):
    judgments=[]
    for fact in payload['contract']['required_facts']['items']:
        for candidate in payload['candidates']:
            judgments.append(dict(fact_id=fact['id'],key=candidate['key'],status='unknown',support_refs=[],
                refutation_refs=[],context_veto_refs=[],reason_code='semantics_unknown',rationale='Synthetic SDK response'))
    answer=dict(version='p5b-evidence-output-draft-v1',request_id=payload['request_id'],input_sha256=payload['input_sha256'],
                execution_status='completed',fact_judgments=judgments,relation_judgments=[])
    return dict(id='synthetic-completion',object='chat.completion',created=0,model=MODEL,
        choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content=content or json.dumps(answer)))],
        usage=dict(prompt_tokens=100,completion_tokens=30,total_tokens=130,prompt_tokens_details=dict(cached_tokens=20)))


def create_mock_judge(mode='valid',marker=None,**config):
    def handle(request):
        if marker:Path(marker).write_text(str(os.getpid()),encoding='ascii')
        if mode=='hang':
            while True:sleep(.02)
        if mode=='exit':os._exit(17)
        body=json.loads(request.content)
        payload=json.loads(body['messages'][1]['content'])
        result=completion(payload)
        return httpx.Response(200,headers={'content-type':'application/json'},
                              stream=httpx.ByteStream(json.dumps(result).encode()))
    return OpenAIShadowJudge(OpenAIShadowConfig.model_validate_json(json.dumps(config)),transport=httpx.MockTransport(handle))
