"""Second immutable matcher binding, same approved Oracle and unmodified guarded business harness."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'src')]
from run_agent_stage1_oracle_v2 import verify as verify_original, read, digest, timestamp


def verify():
    original,oracle=verify_original()
    fix=ROOT/'tests/fixtures/agent_stage1';m=read(fix/'freeze-manifest.v2.1.json')
    if digest(fix/'freeze-manifest.v2.json')!=m['original_manifest_sha256']:raise ValueError('Original freeze changed')
    if digest(fix/'oracle.v2.json')!=m['oracle_sha256']:raise ValueError('Approved Oracle changed')
    if digest(fix/'scoring-contract.v2.1.json')!=m['scoring_contract_sha256']:raise ValueError('Corrected binding changed')
    for p,h in m['evaluation_code_sha256'].items():
        if digest(ROOT/p)!=h:raise ValueError('Matcher revision code changed: '+p)
    return original,m,oracle


def main():
    original,m,oracle=verify()
    from agent_stage1_multiturn_evaluation import Harness, use_source_revision, serial
    from agent_stage1_v2_1_scoring import summarize
    from datetime import datetime
    use_source_revision(ROOT/original['source_revision_path'])
    started=timestamp()
    if datetime.fromisoformat(started)<=datetime.fromisoformat(m['frozen_at']):raise ValueError('Matcher must freeze first')
    out=ROOT/'.cache/agent-stage1-oracle-v2';i=1
    while (out/f'run-{i:02d}.json').exists():i+=1
    h=Harness();h.oracle=oracle;turns=h.run();verify()
    run={'started_at':started,'finished_at':timestamp(),'timezone':'Asia/Taipei','oracle_sha256':m['oracle_sha256'],
        'scoring_contract_sha256':m['scoring_contract_sha256'],'frozen_manifest_sha256':digest(ROOT/'tests/fixtures/agent_stage1/freeze-manifest.v2.1.json'),
        'turns':turns,'forbidden_attempts':h.forbidden_attempts,'connection_doubles':len(h.connections),
        'closed_connection_doubles':sum(c.closed for c in h.connections),'real_database_connections':0,
        'real_paid_provider_calls':0,'real_embedding_calls':0}
    path=out/f'run-{i:02d}.json'
    with path.open('x',encoding='utf-8') as f:json.dump(serial(run),f,ensure_ascii=False,indent=2);f.write('\n')
    result=summarize(run,oracle)
    result.update(execution_file=path.relative_to(ROOT).as_posix(),execution_sha256=digest(path),
        frozen_manifest_sha256=run['frozen_manifest_sha256'],scored_at=timestamp())
    with (out/f'assessment-{i:02d}.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'PASS':result['pass_count'],'FAIL':result['fail_count'],'PENDING':result['pending_count'],
        'forbidden_attempts':h.forbidden_attempts,'evidence':str(path)},ensure_ascii=False))
    for row in result['turns']:
        if row['status']=='FAIL':print(row['id'],row['first_deviation'],row['failures'])
    return 1 if h.forbidden_attempts or any(not c.closed for c in h.connections) else 0


if __name__=='__main__':raise SystemExit(main())
