"""批量提取评论的六维度观点：并发请求模型，校验原文证据后串行保存结果。"""

import json
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.reviews.label_store import (
    check_labeling_schema, pending_reviews, save_annotation_on_connection,
)
# 保留这些导出，兼容从本模块导入观点定义和校验函数的调用方。
from data_analysis_agent.reviews.opinions import (
    ASPECTS, Extraction, Opinion, OpinionValidationError, validate_batch_opinions,
    validate_opinions,
)
from data_analysis_agent.reviews.prompts import (
    BATCH_SYSTEM_PROMPT, MAX_BATCH_CHARS, MAX_TEXT_CHARS, PROMPT_VERSION, SYSTEM_PROMPT,
)
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.store import connect_review_database

TRANSIENT_ERROR_LIMIT = 3
TRANSIENT_ERRORS = {"model_timeout", "model_connection_error", "model_server_error"}


@dataclass
class _BatchOutcome:
    items: list[dict]
    model_calls: int
    should_stop: bool = False
    stop_error: str | None = None


def _metadata(response, secret: str) -> dict:
    """提取模型版本和用量；响应中的文本字段在保存前移除密钥。"""
    def redact_text(value):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        return None

    token_usage = {}
    if response.usage is not None:
        usage_fields = (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        )
        for result_field, response_field in usage_fields:
            value = getattr(response.usage, response_field, None)
            # bool 也是 int 的子类，但不能作为合法的 token 数量保存。
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                token_usage[result_field] = value

    return {
        "model_version": redact_text(response.model),
        "response_id": redact_text(response.id),
        "usage": token_usage,
    }


def _label_review(
    client: OpenAI, review: dict, settings: AgentSettings,
) -> tuple[list[dict], dict, str | None, bool, bool, float]:
    """处理单条评论，不写数据库；返回观点、元数据、错误码、停批标记、调用标记和耗时。"""
    started = perf_counter()
    opinions = []
    metadata = {}
    error_code = None
    should_stop = False
    model_called = False

    if len(review["text"]) > MAX_TEXT_CHARS:
        error_code = "input_too_long"
    else:
        try:
            # 请求失败也计入模型调用次数；SDK 内部重试不另计。
            model_called = True
            response = client.chat.completions.create(
                model=settings.model,
                temperature=0,
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps({"review_text": review["text"]}, ensure_ascii=False),
                    },
                ],
            )
            metadata = _metadata(response, settings.api_key)

            # 截断的输出不能作为完整标注；观点还必须能定位到评论原文。
            if not response.choices or response.choices[0].finish_reason != "stop":
                raise OpinionValidationError("模型没有完整完成观点输出。")
            opinions = validate_opinions(response.choices[0].message.content, review["text"])
        except OpinionValidationError:
            error_code = "invalid_output"
        # 认证、限流及其他不可恢复的 API 错误会阻止后续评论继续派发。
        except AuthenticationError:
            error_code = "authentication_error"
            should_stop = True
        except RateLimitError:
            error_code = "rate_limit"
            should_stop = True
        except APITimeoutError:
            error_code = "model_timeout"
        except APIConnectionError:
            error_code = "model_connection_error"
        except InternalServerError:
            error_code = "model_server_error"
        except APIError:
            error_code = "model_api_error"
            should_stop = True

    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    return opinions, metadata, error_code, should_stop, model_called, elapsed_ms


def _single_outcome(client: OpenAI, review: dict, settings: AgentSettings) -> _BatchOutcome:
    opinions, metadata, error_code, should_stop, model_called, elapsed_ms = _label_review(
        client, review, settings,
    )
    return _BatchOutcome(
        items=[{
            "review": review,
            "opinions": opinions,
            "metadata": metadata,
            "error_code": error_code,
            "elapsed_ms": elapsed_ms,
        }],
        model_calls=int(model_called),
        should_stop=should_stop,
        stop_error=error_code if should_stop else None,
    )


def _metadata_for_item(metadata: dict, index: int, count: int) -> dict:
    """将一次批量请求的 token 用量按条目均分，确保数据库汇总不重复计算。"""
    usage = {}
    for name, value in metadata.get("usage", {}).items():
        quotient, remainder = divmod(value, count)
        usage[name] = quotient + int(index < remainder)
    return {**metadata, "usage": usage}


def _failed_batch(
    reviews: list[dict], *, metadata: dict, error_code: str, elapsed_ms: float,
    should_stop: bool,
) -> _BatchOutcome:
    per_review_elapsed = round(elapsed_ms / len(reviews), 3)
    return _BatchOutcome(
        items=[{
            "review": review,
            "opinions": [],
            "metadata": _metadata_for_item(metadata, index, len(reviews)),
            "error_code": error_code,
            "elapsed_ms": per_review_elapsed,
        } for index, review in enumerate(reviews)],
        model_calls=1,
        should_stop=should_stop,
        stop_error=error_code if should_stop else None,
    )


