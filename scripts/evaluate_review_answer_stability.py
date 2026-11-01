"""阶段 3.5 同快照、同预算的合成模型对照；运行前不读取任何生产数据库。"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from contextvars import ContextVar
from copy import deepcopy
import io
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
import types
from unittest.mock import patch

import httpx
from openai import OpenAI

from scripts.review_answer_cases import fixed_cases, ROOT
from test_agent import SETTINGS, completion
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator, unavailable_answer
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_insights import fact_catalog

BASELINE_REVISION = 'e3cb450b9df34dfc304fc1e0ee11ed298935332b'
CURRENT_RUN = ContextVar('synthetic_answer_evaluation', default=None)


def baseline_generator():
    """读取明确的第三阶段 Git 快照，全部回答模块相互绑定到同一个旧版本。"""
    loaded = {}
    for name in ('review_answer_facts','review_insights','review_answers'):
        path = f'src/data_analysis_agent/agents/{name}.py'
        source = subprocess.check_output(['git','show',BASELINE_REVISION+':'+path],cwd=ROOT).decode('utf-8')
        module = types.ModuleType('_phase35_baseline_'+name); sys.modules[module.__name__] = module
        exec(compile(source, f'{BASELINE_REVISION}/{path}', 'exec'),module.__dict__)
        if name == 'review_insights':
            for key in ('AnswerFact','answer_caveats','build_answer_facts','percent'):
                setattr(module,key,getattr(loaded['review_answer_facts'],key))
        elif name == 'review_answers':
            for dependency in loaded.values():
                for key in list(module.__dict__):
                    if hasattr(dependency,key) and not key.startswith('__'):
                        setattr(module,key,getattr(dependency,key))
        loaded[name]=module
    return loaded['review_answers'].NaturalLanguageAnswerGenerator()


def scripted(payload):
    if payload['stage']=='answer_support_review':
        return {'decisions':[{'index':row.get('index',index),'supported':True,'reason':'supported',
            'detail':'合成离线协议夹具，不用于真实质量结论。'} for index,row in enumerate(payload['insights'])]}
    ids=payload['required_focus']
    if CURRENT_RUN.get()['variant']=='before':
        return {'schema_version':'2.0','focus':ids,'insights':[]}
    topic=next((row for row in payload['facts'] if row['kind']=='topic'),None)
    return {'focus':ids,'insights':[] if not topic else [{'kind':'interpretation','refs':[topic['fact_id']],
        'text':'这些评价反映了已标注样本中的使用关注点。'}]}


def model_client(**kwargs):
    run=CURRENT_RUN.get()
    if run is None: raise AssertionError('Only explicit synthetic cases may invoke this client')
    if run.get('replay') is not None:
        def replay_response(request):
            payload=json.loads(json.loads(request.content)['messages'][-1]['content'])
            if not run['replay']: raise AssertionError('回放阶段超出已保存模型请求')
            saved=run['replay'].pop(0)
            if saved['stage']!=payload['stage']: raise AssertionError('回放模型阶段不匹配')
            if saved.get('error_type'): raise httpx.ReadTimeout('replayed synthetic timeout')
            result=completion({'role':'assistant','content':saved['content']}).json()
            result['usage']=saved.get('usage')
            result['choices'][0]['finish_reason']=saved.get('finish_reason','stop')
            return httpx.Response(200,json=result)
        return OpenAI(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(replay_response)))
    if not run['live']:
        def handle(request):
            payload=json.loads(json.loads(request.content)['messages'][-1]['content'])
            return completion({'role':'assistant','content':json.dumps(scripted(payload),ensure_ascii=False)})
        return OpenAI(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    client=OpenAI(**kwargs); original=client.chat.completions.create
    def create(**request):
        payload=json.loads(request['messages'][-1]['content']); started=perf_counter()
        record={'stage':payload['stage'],'input_bytes':len(canonical_json(request['messages']).encode('utf-8'))}
        run['requests'].append(record)
        try:
            response=original(**request)
            record.update(content=response.choices[0].message.content,finish_reason=response.choices[0].finish_reason,
                usage=response.usage.model_dump() if response.usage else None)
            return response
        except Exception as exc:
            record['error_type']=type(exc).__name__; raise
        finally:
            record['elapsed_seconds']=round(perf_counter()-started,4)
    client.chat.completions.create=create
    return client


def prose(answer):
    return '\n\n'.join(filter(None,[answer['summary'],*[row['text'] for row in answer['highlights']],
        *[('可能的解释：' if row['kind']=='interpretation' else '分析建议：')+row['text'] for row in answer.get('insights',[])],
        answer['sample_note'],*answer['caveats']]))


def check_answer(answer, context):
    if context is None:
        return {'no_false_success':answer['status']=='unsupported','no_fake_metrics':not answer['metrics']}
    catalog,_=fact_catalog(CompactAnalysisContext.model_validate(context)); by_id={row['fact_id']:row for row in catalog}
    refs={row['finding_ref'] for row in answer['highlights']}
    def metric_valid(row):
        fact=by_id.get(row['finding_ref'])
        if not fact: return False
        # v1 普通正文与 detailed 目录可在文字长度不同，但指标取值必须完全一致。
        return canonical_json(row['values'])==canonical_json(fact['metrics'])
    return {'known_refs':refs<=set(by_id),'source_refs':set(answer['source_result_ids'])<=set(context['source_result_ids']),
        'backend_metrics':all(metric_valid(row) for row in answer['metrics']),
        'evidence_refs':canonical_json(answer['evidence_refs'])==canonical_json(context['evidence']),
        'sample_limit':not any(row['population_mode']=='sample' for row in catalog) or '样本' in answer['summary']+''.join(answer['caveats']),
        'candidate_limit':not any(row['candidate_topic'] for row in catalog) or '未经人工确认' in ''.join(answer['caveats']),
        'known_insight_refs':all(set(item['finding_refs'])<=set(by_id) for item in answer.get('insights',[])),
        'no_test_forgery':not any(token in prose(answer) for token in ('99999','虚构商品','虚构评论原文','invented.invalid'))}


def execute_case(case, variant, generator, settings, live, replay=None):
    context=case['context']; started=perf_counter()
    run={'case':case['case'],'variant':variant,'live':live,'requests':[]}
    if replay is not None: run['replay']=deepcopy(replay)
    token=CURRENT_RUN.set(run)
    try:
        if context is None:
            answer=unavailable_answer('unsupported',case['clarification'])
            diag={'attempts':0,'generation_status':'unavailable','strategy':'clarification','elapsed_seconds':0}
        else:
            answer,diag=generator.generate(context['question'],deepcopy(context),settings=settings,validated=True)
    finally: CURRENT_RUN.reset(token)
    elapsed=perf_counter()-started
    return {'answer':answer,'diagnostics':diag,'requests':run['requests'],'wall_seconds':round(elapsed,4),
        'estimated_query_plus_answer_seconds':round(case['query_seconds']+elapsed,4),
        'checks':check_answer(answer,context),'text':prose(answer)}


def distribution(values):
    values=sorted(values)
    def quantile(q):
        return round(values[min(len(values)-1,round((len(values)-1)*q))],4) if values else None
    return {'n':len(values),'p50':quantile(.5),'p90':quantile(.9),'p95':quantile(.95),'max':max(values) if values else None}


def summary(rows):
    output={}
    for variant in ('before','after'):
        values=[row[variant] for row in rows]; modeled=[row for row in values if row['diagnostics'].get('strategy')=='llm']
        decisions=[item for row in values for item in row['diagnostics'].get('insight_decisions',[]) if 'detail_supplied' in item]
        requests=[item for row in values for item in row['requests']]
        usage=[item['usage'] for item in requests if item.get('usage')]
        fallback=[row for row in modeled if row['answer']['generation_status']=='fallback']
        output[variant]={'cases':len(values),'model_applicable_cases':len(modeled),
            'fallbacks':len(fallback),'unexpected_fallback_rate':len(fallback)/len(modeled) if modeled else 0,
            'model_protocol_success_rate':sum(row['answer']['generation_status']=='llm' for row in modeled)/len(modeled) if modeled else None,
            'explanation_published_cases':sum(bool(row['answer']['insights']) for row in modeled),
            'explanation_publication_rate':sum(bool(row['answer']['insights']) for row in modeled)/len(modeled) if modeled else None,
            'program_omitted_no_basis':sum(item['reason']=='no_incremental_evidence' for row in values
                for item in row['diagnostics'].get('insight_decisions',[])),
            'failure_reasons':dict(Counter(row['diagnostics'].get('failure_reason') or row['diagnostics'].get('error_code') or 'unspecified' for row in fallback)),
            'failure_stages':dict(Counter(row['diagnostics'].get('failed_stage') or 'unknown' for row in fallback)),
            'support_decisions':len(decisions),'support_accepted':sum(item['accepted'] for item in decisions),
            'redundant_omitted':sum(item['reason']=='redundant' for item in decisions),
            'support_acceptance_rate':sum(item['accepted'] for item in decisions)/len(decisions) if decisions else None,
            'requests':len(requests),'request_errors':dict(Counter(item['error_type'] for item in requests if item.get('error_type'))),
            'timeout_requests':sum('Timeout' in item.get('error_type','') for item in requests),
            'input_tokens':sum(item.get('prompt_tokens',0) for item in usage),'output_tokens':sum(item.get('completion_tokens',0) for item in usage),
            'usage_unknown_requests':len(requests)-len(usage),
            'request_latency':{stage:distribution([item['elapsed_seconds'] for item in requests if item['stage']==stage]) for stage in ('answer_draft','answer_support_review')},
            'answer_latency_model_cases':distribution([row['wall_seconds'] for row in modeled]),
            'query_plus_answer_latency':distribution([row['estimated_query_plus_answer_seconds'] for row in values]),
            'call_distribution':dict(Counter(row['diagnostics']['attempts'] for row in values)),
            'checks_passed':all(all(row['checks'].values()) for row in values)}
    return output


def write_review(rows, output):
    text=['# 固定合成案例前后对照','第三阶段基线与阶段 3.5 使用完全相同快照。此文件没有人工评分。']
    blind=[]
    for index,row in enumerate(rows):
        text.extend(['## '+row['case']+' '+row['question'],'### 第三阶段','\n'+row['before']['text'],
            '### 阶段 3.5','\n'+row['after']['text']])
        order=('before','after') if index%2 else ('after','before')
        blind.append({'case':row['case'],'question':row['question'],'A':row[order[0]]['text'],'B':row[order[1]]['text'],
            'scores':{'A':None,'B':None},'score_fields':['directness','coverage','analytical_value','evidence_support','concision','sample_accuracy'],
            'scale':'0=不满足，1=部分，2=满足','human_reviewer':None,'notes':None})
    (output/'before-after.md').write_text('\n\n'.join(text)+'\n',encoding='utf-8')
    (output/'human-review.json').write_text(json.dumps(blind,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'blind-key.json').write_text(json.dumps({row['case']:({'A':'before','B':'after'} if i%2 else {'A':'after','B':'before'})
        for i,row in enumerate(rows)},indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-synthetic',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,choices=(1,2),default=2)
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--reuse-before',type=Path,help='复用同快照的已完成真实基线，不再支付旧版本调用。')
    parser.add_argument('--replay-after',type=Path,help='回放已保存的真实合成响应，只验证本地修订；零外部调用。')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    with redirect_stdout(io.StringIO()): cases=fixed_cases()
    if args.cases: cases=[case for case in cases if case['case'] in args.cases]
    if not cases: parser.error('没有匹配固定案例')
    if args.replay_after and args.live_synthetic: parser.error('响应回放不可与真实模型调用一起使用')
    settings=AgentSettings.from_values(read_settings()) if args.live_synthetic else SETTINGS
    before=baseline_generator(); after=NaturalLanguageAnswerGenerator()
    reused={}
    replayed={}
    if args.replay_after:
        replayed={row['case']:row for row in json.loads((args.replay_after/'answers.json').read_text(encoding='utf-8'))}
        for case in cases:
            if case['case'] not in replayed or replayed[case['case']].get('snapshot_hash')!=case.get('snapshot_hash'):
                parser.error('回放必须使用完全相同的合成快照。')
    if args.reuse_before:
        for path in args.reuse_before.glob('*.json'):
            try:
                row=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(row,dict) and 'before' in row and 'case' in row: reused[row['case']]=row
            except (ValueError,KeyError): continue
        for case in cases:
            old=reused.get(case['case'])
            if old is None or old.get('snapshot_hash')!=case.get('snapshot_hash') or old.get('question')!=case['question']:
                parser.error('复用基线必须覆盖每题，且问题和快照完全一致。')
    (args.output/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    started=perf_counter(); rows=[]
    def pair(index,case):
        row={key:case[key] for key in ('case','group','question','query_seconds','source')}
        row['snapshot_hash']=case.get('snapshot_hash'); row['real_model_configuration']=args.live_synthetic
        for variant in (('before','after') if index%2 else ('after','before')):
            row[variant]=(reused[case['case']]['before'] if variant=='before' and reused else
                execute_case(case,variant,before if variant=='before' else after,settings,args.live_synthetic,
                    replayed[case['case']]['after']['requests'] if variant=='after' and replayed else None))
        return row
    # ContextVar 隔离每个并发案例的合成输入与诊断，最多两个请求在途。
    with patch('openai.OpenAI',side_effect=model_client), ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(pair,index,case) for index,case in enumerate(cases)]
        for future in as_completed(futures):
            row=future.result(); rows.append(row)
            print(json.dumps({'completed':len(rows),'case':row['case'],'before':row['before']['answer']['generation_status'],
                'after':row['after']['answer']['generation_status'],'reason':row['after']['diagnostics'].get('failure_reason')},ensure_ascii=False),flush=True)
            (args.output/(row['case']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    rows.sort(key=lambda row:next(i for i,case in enumerate(cases) if case['case']==row['case']))
    (args.output/'answers.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'mode':'replayed_real_responses_no_external_calls' if args.replay_after else 'real_config_model_synthetic_only' if args.live_synthetic else 'offline_scripted',
        'model':settings.model if args.live_synthetic else 'scripted_mock',
        'baseline_revision':BASELINE_REVISION,'case_count':len(rows),'distinct_snapshots':len({case.get('snapshot_hash') for case in cases if case['context']}),
        'workers':args.workers,'wall_seconds':round(perf_counter()-started,3),'timeout_seconds_per_request':settings.answer_timeout_seconds,
        'reused_before_from':str(args.reuse_before) if args.reuse_before else None,
        'replayed_after_from':str(args.replay_after) if args.replay_after else None,
        'new_external_requests_this_run':sum(len(row[variant]['requests']) for row in rows
            for variant in (('after',) if args.reuse_before else ('before','after'))),
        'results':summary(rows),'human_review_status':'pending','known_forged_publications':None}
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    write_review(rows,args.output)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
