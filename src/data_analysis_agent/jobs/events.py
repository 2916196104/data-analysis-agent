"""任务状态与队列变更通知中心：提供异步 SSE 事件唤醒、跨进程 PostgreSQL LISTEN/NOTIFY 桥接及 Worker 低延迟唤醒。"""

import asyncio
import atexit
import logging
from threading import Condition, Event as ThreadEvent, Lock, Thread

import psycopg

from data_analysis_agent.core.database import database_identity, is_postgres

logger = logging.getLogger(__name__)

PG_TASK_EVENT_CHANNEL = "review_task_events"

_CONDITION = Condition()
_VERSIONS: dict[str, int] = {}
_ASYNC_SUBSCRIBERS: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
_MAX_TRACKED = 1024

_QUEUE_CONDITION = Condition()
_QUEUE_VERSION = 0

_PG_LOCK = Lock()
_PG_LISTENERS: dict[str, "_PgTaskListener"] = {}


def task_event_version(task_id: str) -> int:
    with _CONDITION:
        return _VERSIONS.get(task_id, 0)


def notify_task_update(task_id: str) -> None:
    """标记任务状态或进度已写入，唤醒所有同步与异步等待该任务的 SSE 监听者。"""
    with _CONDITION:
        _VERSIONS[task_id] = _VERSIONS.get(task_id, 0) + 1
        if len(_VERSIONS) > _MAX_TRACKED:
            oldest = next(iter(_VERSIONS))
            if oldest != task_id:
                _VERSIONS.pop(oldest, None)
        _CONDITION.notify_all()
        subscribers = tuple(_ASYNC_SUBSCRIBERS.get(task_id, ()))
    for loop, event in subscribers:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # 事件循环已关闭时忽略残余订阅者，由协程 finally 自动清理。
            pass


def wait_task_update(task_id: str, last_version: int, *, timeout: float = 0.5) -> int:
    """同步阻塞等待任务版本号推进或超时；保留供同步调用方兼容使用。"""
    with _CONDITION:
        current = _VERSIONS.get(task_id, 0)
        if current != last_version:
            return current
        _CONDITION.wait(timeout=timeout)
        return _VERSIONS.get(task_id, 0)


async def async_wait_task_update(task_id: str, last_version: int, *, timeout: float = 15.0) -> int:
    """非阻塞异步等待任务版本号推进或超时，不占用 AnyIO 线程池。"""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    subscriber = (loop, event)
    with _CONDITION:
        current = _VERSIONS.get(task_id, 0)
        if current != last_version:
            return current
        _ASYNC_SUBSCRIBERS.setdefault(task_id, set()).add(subscriber)
    try:
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.0, float(timeout)))
        except asyncio.TimeoutError:
            pass
    finally:
        with _CONDITION:
            bucket = _ASYNC_SUBSCRIBERS.get(task_id)
            if bucket is not None:
                bucket.discard(subscriber)
                if not bucket:
                    _ASYNC_SUBSCRIBERS.pop(task_id, None)
            current = _VERSIONS.get(task_id, 0)
    return current


class _PgTaskListener:
    """单个 PostgreSQL 目标的共享 LISTEN 后台守护线程，将跨进程 NOTIFY 桥接到进程内事件中心。"""

    def __init__(self, dsn: str, identity: str):
        self._dsn = dsn
        self._identity = identity
        self._stop = ThreadEvent()
        self._connected = ThreadEvent()
        self._thread = Thread(target=self._run, name=f"pg-task-listen-{identity[-24:]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def active(self) -> bool:
        return self._thread.is_alive() and self._connected.is_set()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._connected.clear()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                with psycopg.connect(self._dsn, autocommit=True, connect_timeout=5) as conn:
                    conn.execute(f"LISTEN {PG_TASK_EVENT_CHANNEL}")
                    self._connected.set()
                    backoff = 0.5
                    while not self._stop.is_set():
                        for notify in conn.notifies(timeout=0.25):
                            if self._stop.is_set():
                                break
                            payload = getattr(notify, "payload", "")
                            if payload:
                                notify_task_update(str(payload))
            except Exception as exc:
                self._connected.clear()
                if not self._stop.is_set():
                    logger.debug(
                        "PostgreSQL LISTEN 断线，将在 %.1fs 后重试 (%s: %s)",
                        backoff,
                        self._identity,
                        type(exc).__name__,
                    )
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2.0, 10.0)
            finally:
                self._connected.clear()


def ensure_pg_task_listener(task_db) -> bool:
    """为 PostgreSQL 任务库按目标幂等启动共享 LISTEN 监听线程；SQLite 目标直接返回 False。"""
    if not is_postgres(task_db):
        return False
    try:
        identity = database_identity(task_db)
    except Exception:
        return False
    with _PG_LOCK:
        listener = _PG_LISTENERS.get(identity)
        if listener is not None and listener._thread.is_alive() and not listener._stop.is_set():
            return True
        listener = _PgTaskListener(task_db, identity)
        _PG_LISTENERS[identity] = listener
        listener.start()
        return True


def pg_task_listener_active(task_db) -> bool:
    """判断指定 PostgreSQL 任务库的共享 LISTEN 连接当前是否处于就绪态。"""
    if not is_postgres(task_db):
        return False
    try:
        identity = database_identity(task_db)
    except Exception:
        return False
    with _PG_LOCK:
        listener = _PG_LISTENERS.get(identity)
        return bool(listener is not None and listener.active)


def stop_pg_task_listeners(task_db=None) -> int:
    """关闭指定或全部 PostgreSQL LISTEN 监听线程并等待底层连接释放。"""
    target_identity = None
    if task_db is not None:
        if not is_postgres(task_db):
            return 0
        try:
            target_identity = database_identity(task_db)
        except Exception:
            return 0
    with _PG_LOCK:
        keys = [
            key for key in list(_PG_LISTENERS)
            if target_identity is None or key == target_identity
        ]
        selected = [_PG_LISTENERS.pop(key) for key in keys]
    for listener in selected:
        listener.stop()
    return len(selected)


atexit.register(stop_pg_task_listeners)


def queue_event_version() -> int:
    with _QUEUE_CONDITION:
        return _QUEUE_VERSION


def notify_queue_update() -> None:
    """标记新任务入队或 Worker 停止信号，低延迟唤醒所有等待中的任务通道。"""
    global _QUEUE_VERSION
    with _QUEUE_CONDITION:
        _QUEUE_VERSION += 1
        _QUEUE_CONDITION.notify_all()


def wait_queue_update(last_version: int, *, timeout: float = 1.0) -> int:
    """等待队列新任务通知或超时；跨进程提交仍按 timeout 兜底轮询。"""
    with _QUEUE_CONDITION:
        if _QUEUE_VERSION != last_version:
            return _QUEUE_VERSION
        _QUEUE_CONDITION.wait(timeout=timeout)
        return _QUEUE_VERSION
