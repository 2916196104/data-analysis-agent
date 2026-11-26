"""进程内轻量任务状态与队列变更通知中心，供 SSE 进度推送与 Worker 低延迟唤醒。"""

from threading import Condition

_CONDITION = Condition()
_VERSIONS: dict[str, int] = {}
_MAX_TRACKED = 1024

_QUEUE_CONDITION = Condition()
_QUEUE_VERSION = 0


def task_event_version(task_id: str) -> int:
    with _CONDITION:
        return _VERSIONS.get(task_id, 0)


def notify_task_update(task_id: str) -> None:
    """标记任务状态或进度已写入，唤醒所有等待该任务的 SSE 监听者。"""
    with _CONDITION:
        _VERSIONS[task_id] = _VERSIONS.get(task_id, 0) + 1
        if len(_VERSIONS) > _MAX_TRACKED:
            oldest = next(iter(_VERSIONS))
            if oldest != task_id:
                _VERSIONS.pop(oldest, None)
        _CONDITION.notify_all()


def wait_task_update(task_id: str, last_version: int, *, timeout: float = 0.5) -> int:
    """阻塞等待任务版本号推进或超时；跨进程 worker 场景下会按 timeout 自动兜底复查。"""
    with _CONDITION:
        current = _VERSIONS.get(task_id, 0)
        if current != last_version:
            return current
        _CONDITION.wait(timeout=timeout)
        return _VERSIONS.get(task_id, 0)


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
