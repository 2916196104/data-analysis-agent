"""Shared opinion schemas and exact-source evidence validation; no model calls."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from data_analysis_agent.core.serialization import strict_json_loads

Aspect = Literal["quality", "effectiveness", "price", "packaging", "logistics", "service"]
Sentiment = Literal["positive", "negative", "neutral", "unknown"]

ASPECTS = {"quality": "商品质量", "effectiveness": "使用效果", "price": "价格",
           "packaging": "包装", "logistics": "物流", "service": "客服"}

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

class OpinionValidationError(ValueError):
    """A model response has no verifiable, schema-conforming interpretation."""

def validate_opinions(response_text, review_text):
    """Parse opinion JSON and return deduplicated, exact character spans in the source."""
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
