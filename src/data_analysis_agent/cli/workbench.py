"""Command-line entry for api.workbench."""

import argparse
import json

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.database import DATABASE_ERRORS, database_argument
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.api.workbench import create_server
from data_analysis_agent.reviews.human_review import init_workbench


def main():
    parser = argparse.ArgumentParser(description="本机评论标注与人工复核工作台")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="由你显式增加工作台表，保留已有模型记录")
    serve = commands.add_parser("serve", help="启动本机网页，不会升级数据库或调用模型")
    for command in (setup, serve):
        command.add_argument("--db", required=True, type=database_argument)
    serve.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    if args.command == "serve" and not 1 <= args.port <= 65535:
        parser.error("--port 必须是 1—65535 的整数。")
    try:
        if args.command == "init":
            print(json.dumps({"status": "ok", **init_workbench(args.db)}, ensure_ascii=False))
        else:
            with create_server(args.db, args.port) as server:
                print(f"评论标注工作台：http://127.0.0.1:{server.server_port}（Ctrl+C 停止）", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (ReviewDataError, *DATABASE_ERRORS, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
