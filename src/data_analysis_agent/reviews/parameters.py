"""各传输层共用的评论范围、身份和规则输入。"""

import math
import re
from datetime import date

from pydantic import Field, model_validator

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.validation import Input, Text, validate_integer, validate_text
from data_analysis_agent.reviews.scope import review_scope


DEFAULT_RULES = {
    "name": "negative-rate-growth",
    "min_samples": 20,
    "min_coverage": 0.8,
    "min_negative_count": 3,
    "min_increase_pp": 10.0,
    "cooldown_days": 30,
}


def validate_rules(values=None):
    """返回规范化监控规则，或拒绝不支持/无效的值。"""
    if values is not None and not isinstance(values, dict):
        raise ReviewDataError("rules 必须是规则参数字典。")
    if values and set(values) - DEFAULT_RULES.keys():
        raise ReviewDataError("rules 含不支持的参数。")
    result = {**DEFAULT_RULES, **(values or {})}
    result["name"] = validate_text(result["name"], "规则名称", maximum=100)
    for name in ("min_samples", "min_negative_count"):
        validate_integer(result[name], name, maximum=1000000)
    validate_integer(result["cooldown_days"], "cooldown_days", minimum=0, maximum=3650)
    for name, lower, upper in (("min_coverage", 0, 1), ("min_increase_pp", 0, 100)):
        value = result[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper
                or not math.isfinite(value) or (name == "min_increase_pp" and value == 0)):
            raise ReviewDataError(f"{name} 必须是 {lower}—{upper} 的有限数值，占比增幅须大于 0。")
        result[name] = float(value)
    return result


def parse_month(value, name):
    """将严格的 YYYY-MM 月份解析为该月第一天。"""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ReviewDataError(f"{name} 请使用 YYYY-MM 月份。")
    try:
        return date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise ReviewDataError(f"{name} 请使用有效月份。") from exc


def shift_month(day, amount):
    """返回偏移月份的第一天，并校验日历范围。"""
    year, month = divmod(day.year * 12 + day.month - 1 + amount, 12)
    try:
        return date(year, month + 1, 1)
    except ValueError as exc:
        raise ReviewDataError("月份或基线超出支持的日期范围。") from exc


class Scope(Input):
    product_ids: list[Text] = Field(default_factory=list, max_length=100)
    date_from: str | None = Field(default=None, max_length=10)
    date_to: str | None = Field(default=None, max_length=10)
    as_of: str | None = Field(default=None, max_length=40)
    source: Text | None = None
    dataset_version: Text | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        review_scope(**{name: getattr(self, name) for name in Scope.model_fields})
        return self


class Identity(Input):
    model: Text
    base_url: Text
    prompt_version: Text


class AnalysisScope(Scope, Identity):
    date_from: str = Field(max_length=10)
    date_to: str = Field(max_length=10)


class BaselineScope(AnalysisScope):
    baseline_from: str = Field(max_length=10)
    baseline_to: str = Field(max_length=10)

    @model_validator(mode="after")
    def valid_baseline(self):
        review_scope(date_from=self.baseline_from, date_to=self.baseline_to)
        if self.baseline_to >= self.date_from:
            raise ValueError("基线结束日期必须早于当前窗口开始日期。")
        return self


class Rules(Input):
    name: str = DEFAULT_RULES["name"]
    min_samples: int = Field(default=DEFAULT_RULES["min_samples"], strict=True)
    min_coverage: float = Field(default=DEFAULT_RULES["min_coverage"], strict=True)
    min_negative_count: int = Field(default=DEFAULT_RULES["min_negative_count"], strict=True)
    min_increase_pp: float = Field(default=DEFAULT_RULES["min_increase_pp"], strict=True)
    cooldown_days: int = Field(default=DEFAULT_RULES["cooldown_days"], strict=True)

    @model_validator(mode="after")
    def valid_rules(self):
        validate_rules(self.model_dump())
        return self
