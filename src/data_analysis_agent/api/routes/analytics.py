"""分析 HTTP 端点；业务操作仍位于各自服务中。"""

from typing import Annotated

from fastapi import APIRouter, Query

from data_analysis_agent.api.responses import Result, ok_result
from data_analysis_agent.api.schemas import (
    AnalysisQuery, AspectTrendQuery, BaselineQuery, EvidenceQuery, IssueQuery, RatingQuery, TrendQuery,
)
from data_analysis_agent.reviews.analytics import (
    compare_products, compare_time_windows, get_aspect_trend, get_data_quality,
    get_evidence, get_rating_distribution, get_top_issues, get_trend,
)


def create_router(db):
    """构建绑定到当前应用的路由，不使用全局数据库状态。"""
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

    @router.get("/analytics/data-quality", response_model=Result, tags=["统计分析"])
    def quality(query: Annotated[AnalysisQuery, Query()]):
        return ok_result(get_data_quality(db, **query.model_dump()))

    @router.get("/analytics/aspect-trend", response_model=Result, tags=["统计分析"])
    def aspect_trend(query: Annotated[AspectTrendQuery, Query()]):
        return ok_result(get_aspect_trend(db, **query.model_dump()))

    @router.get("/analytics/rating-distribution", response_model=Result, tags=["统计分析"])
    def ratings(query: Annotated[RatingQuery, Query()]):
        return ok_result(get_rating_distribution(db, **query.model_dump()))

    @router.get("/analytics/compare-windows", response_model=Result, tags=["统计分析"])
    def windows(query: Annotated[BaselineQuery, Query()]):
        return ok_result(compare_time_windows(db, **query.model_dump()))

    return router
