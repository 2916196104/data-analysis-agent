"""HTTP endpoints for analytics; business operations stay in their services."""

from typing import Annotated

from fastapi import APIRouter, Query

from data_analysis_agent.api.responses import Result, ok_result
from data_analysis_agent.api.schemas import AnalysisQuery, EvidenceQuery, IssueQuery, TrendQuery
from data_analysis_agent.reviews.analytics import compare_products, get_evidence, get_top_issues, get_trend


def create_router(db):
    """Build routes bound to this application, without global database state."""
    router = APIRouter()

    @router.get("/analytics/trend", response_model=Result, tags=["统计分析"])
    def trend(query: Annotated[TrendQuery, Query()]):
        return ok_result(get_trend(db, **query.model_dump()))

    @router.get("/analytics/top-issues", response_model=Result, tags=["统计分析"])
    def issues(query: Annotated[IssueQuery, Query()]):
        return ok_result(get_top_issues(db, **query.model_dump()))

    @router.get("/analytics/compare-products", response_model=Result, tags=["统计分析"])
    def comparison(query: Annotated[AnalysisQuery, Query()]):
        return ok_result(compare_products(db, **query.model_dump()))

    @router.get("/analytics/evidence", response_model=Result, tags=["统计分析"])
    def evidence(query: Annotated[EvidenceQuery, Query()]):
        values = query.model_dump()
        if values["sentiment"] == "all":
            values["sentiment"] = None
        return ok_result(get_evidence(db, **values))

    return router
