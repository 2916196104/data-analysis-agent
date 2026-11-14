"""Exact reviewed SQL templates plus fixed parameter bindings, never SELECT-as-permission."""
import json
import math
from pathlib import Path
import re


def canonical(sql):
    if not isinstance(sql,str):raise RuntimeError('f02_SQL_type')
    result=[];quoted=None;i=0
    while i<len(sql):
        ch=sql[i]
        if quoted:
            result.append(ch)
            if ch==quoted:
                if i+1<len(sql) and sql[i+1]==quoted:result.append(sql[i+1]);i+=1
                else:quoted=None
        elif sql.startswith(('--','/*','*/'),i) or ch in (';','$'):
            raise RuntimeError('f02_SQL_comments_or_multiple_statements')
        elif ch in ("'",'"'):
            quoted=ch;result.append(ch)
        elif ch.isspace():
            if result and result[-1]!=' ':result.append(' ')
        elif ch=='?':result.append('%s')
        else:result.append(ch)
        i+=1
    if quoted:raise RuntimeError('f02_SQL_quote')
    return ''.join(result).strip()


def validate_sql(sql, parameters=None, *, task_id=None, ledger=None):
    from p7c_real_guard import IDENTITY
    templates=json.loads((Path(__file__).parent/'f02_sql_templates.json').read_text(encoding='utf-8'))
    q=canonical(sql)
    record=next((r for r in templates if r['sql']==q),None)
    if record is None:raise RuntimeError('f02_SQL_not_allowlisted')
    p=tuple(parameters or ())
    if task_id and record.get('startup_only'):
        raise RuntimeError('f02_SQL_startup_metadata_during_task')
    identity=tuple(IDENTITY.values())
    bounds=('100013588055','reviews_csv','reviews-csv','2021-09-11T00:00:00.000+00:00','2022-08-27T00:00:00.000+00:00')
    kind=record['binding']
    if kind=='none':valid=not p
    elif kind=='business_namespace':valid=p==('business',)
    elif kind=='worker_lock':valid=p==(8358523899158648126,)
    elif kind=='identity':valid=p==identity
    elif kind=='dataset':valid=p==('reviews_csv','reviews-csv')
    elif kind=='product':valid=p==('reviews_csv','reviews-csv','100013588055')
    elif kind=='scoped_identity':valid=p==identity+bounds
    elif kind=='dataset_window':valid=not task_id and p==identity+bounds[1:]
    elif kind=='review_context':
        valid=(len(p)==5+record['ids'] and p[:5]==('reviews_csv','reviews-csv','100013588055','2021-09-11','2022-08-26')
               and all(type(v) is int and v>0 for v in p[5:]) and tuple(sorted(set(p[5:])))==p[5:])
    elif kind=='vector':
        from data_analysis_agent.reviews.embedding_store import SPACE_ID
        from data_analysis_agent.core.serialization import content_sha256
        from data_analysis_agent.agents.review_retrieval import retrieval_request
        from p7c_real_guard import QUESTION
        request=retrieval_request(QUESTION)
        valid=len(p)==15 and p[2:7]==bounds and p[7:10]==(SPACE_ID,'quote-utf8-v1',content_sha256(list(identity))) and p[10:]==(request.aspect,request.aspect,request.sentiment,request.sentiment,5)
        if valid:
            vector=json.loads(p[0]);eligible=json.loads(p[1])
            valid=(isinstance(vector,list) and len(vector)==1024 and all(type(v) in (int,float) and math.isfinite(v) for v in vector)
                   and abs(sum(v*v for v in vector)-1)<1e-5 and isinstance(eligible,list)
                   and len({r['key'] for r in eligible})==len(eligible)
                   and all(set(r)=={'key','snapshot'} and re.fullmatch('rag-opinion-v1:[0-9a-f]{64}',r['key']) and re.fullmatch('[0-9a-f]{64}',r['snapshot']) for r in eligible))
        if not valid or not task_id or ledger is None:raise RuntimeError('f02_SQL_vector_binding')
        ledger.check_task(task_id);ledger.check_vector(vector)
        return True
    else:raise RuntimeError('f02_SQL_binding_type')
    if not valid:raise RuntimeError('f02_SQL_parameters')
    return False
