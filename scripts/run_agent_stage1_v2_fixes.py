"""Run the unchanged v2.1 scorer/Harness against a separately bound business-source revision."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT/'src')]
from run_agent_stage1_oracle_v2 import read, digest, timestamp

REVISION = ROOT/'docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json'


def verify(revision=REVISION):
    fix = ROOT/'tests/fixtures/agent_stage1'
    m, m1 = read(fix/'freeze-manifest.v2.json'), read(fix/'freeze-manifest.v2.1.json')
    if digest(fix/'freeze-manifest.v2.json') != m1['original_manifest_sha256']:
        raise ValueError('Original freeze changed')
    for name, key in {'oracle.v2.json':'oracle_sha256','scoring-contract.v2.json':'scoring_contract_sha256',
            'oracle.v1.json':'v1_oracle_sha256','oracle.v2.pending.json':'pending_oracle_sha256',
            'freeze-manifest.json':'v1_manifest_sha256','corpus.v1.json':'corpus_sha256'}.items():
        if digest(fix/name) != m[key]:raise ValueError('Frozen asset changed: '+name)
    if digest(fix/'scoring-contract.v2.1.json') != m1['scoring_contract_sha256'] or m['oracle_sha256'] != m1['oracle_sha256']:
        raise ValueError('v2.1 contract changed')
    for name, expected in {**m['evaluation_code_sha256'], **m1['evaluation_code_sha256'],
            'tests/agent_stage1_multiturn_evaluation.py':m['existing_harness_sha256'],
            'scripts/summarize_agent_stage1_evaluation.py':m['v1_scorer_sha256'],
            'docs/evaluation/agent-stage1-human-adjudication.v1.json':m['adjudication_sha256'],
            m['source_revision_path']:m['source_revision_sha256']}.items():
        if digest(ROOT/name) != expected:raise ValueError('Frozen dependency changed: '+name)
    before, after = read(ROOT/m['source_revision_path']), read(revision)
    if (after['parent_revision'] != m['source_revision_path'] or after['parent_revision_sha256'] != m['source_revision_sha256']
            or set(before['production_source_sha256']) != set(after['production_source_sha256'])):
        raise ValueError('Invalid v5 lineage')
    changed = {p for p,h in after['production_source_sha256'].items() if before['production_source_sha256'][p]!=h}
    if changed != set(after['changed_since_v4']) or changed != {
            'src/data_analysis_agent/agents/review_acceptance.py', 'src/data_analysis_agent/agents/review_intent.py'}:
        raise ValueError('Business change exceeds approved scope')
    for name,h in after['production_source_sha256'].items():
        if digest(ROOT/name)!=h:raise ValueError('v5 source changed: '+name)
    return read(fix/'oracle.v2.json'), m1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=ROOT/'.cache/agent-stage1-v2-fix/final')
    args = parser.parse_args()
    oracle,m = verify()
    from agent_stage1_multiturn_evaluation import Harness, use_source_revision, serial
    from agent_stage1_v2_1_scoring import summarize
    use_source_revision(REVISION)
    started=timestamp();h=Harness();h.oracle=oracle;turns=h.run();verify()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    index=1
    while (args.output_dir/f'run-{index:02d}.json').exists():index+=1
    path=args.output_dir/f'run-{index:02d}.json'
    run=dict(started_at=started,finished_at=timestamp(),timezone='Asia/Taipei',
        oracle_sha256=m['oracle_sha256'],scoring_contract_sha256=m['scoring_contract_sha256'],
        source_revision=REVISION.relative_to(ROOT).as_posix(),source_revision_sha256=digest(REVISION),
        turns=turns,forbidden_attempts=h.forbidden_attempts,connection_doubles=len(h.connections),
        closed_connection_doubles=sum(c.closed for c in h.connections),real_database_connections=0,
        real_paid_provider_calls=0,real_embedding_calls=0)
    with path.open('x',encoding='utf-8') as f:json.dump(serial(run),f,ensure_ascii=False,indent=2);f.write('\n')
    score=summarize(run,oracle)
    score.update(execution_file=path.relative_to(ROOT).as_posix(),execution_sha256=digest(path),scored_at=timestamp())
    with (args.output_dir/f'assessment-{index:02d}.json').open('x',encoding='utf-8') as f:json.dump(score,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(dict(PASS=score['pass_count'],FAIL=score['fail_count'],forbidden=h.forbidden_attempts,run=str(path)),ensure_ascii=False))
    for row in score['turns']:
        if row['status']=='FAIL':print(row['id'],row['first_deviation'],row['failures'])
    return int(bool(h.forbidden_attempts) or any(not c.closed for c in h.connections))


if __name__=='__main__':raise SystemExit(main())
