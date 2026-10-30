"""创建独立 PostgreSQL，串行完成数据库测试、真实模型评测及浏览器验收。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="显式使用 .env 配置的真实模型额度，仅发送合成评论")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--pg-bin", type=Path, default=Path(r"C:\Program Files\PostgreSQL\18\bin"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    if not args.live:
        parser.error("必须显式指定 --live；此操作会调用真实模型。")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    pgdata = output / "pgdata"
    suffix = ".exe" if os.name == "nt" else ""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    admin = f"postgresql://agent_eval@127.0.0.1:{port}/postgres"
    env = {**os.environ, "TEST_POSTGRES_ADMIN_URL": admin, "PYTHONUTF8": "1", "LABEL_EXECUTION_MODE": "local",
           "PYTHONPATH": os.pathsep.join(str(p) for p in (root, root / "src", root / "tests"))}
    results = {}
    def run(name, command):
        with (output / (name + ".log")).open("w", encoding="utf-8") as log:
            results[name] = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        print(name, results[name], flush=True)
        return results[name]
    if run("initdb", [str(args.pg_bin / ("initdb" + suffix)), "-D", str(pgdata), "-U", "agent_eval", "-A", "trust", "--encoding=UTF8", "--no-locale"]):
        return 1
    if run("pg-start", [str(args.pg_bin / ("pg_ctl" + suffix)), "-D", str(pgdata), "-l", str(output / "postgres.log"),
                        "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"]):
        return 1
    try:
        if not run("pg-tests", [sys.executable, "-m", "unittest", "test_postgres", "test_review_agent_postgres", "test_review_evaluation", "-v"]):
            run("live-evaluation", [sys.executable, "-m", "scripts.evaluate_review_agent", "--live", "--env-file", str(args.env_file.resolve()),
                                    "--business-readonly", "--output", str(output / "live"), *[arg for case in args.case for arg in ("--case", case)]])
            name = "agent_test_review_web_" + uuid.uuid4().hex
            with psycopg.connect(admin, autocommit=True) as connection:
                connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
            try:
                env["E2E_DATABASE_URL"] = admin.replace("/postgres", "/" + name)
                run("browser", ["node", "frontend/tests/assistant.e2e.mjs"])
            finally:
                with psycopg.connect(admin, autocommit=True) as connection:
                    connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    finally:
        stopped = run("pg-stop", [str(args.pg_bin / ("pg_ctl" + suffix)), "-D", str(pgdata), "-m", "fast", "-w", "stop"]) == 0
        if stopped and pgdata.resolve().parent == output:
            shutil.rmtree(pgdata)
        (output / "stages.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return int(any(results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
