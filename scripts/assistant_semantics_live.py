"""本次口语回归的真实数据入口；业务库只读，任务落在独立 SQLite。"""

import argparse
import json
from pathlib import Path
from contextlib import asynccontextmanager

from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.jobs.worker import TaskWorker, WorkerLock
from data_analysis_agent.reviews.human_review import workbench_options
from data_analysis_agent.reviews.analytics import get_data_quality
from scripts.evaluate_review_agent import snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'serve', 'verify'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--origin')
    args = parser.parse_args()
    db = read_settings()['DATABASE_URL']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.mode == 'prepare':
        catalog = workbench_options(db)
        dataset = next(row for row in catalog['datasets'] if row['source'] == 'reviews_csv' and row['dataset_version'] == 'reviews-csv')
        profile = next(row for row in catalog['profiles'] if row['requested_model'] == 'Pro/deepseek-ai/DeepSeek-V3.2')
        parameters = {key: profile[key] for key in ('base_url', 'prompt_version')}
        parameters.update(model=profile['requested_model'], source=dataset['source'], dataset_version=dataset['dataset_version'],
                          product_ids=['100013588053'], date_from=dataset['published_at_start'][:10], date_to=dataset['published_at_end'][:10])
        quality = get_data_quality(db, **parameters)['summary']
        selection = {key: parameters[key] for key in ('source', 'dataset_version', 'product_ids', 'date_from', 'date_to')}
        selection.update(profile_id=profile['profile_id'], dataset_reviews=dataset['review_count'], product_summary=quality)
        (out / 'selection.json').write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'before.json').write_text(json.dumps(snapshot(db)), encoding='utf-8')
        init_tasks(out / 'tasks.sqlite3', db)
        print(json.dumps(selection, ensure_ascii=False))
    elif args.mode == 'verify':
        before = json.loads((out / 'before.json').read_text(encoding='utf-8'))
        after = snapshot(db)
        # 现有服务仍会更新心跳；单独记录，不把并发心跳变动归因于本次只读验收。
        checks = {key: before[key] == after[key] for key in before if key not in {'worker_state', 'label_workers'}}
        (out / 'business-unchanged.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
        assert all(checks.values()), checks
        print(json.dumps(checks))
    else:
        import uvicorn
        class ReadOnlyValidationWorker(TaskWorker):
            def _execute(self, task):
                if task['kind'] != 'analysis' or not task['parameters'].get('question'):
                    raise ValueError('验收执行器只允许只读问答，不执行导入、标注和监控写入。')
                return super()._execute(task)
        worker = ReadOnlyValidationWorker(db, out / 'tasks.sqlite3', import_root=out, env_file='.env')
        # 仅隔离问答队列拥有本地锁；Agent 的业务连接强制 readonly，不占用现有写入 worker 的锁。
        worker.lock = WorkerLock(out / 'tasks.sqlite3')
        app = create_app(db, out / 'tasks.sqlite3', import_root=out, allowed_origins=[args.origin], embedded_worker=False)
        original_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(app):
            async with original_lifespan(app):
                worker.start()
                try:
                    yield
                finally:
                    worker.stop()
        app.router.lifespan_context = lifespan
        uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
