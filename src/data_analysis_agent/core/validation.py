"""Input primitives shared by business parameters and transport adapters."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from data_analysis_agent.core.errors import ReviewDataError

Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class Input(BaseModel):
    """Reject unknown input fields at both HTTP and task boundaries."""

    model_config = ConfigDict(extra="forbid")


def validate_integer(value, name, minimum=1, maximum=2147483647):
    """Validate a bounded integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ReviewDataError(f"{name} 必须是 {minimum}—{maximum} 的整数。")
    return value


def validate_text(value, name, maximum=200, allow_empty=False):
    """Validate a bounded string and return its trimmed value."""
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ReviewDataError(f"{name} 必须是长度不超过 {maximum} 的{'文本' if allow_empty else '非空文本'}。")
    return value.strip()
