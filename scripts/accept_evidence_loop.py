"""真实回环 HTTP→持久队列→后台 Worker→补查→公开读回；仅临时合成业务数据。"""
import argparse
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket
import threading
from time import perf_counter,sleep
from unittest.mock import patch

import httpx
from openai import OpenAI
import uvicorn

from scripts.insight_evidence_cases import EvidenceFixture,QUOTES
from test_agent import SETTINGS,completion,final
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.config import read_settings,AgentSettings
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews import analytics
from scripts.insight_evidence_cases import semantic_fixture
from data_analysis_agent.agents.review_topics import TopicBatch

CASES=[
    ('P01',['IE-PACK'],'包装差评具体反映什么问题？',('外箱状态','盒内固定','商品完好')),
    ('L02',['IE-LOG-A','IE-LOG-B'],'对比这两款商品的物流差评，具体发生在哪些环节？',('IE-LOG-A','IE-LOG-B','发货前等待','运输追踪','预约履约')),
    ('T02',['IE-TREND'],'物流负面占比按月有什么变化？具体问题类型在首末月份是否不同？',('2023-01-01','2023-03-31','发货前等待','预约履约','不能据此确认')),
]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-synthetic',action='store_true');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--controls',action='store_true',help='隔离故障注入的完整 HTTP/队列/Worker 回归，禁止外部模型调用。')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.controls and args.live_synthetic:parser.error('故障控制集使用离线 SDK，不混入真实模型成功率')
    fixture=EvidenceFixture();fixture.setUp();records=[];rows=[];current={'case':None};lock=threading.Lock()
    with redirect_stdout(io.StringIO()):fixture.seed();init_monitoring(fixture.db)
    config=read_settings()if args.live_synthetic else {'LLM_MODEL_ID':SETTINGS.model,'LLM_API_KEY':SETTINGS.api_key,'LLM_BASE_URL':SETTINGS.base_url,'AGENT_MAX_TOOL_CALLS':'12'}
    config={**config,'LABEL_EXECUTION_MODE':'local'}
    settings=AgentSettings.from_values(config)
    scenarios=[(*row,'succeeded')for row in CASES]
    if args.controls:
        scenarios=[(name,CASES[0][1],CASES[0][2],(),'failed')for name in ('BUDGET','QUERY_FAIL','QUOTE','WRITE')]
        scenarios += [('DATES',CASES[2][1],CASES[2][2],(),'failed'),
            ('AMBIGUOUS',['IE-LOG-A','IE-LOG-B','IE-LOG-Z'],CASES[1][2],(),'succeeded'),
            ('PARTIAL',['IE-PACK'],'包装负面观点具体主要集中在哪些方面？',(),'partial'),
            ('FAILED',['IE-PACK'],'包装负面观点具体主要集中在哪些方面？',(),'failed')]
    def model_client(**kwargs):
        active={'record':None}
        def sent(request):
            record=active['record']
            attempt={'index':len(record['http_attempts'])+1}
            record['http_attempts'].append(attempt)
            request.extensions['evidence_attempt']=attempt
        def received(response):
            response.request.extensions['evidence_attempt']['status_code']=response.status_code
        hooks={'request':[sent],'response':[received]}
        if not args.live_synthetic:
            def respond(request):
                body=json.loads(request.content)
                if body.get('tools'):
                    prompt=next(row['content']for row in body['messages']if row['role']=='user'and'<query_plan>'in row.get('content',''))
                    plan=json.loads(prompt.split('<query_plan>\n')[1].split('\n</query_plan>')[0])
                    if any(row['role']=='tool'for row in body['messages']):return completion(final('ok',*[f'r{i+1}'for i in range(len(plan['requirements']))]))
                    calls=[{'id':f'call_{i}','type':'function','function':{'name':item['tool'],'arguments':json.dumps(item['parameters'])}}for i,item in enumerate(plan['requirements'])]
                    return completion({'role':'assistant','content':None,'tool_calls':calls})
                payload=json.loads(body['messages'][-1]['content'])
                assert payload['stage']=='answer_draft',payload.get('stage')
                return completion({'role':'assistant','content':json.dumps({'focus':payload['required_focus'],'insights':[]})})
            client=OpenAI(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(respond),event_hooks=hooks))
        else:client=OpenAI(**kwargs,http_client=httpx.Client(event_hooks=hooks))
        original=client.chat.completions.create
        def create(**request):
            content=canonical_json(request['messages'])
            stage='planning'if request.get('tools')else json.loads(request['messages'][-1]['content']).get('stage','intent')
            record={'case':current['case'],'stage':stage,'input_bytes':len(content.encode('utf-8')),
                'http_attempts':[],
                'raw_fixture_quote_sent':any(quote in content for quote in QUOTES),
                'local_relation_sent':any(token in content for token in ('local_evidence_spans_v1','internal_retention','locally_verified_descriptive'))}
            started=perf_counter()
            active['record']=record
            with lock:records.append(record)
            try:
                response=original(**request)
                record.update(finish_reason=response.choices[0].finish_reason,content=response.choices[0].message.content,
                    usage=response.usage.model_dump()if response.usage else None)
                return response
            except Exception as exc:
                record['error_type']=type(exc).__name__;raise
            finally:record['elapsed_seconds']=round(perf_counter()-started,4)
        client.chat.completions.create=create
        return client
    worker=None;server=None;thread=None;listener=None
    started_all=perf_counter()
    try:
        task_db=fixture.root/'evidence-loop-tasks.sqlite3';init_tasks(task_db,fixture.db)
        options={'import_root':fixture.root,'env_file':Path('.env').resolve()}
        app=create_app(fixture.db,task_db,embedded_worker=False,**options)
        worker=TaskWorker(fixture.db,task_db,**options)
        business_hash=hashlib.sha256(fixture.db.read_bytes()).hexdigest()
        with ExitStack()as stack:
            stack.enter_context(patch('data_analysis_agent.jobs.submission.read_settings',return_value=config))
            stack.enter_context(patch('openai.OpenAI',side_effect=model_client))
            # 使用真实后台循环领取任务，验收不手动 claim、不调用 _execute、不注入计划或快照。
            worker.start()
            listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
            deadline=perf_counter()+10
            while not server.started and perf_counter()<deadline:sleep(.02)
            if not server.started:raise RuntimeError('HTTP server did not start')
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=10)as client:
                for case,products,question,required,expected_status in scenarios:
                    current['case']=case;started=perf_counter()
                    faults=ExitStack();stack.callback(faults.close)
                    previous_limit=config.get('AGENT_MAX_TOOL_CALLS','12')
                    if case=='BUDGET':config['AGENT_MAX_TOOL_CALLS']='2'
                    if case in ('QUERY_FAIL','QUOTE','WRITE'):
                        original_query=analytics.get_opinion_breakdown
                        def fault_query(*values,**keywords):
                            if case=='QUERY_FAIL':raise TimeoutError('synthetic supplementary query timeout')
                            if case=='WRITE':values[0].execute('UPDATE reviews SET rating=5 WHERE product_id=?',('IE-PACK',))
                            data=original_query(*values,**keywords)
                            if case=='QUOTE':data['items'][0]['opinions'][0]['quote']='伪造原文'
                            return data
                        faults.enter_context(patch.object(analytics,'get_opinion_breakdown',side_effect=fault_query))
                    if case=='DATES':
                        from data_analysis_agent.agents.review_evidence import assess_evidence
                        def corrupt_window(context):
                            value=deepcopy(assess_evidence(context))
                            value['followup_queries'][0]['requested_subwindow']['date_from']='2022-01-01'
                            return value
                        faults.enter_context(patch('data_analysis_agent.agents.review_evidence_loop.assess_evidence',side_effect=corrupt_window))
                    if case in ('PARTIAL','FAILED'):
                        def partial_topics(settings,prompt,payload,schema,timeout):
                            if case=='FAILED'or(schema is TopicBatch and payload['opinions'][0]['quote']=='内衬移位导致配件在盒内晃动'):raise TimeoutError('synthetic topic failure')
                            return semantic_fixture(settings,prompt,payload,schema,timeout)
                        faults.enter_context(patch('data_analysis_agent.agents.review_topics.BATCH_SIZE',1))
                        faults.enter_context(patch('data_analysis_agent.agents.review_topics._call',side_effect=partial_topics))
                    body={'kind':'analysis','parameters':{**fixture.scope(dataset_version='insight-evidence-v1',product_ids=products,
                        date_from='2023-01-01',date_to='2023-03-31'),'question':question,'conversation_id':'evidence-loop-acceptance'}}
                    headers={'Idempotency-Key':'evidence-loop-'+case}
                    response=client.post('/api/v1/tasks',json=body,headers=headers)
                    if response.status_code!=202:raise AssertionError(response.text)
                    task_id=response.json()['result']['task']['task_id']
                    repeated=client.post('/api/v1/tasks',json=body,headers=headers)
                    assert repeated.status_code==202 and repeated.json()['result']['task']['task_id']==task_id
                    deadline=perf_counter()+240;polls=0
                    while True:
                        task=client.get('/api/v1/tasks/'+task_id).json()['result'];polls+=1
                        if task['status']in('succeeded','failed','partial'):break
                        if perf_counter()>deadline:raise TimeoutError('task acceptance deadline exceeded')
                        sleep(.1)
                    elapsed=perf_counter()-started;result=task.get('result')or{}
                    calls_before=len(records)
                    public=client.get('/api/v1/public/tasks/'+task_id).json()['result']
                    again=client.get('/api/v1/public/tasks/'+task_id).json()['result']
                    answer=public['public_answer'];summary=answer['summary'];closure=result.get('evidence_completion',{})
                    checks={'task_succeeded':task['status']=='succeeded','public_ok':answer['status']=='ok',
                        'automatic_completion':closure.get('automatic')is True,'single_cycle':closure.get('cycles')==1,
                        'completion_succeeded':closure.get('status')=='completed',
                        'independent_validation':all(request.get('validation',{}).get('status')=='passed'for request in closure.get('requests',[])),
                        'merged_validation':closure.get('merged_validation',{}).get('status')=='passed',
                        'direct_answer_AI_rule_check':all(token in summary for token in required),
                        'evidence_relations_bound':bool(answer.get('evidence_relations'))and all(relation['bindings']and relation['evidence_ids']for relation in answer.get('evidence_relations',[])),
                        'idempotent_submission':repeated.json()['result']['task']['task_id']==task_id,
                        'public_read_stable_zero_model_calls':again==public and calls_before==len(records),
                        'business_readonly':business_hash==hashlib.sha256(fixture.db.read_bytes()).hexdigest()}
                    if args.controls:
                        checks={'expected_task_status':task['status']==expected_status,
                            'no_false_public_success':answer['status']!='ok',
                            'idempotent_submission':repeated.json()['result']['task']['task_id']==task_id,
                            'public_read_stable_zero_model_calls':again==public and calls_before==len(records),
                            'business_readonly':business_hash==hashlib.sha256(fixture.db.read_bytes()).hexdigest()}
                        if case=='AMBIGUOUS':checks['specific_clarification']=answer['status']=='unsupported'and'两款'in summary
                        if case in ('PARTIAL','FAILED','AMBIGUOUS'):checks['no_completion_after_incomplete_or_ambiguous']=closure.get('automatic')is False
                        if case in ('DATES','BUDGET'):checks['no_out_of_bounds_or_over_budget_query']=closure.get('additional_tool_calls')==0
                        if case=='QUOTE':checks['independent_quote_validation_failed']=closure.get('failed_stage')=='independent_validation'
                        if case in ('BUDGET','QUERY_FAIL','QUOTE','WRITE','DATES'):
                            checks['raw_public_analysis_not_stale_success']=result.get('public_analysis',{}).get('status')!='ok'
                            checks['combined_validation_not_stale_success']=result.get('validation',{}).get('status')=='failed'
                    row={'case':case,'question':question,'source':'SYNTHETIC_SQLITE_ONLY','task_id':task_id,'task_status':task['status'],
                        'end_to_end_seconds':round(elapsed,4),'polls':polls,'checks':checks,'answer':answer,
                        'automatic_evidence_completion':closure,'tool_calls':result.get('tool_calls'),
                        'query_executions':result.get('query_executions'),'performance':result.get('performance'),
                        'answer_generation':result.get('answer_generation'),'validation':result.get('validation'),
                        'trace':result.get('trace'),'error':task.get('error'),
                        'model_requests':[record for record in records if record['case']==case]}
                    rows.append(row)
                    faults.close();config['AGENT_MAX_TOOL_CALLS']=previous_limit
                    (args.output/(case+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps({'case':case,'task_status':task['status'],'seconds':row['end_to_end_seconds'],'checks':checks},ensure_ascii=False),flush=True)
            worker.stop();worker=None
        usage=[record['usage']for record in records if record.get('usage')]
        report={'mode':'real_config_model_synthetic_only'if args.live_synthetic else 'scripted_sdk_synthetic_only',
            'transport':'real_loopback_HTTP_uvicorn_background_TaskWorker_persisted_queue',
            'model':settings.model if args.live_synthetic else 'mock','wall_seconds':round(perf_counter()-started_all,4),
            'cases':rows,'all_checks_passed':all(all(row['checks'].values())for row in rows)and len(rows)==len(scenarios),
            'actual_external_requests':sum(len(record['http_attempts'])for record in records)if args.live_synthetic else 0,'sdk_requests':len(records),
            'http_attempts_instrumented':True,'planning_sdk_max_retries':settings.max_retries,'answer_sdk_max_retries':0,
            'input_tokens':sum(u['prompt_tokens']for u in usage),'output_tokens':sum(u['completion_tokens']for u in usage),
            'usage_unreported_requests':len(records)-len(usage),
            'new_raw_text_or_relations_egress':any(r['raw_fixture_quote_sent']or r['local_relation_sent']for r in records),
            'human_quality_acceptance':'not_performed','quality_rule_checks_are_AI_not_human':True,
            'boundary':'No production PostgreSQL, real business text, browser RTT or concurrency load test; queue poll .1s.'}
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({key:value for key,value in report.items()if key!='cases'},ensure_ascii=False))
        if not report['all_checks_passed']:raise SystemExit(1)
    finally:
        if worker:worker.stop()
        if server:server.should_exit=True
        if thread:thread.join(timeout=10)
        if listener:listener.close()
        fixture.doCleanups()


if __name__=='__main__':main()
