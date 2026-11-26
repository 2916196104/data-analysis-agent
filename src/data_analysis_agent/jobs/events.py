"""进程内轻量任务状态变更通知中心，供 SSE 进度推送低延迟唤醒。"""

from threading import Condition

_CONDITION = Condition()
_VERSIONS: dict[str, int] = {}
_MAX_TRACKED = 1024


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
