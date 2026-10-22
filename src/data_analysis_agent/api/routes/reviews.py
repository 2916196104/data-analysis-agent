"""HTTP endpoints for reviews; business operations stay in their services."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from data_analysis_agent.api.responses import Result, ok_result
from data_analysis_agent.api.schemas import HumanReview, OpinionQuery, ProductQuery, ReviewQuery, StatusQuery
from data_analysis_agent.reviews.catalog import list_products
from data_analysis_agent.reviews.label_store import labeling_status, opinion_evidence, query_review_opinions
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.store import get_quality_summary
from data_analysis_agent.reviews.human_review import (
    ReviewNotFound, list_reviews, review_detail, save_human_review, workbench_options,
)


def create_router(db):
    """Build routes bound to this application, without global database state."""
    router = APIRouter()

    @router.get("/quality", response_model=Result, tags=["商品与评论"])
    def quality():
        return ok_result(get_quality_summary(db))

    @router.get("/products", response_model=Result, tags=["商品与评论"])
    def products(query: Annotated[ProductQuery, Query()]):
        return ok_result(list_products(db, **query.model_dump()))

    @router.get("/options", response_model=Result, tags=["商品与评论"])
    def options(profile_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        return ok_result(workbench_options(db, profile_id))

    @router.get("/reviews", response_model=Result, tags=["商品与评论"])
    def reviews(query: Annotated[ReviewQuery, Query()]):
        return ok_result(list_reviews(db, **query.model_dump()))

    @router.get("/reviews/{review_id}", response_model=Result, tags=["商品与评论"])
    def review(review_id: Annotated[int, Path(ge=1, le=2147483647)],
               profile_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        return ok_result(review_detail(db, review_id, profile_id))

    @router.post("/reviews/{review_id}/human-reviews", response_model=Result, status_code=201, tags=["人工复核"])
    def human_review(review_id: Annotated[int, Path(ge=1, le=2147483647)], body: HumanReview):
        return ok_result(save_human_review(db, review_id, **body.model_dump()))

    @router.get("/labeling/status", response_model=Result, tags=["标注"])
    def label_status(query: Annotated[StatusQuery, Query()]):
        return ok_result(labeling_status(db, **query.model_dump()))

    @router.get("/labeling/opinions", response_model=Result, tags=["标注"])
    def opinions(query: Annotated[OpinionQuery, Query()]):
        return ok_result(query_review_opinions(db, **query.model_dump()))

    @router.get("/labeling/evidence/{review_id}", response_model=Result, tags=["标注"])
    def labeling_evidence(review_id: Annotated[int, Path(ge=1, le=2147483647)]):
        try:
            return ok_result(opinion_evidence(db, review_id))
        except ReviewDataError as exc:
            if str(exc) == "评论 ID 不存在。":
                raise ReviewNotFound(str(exc)) from exc
            raise

    return router
