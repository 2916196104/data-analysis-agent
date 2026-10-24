"""启动临时 PostgreSQL 集群运行模拟验收；从不读取 .env 或连接业务库。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, default=Path(r"C:\Program Files\PostgreSQL\18\bin"))
    parser.add_argument("--stage", choices=("1", "2", "3", "all"), default="all")
    parser.add_argument("--benchmark", type=int, help="模拟评论数量；不发真实模型请求")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2], help="模拟基准 Worker 数量")
    parser.add_argument("--all-backend", action="store_true", help="启用隔离 PostgreSQL 运行完整后端回归")
    args = parser.parse_args()
    executable = ".exe" if os.name == "nt" else ""
    pg_bin = args.pg_bin
    if not (pg_bin / ("initdb" + executable)).is_file():
        found = shutil.which("initdb")
        if not found:
            parser.error("请使用 --pg-bin 指向 PostgreSQL bin 目录。")
        pg_bin = Path(found).parent
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # 保留诊断日志，数据目录由 TemporaryDirectory 在服务停止后删除。
    output = ROOT / "outputs" / "queue-validation"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-queue-pg-", dir=str(output)) as temp:
        root = Path(temp)
        log = output / (root.name + "-postgres.log")
        def run(name, *params):
            # Windows 服务子进程会继承管道句柄；使用普通文件避免 pg_ctl 返回后仍等待 EOF。
            command_log = output / (root.name + "-" + name + ".log")
            with command_log.open("wb") as handle:
                result = subprocess.run([str(pg_bin / (name + executable)), *params], stdout=handle, stderr=handle)
            if result.returncode:
                raise RuntimeError(name + ": " + command_log.read_text(encoding="utf-8", errors="replace"))
        run("initdb", "-D", str(root / "data"), "-U", "queue_test", "-A", "trust", "--encoding=UTF8", "--no-locale")
        run("pg_ctl", "-D", str(root / "data"), "-l", str(log), "-o", f"-h 127.0.0.1 -p {port}", "-w", "start")
        try:
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1",
                   "TEST_POSTGRES_ADMIN_URL": f"postgresql://queue_test@127.0.0.1:{port}/postgres"}
            if args.benchmark:
                command = [sys.executable, str(ROOT / "scripts/benchmark_queue.py"), "--reviews", str(args.benchmark),
                           "--workers", *[str(w) for w in args.workers]]
            elif args.all_backend:
                command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
            else:
                command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_label_queue.py", "-v"]
                if args.stage != "all":
                    command += ["-k", "stage" + args.stage]
            filename = (f"benchmark-{args.benchmark}.log" if args.benchmark else
                        "all-backend.log" if args.all_backend else "stage-" + args.stage + ".log")
            with (output / filename).open("w", encoding="utf-8") as transcript:
                with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace") as process:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                        transcript.write(line)
                        transcript.flush()
                    process.wait()
            return process.returncode
        finally:
            run("pg_ctl", "-D", str(root / "data"), "-m", "immediate", "-w", "stop")


if __name__ == "__main__":
    raise SystemExit(main())
