"""Task payload contracts independent of HTTP queries and request envelopes."""

from typing import Literal

from pydantic import Field, model_validator

from data_analysis_agent.core.validation import Input, Text
from data_analysis_agent.reviews.loader import DATASET_VERSION
from data_analysis_agent.reviews.parameters import BaselineScope, Identity, Rules, Scope, parse_month, shift_month
from data_analysis_agent.reviews.scope import review_scope

TaskStatus = Literal["queued", "running", "succeeded", "partial", "failed"]
TaskKind = Literal["import", "label", "analysis", "replay"]


class ImportParameters(Input):
    reviews_path: str | None = Field(default=None, min_length=1, max_length=4000)
    meta_path: str | None = Field(default=None, min_length=1, max_length=4000)
    dataset_version: Text = DATASET_VERSION
    product_ids: list[Text] = Field(default_factory=list, max_length=100)
    max_reviews: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def has_file(self):
        if self.reviews_path is None and self.meta_path is None:
            raise ValueError("至少指定 reviews_path 或 meta_path。")
        if self.max_reviews is not None and self.reviews_path is None:
            raise ValueError("max_reviews 需要同时指定 reviews_path。")
        return self


class LabelParameters(Scope):
    limit: int = Field(default=20, ge=1, le=1000, strict=True)
    workers: int = Field(default=1, ge=1, le=4, strict=True)
    retry_failed: bool = Field(default=False, strict=True)


class AnalysisParameters(BaselineScope):
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
        start, end = parse_month(self.month_from, "month_from"), parse_month(self.month_to, "month_to")
        if not 1 <= (end.year - start.year) * 12 + end.month - start.month + 1 <= 1200:
            raise ValueError("回放月份必须有序，且最多 1200 个月。")
        shift_month(start, -self.baseline_months)
        shift_month(end, 1)
        review_scope(product_ids=self.product_ids, source=self.source, dataset_version=self.dataset_version)
        return self

PARAMETERS = {"import": ImportParameters, "label": LabelParameters,
              "analysis": AnalysisParameters, "replay": ReplayParameters}
