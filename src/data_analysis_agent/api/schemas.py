"""基于共享任务契约的 HTTP 查询参数和请求封装。"""

from typing import Literal

from pydantic import Field, model_validator

from data_analysis_agent.core.validation import Input, Text
from data_analysis_agent.jobs.parameters import (
    AnalysisParameters, ImportParameters, LabelParameters, ReplayParameters, TaskKind, TaskStatus,
)
from data_analysis_agent.reviews.parameters import (
    AnalysisScope as AnalysisQuery, BaselineScope as BaselineQuery, Identity, Rules, Scope,
)
from data_analysis_agent.reviews.opinions import Aspect, Opinion, Sentiment
from data_analysis_agent.reviews.scope import model_identity



class Page(Input):
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=20, ge=1, le=100)


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


class TrendQuery(AnalysisQuery):
    period: Literal["day", "month"] = "month"


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
