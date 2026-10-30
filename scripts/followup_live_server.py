"""真实数据追问验收：独立任务库，业务库只读，凭据不落盘。"""
import argparse
import json
from pathlib import Path
from data_analysis_agent.core.config import read_settings, AgentSettings
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.api.app import create_app
from scripts.evaluate_review_agent import snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'serve', 'verify'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--origin')
    args = parser.parse_args()
    settings = read_settings()
    db = settings['DATABASE_URL']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.mode == 'prepare':
        c = connect_review_database(db, readonly=True)
        rows = c.execute("""SELECT r.product_id, COUNT(*) AS n FROM reviews r
            JOIN review_annotations a ON a.review_id=r.id JOIN review_opinions o ON o.annotation_id=a.id
            WHERE r.source='reviews_csv' AND o.aspect='logistics' AND o.sentiment='negative'
            AND r.published_at>='2022-01-01' GROUP BY r.product_id ORDER BY n DESC LIMIT 2""").fetchall()
        c.close()
        model = AgentSettings.from_values(settings).model
        (out/'selection.json').write_text(json.dumps(dict(source='reviews_csv', dataset_version='reviews-csv',
            product_ids=[r['product_id'] for r in rows], model=model, counts=[r['n'] for r in rows],
            current=['2022-01-01', '2022-08-27'], baseline=['2021-01-01', '2021-12-31']), ensure_ascii=False), encoding='utf-8')
        (out/'before.json').write_text(json.dumps(snapshot(db)), encoding='utf-8')
        init_tasks(out/'tasks.sqlite3', db)
    elif args.mode == 'verify':
        before = json.loads((out/'before.json').read_text())
        after = snapshot(db)
        checks = {k: before[k] == after[k] for k in before}
        (out/'business-unchanged.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
        print(json.dumps(checks))
        assert all(checks.values())
    else:
        import uvicorn
        from data_analysis_agent.agents import reviews
        original_llm = reviews.create_llm
        def traced_llm(config):
            llm = original_llm(config)
            def record(request):
                body = json.loads(request.content)
                messages = body.get('messages', [])
                diagnostic = {'roles': [m['role'] for m in messages],
                    'user_questions': [m['content'].split('\n\n<bound_context>')[0]
                                       for m in messages if m['role'] == 'user'],
                    'tool_outputs': [{'length': len(m.get('content') or ''), 'prefix': (m.get('content') or '')[:220]}
                                     for m in messages if m['role'] == 'tool'],
                    'tool_choice': body.get('tool_choice')}
                with (out/'request-diagnostics.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(diagnostic, ensure_ascii=False)+'\n')
            llm._adapter._client._client.event_hooks['request'].append(record)
            return llm
        reviews.create_llm = traced_llm
        uvicorn.run(create_app(db, out/'tasks.sqlite3', import_root=out, allowed_origins=[args.origin]), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
