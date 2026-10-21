"""Loopback-only web workbench, with explicit schema setup and no model calls."""

import argparse
import json
import re
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .review_loader import ReviewDataError, _unique_object, _reject_constant
from .review_workbench_store import (
    ReviewConflict, ReviewNotFound, check_workbench, init_workbench, list_reviews,
    review_detail, save_human_review, workbench_options,
)

ASSET_DIR = Path(__file__).with_name("workbench_assets")


def _parameters(query, allowed):
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
    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self):
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

    def _dispatch(self, method):
        if not self._local_request():
            return
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            detail = re.fullmatch(r"/api/reviews/([1-9][0-9]{0,8})", path)
            save = re.fullmatch(r"/api/reviews/([1-9][0-9]{0,8})/human-reviews", path)
            if method == "GET":
                if path in ("/", "/workbench.js", "/workbench.css"):
                    name, mime = {"/": ("index.html", "text/html"),
                                  "/workbench.js": ("workbench.js", "text/javascript"),
                                  "/workbench.css": ("workbench.css", "text/css")}[path]
                    self._send(200, (ASSET_DIR / name).read_bytes(), mime + "; charset=utf-8")
                elif path == "/api/options":
                    result = workbench_options(self.server.db_path, **_parameters(parsed.query, ("profile_id",)))
                    self._send(200, {**result, "csrf_token": self.server.csrf_token})
                elif path == "/api/reviews":
                    params = _parameters(parsed.query, ("profile_id", "product_key", "date_from", "date_to", "aspect",
                                                        "sentiment", "processing_status", "human_status", "page", "page_size"))
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
                body = json.loads(self.rfile.read(int(length)).decode("utf-8"),
                                  object_pairs_hook=_unique_object, parse_constant=_reject_constant)
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
        except sqlite3.Error:
            self._send(503, {"error": "数据库暂不可用，请稍后重试；若持续出现，请检查数据库结构。"})
        except OSError:
            self._send(500, {"error": "工作台文件读取失败。"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def create_server(db_path, port=8765):
    check_workbench(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    server.db_path = Path(db_path).resolve()
    server.csrf_token = secrets.token_urlsafe(32)
    return server


def main():
    parser = argparse.ArgumentParser(description="本机评论标注与人工复核工作台")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="由你显式增加工作台表，保留已有模型记录")
    serve = commands.add_parser("serve", help="启动本机网页，不会升级数据库或调用模型")
    for command in (setup, serve):
        command.add_argument("--db", required=True, type=Path)
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
    except (ReviewDataError, sqlite3.Error, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
