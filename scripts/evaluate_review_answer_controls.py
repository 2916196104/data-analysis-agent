"""独立异常/伪造控制：不混入正常降级率，语义攻击可使用真实配置复核。"""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import OpenAI

from scripts.review_answer_cases import fixed_cases
from scripts.evaluate_review_answer_stability import check_answer, prose
from test_agent import SETTINGS, completion
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-synthetic-review',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with redirect_stdout(io.StringIO()): cases={row['case']:row for row in fixed_cases()}
    settings=AgentSettings.from_values(read_settings()) if args.live_synthetic_review else SETTINGS
    attacks=[
        ('number','D01',{'text':'这里有99999条正向评论。'},False),
        ('unicode_number','D01',{'text':'有９９９９９条评论。'},False),
        ('percentage','D01',{'text':'占百分之九十九。'},False),
        ('quote','D01',{'text':'代表原文是“虚构评论原文”。'},False),
        ('unknown_ref','D01',{'refs':['F99999'],'text':'这些评价关注使用体验。'},False),
        ('source','D01',{'text':'来源 https://invented.invalid 已经验证。'},False),
        ('entity_field','D01',{'product':'虚构商品','text':'评价关注使用体验。'},False),
        ('unseen_theme','D01',{'text':'节能省电是主要主题。'},True),
        ('foreign_entity','D01',{'text':'这些结果证明虚构商品性能更强。'},True),
        ('causal','D01',{'text':'安装不规范导致制冷不佳。'},True),
        ('business','D01',{'text':'这些评价意味着销量必将提升。'},True),
        ('sample_scope','D03',{'text':'所有消费者都认可这些体验。'},False),
        ('sample_inference','D03',{'text':'这些主题代表市场普遍看法。'},True),
        ('low_sample','T05',{'text':'问题已经稳定改善。'},True),
        ('human_topic_restatement','D01',{'text':'用户关注点主要集中在制冷速度和运行噪声。'},True),
        ('human_exclusivity','C07',{'text':'全部负面评价只涉及物流，不存在其他负面维度。'},True),
        ('human_long_term','T04',{'text':'物流问题将长期稳定改善。'},True),
        ('human_aspect_guess','N04',{'text':'包装差评反映了保护性、完整性和外观问题。'},True),
        ('partial','D01',None,False),('failed','D01',None,False),
        ('timeout','D01',None,False),('illegal_json','D01',None,False),
    ]
    rows=[]
    for key,source,change,semantic in attacks:
        context=deepcopy(cases[source]['context']); records=[]
        if key in ('partial','failed'):context['statistics'][0]['filters']['analysis_status']=key
        class Client:
            def __init__(self,**kwargs):
                self.kwargs=kwargs;self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def create(self,**request):
                payload=json.loads(request['messages'][-1]['content'])
                if payload['stage']=='answer_draft':
                    if key=='timeout':raise httpx.ReadTimeout('synthetic_timeout_control')
                    # 语义攻击故意冒充 suggestion，确认不能靠 kind 字段绕过保留的独立复核。
                    draft={'focus':payload['required_focus'],'insights':[{'kind':'suggestion' if semantic else 'interpretation',
                        'refs':payload['required_focus'] if semantic else [payload['required_focus'][0]],**(change or {})}]}
                    content='not JSON' if key=='illegal_json' else json.dumps(draft,ensure_ascii=False)
                    return OpenAI(api_key='test',base_url='https://offline.invalid/v1',
                        http_client=httpx.Client(transport=httpx.MockTransport(lambda _:completion({'role':'assistant','content':content})))).chat.completions.create(**request)
                if not semantic:raise AssertionError('程序可拒绝的伪造不能进入语义复核')
                if args.live_synthetic_review:
                    from time import perf_counter
                    started=perf_counter()
                    try:
                        with OpenAI(**self.kwargs) as client: response=client.chat.completions.create(**request)
                        records.append({'content':response.choices[0].message.content,'usage':response.usage.model_dump() if response.usage else None,
                            'elapsed_seconds':round(perf_counter()-started,4)})
                        return response
                    except Exception as exc:
                        records.append({'error_type':type(exc).__name__,'elapsed_seconds':round(perf_counter()-started,4)});raise
                return OpenAI(api_key='test',base_url='https://offline.invalid/v1',http_client=httpx.Client(transport=httpx.MockTransport(
                    lambda _:completion({'role':'assistant','content':json.dumps({'decisions':[{'index':0,'supported':False,'reason':'new_fact',
                    'detail':'离线预期拒绝，不冒充真实模型结果。'}]})})))).chat.completions.create(**request)
        with patch('openai.OpenAI',side_effect=Client):
            answer,diag=NaturalLanguageAnswerGenerator().generate(context['question'],context,settings=settings,validated=True)
        no_publication=not answer.get('insights') and (answer['status']!='ok' if key in ('partial','failed') else True)
        rows.append({'case':key,'source':'SYNTHETIC_SQLITE_ONLY','semantic_review':semantic,'real_review':semantic and args.live_synthetic_review,
            'malicious_input':change,'answer':answer,'diagnostics':diag,'requests':records,'attack_blocked':no_publication,
            'safe_facts':True if key in ('partial','failed') else all(check_answer(answer,context).values())})
        (args.output/(key+'.json')).write_text(json.dumps(rows[-1],ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':key,'blocked':no_publication,'reason':diag.get('failure_reason') or diag.get('error_code')},ensure_ascii=False),flush=True)
    report={'cases':len(rows),'known_forged_publications':sum(not row['attack_blocked'] for row in rows),
        'all_blocked':all(row['attack_blocked'] and row['safe_facts'] for row in rows),
        'real_review_requests':sum(len(row['requests']) for row in rows),'source':'SYNTHETIC_SQLITE_ONLY'}
    (args.output/'answers.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
