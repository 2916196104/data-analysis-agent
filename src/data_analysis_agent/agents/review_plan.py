"""单轮只读查询计划：能力类型与情感分开，预算由应用执行。"""

from dataclasses import asdict, dataclass, field
import json
import re

from data_analysis_agent.agents.review_prompts import detect_sentiment_intent
from data_analysis_agent.core.errors import ReviewDataError


ASPECT_WORDS = {"质量": "quality", "效果": "effectiveness", "价格": "price", "包装": "packaging",
                "物流": "logistics", "客服": "service", "服务": "service"}
TOOL_KINDS = {"get_positive_aspects": "positive_aspects", "get_top_issues": "issues",
              "get_trend": "trend", "get_aspect_trend": "aspect_trend", "get_evidence": "evidence",
              "get_data_quality": "quality", "get_rating_distribution": "ratings",
              "compare_products": "comparison", "compare_time_windows": "window_comparison"}


@dataclass
class ReviewQueryPlan:
    analysis_type: str
    sentiment: str | None
    scope: dict
    requirements: list[dict] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)
    requires_evidence: bool = False
    max_evidence_pages: int = 0
    max_evidence_items: int = 0
    evidence_start_page: int = 1
    evidence_page_size: int = 3
    unsupported_reason: str | None = None

    @property
    def required_tools(self):
        return list(dict.fromkeys(item["tool"] for item in self.requirements))

    def to_dict(self):
        return {**asdict(self), "required_tools": self.required_tools}

    def check_call(self, name, parameters):
        if name == "get_analysis_context":
            return
        if self.unsupported_reason:
            raise ReviewDataError(self.unsupported_reason)
        candidates = [item for item in self.requirements if item["tool"] == name]
        if name not in self.optional_tools and not any(
            all(parameters.get(k) == v for k, v in item["parameters"].items()) for item in candidates
        ):
            raise ReviewDataError("工具或参数不符合本轮查询计划，请使用计划中的工具和筛选条件。")

    def satisfied(self, results):
        if self.unsupported_reason or not self.requirements:
            return False
        for request in self.requirements:
            if not any(item["kind"] == TOOL_KINDS[request["tool"]] and all(
                item["parameters"].get(k) == v for k, v in request["parameters"].items()
            ) and (request["tool"] not in ("get_trend", "get_aspect_trend") or (
                isinstance(item["data"].get("buckets"), list)
                and item["data"].get("period") == item["parameters"].get("period")
            )) for item in results):
                return False
        return True


def build_query_plan(question, scope):
    sentiment = detect_sentiment_intent(question)
    plan = ReviewQueryPlan("analysis", sentiment, scope)
    requests = []
    def add(tool, **parameters):
        requests.append({"tool": tool, "parameters": parameters})

    # 现有前端已确认的追问继续通过 question 传输，不新增任务必填字段。
    explicit = None
    if question.startswith("执行本轮已确认的只读查询"):
        try:
            explicit = json.loads(question.split("\n", 1)[1])
            if explicit["tool"] not in TOOL_KINDS or not isinstance(explicit["parameters"], dict):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            plan.unsupported_reason = "追问参数无效，请重新选择查询目标。"
            return plan
    if explicit:
        tool, params = explicit["tool"], explicit["parameters"]
        add(tool, **params)
        plan.sentiment = params.get("sentiment") if tool == "get_evidence" else (
            "positive" if tool == "get_positive_aspects" else sentiment)
        if tool == "get_evidence":
            plan.requires_evidence = True
            plan.evidence_start_page = params.get("page", 1)
            plan.evidence_page_size = params.get("page_size", 3)
            plan.max_evidence_pages = 1
            plan.max_evidence_items = plan.evidence_page_size
    else:
        evidence = bool(re.search(r"原文|证据|[条则].*(?:差评|好评)|(?:看|给我).*\d+条", question))
        trend = bool(re.search(r"趋势|变化|按[日月天]|按月份", question))
        ranking = bool(re.search(r"主要.*(?:方面|维度|集中|问题)|集中|排名|哪些方面|优点|缺点|最好|六.*维度", question))
        quality = bool(re.search(r"覆盖率|数据质量|失败标注|缺失正文|待标注", question))
        rating = bool(re.search(r"星级|评分.*分布", question))
        windows = bool(re.search(r"基线|窗口.*(?:比较|对比)|(?:比较|对比).*窗口", question))
        products = bool(re.search(r"商品对比|对比.*商品|比较.*商品", question))
        aspects = list(dict.fromkeys(value for word, value in ASPECT_WORDS.items() if word in question))
        if sentiment == "positive" and (trend or windows or products) and not rating:
            plan.unsupported_reason = "当前支持正向维度汇总和正向原文，尚不支持正向趋势或正向对比；请改问好评主要方面。"
        elif evidence and re.search(r"制冷|静音|安装", question):
            plan.unsupported_reason = "现有六维标注不能单独筛选制冷、静音或安装原文；使用效果不能替代这些细分主题。请改用六维度或明确核对原文。"
        elif sentiment is None and re.search(r"好评|差评|优点|缺点|满意|喜欢|值得购买", question):
            plan.unsupported_reason = "本轮情感方向或混合分析目标尚不明确，请分别说明正向或负向查询。"
        if quality:
            add("get_data_quality")
        if rating:
            add("get_rating_distribution")
        if windows:
            add("compare_time_windows")
        if products and not rating:
            add("compare_products")
        if trend and (not windows or re.search(r"趋势|按[日月天]", question)) and not (rating and not aspects):
            period = "day" if re.search(r"按日|按天", question) else "month"
            trend_text = "".join(part for part in re.split(r"[，。；、]", question) if re.search(r"趋势|变化|按[日月天]", part))
            trend_aspects = list(dict.fromkeys(value for word, value in ASPECT_WORDS.items() if word in trend_text))
            if trend_aspects:
                for aspect in trend_aspects:
                    add("get_aspect_trend", aspect=aspect, period=period)
            else:
                add("get_trend", period=period)
        if ranking or (sentiment and not (evidence or trend or rating or windows or products or quality)):
            add("get_positive_aspects" if sentiment == "positive" else "get_top_issues")
        if evidence:
            count = re.search(r"(\d+)\s*条", question)
            requested = int(count.group(1)) if count else 3
            if not 1 <= requested <= 10:
                plan.unsupported_reason = "每轮证据数量支持 1 至 10 条；需要更多时请显式追问下一页。"
            count = min(10, max(1, requested))
            page_match = re.search(r"第\s*(\d+)\s*页", question)
            start_page = int(page_match.group(1)) if page_match else 1
            size = min(10, count)
            plan.requires_evidence, plan.max_evidence_items = True, count
            plan.max_evidence_pages = 1
            plan.evidence_start_page, plan.evidence_page_size = start_page, size
            for offset in range(plan.max_evidence_pages):
                add("get_evidence", sentiment=sentiment or "negative", aspect=aspects[0] if len(aspects) == 1 else None,
                    page=start_page + offset, page_size=size, window="baseline" if "基线" in question else "current")
        if not requests and not plan.unsupported_reason:
            plan.unsupported_reason = "无法确定本轮需要的分析能力，请明确查询趋势、主要维度、数据质量、星级、对比或原文。"
    plan.requirements = requests
    kinds = list(dict.fromkeys(TOOL_KINDS[item["tool"]] for item in requests))
    plan.analysis_type = kinds[0] if len(kinds) == 1 else "composite"
    return plan
