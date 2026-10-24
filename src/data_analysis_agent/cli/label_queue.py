"""持久标注队列操作入口；模型仅在 Worker 运行时加载。"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.config import AgentSettings, ConfigurationError, LabelQueueSettings, read_settings
from data_analysis_agent.core.database import DATABASE_ERRORS, database_argument
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.jobs import label_queue as store
from data_analysis_agent.jobs.label_metrics import failures, queue_metrics, run_metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description="PostgreSQL + Redis Streams 持久评论标注")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "submit", "seed", "dispatch", "worker", "stats", "failures", "repair", "resubmit", "resume"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True, type=database_argument)
        command.add_argument("--env-file", type=Path, default=Path(".env"))
        if name in {"seed", "worker", "stats", "failures"}:
            command.add_argument("--run-id", required=name == "seed")
        if name == "submit":
            command.add_argument("--limit", type=int, help="省略时处理本次筛选范围全部评论；创建时固定最大评论 ID")
            command.add_argument("--selection", choices=("first", "transient", "failed"), default="first")
            command.add_argument("--product-id", action="append")
            for field in ("date-from", "date-to", "as-of", "source", "dataset-version"):
                command.add_argument("--" + field)
        if name in {"submit", "seed", "dispatch"}:
            command.add_argument("--chunk-size", type=int, default=200)
        if name in {"worker", "dispatch"}:
            command.add_argument("--max-seconds", type=float)
        if name == "dispatch":
            command.add_argument("--once", action="store_true")
        if name == "failures":
            command.add_argument("--after-id", default="")
            command.add_argument("--limit", type=int, default=100)
        if name == "resubmit":
            command.add_argument("--task-id", required=True)
        if name == "resume":
            command.add_argument("--profile-id", required=True)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "chunk_size"):
            validate_integer(args.chunk_size, "chunk_size", maximum=1000)
        if getattr(args, "max_seconds", None) is not None and (
                not math.isfinite(args.max_seconds) or args.max_seconds <= 0):
            raise ConfigurationError("max-seconds 必须是大于零的有限秒数。")
        if args.command == "init":
            result = store.init_queue(args.db)
        elif args.command in {"submit", "seed"}:
            run_id = getattr(args, "run_id", None)
            if args.command == "submit":
                settings = AgentSettings.from_values(read_settings(args.env_file))
                scope = {field: getattr(args, field) for field in ("date_from", "date_to", "as_of", "source", "dataset_version")}
                run_id = store.create_run(args.db, settings, limit=args.limit, selection=args.selection,
                                          product_ids=args.product_id, **scope)
                print("运行已创建 run_id=" + run_id, file=sys.stderr, flush=True)
            with store.queue_session(args.db):
                while not store.run_state(args.db, run_id)["seeded"]:
                    store.seed_run(args.db, run_id, chunk_size=args.chunk_size)
                result = run_metrics(args.db, run_id, finish=True)
        elif args.command in {"worker", "dispatch"}:
            from data_analysis_agent.jobs.label_stream import LabelStream, redis_client

            values = read_settings(args.env_file)
            settings = AgentSettings.from_values(values)
            config = LabelQueueSettings.from_values(values, timeout_seconds=settings.timeout_seconds)
            client = redis_client(config.redis_url)
            stream = LabelStream(client, name=config.stream, group=config.group, reclaim_ms=config.lease_seconds * 1000)
            try:
                if args.command == "worker":
                    from data_analysis_agent.jobs.label_limiter import GlobalLimiter
                    from data_analysis_agent.jobs.label_worker import LabelWorker

                    limiter = GlobalLimiter(client, scope=config.quota_scope, rpm=config.rpm, tpm=config.tpm,
                                            concurrency=config.concurrency, lease_seconds=config.lease_seconds)
                    worker = LabelWorker(args.db, stream, settings, chunk_size=config.chunk_size, batch_size=config.batch_size,
                        lease_seconds=config.lease_seconds, max_calls=config.max_calls, max_retries=config.max_retries,
                        backoff_seconds=config.backoff_seconds, limiter=limiter)
                    reason = worker.run(run_id=args.run_id, max_seconds=args.max_seconds)
                    result = {"stop_reason": reason, **(run_metrics(args.db, args.run_id, finish=True) if args.run_id else queue_metrics(args.db))}
                else:
                    started, published = time.monotonic(), 0
                    while True:
                        published += store.publish_outbox(args.db, stream, limit=args.chunk_size,
                                                         repair_seconds=config.lease_seconds)
                        if args.once or (args.max_seconds and time.monotonic() - started >= args.max_seconds):
                            break
                        time.sleep(0.5)
                    result = {"published_messages": published}
            finally:
                client.close()
        elif args.command == "stats":
            result = run_metrics(args.db, args.run_id, finish=True) if args.run_id else queue_metrics(args.db)
        elif args.command == "failures":
            result = {"failures": failures(args.db, run_id=args.run_id, after_id=args.after_id, limit=args.limit)}
        elif args.command == "repair":
            store.repair_outbox(args.db)
            result = {"message": "已从 PostgreSQL 重建待补发记录；Worker/dispatch 将自动补发。"}
        elif args.command == "resubmit":
            store.resubmit(args.db, args.task_id)
            result = {"task_id": args.task_id}
        else:
            store.resume_profile(args.db, args.profile_id)
            result = {"profile_id": args.profile_id, "message": "暂停已解除；manual 任务仍需显式 resubmit。"}
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped", "message": "已停止；已提交结果保留，未完成任务可恢复。"}, ensure_ascii=False))
        return 130
    except (ConfigurationError, ReviewDataError, *DATABASE_ERRORS) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        # Redis/网络异常可能带连接凭据，只显示类型。
        print(json.dumps({"status": "error", "error_type": type(exc).__name__,
                          "message": "基础设施不可用；任务仍保存在 PostgreSQL，请恢复服务后重启 Worker。"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
