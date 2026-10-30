"""将评论业务函数适配为固定范围、只读且有结果引用的 Agent 工具。"""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from typing import Literal

from hello_agents.tools import Tool, ToolParameter, ToolResponse
from pydantic import Field, ValidationError

from data_analysis_agent.agents.tools import RecordingRegistry
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.core.validation import Input, Text
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS, Aspect
from data_analysis_agent.reviews.parameters import BaselineScope


class ProductSelection(Input):
    product_ids: list[Text] = Field(default_factory=list, max_length=100,
                                   description="当前范围内的完整商品 ID 数组，每项为一个完整 ID，不能拆成字符；空数组沿用全部已选商品")


class TrendInput(ProductSelection):
    period: Literal["day", "month"] = Field(default="month", description="按 UTC 日或月统计")


class WindowInput(ProductSelection):
    window: Literal["current", "baseline"] = "current"


class AspectTrendInput(TrendInput):
    aspect: Aspect | None = Field(default=None, description="省略时返回全部六维度；统计负面评论")


class RatingInput(WindowInput):
    group_by: Literal["product", "month", "all"] = "product"


class WindowComparisonInput(ProductSelection):
    date_from: str | None = Field(default=None, description="自定义当前开始日 YYYY-MM-DD；四个日期必须一起传入")
    date_to: str | None = None
    baseline_from: str | None = None
    baseline_to: str | None = None


class EvidenceInput(ProductSelection):
    aspect: Aspect | None = Field(default=None, description="观点维度；省略表示全部维度")
    sentiment: Literal["positive", "neutral", "negative", "all"] = "negative"
    window: Literal["current", "baseline"] = "current"
    page: int = Field(default=1, ge=1, le=1000000, strict=True)
    page_size: int = Field(default=3, ge=1, le=10, strict=True)


