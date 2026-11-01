"""持续领取固定大小的数据块；模型请求复用现有标注和逐字证据校验。"""

import logging
import math
import threading
import time
import uuid
from types import SimpleNamespace

from openai import OpenAI

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs import label_queue as store
from data_analysis_agent.reviews.labeling import label_batch_once, review_batches
from data_analysis_agent.reviews.opinions import OpinionValidationError
from data_analysis_agent.reviews.prompts import PROMPT_VERSION

logger = logging.getLogger(__name__)


def retry_delay(exc):
    """兼容秒、毫秒与 HTTP 日期格式的 Retry-After，不记录服务端异常正文。"""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    headers = getattr(getattr(exc, "response", None), "headers", {})
    try:
        if headers.get("retry-after-ms"):
            seconds = float(headers["retry-after-ms"]) / 1000
            return max(0, seconds) if math.isfinite(seconds) else 0
        value = headers.get("retry-after", "0")
        try:
            seconds = float(value)
            return max(0, seconds) if math.isfinite(seconds) else 0
        except ValueError:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 0


class RequestCapture:
    def __init__(self, client):
        self.client = client
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.elapsed_ms, self.usage, self.retry_after = 0, {}, 0

    def create(self, **kwargs):
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**kwargs)
            for name, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
                value = getattr(response.usage, field, None)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self.usage[name] = value
            return response
        except Exception as exc:
            self.retry_after = retry_delay(exc)
            raise
        finally:
            self.elapsed_ms = (time.perf_counter() - started) * 1000


