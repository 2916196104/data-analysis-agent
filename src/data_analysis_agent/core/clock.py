"""持久化评论和后台任务共用的 UTC 时间戳。"""

from datetime import datetime, timezone


def utc_now() -> str:
    """返回保持现有毫秒精度的 ISO UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