class ReviewToolContext:
    def __init__(self, connection, scope, run_dir: Path, max_tool_calls, progress=None,
                 sentiment_intent: str | None = None, query_plan=None):
        self.connection, self.scope, self.run_dir = connection, scope, run_dir
        self.max_tool_calls, self.progress = max_tool_calls, progress
        self.sentiment_intent = {"positive_aspects": "positive", "negative_aspects": "negative"}.get(
            sentiment_intent, sentiment_intent,
        )
        self.tool_calls, self.limit_reached = 0, False
        self.query_plan = query_plan
        self.cache_hits, self.query_executions, self.model_tool_attempts = 0, 0, 0
        self.evidence_queries, self.evidence_items = 0, 0
        self.last_cache_hit = False
        self.results, self.cache, self.trace = {}, {}, []
        filters, values = ["source=?", "dataset_version=?"], [scope["source"], scope["dataset_version"]]
        if scope["product_ids"]:
            filters.append("product_id IN (" + ",".join("?" for _ in scope["product_ids"]) + ")")
            values.extend(scope["product_ids"])
        self.products = [dict(row) for row in connection.execute(
            "SELECT product_id,title FROM products WHERE " + " AND ".join(filters)
            + " ORDER BY product_id LIMIT 101", values)]
        self.allowed_products = {item["product_id"] for item in self.products}
        if len(self.products) > 100:
            raise ReviewDataError("Agent 首版最多分析 100 款商品，请缩小商品范围。")
        if set(scope["product_ids"]) - self.allowed_products:
            raise ReviewDataError("部分已选商品不属于当前数据集，请重新选择。")

    def get_result(self, result_id):
        if result_id not in self.results:
            raise ValueError("结果引用不存在。")
        return self.results[result_id]

    def query_scope(self, product_ids=(), *, baseline=False):
        if set(product_ids) - self.allowed_products:
            raise ReviewDataError("工具只能查询本次已选范围内的商品。")
        scope = {key: value for key, value in self.scope.items() if key not in ("baseline_from", "baseline_to")}
        scope["product_ids"] = list(product_ids) or self.scope["product_ids"] or sorted(self.allowed_products)
        if baseline:
            scope.update(date_from=self.scope["baseline_from"], date_to=self.scope["baseline_to"])
        return scope

    def query(self, function, **parameters):
        # 统计业务查询的实际执行（含执行后报错），不是底层 SQL 语句数。
        self.query_executions += 1
        return function(self.connection, **parameters)

    def execute(self, name, parameters):
        self.last_cache_hit = False
        if self.query_plan:
            self.query_plan.check_call(name, parameters)
        normalized = dict(parameters)
        if "product_ids" in normalized:
            normalized["product_ids"] = sorted(set(normalized["product_ids"] or self.scope["product_ids"] or self.allowed_products))
        key = canonical_json([name, normalized])
        if key in self.cache:
            self.cache_hits += 1
            self.last_cache_hit = True
            return deepcopy(self.results[self.cache[key]])
        values = dict(parameters)
        negative_tools = {"get_trend", "get_aspect_trend", "compare_products",
                          "get_top_issues", "compare_time_windows"}
        if self.sentiment_intent == "positive" and name in negative_tools:
            raise ReviewDataError("当前问题要求正向维度统计，请调用 get_positive_aspects。")
        if self.sentiment_intent == "negative" and name == "get_positive_aspects":
            raise ReviewDataError("当前问题要求负向分析，请使用负面统计工具。")
        if name == "get_evidence" and self.sentiment_intent in {"positive", "negative"}:
            expected = self.sentiment_intent
            if values.get("sentiment", "negative") != expected:
                raise ReviewDataError(f"当前问题要求{('正向' if expected == 'positive' else '负向')}证据，sentiment 必须为 {expected}。")
        products = values.pop("product_ids", [])
        scope = self.query_scope(products, baseline=values.pop("window", "current") == "baseline")
        if name == "get_evidence" and self.query_plan:
            if self.evidence_queries >= self.query_plan.max_evidence_pages:
                raise ReviewDataError("本轮证据页数预算已用完；请明确追问下一页。")
            remaining = self.query_plan.max_evidence_items - self.evidence_items
            if remaining <= 0:
                raise ReviewDataError("本轮证据条数预算已用完。")
            values["page_size"] = min(values["page_size"], remaining)
        if name == "get_analysis_context":
            summary = self.query(analytics.get_trend, **scope)["summary"]
            kind, data = "context", {"scope": self.scope, "products": self.products, "summary": summary,
                                     "aspects": ASPECTS, "sentiments": SENTIMENTS}
        elif name in ("get_trend", "get_aspect_trend"):
            start, end = date.fromisoformat(scope["date_from"]), date.fromisoformat(scope["date_to"])
            buckets = (end - start).days + 1 if values["period"] == "day" else (end.year-start.year)*12 + end.month-start.month + 1
            if buckets > (366 if values["period"] == "day" else 240):
                raise ReviewDataError("Agent 趋势最多返回 366 天或 240 个月，请调整时间范围或统计周期。")
            kind, data = (("trend", self.query(analytics.get_trend, **scope, **values)) if name == "get_trend"
                          else ("aspect_trend", self.query(analytics.get_aspect_trend, **scope, **values)))
        elif name == "get_data_quality":
            kind, data = "quality", self.query(analytics.get_data_quality, **scope)
        elif name == "get_positive_aspects":
            kind, data = "positive_aspects", self.query(analytics.get_positive_aspects, **scope)
        elif name == "get_rating_distribution":
            kind, data = "ratings", self.query(analytics.get_rating_distribution, **scope, **values)
        elif name == "compare_time_windows":
            dates = {key: value for key, value in values.items() if value is not None}
            if dates and len(dates) != 4:
                raise ReviewDataError("自定义比较必须同时提供当前和基线的四个日期。")
            selected = BaselineScope.model_validate({**scope, "baseline_from": self.scope["baseline_from"],
                                                    "baseline_to": self.scope["baseline_to"], **dates}).model_dump()
            allowed = [(self.scope["date_from"], self.scope["date_to"]),
                       (self.scope["baseline_from"], self.scope["baseline_to"])]
            for start, end in [(selected["date_from"], selected["date_to"]),
                               (selected["baseline_from"], selected["baseline_to"])]:
                if not any(a <= start <= end <= b for a, b in allowed):
                    raise ReviewDataError("自定义窗口必须完整位于本次当前或基线范围内；扩大日期请先切换范围。")
            kind, data = "window_comparison", self.query(analytics.compare_time_windows, **selected)
        elif name == "compare_products":
            kind, data = "comparison", self.query(analytics.compare_products, **scope)
        elif name == "get_top_issues":
            kind, data = "issues", self.query(analytics.get_top_issues,
                **scope, baseline_from=self.scope["baseline_from"], baseline_to=self.scope["baseline_to"])
        elif name == "get_evidence":
            if values["sentiment"] == "all":
                values["sentiment"] = None
            kind, data = "evidence", self.query(analytics.get_evidence, **scope, **values)
            # 原文引用保留文件指纹和行号，不向模型提供服务器文件路径。
            for item in data["items"]:
                item.pop("file_path", None)
        else:
            raise ReviewDataError("不支持的评论分析工具。")
        if name == "get_evidence":
            self.evidence_queries += 1
            self.evidence_items += len(data["items"])
        result_id = "overview" if kind == "context" else "r" + str(len(self.results))
        result = {"result_id": result_id, "kind": kind, "parameters": parameters, "data": data}
        self.results[result_id], self.cache[key] = result, result_id
        return deepcopy(result)


