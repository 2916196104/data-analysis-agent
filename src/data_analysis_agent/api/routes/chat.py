"""基础问答 HTTP 入口，提供原有 JSON 响应及增量 SSE 流式响应。"""

import asyncio
import json
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from data_analysis_agent.agents.chat import (
    ChatError,
    ChatHistoryTurn,
    answer_message,
    stream_answer_message,
)
from data_analysis_agent.api.responses import ErrorResult, Result, error_response, ok_result
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.validation import Input


class ChatRequest(Input):
    message: str = Field(min_length=1, max_length=4000, pattern=r"\S", strict=True)
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=20)


def _format_sse(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_router(env_file):
    router = APIRouter()

    @router.post("/chat", response_model=Result, tags=["AI 助手"],
                 responses={502: {"model": ErrorResult}, 504: {"model": ErrorResult}})
    def chat(body: ChatRequest):
        settings = AgentSettings.from_values(read_settings(env_file))
        try:
            return ok_result({"answer": answer_message(body.message, settings=settings, history=body.history)})
        except ChatError as exc:
            status = {"model_timeout": 504, "authentication_error": 503,
                      "rate_limit": 503, "model_connection_error": 503}.get(exc.code, 502)
            return error_response(status, exc.code, str(exc))

    @router.post("/chat/stream", tags=["AI 助手"],
                 responses={502: {"model": ErrorResult}, 504: {"model": ErrorResult}})
    async def chat_stream(body: ChatRequest, request: Request):
        """SSE 流式基础问答：先校验配置和输入，再持续推送 delta 并在校验 stop 后发送 done。"""
        settings = AgentSettings.from_values(read_settings(env_file))
        # 在建立流式响应前先校验消息与历史，不合法请求直接走标准 HTTP 错误响应。
        chunk_iterator = stream_answer_message(body.message, settings=settings, history=body.history)

        async def _stream():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
            cancelled = threading.Event()

            def _produce():
                parts: list[str] = []
                try:
                    for delta in chunk_iterator:
                        if cancelled.is_set():
                            return
                        parts.append(delta)
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                    if not cancelled.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", "".join(parts).strip()))
                except ChatError as exc:
                    if not cancelled.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", (exc.code, str(exc))))
                except Exception:
                    if not cancelled.is_set():
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("error", ("model_api_error", "模型服务调用失败，请检查后端模型配置或稍后重试。")),
                        )
                finally:
                    if hasattr(chunk_iterator, "close"):
                        try:
                            chunk_iterator.close()
                        except Exception:
                            pass

            worker = threading.Thread(target=_produce, name="chat-sse-stream", daemon=True)
            worker.start()
            yield _format_sse("start", {"mode": "basic"})
            try:
                while True:
                    if await request.is_disconnected():
                        cancelled.set()
                        return
                    try:
                        kind, payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            cancelled.set()
                            return
                        yield ": keep-alive\n\n"
                        continue
                    if kind == "delta":
                        yield _format_sse("delta", {"delta": payload})
                    elif kind == "done":
                        yield _format_sse("done", {"answer": payload})
                        return
                    elif kind == "error":
                        code, message = payload
                        yield _format_sse("error", {"code": code, "message": message})
                        return
            finally:
                cancelled.set()

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