def _request_batch(client: OpenAI, reviews: list[dict], settings: AgentSettings) -> _BatchOutcome:
    started = perf_counter()
    metadata = {}
    try:
        response = client.chat.completions.create(
            model=settings.model,
            temperature=0,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "items": [
                        {"review_id": review["id"], "review_text": review["text"]}
                        for review in reviews
                    ]
                }, ensure_ascii=False)},
            ],
        )
        metadata = _metadata(response, settings.api_key)
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise OpinionValidationError("模型没有完整完成批量观点输出。")
        opinions_by_id = validate_batch_opinions(
            response.choices[0].message.content,
            {review["id"]: review["text"] for review in reviews},
        )
    except OpinionValidationError:
        raise
    except AuthenticationError:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="authentication_error",
            elapsed_ms=elapsed_ms, should_stop=True,
        )
    except RateLimitError:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="rate_limit",
            elapsed_ms=elapsed_ms, should_stop=True,
        )
    except APITimeoutError:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="model_timeout",
            elapsed_ms=elapsed_ms, should_stop=False,
        )
    except APIConnectionError:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="model_connection_error",
            elapsed_ms=elapsed_ms, should_stop=False,
        )
    except InternalServerError:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="model_server_error",
            elapsed_ms=elapsed_ms, should_stop=False,
        )
    except APIError as exc:
        # 请求体过大或服务端拒绝当前批大小时，允许递归拆分；其他 API 错误保持停批语义。
        if getattr(exc, "status_code", None) in {400, 413, 422}:
            raise OpinionValidationError("模型拒绝了当前批量请求。") from exc
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return _failed_batch(
            reviews, metadata=metadata, error_code="model_api_error",
            elapsed_ms=elapsed_ms, should_stop=True,
        )

    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    per_review_elapsed = round(elapsed_ms / len(reviews), 3)
    return _BatchOutcome(
        items=[{
            "review": review,
            "opinions": opinions_by_id[review["id"]],
            "metadata": _metadata_for_item(metadata, index, len(reviews)),
            "error_code": None,
            "elapsed_ms": per_review_elapsed,
        } for index, review in enumerate(reviews)],
        model_calls=1,
    )


def _split_batch(
    client: OpenAI, reviews: list[dict], settings: AgentSettings, parent_calls: int,
) -> _BatchOutcome:
    midpoint = len(reviews) // 2
    left = _label_batch(client, reviews[:midpoint], settings)
    if left.should_stop:
        return _BatchOutcome(
            items=left.items,
            model_calls=parent_calls + left.model_calls,
            should_stop=True,
            stop_error=left.stop_error,
        )
    right = _label_batch(client, reviews[midpoint:], settings)
    return _BatchOutcome(
        items=left.items + right.items,
        model_calls=parent_calls + left.model_calls + right.model_calls,
        should_stop=right.should_stop,
        stop_error=right.stop_error,
    )


def _label_batch(client: OpenAI, reviews: list[dict], settings: AgentSettings) -> _BatchOutcome:
    if len(reviews) == 1:
        return _single_outcome(client, reviews[0], settings)
    try:
        outcome = _request_batch(client, reviews, settings)
    except OpinionValidationError:
        return _split_batch(client, reviews, settings, parent_calls=1)
    if (
        len(reviews) > 1
        and outcome.items
        and outcome.items[0]["error_code"] in TRANSIENT_ERRORS
    ):
        return _split_batch(client, reviews, settings, parent_calls=outcome.model_calls)
    return outcome


def _review_batches(reviews: list[dict], batch_size: int):
    current = []
    current_chars = 0
    for review in reviews:
        text_chars = len(review["text"])
        if text_chars > MAX_TEXT_CHARS:
            if current:
                yield current
                current, current_chars = [], 0
            yield [review]
            continue
        if current and (
            len(current) >= batch_size or current_chars + text_chars > MAX_BATCH_CHARS
        ):
            yield current
            current, current_chars = [], 0
        current.append(review)
        current_chars += text_chars
    if current:
        yield current


