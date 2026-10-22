"""UTC timestamps shared by persisted reviews and background jobs."""

from datetime import datetime, timezone


def utc_now() -> str:
    """Return an ISO UTC timestamp with the existing millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
