"""合成评论的 HTTP 提交→真实 Worker→已配置回答模型→轮询读回耗时。"""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient

from scripts.review_answer_cases import _seed
from test_review_answers import AnswerTests
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.jobs.store import init_tasks,worker_started,claim_task
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.agents.review_compression import read_public_analysis


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    case=AnswerTests();case.setUp();results=[]
    # 模型配置只读，不把密钥或服务地址写进产物。业务数据库始终是临时 SQLite。
    configuration=read_settings();configuration['LABEL_EXECUTION_MODE']='local'
    try:
        with redirect_stdout(io.StringIO()): _seed(case);init_monitoring(case.db)
        task_db=case.root/'roundtrip-tasks.sqlite3';init_tasks(task_db,case.db)
        options={'import_root':case.root,'env_file':Path('.env').resolve()}
        app=create_app(case.db,task_db,embedded_worker=False,**options)
        worker=TaskWorker(case.db,task_db,**options);worker_started(task_db,worker.worker_id)
        with patch('data_analysis_agent.jobs.submission.read_settings',return_value=configuration),TestClient(app,base_url='http://127.0.0.1') as client:
            for name,tool,params,question,products in [
                ('simple_positive','get_positive_aspects',{},'好评集中在哪些方面？',['S35-A']),
                ('comparison','compare_products',{},'这两款商品评价有哪些差别？请解释。',['S35-A','S35-B'])]:
                text='执行本轮已确认的只读查询\n'+json.dumps({'tool':tool,'parameters':params,'question':question},ensure_ascii=False)
                body={'kind':'analysis','parameters':{**case.scope(dataset_version='phase35-fixed',product_ids=products,
                    date_from='2023-04-01',date_to='2023-04-30'),'question':text}}
                started=perf_counter();response=client.post('/api/v1/tasks',json=body)
                if response.status_code!=202:raise AssertionError(response.text)
                task_id=response.json()['result']['task']['task_id'];submitted=perf_counter()
                claimed=claim_task(task_db,worker.worker_id)
                if not claimed or claimed['task_id']!=task_id:raise AssertionError('wrong claimed task')
                with redirect_stdout(io.StringIO()):worker._execute(claimed)
                ended=perf_counter()
                restored=client.get('/api/v1/tasks/'+task_id).json()['result'];read_back=perf_counter()
                result=restored.get('result') or {};public=read_public_analysis(restored)
                results.append({'case':name,'source':'SYNTHETIC_SQLITE_ONLY','task_status':restored['status'],
                    'public_status':public['status'],'submit_seconds':round(submitted-started,4),
                    'worker_seconds':round(ended-submitted,4),'readback_seconds':round(read_back-ended,4),
                    'user_observed_seconds':round(read_back-started,4),'answer_generation':result.get('answer_generation'),
                    'answer':result.get('public_answer'),'performance':result.get('performance'),
                    'validation':result.get('validation'),'planning_model_calls':result.get('model_calls')})
                print(json.dumps({key:results[-1][key] for key in ('case','task_status','public_status','user_observed_seconds')},ensure_ascii=False),flush=True)
        (args.output/'report.json').write_text(json.dumps({'source':'SYNTHETIC_SQLITE_ONLY','cases':results,
            'boundary':'in-process FastAPI HTTP client + real persisted queue/worker; no browser/network RTT; no background poll interval'},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:case.doCleanups()


if __name__=='__main__':main()
