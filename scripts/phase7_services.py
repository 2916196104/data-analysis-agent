"""阶段 7 专用隔离服务；只操作本次创建的 PostgreSQL 目录与 Redis 容器。"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "output"):
        parser.error("服务目录必须位于项目 output 内")
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "services.json"
    pg_bin = Path(r"C:\Program Files\PostgreSQL\18\bin")
    def run(command, name):
        with (output / (name + ".log")).open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see local log")
    if args.action == "start":
        if state_path.exists():
            parser.error("该目录已有服务记录，请使用新目录")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        state = {"pgdata": str(output / "pgdata"), "port": port,
                 "admin_url": f"postgresql://phase7_test@127.0.0.1:{port}/postgres",
                 "redis_container": "agent-phase7-" + uuid.uuid4().hex}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        try:
            run([str(pg_bin / "initdb.exe"), "-D", state["pgdata"], "-U", "phase7_test", "-A", "trust", "--encoding=UTF8", "--no-locale"], "initdb")
            run([str(pg_bin / "pg_ctl.exe"), "-D", state["pgdata"], "-l", str(output / "postgres.log"), "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"], "pg-start")
            run(["docker", "run", "--detach", "--name", state["redis_container"], "-p", "127.0.0.1::6379", "redis:7", "redis-server", "--save", "", "--appendonly", "no"], "redis-start")
            mapping = subprocess.check_output(["docker", "port", state["redis_container"], "6379/tcp"], text=True).strip()
            assert mapping.startswith("127.0.0.1:"), mapping
            state["redis_url"] = "redis://" + mapping + "/0"
            state["started"] = True
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            print("启动失败；保留服务记录以便执行 stop", flush=True)
            raise
        print("隔离 PostgreSQL / Redis 已启动；连接信息只写入本地服务记录")
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert Path(state["pgdata"]).resolve() == output / "pgdata"
        assert state["redis_container"].startswith("agent-phase7-")
        run(["docker", "rm", "--force", state["redis_container"]], "redis-stop")
        run([str(pg_bin / "pg_ctl.exe"), "-D", state["pgdata"], "-m", "fast", "-w", "stop"], "pg-stop")
        state["stopped"] = True
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print("本次隔离服务已停止；日志及数据目录保留")


if __name__ == "__main__":
    main()
