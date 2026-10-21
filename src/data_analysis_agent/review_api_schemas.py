"""Validated HTTP and persisted task inputs; business validation stays shared."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_labeler import Opinion
from .review_loader import DATASET_VERSION
from .review_monitor import _month, _rules, _shift_month
from .review_scope import model_identity, review_scope

Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]
Aspect = Literal["quality", "effectiveness", "price", "packaging", "logistics", "service"]
Sentiment = Literal["positive", "negative", "neutral", "unknown"]
TaskStatus = Literal["queued", "running", "succeeded", "partial", "failed"]
TaskKind = Literal["import", "label", "analysis", "replay"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(Input):
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=20, ge=1, le=100)


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


class StatusQuery(Scope):
    model: Text | None = None
    base_url: Text | None = None
    prompt_version: Text | None = None

    @model_validator(mode="after")
    def valid_identity(self):
        model_identity(self.model, self.base_url, self.prompt_version)
        return self


class OpinionQuery(Scope, Identity, Page):
    pass


class AnalysisQuery(Scope, Identity):
    date_from: str = Field(max_length=10)
    date_to: str = Field(max_length=10)


class TrendQuery(AnalysisQuery):
    period: Literal["day", "month"] = "month"


class BaselineQuery(AnalysisQuery):
    baseline_from: str = Field(max_length=10)
    baseline_to: str = Field(max_length=10)

    @model_validator(mode="after")
    def valid_baseline(self):
        review_scope(date_from=self.baseline_from, date_to=self.baseline_to)
        if self.baseline_to >= self.date_from:
            raise ValueError("基线结束日期必须早于当前窗口开始日期。")
        return self


class IssueQuery(BaselineQuery):
    top_n: int = Field(default=6, ge=1, le=6)


class EvidenceQuery(AnalysisQuery, Page):
    aspect: Aspect | None = None
    sentiment: Sentiment | Literal["all"] = "negative"
    review_id: int | None = Field(default=None, ge=1, le=2147483647)


class ProductQuery(Page):
    source: Text | None = None
    dataset_version: Text | None = None


class ReviewQuery(Page):
    profile_id: int | None = Field(default=None, ge=1, le=2147483647)
    product_key: str | None = Field(default=None, max_length=4000)
    date_from: str | None = Field(default=None, max_length=10)
    date_to: str | None = Field(default=None, max_length=10)
    aspect: Aspect | None = None
    sentiment: Sentiment | None = None
    processing_status: Literal["pending", "success", "failed", "empty"] | None = None
    human_status: Literal["unreviewed", "confirmed", "corrected"] | None = None


class HumanReview(Input):
    annotation_id: int = Field(ge=1, le=2147483647, strict=True)
    expected_revision_id: int | None = Field(ge=1, le=2147483647, strict=True)
    action: Literal["confirmed", "corrected"]
    reviewer: str = Field(min_length=1, max_length=80, pattern=r"\S")
    note: str = Field(default="", max_length=2000)
    opinions: list[Opinion] | None = Field(default=None, max_length=30)


class AlertQuery(Page):
    status: Literal["pending", "confirmed", "ignored"] | None = None
    product_id: Text | None = None
    aspect: Aspect | None = None
    source: Text | None = None
    dataset_version: Text | None = None
    run_id: Text | None = None


class AlertAction(Input):
    status: Literal["confirmed", "ignored"]
    actor: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    note: str = Field(default="", max_length=2000)


class Rules(Input):
    name: str = "negative-rate-growth"
    min_samples: int = Field(default=20, strict=True)
    min_coverage: float = Field(default=0.8, strict=True)
    min_negative_count: int = Field(default=3, strict=True)
    min_increase_pp: float = Field(default=10.0, strict=True)
    cooldown_days: int = Field(default=30, strict=True)

    @model_validator(mode="after")
    def valid_rules(self):
        _rules(self.model_dump())
        return self


class ImportParameters(Input):
    reviews_path: str | None = Field(default=None, min_length=1, max_length=4000)
    meta_path: str | None = Field(default=None, min_length=1, max_length=4000)
    dataset_version: Text = DATASET_VERSION
    product_ids: list[Text] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def has_file(self):
        if self.reviews_path is None and self.meta_path is None:
            raise ValueError("至少指定 reviews_path 或 meta_path。")
        return self


class LabelParameters(Scope):
    limit: int = Field(default=20, ge=1, le=1000, strict=True)
    retry_failed: bool = Field(default=False, strict=True)


class AnalysisParameters(BaselineQuery):
    rules: Rules = Field(default_factory=Rules)


class ReplayParameters(Identity):
    month_from: str = Field(max_length=7)
    month_to: str = Field(max_length=7)
    baseline_months: int = Field(default=1, ge=1, le=120, strict=True)
    product_ids: list[Text] = Field(default_factory=list, max_length=100)
    source: Text | None = None
    dataset_version: Text | None = None
    rules: Rules = Field(default_factory=Rules)

    @model_validator(mode="after")
    def valid_months(self):
        start, end = _month(self.month_from, "month_from"), _month(self.month_to, "month_to")
        if not 1 <= (end.year - start.year) * 12 + end.month - start.month + 1 <= 1200:
            raise ValueError("回放月份必须有序，且最多 1200 个月。")
        _shift_month(start, -self.baseline_months)
        _shift_month(end, 1)
        review_scope(product_ids=self.product_ids, source=self.source, dataset_version=self.dataset_version)
        return self


class ImportTask(Input):
    kind: Literal["import"]
    parameters: ImportParameters


class LabelTask(Input):
    kind: Literal["label"]
    parameters: LabelParameters


class AnalysisTask(Input):
    kind: Literal["analysis"]
    parameters: AnalysisParameters


class ReplayTask(Input):
    kind: Literal["replay"]
    parameters: ReplayParameters


TaskInput = ImportTask | LabelTask | AnalysisTask | ReplayTask


class TaskQuery(Page):
    status: TaskStatus | None = None
    kind: TaskKind | None = None
