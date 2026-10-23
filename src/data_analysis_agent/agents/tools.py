"""绑定到应用选定数据集的四个窄范围 HelloAgents 工具。"""

from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from pathlib import Path
from time import perf_counter

from hello_agents.tools import Tool, ToolParameter, ToolRegistry, ToolResponse

from data_analysis_agent.sales.analysis import get_data_overview, summarize_sales_by_product
from data_analysis_agent.core.serialization import safe_json, write_json
from data_analysis_agent.sales.loader import REQUIRED_FIELDS, SalesRecord
from data_analysis_agent.sales.reporting import generate_sales_chart, write_markdown_report


class SalesToolContext:
    """运行期间的数据、已保存结果和固定输出路径；绝不使用模型提供的路径。"""

    def __init__(self, records: list[SalesRecord], source: dict, run_dir: Path,
                 max_tool_calls: int = 12, secret: str = ""):
        self.records = records
        self.source = source
        self.run_dir = run_dir
        self.max_tool_calls = max_tool_calls
        self.secret = secret
        self.results: dict[str, dict] = {}
        self.tool_calls = 0
        self.limit_reached = False

    def save_result(self, result_id: str, kind: str, data: dict,
                    source_result_ids: tuple[str, ...] = ()) -> dict:
        result = {
            "result_id": result_id, "kind": kind, "data": data,
            "source_sha256": self.source["sha256"],
            "source": deepcopy(self.source),
            "source_result_ids": list(source_result_ids),
        }
        write_json(self.run_dir / "results" / f"{result_id}.json", result, self.secret)
        self.results[result_id] = deepcopy(result)
        return deepcopy(result)

    def get_result(self, result_id: str, kind: str | None = None) -> dict:
        if result_id not in self.results:
            raise ValueError("结果引用不存在；请先调用对应统计工具。")
        result = self.results[result_id]
        if kind is not None and result["kind"] != kind:
            raise ValueError("结果引用类型不匹配。")
        return deepcopy(result)

    def inspect_dataset(self) -> dict:
        """返回字段名和计算后的概览，不暴露原始销售行。"""
        return self.save_result("overview", "overview", {
            "fields": list(REQUIRED_FIELDS),
            "overview": get_data_overview(self.records),
            "limitations": "仅包含日期、商品和销售额；没有成本字段，无法计算利润率。",
        })

    def summarize_sales(self) -> dict:
        """返回现有分析函数计算出的精确商品汇总。"""
        totals = summarize_sales_by_product(self.records)
        overview = get_data_overview(self.records)
        if sum((Decimal(value) for value in totals.values()), Decimal(0)) != Decimal(overview["total_sales"]):
            raise ValueError("商品汇总与总销售额校验不一致。")
        return self.save_result("sales_by_product", "summary", {"sales_by_product": totals})

    def create_chart(self, result_id: str) -> dict:
        """仅根据已保存的汇总结果创建 PNG。"""
        summary = self.get_result(result_id, "summary")
        chart = generate_sales_chart(
            summary["data"]["sales_by_product"], self.run_dir / "sales_by_product.png"
        )
        return self.save_result("chart", "chart", {"chart_path": chart}, (result_id,))

    def build_report(self, result_id: str, chart_id: str) -> dict:
        """根据匹配的已保存汇总和图表结果创建确定性报告。"""
        summary = self.get_result(result_id, "summary")
        chart = self.get_result(chart_id, "chart")
        if chart["source_result_ids"] != [result_id]:
            raise ValueError("图表与统计结果的来源不一致。")
        if not Path(chart["data"]["chart_path"]).is_file():
            raise ValueError("图表文件不存在；请重新生成图表。")
        if "overview" not in self.results:
            self.inspect_dataset()
        overview = self.get_result("overview", "overview")
        report = write_markdown_report(
            overview["data"]["overview"], summary["data"]["sales_by_product"],
            chart["data"]["chart_path"], self.run_dir / "report.md", self.source["name"],
            source=self.source,
        )
        with Path(report).open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## 计算依据\n\n"
                f"数据 SHA-256：`{self.source['sha256']}`\n\n"
                f"统计结果：`results/{result_id}.json`；图表结果：`results/{chart_id}.json`。\n"
            )
        return self.save_result("report", "report", {"report_path": report},
                                ("overview", result_id, chart_id))


