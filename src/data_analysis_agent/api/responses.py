"""Shared HTTP response envelopes and error formatting."""

from fastapi.responses import JSONResponse
from pydantic import BaseModel

class Result(BaseModel):
    status: str = "ok"
    result: dict

class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None

class ErrorResult(BaseModel):
    status: str = "error"
    error: ErrorBody

def ok_result(result):
    return {"status": "ok", "result": result}

def error_response(status, code, message, details=None):
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"status": "error", "error": body})