class LabelWorker:
    def __init__(self, db, stream, settings, *, chunk_size=100, batch_size=4, lease_seconds=120,
                 max_calls=6, max_retries=2, backoff_seconds=10, client=None, limiter=None):
        from data_analysis_agent.core.validation import validate_integer

        for name, value, maximum in (("chunk_size", chunk_size, 1000), ("batch_size", batch_size, 8),
                                      ("max_calls", max_calls, 100), ("lease_seconds", lease_seconds, 3600)):
            validate_integer(value, name, maximum=maximum)
        if max_retries < 0 or max_retries > 20 or backoff_seconds < 0:
            raise ReviewDataError("恢复次数须为 0–20，退避时间不能为负。")
        self.db, self.stream, self.settings = db, stream, settings
        self.chunk_size, self.batch_size, self.lease_seconds = chunk_size, batch_size, lease_seconds
        self.max_calls, self.max_retries, self.backoff_seconds = max_calls, max_retries, backoff_seconds
        self.worker_id = uuid.uuid4().hex
        self.profile_id = content_sha256([settings.model, settings.base_url, PROMPT_VERSION])
        self.warned_incompatible = False
        self.stop = threading.Event()
        self.limiter = limiter
        self.owns_client = client is None
        self.client = client or OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                                       timeout=settings.timeout_seconds, max_retries=0)

    def _one(self, tasks):
        if not tasks or self.stop.is_set():
            return
        if any((t["model"], t["base_url"], t["prompt_version"]) !=
               (self.settings.model, self.settings.base_url, PROMPT_VERSION) for t in tasks):
            raise ReviewDataError("Worker 模型配置与任务身份不匹配，请使用原配置恢复。")
        reservation = None
        if self.limiter:
            from data_analysis_agent.jobs.label_limiter import QuotaRequestTooLarge

            try:
                reservation = self.limiter.acquire(tasks, self.stop)
            except QuotaRequestTooLarge:
                outcome = SimpleNamespace(items=[{"review": t, "error_code": "quota_request_too_large",
                    "opinions": [], "metadata": {}, "elapsed_ms": 0} for t in tasks])
                store.finish_tasks(self.db, tasks, outcome)
                return
            if reservation is None:
                return
        try:
            request_id, ready = store.prepare_request(self.db, tasks, self.worker_id, max_calls=self.max_calls)
            if not ready:
                return
            capture = RequestCapture(self.client)
            try:
                outcome = label_batch_once(capture, ready, self.settings)
            except OpinionValidationError:
                store.request_finished(self.db, request_id, elapsed_ms=capture.elapsed_ms,
                                       error_code="invalid_output", usage=capture.usage, was_split=True)
                if self.limiter:
                    self.limiter.release(reservation, capture.usage)
                    reservation = None
                store.record_split(self.db, ready)
                midpoint = len(ready) // 2
                self._one(ready[:midpoint])
                self._one(ready[midpoint:])
                return
            code = next((i["error_code"] for i in outcome.items if i["error_code"]), None)
            store.request_finished(self.db, request_id, elapsed_ms=capture.elapsed_ms, error_code=code,
                                   usage=capture.usage, retry_after=capture.retry_after)
            if self.limiter and code == "rate_limit":
                self.limiter.cooldown(max(capture.retry_after, self.backoff_seconds))
            if code == "authentication_error":
                logger.error("模型认证失败，已暂停该模型身份派发；请检查本地 .env 的模型、密钥和服务地址后执行 queue resume。")
            # 提交出错向上传播，step 不执行 ACK；租约超时后由其他 Worker 接管。
            store.finish_tasks(self.db, ready, outcome, max_calls=self.max_calls, max_retries=self.max_retries,
                               backoff_seconds=self.backoff_seconds, retry_after=capture.retry_after)
        finally:
            if self.limiter and reservation:
                self.limiter.release(reservation, locals().get("capture", SimpleNamespace(usage={})).usage)

    def step(self):
        store.pulse(self.db, self.worker_id, lease_seconds=self.lease_seconds)
        messages = self.stream.receive(self.worker_id, self.chunk_size)
        ids = [tid for _, tid in messages]
        tasks = store.claim_tasks(self.db, ids, self.worker_id, lease_seconds=self.lease_seconds,
                                  profile_id=self.profile_id)
        for batch in review_batches(tasks, self.batch_size):
            self._one(batch)
        ack_ids, deferred = store.delivery_ack_ids(self.db, ids, self.profile_id)
        if deferred and not self.warned_incompatible:
            logger.warning("已跳过其他模型身份的标注通知；旧任务与发件箱保留，请用原模型、服务地址和标注提示词配置恢复。")
            self.warned_incompatible = True
        self.stream.ack([mid for mid, tid in messages if tid in ack_ids])
        return len(tasks)

    def _heartbeat(self):
        while not self.stop.wait(min(5, self.lease_seconds / 3)):
            try:
                store.pulse(self.db, self.worker_id, lease_seconds=self.lease_seconds)
                if self.limiter:
                    self.limiter.heartbeat()
            except Exception:
                logger.error("Worker 心跳失败，停止派发；未提交任务将在租约超时后恢复。")
                self.stop.set()

    def run(self, *, run_id=None, max_seconds=None):
        with store.queue_session(self.db):
            return self._run_loop(run_id=run_id, max_seconds=max_seconds)

    def _run_loop(self, *, run_id=None, max_seconds=None):
        if run_id and store.run_state(self.db, run_id)["profile_id"] != self.profile_id:
            raise ReviewDataError("Worker 模型配置与任务身份不匹配，请使用原配置恢复。")
        started = time.monotonic()
        pulse = threading.Thread(target=self._heartbeat, daemon=True)
        pulse.start()
        timer = threading.Timer(max_seconds, self.stop.set) if max_seconds else None
        if timer:
            timer.start()
        reason = "stopped"
        try:
            while not self.stop.is_set():
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    reason = "time_limit"
                    break
                if run_id:
                    store.seed_run(self.db, run_id, chunk_size=self.chunk_size)
                store.publish_outbox(self.db, self.stream, limit=self.chunk_size,
                                     repair_seconds=self.lease_seconds, profile_id=self.profile_id)
                count = self.step()
                if run_id:
                    status = store.completion_state(self.db, run_id)
                    if status in {"completed", "partial", "paused"}:
                        reason = status
                        break
                if not count:
                    self.stop.wait(0.25)
        finally:
            self.stop.set()
            if timer:
                timer.cancel()
            pulse.join()
            store.release_worker(self.db, self.worker_id)
            store.pulse(self.db, self.worker_id, stopped=True)
            if self.owns_client:
                self.client.close()
        if reason == "stopped" and max_seconds and time.monotonic() - started >= max_seconds:
            reason = "time_limit"
        return reason
