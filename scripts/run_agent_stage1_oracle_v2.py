"""Execute existing guarded Worker harness only after immutable v2 freeze verification."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/agent_stage1'
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'src')]


def digest(path):return sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def timestamp():return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat(timespec='microseconds')


def verify():
    m=read(FIX/'freeze-manifest.v2.json')
    bindings={'oracle.v2.json':'oracle_sha256','scoring-contract.v2.json':'scoring_contract_sha256',
       'oracle.v1.json':'v1_oracle_sha256','oracle.v2.pending.json':'pending_oracle_sha256',
       'freeze-manifest.json':'v1_manifest_sha256','corpus.v1.json':'corpus_sha256'}
    for name,key in bindings.items():
        if digest(FIX/name)!=m[key]:raise ValueError('Frozen asset changed: '+name)
    if digest(ROOT/'docs/evaluation/agent-stage1-human-adjudication.v1.json')!=m['adjudication_sha256']:
        raise ValueError('Human decision changed')
    if digest(ROOT/m['source_revision_path'])!=m['source_revision_sha256']:raise ValueError('v4 revision changed')
    for name,expected in m['evaluation_code_sha256'].items():
        if digest(ROOT/name)!=expected:raise ValueError('Frozen matcher/runner/tests changed: '+name)
    for path,key in [('tests/agent_stage1_multiturn_evaluation.py','existing_harness_sha256'),
                     ('scripts/summarize_agent_stage1_evaluation.py','v1_scorer_sha256')]:
        if digest(ROOT/path)!=m[key]:raise ValueError('Original evaluator changed')
    revision=read(ROOT/m['source_revision_path'])
    for name,expected in revision['production_source_sha256'].items():
        if digest(ROOT/name)!=expected:raise ValueError('v4 business source changed: '+name)
    oracle=read(FIX/'oracle.v2.json')
    if not oracle.get('formal') or oracle['version']!='agent-stage1-independent-oracle-v2':raise ValueError('Not a formal oracle')
    return m,oracle


def main():
    m,oracle=verify()
    # Importing the original fixture does not execute it. All external dependency guards remain unchanged.
    from agent_stage1_multiturn_evaluation import Harness, use_source_revision, serial
    from agent_stage1_v2_scoring import summarize
    use_source_revision(ROOT/m['source_revision_path'])
    started=timestamp()
    if datetime.fromisoformat(started)<=datetime.fromisoformat(m['frozen_at']):raise ValueError('Oracle must freeze before execution')
    out=ROOT/'.cache/agent-stage1-oracle-v2';out.mkdir(parents=True,exist_ok=True)
    index=1
    while (out/f'run-{index:02d}.json').exists():index+=1
    harness=Harness();harness.oracle=oracle
    turns=harness.run()
    verify()
    run={'started_at':started,'finished_at':timestamp(),'timezone':'Asia/Taipei',
        'oracle_sha256':m['oracle_sha256'],'scoring_contract_sha256':m['scoring_contract_sha256'],
        'frozen_manifest_sha256':digest(FIX/'freeze-manifest.v2.json'),'turns':turns,
        'forbidden_attempts':harness.forbidden_attempts,'connection_doubles':len(harness.connections),
        'closed_connection_doubles':sum(c.closed for c in harness.connections),
        'real_database_connections':0,'real_paid_provider_calls':0,'real_embedding_calls':0}
    path=out/f'run-{index:02d}.json'
    with path.open('x',encoding='utf-8') as f:json.dump(serial(run),f,ensure_ascii=False,indent=2);f.write('\n')
    score=summarize(run,oracle)
    score.update(execution_file=path.relative_to(ROOT).as_posix(),execution_sha256=digest(path),
        frozen_manifest_sha256=digest(FIX/'freeze-manifest.v2.json'),scored_at=timestamp())
    with (out/f'assessment-{index:02d}.json').open('x',encoding='utf-8') as f:json.dump(score,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'PASS':score['pass_count'],'FAIL':score['fail_count'],'PENDING':score['pending_count'],
        'forbidden_attempts':harness.forbidden_attempts,'evidence':str(path)},ensure_ascii=False))
    for row in score['turns']:
        if row['status']=='FAIL':print(row['id'],row['first_deviation'],row['failures'])
    return 1 if harness.forbidden_attempts or any(not c.closed for c in harness.connections) else 0


if __name__=='__main__':raise SystemExit(main())
