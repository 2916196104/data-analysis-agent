"""共享观点结构和原文精确证据校验；不调用模型。"""

import json
import re
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from data_analysis_agent.core.serialization import strict_json_loads

Aspect = Literal["quality", "effectiveness", "price", "packaging", "logistics", "service"]
Sentiment = Literal["positive", "negative", "neutral", "unknown"]

ASPECTS = {"quality": "商品质量", "effectiveness": "使用效果", "price": "价格",
           "packaging": "包装", "logistics": "物流", "service": "客服"}
SENTIMENTS = {"positive": "正向", "negative": "负向", "neutral": "中性", "unknown": "未知"}


class Opinion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    aspect: Aspect
    sentiment: Sentiment
    quote: str = Field(min_length=1, max_length=2000)

    @field_validator("quote")
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError("原文片段不能为空白。")
        return value


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    opinions: list[Opinion] = Field(max_length=30)


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    review_id: int = Field(ge=1)
    opinions: list[Opinion] = Field(max_length=30)


class BatchExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[BatchItem] = Field(max_length=8)


class OpinionValidationError(ValueError):
    """模型响应没有可校验且符合结构的解释。"""


def validate_opinions(response_text, review_text):
    """解析观点 JSON，并返回原文中去重后的精确字符区间。"""
    if not isinstance(response_text, str) or len(response_text) > 100000:
        raise OpinionValidationError("模型输出不是受支持的 JSON 文本。")
    text = response_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = strict_json_loads(text)
        extraction = Extraction.model_validate(data)
    except (ValueError, ValidationError, RecursionError) as exc:
        raise OpinionValidationError("模型输出未通过观点格式校验。") from exc
    opinions, seen = [], set()
    for opinion in extraction.opinions:
        start = review_text.find(opinion.quote)
        if start < 0:
            raise OpinionValidationError("模型原文片段未在评论正文中找到。")
        end = start + len(opinion.quote)
        key = (opinion.aspect, opinion.sentiment, start, end)
        if key not in seen:
            opinions.append({**opinion.model_dump(), "quote_start": start, "quote_end": end})
            seen.add(key)
    return opinions


def validate_batch_opinions(response_text, reviews: Mapping[int, str]):
    """解析批量观点，并按 review_id 校验每条评论的原文证据。"""
    if not isinstance(response_text, str) or len(response_text) > 800000:
        raise OpinionValidationError("模型批量输出不是受支持的 JSON 文本。")
    text = response_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        extraction = BatchExtraction.model_validate(strict_json_loads(text))
    except (ValueError, ValidationError, RecursionError) as exc:
        raise OpinionValidationError("模型批量输出未通过观点格式校验。") from exc

    expected_ids = set(reviews)
    actual_ids = [item.review_id for item in extraction.items]
    if set(actual_ids) != expected_ids or len(actual_ids) != len(set(actual_ids)):
        raise OpinionValidationError("模型批量输出缺少、重复或新增了评论 ID。")

    validated = {}
    for item in extraction.items:
        item_json = json.dumps(
            {"opinions": [opinion.model_dump() for opinion in item.opinions]},
            ensure_ascii=False,
        )
        validated[item.review_id] = validate_opinions(item_json, reviews[item.review_id])
    return validated