class ReviewTool(Tool):
    def __init__(self, context, name, description, parameter_type):
        super().__init__(name, description)
        self.context, self.parameter_type = context, parameter_type

    def get_parameters(self):
        schema = self.parameter_type.model_json_schema()
        if "product_ids" in schema["properties"]:
            # 把绑定范围写入声明，帮助真实模型准确填写 ID；执行时仍独立校验权限。
            products = schema["properties"]["product_ids"]
            if self.context.allowed_products:
                products["items"]["enum"] = sorted(self.context.allowed_products)
            else:
                products["maxItems"] = 0
        return [ToolParameter(name=name, type=prop.get("type", "string"),
                              description=prop.get("description", name), required=name == "period" or name in schema.get("required", []),
                              json_schema=prop) for name, prop in schema.get("properties", {}).items()]

    def run(self, parameters):
        try:
            values = self.parameter_type.model_validate(parameters).model_dump()
            result = self.context.execute(self.name, values)
            completed = [key for key in self.context.results if key != 'overview']
            message = ('查询完成，不要重复此工具及参数。若本轮所需查询均完成，直接返回 '
                       + json.dumps({"status": "ok", "result_ids": completed})) if completed else '绑定上下文已就绪，请按本轮问题调用业务查询工具。'
            return ToolResponse.success(message, result)
        except ValidationError:
            return ToolResponse.error("INVALID_PARAM", "参数无效或超出已选范围，请依据工具声明和上下文修正。")
        except ReviewDataError as exc:
            return ToolResponse.error("INVALID_PARAM", str(exc))
        except Exception:
            return ToolResponse.error("QUERY_FAILED", "查询失败，请检查已有数据、标注版本和统计范围。")

    async def arun(self, parameters):
        # 同一任务的工具沿用当前线程的只读事务，保证 SQLite 连接和快照一致。
        return self.run(parameters)


class ReviewRegistry(RecordingRegistry):
    def execute_tool(self, name, input_text, *, automatic=False):
        if not automatic:
            self.context.model_tool_attempts += 1
        return super().execute_tool(name, input_text)

    async def aexecute_tool(self, name, input_text):
        self.context.model_tool_attempts += 1
        return await super().aexecute_tool(name, input_text)

    def _record(self, name, input_text, response, started):
        from time import perf_counter

        value = response.to_dict()
        self.context.trace.append({"event": "tool_call", "tool": name, "parameters": input_text,
                                   "status": value["status"], "result_id": (value.get("data") or {}).get("result_id"),
                                   "cache_hit": self.context.last_cache_hit if value["status"] == "success" else False,
                                   "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
        if self.context.progress:
            self.context.progress({"stage": "agent_tools", "completed": self.context.tool_calls,
                                   "total": None, "unit": "tools", "tool": name})
        return response


def create_review_registry(context):
    registry = ReviewRegistry(context)
    for name, description, parameters in (
        ("get_analysis_context", "查询绑定范围、商品 ID、标注覆盖率、维度及情感枚举。", Input),
        ("get_trend", "查询当前窗口按日/月的评论量、负面评论量、覆盖率和占比。", TrendInput),
        ("get_data_quality", "查询当前或基线窗口缺失正文、失败类型、待标注、覆盖率和导入来源指纹。", WindowInput),
        ("get_positive_aspects", "查询正向观点在六个业务维度的去重评论数和占比；正向问题必须优先使用此工具。", ProductSelection),
        ("get_aspect_trend", "按日/月查看包装、物流等六维度的负面评论数、占比及覆盖率。", AspectTrendInput),
        ("get_rating_distribution", "按商品/月/总体查询原始 1–5 星分布；可选择当前或基线窗口，不依赖标注。", RatingInput),
        ("compare_time_windows", "比较已选当前/基线窗口的计数、覆盖率、负面占比及六维度变化；可传四个日期缩小窗口，不能超出绑定范围。", WindowComparisonInput),
        ("compare_products", "比较已选商品在当前窗口的评论量、覆盖率、负面数量和占比。", ProductSelection),
        ("get_top_issues", "比较当前窗口与已选基线的六维度负面数量和占比变化。", ProductSelection),
        ("get_evidence", "分页查询带原文位置的评论证据，可筛选维度、情感、商品子集和当前/基线窗口。", EvidenceInput),
    ):
        if context.query_plan and name != "get_analysis_context" and name not in (
            context.query_plan.required_tools + context.query_plan.optional_tools
        ):
            continue
        registry.register_tool(ReviewTool(context, name, description, parameters))
    return registry
