"""Validated six-aspect opinion labeling using the existing model configuration."""

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from time import perf_counter

from openai import (APIConnectionError, APIError, APITimeoutError, AuthenticationError,
                    InternalServerError, OpenAI, RateLimitError)

from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.validation import validate_integer
# Re-export the existing contract for callers of review_labeler/reviews.labeling.
from data_analysis_agent.reviews.opinions import ASPECTS, Extraction, Opinion, OpinionValidationError, validate_opinions
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.prompts import MAX_TEXT_CHARS, PROMPT_VERSION, SYSTEM_PROMPT

TRANSIENT_ERROR_LIMIT = 3
TRANSIENT_ERRORS = {"model_timeout", "model_connection_error", "model_server_error"}




def _metadata(response, secret):
    def clean(value):
        return value.replace(secret, "[REDACTED]") if isinstance(value, str) else None

    usage = {}
    if response.usage is not None:
        for public, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                              ("total_tokens", "total_tokens")):
            value = getattr(response.usage, field, None)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[public] = value
    return {"model_version": clean(response.model), "response_id": clean(response.id), "usage": usage}


def _label_review(client, review, settings):
    started = perf_counter()
    opinions, metadata, error, stop, called = [], {}, None, False, False
    if len(review["text"]) > MAX_TEXT_CHARS:
        error = "input_too_long"
    else:
        try:
            called = True
            response = client.chat.completions.create(
                model=settings.model, temperature=0, max_tokens=4096,
                response_format={"type": "json_object"}, messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({"review_text": review["text"]}, ensure_ascii=False)},
                ],
            )
            metadata = _metadata(response, settings.api_key)
            if not response.choices or response.choices[0].finish_reason != "stop":
                raise OpinionValidationError("模型没有完整完成观点输出。")
            opinions = validate_opinions(response.choices[0].message.content, review["text"])
        except OpinionValidationError:
            error = "invalid_output"
        except AuthenticationError:
            error, stop = "authentication_error", True
        except RateLimitError:
            error, stop = "rate_limit", True
        except APITimeoutError:
            error = "model_timeout"
        except APIConnectionError:
            error = "model_connection_error"
        except InternalServerError:
            error = "model_server_error"
        except APIError:
            error, stop = "model_api_error", True
    return opinions, metadata, error, stop, called, round((perf_counter() - started) * 1000, 3)


def run_labeling(db_path, *, limit=20, retry_failed=False, product_ids=None, env_file=".env", settings=None,
                 date_from=None, date_to=None, as_of=None, source=None, dataset_version=None, progress=None,
                 workers=1):
    validate_integer(limit, "limit", maximum=1000)
    validate_integer(workers, "workers", maximum=4)
    if product_ids is not None and (not isinstance(product_ids, list)
                                  or any(not isinstance(item, str) or not item.strip() for item in product_ids)):
        raise ReviewDataError("product_ids 必须是非空商品 ID 的列表。")
    _, _, scope = review_scope(product_ids=product_ids, date_from=date_from, date_to=date_to,
                              as_of=as_of, source=source, dataset_version=dataset_version)
    settings = settings or AgentSettings.from_values(read_settings(env_file))
    identity = {"model": settings.model, "base_url": settings.base_url, "prompt_version": PROMPT_VERSION}
    reviews = pending_reviews(db_path, **identity, limit=limit, retry_failed=retry_failed,
                              **{k: v for k, v in scope.items() if k != "timezone"})
    result = {"status": "ok", **identity, "selected_reviews": len(reviews), "processed_reviews": 0,
              "successful_reviews": 0, "failed_reviews": 0, "opinion_count": 0,
              "already_completed": 0, "model_calls": 0, "stopped_reason": None, "results": [], "scope": scope}
    def notify():
        if progress:
            progress({"stage": "labeling", "completed": result["processed_reviews"], "total": len(reviews),
                      "unit": "reviews", "successful_reviews": result["successful_reviews"],
                      "failed_reviews": result["failed_reviews"], "model_calls": result["model_calls"]})

    notify()
    if not reviews:
        return result
    # One runner owns the batch; only model requests overlap, and SQLite writes stay on this thread.
    with OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout_seconds,
                max_retries=settings.max_retries) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        remaining = iter(reviews)
        pending = {}
        for _ in range(min(workers, len(reviews))):
            review = next(remaining)
            pending[pool.submit(_label_review, client, review, settings)] = review
        stopping = False
        transient_errors = 0
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                review = pending.pop(future)
                opinions, metadata, error, stop, called, elapsed_ms = future.result()
                result["model_calls"] += int(called)
                annotation_id = save_annotation(
                    db_path, review, **identity, status="failed" if error else "success", opinions=opinions,
                    error_code=error, elapsed_ms=elapsed_ms, **metadata,
                )
                result["processed_reviews"] += 1
                if annotation_id is None:
                    result["already_completed"] += 1
                else:
                    result["failed_reviews" if error else "successful_reviews"] += 1
                    result["opinion_count"] += len(opinions)
                result["results"].append({"review_id": review["id"], "annotation_id": annotation_id,
                                          "status": "already_completed" if annotation_id is None else (
                                              "failed" if error else "success"), "error_code": error,
                                          "opinion_count": len(opinions)})
                notify()
                transient_errors = transient_errors + 1 if error in TRANSIENT_ERRORS else 0
                if stop:
                    stopping = True
                    result["stopped_reason"] = result["stopped_reason"] or error
                elif transient_errors >= TRANSIENT_ERROR_LIMIT:
                    stopping = True
                    result["stopped_reason"] = result["stopped_reason"] or "repeated_model_api_errors"
            while not stopping and len(pending) < workers:
                next_review = next(remaining, None)
                if next_review is None:
                    break
                pending[pool.submit(_label_review, client, next_review, settings)] = next_review
    if result["failed_reviews"] or result["stopped_reason"]:
        result["status"] = "partial"
    return result