class SalesTool(Tool):
    """小型框架适配器；业务函数仍可独立调用。"""

    def __init__(self, context: SalesToolContext, name: str, description: str,
                 parameter_names: tuple[str, ...] = ()):
        super().__init__(name, description)
        self.context = context
        self.parameter_names = parameter_names

    def get_parameters(self):
        return [ToolParameter(name=name, type="string", required=True,
                              description="先前工具返回的结果 ID；不能填写文件路径或计算数字")
                for name in self.parameter_names]

    def run(self, parameters):
        if (not isinstance(parameters, dict) or set(parameters) != set(self.parameter_names)
                or any(not isinstance(value, str) or not 0 < len(value) <= 80
                       for value in parameters.values())):
            return ToolResponse.error("INVALID_PARAM", "参数必须严格匹配工具声明，结果 ID 必须是非空字符串。")
        try:
            data = getattr(self.context, self.name)(**parameters)
            return ToolResponse.success("操作完成；请使用返回的 result_id。", data=data)
        except ValueError as exc:
            return ToolResponse.error("INVALID_PARAM", str(exc))
        except Exception:
            return ToolResponse.error("EXECUTION_ERROR", "工具执行失败，请检查数据或输出目录。")


class RecordingRegistry(ToolRegistry):
    """记录实际调度（包括无效工具），并强制执行工具调用上限。"""

    def __init__(self, context: SalesToolContext):
        super().__init__()
        self.context = context

    def _check_limit(self):
        self.context.tool_calls += 1
        if self.context.tool_calls > self.context.max_tool_calls:
            self.context.limit_reached = True
            return ToolResponse.error("TOOL_LIMIT", "已达到本次分析的工具调用上限。")
        return None

    def _record(self, name, input_text, response, started):
        event = {
            "event": "tool_call", "sequence": self.context.tool_calls,
            "tool": name, "parameters": input_text,
            "response": response.to_dict(),
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        }
        with (self.context.run_dir / "trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(safe_json(event, self.context.secret, indent=None) + "\n")
        return response

    def execute_tool(self, name, input_text):
        started = perf_counter()
        response = self._check_limit()
        if response is None:
            response = super().execute_tool(name, input_text)
        return self._record(name, input_text, response, started)

    async def aexecute_tool(self, name, input_text):
        # SimpleAgent.run 也会通过异步注册表 API 调度。
        started = perf_counter()
        response = self._check_limit()
        if response is None:
            response = await super().aexecute_tool(name, input_text)
        return self._record(name, input_text, response, started)


def create_tool_registry(context: SalesToolContext) -> RecordingRegistry:
    """只注册四个销售工具；不提供通用文件、Shell 或代码执行工具。"""
    registry = RecordingRegistry(context)
    definitions = [
        ("inspect_dataset", "检查当前数据字段和概览，计算总销售额、记录数等；识别数据限制。", ()),
        ("summarize_sales", "调用统计函数按商品汇总销售额，返回结果 ID 和精确金额。", ()),
        ("create_chart", "根据已有商品汇总结果 ID 生成柱状图，返回图表结果 ID。", ("result_id",)),
        ("build_report", "根据商品汇总和匹配图表的结果 ID 生成有计算依据的 Markdown 报告。",
         ("result_id", "chart_id")),
    ]
    with redirect_stdout(StringIO()):
        for name, description, parameters in definitions:
            registry.register_tool(SalesTool(context, name, description, parameters))
    return registry
