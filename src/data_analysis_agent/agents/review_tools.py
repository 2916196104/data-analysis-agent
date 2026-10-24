"""将评论业务函数适配为固定范围、只读且有结果引用的 Agent 工具。"""

from copy import deepcopy
from datetime import date
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


class ProductSelection(Input):
    product_ids: list[Text] = Field(default_factory=list, max_length=100,
                                   description="当前范围内的商品 ID 子集；空数组表示沿用全部已选商品")


class TrendInput(ProductSelection):
    period: Literal["day", "month"] = Field(default="month", description="按 UTC 日或月统计")


class EvidenceInput(ProductSelection):
    aspect: Aspect | None = Field(default=None, description="观点维度；省略表示全部维度")
    sentiment: Literal["positive", "neutral", "negative", "all"] = "negative"
    window: Literal["current", "baseline"] = "current"
    page: int = Field(default=1, ge=1, le=1000000, strict=True)
    page_size: int = Field(default=5, ge=1, le=10, strict=True)


class ReviewToolContext:
    def __init__(self, connection, scope, run_dir: Path, max_tool_calls, progress=None):
        self.connection, self.scope, self.run_dir = connection, scope, run_dir
        self.max_tool_calls, self.progress = max_tool_calls, progress
        self.tool_calls, self.limit_reached = 0, False
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

    def execute(self, name, parameters):
        key = canonical_json([name, parameters])
        if key in self.cache:
            return deepcopy(self.results[self.cache[key]])
        values = dict(parameters)
        products = values.pop("product_ids", [])
        scope = self.query_scope(products, baseline=values.pop("window", "current") == "baseline")
        if name == "get_analysis_context":
            summary = analytics.get_trend(self.connection, **scope)["summary"]
            kind, data = "context", {"scope": self.scope, "products": self.products, "summary": summary,
                                     "aspects": ASPECTS, "sentiments": SENTIMENTS}
        elif name == "get_trend":
            start, end = date.fromisoformat(scope["date_from"]), date.fromisoformat(scope["date_to"])
            buckets = (end - start).days + 1 if values["period"] == "day" else (end.year-start.year)*12 + end.month-start.month + 1
            if buckets > (366 if values["period"] == "day" else 240):
                raise ReviewDataError("Agent 趋势最多返回 366 天或 240 个月，请调整时间范围或统计周期。")
            kind, data = "trend", analytics.get_trend(self.connection, **scope, **values)
        elif name == "compare_products":
            kind, data = "comparison", analytics.compare_products(self.connection, **scope)
        elif name == "get_top_issues":
            kind, data = "issues", analytics.get_top_issues(
                self.connection, **scope, baseline_from=self.scope["baseline_from"], baseline_to=self.scope["baseline_to"])
        else:
            if values["sentiment"] == "all":
                values["sentiment"] = None
            kind, data = "evidence", analytics.get_evidence(self.connection, **scope, **values)
            # 原文引用保留文件指纹和行号，不向模型提供服务器文件路径。
            for item in data["items"]:
                item.pop("file_path", None)
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
        return [ToolParameter(name=name, type=prop.get("type", "string"),
                              description=prop.get("description", name), required=name in schema.get("required", []),
                              json_schema=prop) for name, prop in schema.get("properties", {}).items()]

    def run(self, parameters):
        try:
            values = self.parameter_type.model_validate(parameters).model_dump()
            return ToolResponse.success("只读查询完成，请引用 result_id。", self.context.execute(self.name, values))
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
    def _record(self, name, input_text, response, started):
        from time import perf_counter

        value = response.to_dict()
        self.context.trace.append({"event": "tool_call", "tool": name, "parameters": input_text,
                                   "status": value["status"], "result_id": (value.get("data") or {}).get("result_id"),
                                   "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
        if self.context.progress:
            self.context.progress({"stage": "agent_tools", "completed": self.context.tool_calls,
                                   "total": None, "unit": "tools", "tool": name})
        return response


def create_review_registry(context):
    registry = ReviewRegistry(context)
    for name, description, parameters in (
        ("get_analysis_context", "查询绑定范围、商品 ID、标注覆盖率、维度及情感枚举。", Input),
        ("get_trend", "查询当前窗口按日/月的评论量、负面评论量、覆盖率和占比；不支持单维度趋势。", TrendInput),
        ("compare_products", "比较已选商品在当前窗口的评论量、覆盖率、负面数量和占比。", ProductSelection),
        ("get_top_issues", "比较当前窗口与已选基线的六维度负面数量和占比变化。", ProductSelection),
        ("get_evidence", "分页查询带原文位置的评论证据，可筛选维度、情感、商品子集和当前/基线窗口。", EvidenceInput),
    ):
        registry.register_tool(ReviewTool(context, name, description, parameters))
    return registry
