"""一键启动评论分析前后端，自动打开页面，停止时一起关闭。"""

import argparse
import json
import os
import shutil
import signal
import socket
import sys
import time
import webbrowser
from contextlib import closing
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Sequence
from urllib.parse import urlsplit

from data_analysis_agent.core.config import ConfigurationError, read_settings
from data_analysis_agent.core.database import database_argument
from data_analysis_agent.dev_processes import ProcessFamily


ROOT = Path(__file__).resolve().parent


def check_port(port: int) -> None:
    """确认启动端口可用，避免覆盖用户已经运行的服务。"""
    with socket.socket() as listener:
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'端口 {port} 已被占用，请先停止对应服务，或通过 --api-port / --web-port 指定空闲端口。') from None


def get_local(url: str) -> tuple[int, bytes]:
    """请求本机服务的一个健康检查地址。"""
    target = urlsplit(url)
    with closing(HTTPConnection(target.hostname, target.port, timeout=1)) as connection:
        connection.request('GET', target.path or '/')
        response = connection.getresponse()
        return response.status, response.read()


def wait_ready(
    processes: Sequence[tuple[str, Popen]],
    urls: Sequence[str],
    stop: Event,
    timeout: int = 30,
) -> bool:
    """等待前后端同时就绪；任一子进程提前退出都会立即失败。"""
    deadline = time.monotonic() + timeout
    while not stop.is_set():
        for name, process in processes:
            if process.poll() is not None:
                raise RuntimeError(f'{name}已退出，请查看上方启动日志。')
        try:
            api_status, content = get_local(urls[0])
            health = json.loads(content)
            web_status, _ = get_local(urls[1])
            worker_available = health.get('result', {}).get('worker_available')
            services_ready = (
                api_status == 200
                and web_status == 200
                and health.get('status') == 'ok'
                and worker_available
            )
            if services_ready:
                return True
        except (HTTPException, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(
                '服务启动超时，请查看上方的后端、数据库或前端错误日志。'
            )
        stop.wait(0.2)
    return False


def launch(api_port: int = 8000, web_port: int = 5173, open_browser: bool = True) -> int:
    """启动后端和前端，并在退出时统一回收本次启动的进程。"""
    if api_port == web_port:
        raise RuntimeError('前端和后端必须使用不同端口。')
    check_port(api_port)
    check_port(web_port)
    database_url = read_settings(ROOT / '.env').get('DATABASE_URL', '').strip()
    if not database_url:
        raise ConfigurationError('请在项目根目录 .env 中配置 DATABASE_URL。')
    database_argument(database_url)
    node = shutil.which('node')
    vite = ROOT / 'frontend/node_modules/vite/bin/vite.js'
    if not node:
        raise RuntimeError('未找到 Node.js，请安装 Node.js 并加入 PATH 后重启 IDEA。')
    if not vite.is_file():
        raise RuntimeError('前端依赖未安装，请先在 frontend 目录运行 npm ci。')
    api_url = f'http://127.0.0.1:{api_port}'
    web_url = f'http://127.0.0.1:{web_port}'
    environment = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'}
    stop = Event()
    previous_handlers = {}
    for event in (signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if os.name == 'nt' else [])):
        previous_handlers[event] = signal.signal(event, lambda *_: stop.set())
    try:
        with ProcessFamily() as family:
            print(
                f'启动后端：{api_url}\n启动前端：{web_url}',
                flush=True,
            )
            backend_command = [
                sys.executable,
                '-m',
                'data_analysis_agent.cli.server',
                'serve',
                '--db',
                'env:DATABASE_URL',
                '--env-file',
                str(ROOT / '.env'),
                '--import-root',
                str(ROOT / 'data/private'),
                '--host',
                '127.0.0.1',
                '--port',
                str(api_port),
                '--allow-origin',
                web_url,
            ]
            backend = family.start(backend_command, cwd=ROOT, env=environment)

            frontend_command = [
                node, str(vite), '--host', '127.0.0.1', '--port', str(web_port), '--strictPort',
            ]
            frontend_environment = {
                **environment,
                'VITE_API_BASE_URL': f'{api_url}/api/v1',
            }
            frontend = family.start(
                frontend_command,
                cwd=ROOT / 'frontend',
                env=frontend_environment,
            )
            processes = [('后端', backend), ('前端', frontend)]
            if wait_ready(processes, [f'{api_url}/api/v1/health', web_url], stop):
                print(
                    f'前后端已就绪：{web_url}\n'
                    '按 Ctrl+C 或 IDEA 停止按钮，一起关闭前后端。',
                    flush=True,
                )
                if open_browser and not webbrowser.open(web_url, new=2):
                    print(f'浏览器未自动打开，请手动访问：{web_url}', flush=True)
                while not stop.wait(0.5):
                    for name, process in processes:
                        if process.poll() is not None:
                            raise RuntimeError(f'{name}已停止，正在关闭其他服务。')
    finally:
        for event, handler in previous_handlers.items():
            signal.signal(event, handler)
        print('本次启动的前后端已关闭。', flush=True)
    return 0


def port_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('端口必须在 1–65535 之间。')
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-port', type=port_number, default=8000, help='后端端口，默认 8000')
    parser.add_argument('--web-port', type=port_number, default=5173, help='前端端口，默认 5173')
    parser.add_argument('--no-browser', action='store_true', help='启动后不自动打开浏览器')
    args = parser.parse_args()
    try:
        return launch(args.api_port, args.web_port, not args.no_browser)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'启动失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
