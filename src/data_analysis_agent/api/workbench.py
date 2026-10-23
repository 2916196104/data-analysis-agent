"""仅供本机回环访问的网页工作台，显式初始化数据表且不调用模型。"""

import json
import re
import secrets
import sysconfig
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __name__ == "__main__" and not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.database import DATABASE_ERRORS, database_target
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import strict_json_loads
from data_analysis_agent.reviews.human_review import (
    ReviewConflict, ReviewNotFound, check_workbench, list_reviews,
    review_detail, save_human_review, workbench_options,
)


def _asset_dir() -> Path:
    """优先使用源码工作台，安装后再回退到包数据目录。"""
    source = Path(__file__).resolve().parents[3] / "frontend" / "workbench"
    if source.is_dir():
        return source
    return Path(sysconfig.get_path("data")) / "share" / "data-analysis-agent" / "workbench"


ASSET_DIR = _asset_dir()


def _parameters(query: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    """解析单值查询参数，拒绝重复字段和工作台未声明的字段。"""
    values = parse_qs(query, keep_blank_values=True, max_num_fields=20)
    if set(values) - set(allowed) or any(len(v) != 1 for v in values.values()):
        raise ReviewDataError("查询参数重复或包含未知字段。")
    result = {k: v[0] for k, v in values.items() if v[0] != ""}
    for key in ("profile_id", "page", "page_size"):
        if key in result:
            if not re.fullmatch(r"[0-9]{1,9}", result[key]):
                raise ReviewDataError(f"{key} 必须是整数。")
            result[key] = int(result[key])
    return result


class WorkbenchHandler(BaseHTTPRequestHandler):
    """把本机工作台请求映射到评论复核业务函数。"""

    def log_message(self, *_args: object) -> None:
        pass

    def _send(
        self,
        code: int,
        body: bytes | dict[str, Any],
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        """写入统一响应头；工作台响应禁止缓存，避免复核结果过期。"""
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; "
            "style-src 'self'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self) -> bool:
        """限制 Host、Origin 和跨站请求，工作台只服务本机浏览器。"""
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed:
            self._send(403, {"error": "只接受本机工作台请求。"})
            return False
        origin = self.headers.get("Origin")
        if (origin is not None and origin not in {"http://" + host for host in allowed}) or self.headers.get(
                "Sec-Fetch-Site") == "cross-site":
            self._send(403, {"error": "请求来源不匹配。"})
            return False
        return True

    def _dispatch(self, method: str) -> None:
        """按 HTTP 方法分派静态资源、查询和人工复核保存请求。"""
        if not self._local_request():
            return
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            detail = re.fullmatch(r"/api/reviews/([1-9][0-9]{0,8})", path)
            save = re.fullmatch(r"/api/reviews/([1-9][0-9]{0,8})/human-reviews", path)
            if method == "GET":
                if path in ("/", "/workbench.js", "/workbench.css"):
                    asset_name, mime_type = {
                        "/": ("index.html", "text/html"),
                        "/workbench.js": ("workbench.js", "text/javascript"),
                        "/workbench.css": ("workbench.css", "text/css"),
                    }[path]
                    self._send(
                        200,
                        (ASSET_DIR / asset_name).read_bytes(),
                        mime_type + "; charset=utf-8",
                    )
                elif path == "/api/options":
                    result = workbench_options(self.server.db_path, **_parameters(parsed.query, ("profile_id",)))
                    self._send(200, {**result, "csrf_token": self.server.csrf_token})
                elif path == "/api/reviews":
                    params = _parameters(
                        parsed.query,
                        (
                            "profile_id", "product_key", "date_from", "date_to",
                            "aspect", "sentiment", "processing_status", "human_status",
                            "page", "page_size",
                        ),
                    )
                    self._send(200, list_reviews(self.server.db_path, **params))
                elif detail:
                    self._send(200, review_detail(self.server.db_path, int(detail[1]),
                                                 **_parameters(parsed.query, ("profile_id",))))
                else:
                    self._send(404, {"error": "页面或接口不存在。"})
            elif save:
                if parsed.query:
                    raise ReviewDataError("保存接口不接受查询参数。")
                token = self.headers.get("X-CSRF-Token", "")
                if not token.isascii() or not secrets.compare_digest(token, self.server.csrf_token):
                    self._send(403, {"error": "页面凭证失效，请刷新后再试。"})
                    return
                if self.headers.get_content_type() != "application/json":
                    raise ReviewDataError("请提交 application/json。")
                length = self.headers.get("Content-Length", "")
                if not length.isascii() or not length.isdecimal() or not 0 < int(length) <= 200000:
                    raise ReviewDataError("提交内容长度无效或超过 200 KB。")
                body = strict_json_loads(self.rfile.read(int(length)).decode("utf-8"))
                required = {"annotation_id", "expected_revision_id", "action", "reviewer"}
                if not isinstance(body, dict) or not required <= body.keys() or body.keys() - required - {"opinions", "note"}:
                    raise ReviewDataError("提交字段缺失或包含未知字段。")
                self._send(201, save_human_review(self.server.db_path, int(save[1]), **body))
            else:
                self._send(404, {"error": "接口不存在。"})
        except ReviewConflict as exc:
            self._send(409, {"error": str(exc)})
        except ReviewNotFound as exc:
            self._send(404, {"error": str(exc)})
        except (ReviewDataError, ValueError, UnicodeError, RecursionError) as exc:
            self._send(400, {"error": str(exc) if isinstance(exc, ReviewDataError) else "请求格式错误。"})
        except DATABASE_ERRORS:
            self._send(503, {"error": "数据库暂不可用，请稍后重试；若持续出现，请检查数据库结构。"})
        except OSError:
            self._send(500, {"error": "工作台文件读取失败。"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


def create_server(db_path: str | Path, port: int = 8765) -> ThreadingHTTPServer:
    """创建绑定本机地址的工作台服务，不在此处启动线程。"""
    check_workbench(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    server.db_path = database_target(db_path)
    server.csrf_token = secrets.token_urlsafe(32)
    return server


def main() -> int:
    """兼容入口；命令解析位于 cli/。"""
    from data_analysis_agent.cli.workbench import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