def run_labeling(
    db_path: str | Path,
    *,
    limit: int = 20,
    retry_failed: bool = False,
    product_ids: list[str] | None = None,
    env_file: str | Path = ".env",
    settings: AgentSettings | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    as_of: str | None = None,
    source: str | None = None,
    dataset_version: str | None = None,
    progress: Callable[[dict], None] | None = None,
    workers: int = 1,
    batch_size: int = 1,
) -> dict:
    """按筛选范围标注一批评论，将结果写入已初始化的数据库并返回批次摘要。

    limit 限制整批评论数，workers 控制批量模型请求并发数，batch_size 控制每次请求的评论数。成功记录自动跳过，
    失败记录只有在 retry_failed=True 时才重新选取。未传 settings 时读取配置。
    progress 在每条结果保存后接收进度，开始时也会收到一次初始进度。
    """
    # 先检查输入和筛选范围，避免无效参数触发模型请求。
    validate_integer(limit, "limit", maximum=1000)
    validate_integer(workers, "workers", maximum=4)
    validate_integer(batch_size, "batch_size", maximum=8)
    if product_ids is not None:
        if not isinstance(product_ids, list) or any(
            not isinstance(product_id, str) or not product_id.strip()
            for product_id in product_ids
        ):
            raise ReviewDataError("product_ids 必须是非空商品 ID 的列表。")

    _, _, scope = review_scope(
        product_ids=product_ids,
        date_from=date_from,
        date_to=date_to,
        as_of=as_of,
        source=source,
        dataset_version=dataset_version,
    )
    settings = settings or AgentSettings.from_values(read_settings(env_file))

    # 模型、服务地址和提示词版本共同决定哪些评论已经完成标注。
    model_identity = {
        "model": settings.model,
        "base_url": settings.base_url,
        "prompt_version": PROMPT_VERSION,
    }
    review_filters = {
        name: value for name, value in scope.items() if name != "timezone"
    }
    reviews = pending_reviews(
        db_path, **model_identity, limit=limit, retry_failed=retry_failed, **review_filters,
    )
    batch_result = {
        "status": "ok",
        **model_identity,
        "batch_size": batch_size,
        "selected_reviews": len(reviews),
        "processed_reviews": 0,
        "successful_reviews": 0,
        "failed_reviews": 0,
        "opinion_count": 0,
        "already_completed": 0,
        "model_calls": 0,
        "stopped_reason": None,
        "results": [],
        "scope": scope,
    }

    def notify_progress() -> None:
        if progress:
            progress({
                "stage": "labeling",
                "completed": batch_result["processed_reviews"],
                "total": len(reviews),
                "unit": "reviews",
                "successful_reviews": batch_result["successful_reviews"],
                "failed_reviews": batch_result["failed_reviews"],
                "model_calls": batch_result["model_calls"],
            })

    notify_progress()
    if not reviews:
        return batch_result

    # 工作线程只请求模型；数据库写入和批次计数留在当前线程，避免并发写入冲突。
    with (
        closing(connect_review_database(db_path)) as write_connection,
        OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as executor,
    ):
        check_labeling_schema(write_connection)
        remaining_batches = iter(_review_batches(reviews, batch_size))
        pending_requests = {}
        for _ in range(workers):
            review_batch = next(remaining_batches, None)
            if review_batch is None:
                break
            future = executor.submit(_label_batch, client, review_batch, settings)
            pending_requests[future] = review_batch

        stopping = False
        consecutive_transient_errors = 0
        while pending_requests:
            completed_requests, _ = wait(pending_requests, return_when=FIRST_COMPLETED)
            for future in completed_requests:
                pending_requests.pop(future)
                outcome = future.result()
                batch_result["model_calls"] += outcome.model_calls

                for item in outcome.items:
                    review = item["review"]
                    opinions = item["opinions"]
                    metadata = item["metadata"]
                    error_code = item["error_code"]
                    annotation_status = "failed" if error_code else "success"
                    annotation_id = save_annotation_on_connection(
                        write_connection,
                        review,
                        **model_identity,
                        status=annotation_status,
                        opinions=opinions,
                        error_code=error_code,
                        elapsed_ms=item["elapsed_ms"],
                        **metadata,
                    )
                    batch_result["processed_reviews"] += 1

                    # 保存时若已有成功记录，存储层返回 None，不重复累计成功数和观点数。
                    if annotation_id is None:
                        batch_result["already_completed"] += 1
                        review_status = "already_completed"
                    else:
                        if error_code:
                            batch_result["failed_reviews"] += 1
                        else:
                            batch_result["successful_reviews"] += 1
                        batch_result["opinion_count"] += len(opinions)
                        review_status = annotation_status

                    batch_result["results"].append({
                        "review_id": review["id"],
                        "annotation_id": annotation_id,
                        "status": review_status,
                        "error_code": error_code,
                        "opinion_count": len(opinions),
                    })
                    notify_progress()

                    # 按完成结果的处理顺序累计连续瞬时错误，其他结果会重置计数。
                    if error_code in TRANSIENT_ERRORS:
                        consecutive_transient_errors += 1
                    else:
                        consecutive_transient_errors = 0

                    if consecutive_transient_errors >= TRANSIENT_ERROR_LIMIT:
                        stopping = True
                        batch_result["stopped_reason"] = (
                            batch_result["stopped_reason"] or "repeated_model_api_errors"
                        )

                if outcome.should_stop:
                    stopping = True
                    batch_result["stopped_reason"] = (
                        batch_result["stopped_reason"] or outcome.stop_error
                    )

            # 停批后只收尾已提交的请求；正常情况下补足空闲并发名额。
            while not stopping and len(pending_requests) < workers:
                next_batch = next(remaining_batches, None)
                if next_batch is None:
                    break
                future = executor.submit(_label_batch, client, next_batch, settings)
                pending_requests[future] = next_batch

    if batch_result["failed_reviews"] or batch_result["stopped_reason"]:
        batch_result["status"] = "partial"
    return batch_result
