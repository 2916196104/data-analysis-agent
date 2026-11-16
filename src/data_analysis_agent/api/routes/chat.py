"""基础问答 HTTP 入口，沿用当前应用的后端配置和响应协议。"""

from fastapi import APIRouter
from pydantic import Field

from data_analysis_agent.agents.chat import ChatError, ChatHistoryTurn, answer_message
from data_analysis_agent.api.responses import ErrorResult, Result, error_response, ok_result
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.validation import Input


class ChatRequest(Input):
    message: str = Field(min_length=1, max_length=4000, pattern=r"\S", strict=True)
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=20)


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

    return router
