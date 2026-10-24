"""复用 HelloAgents 的单轮评论分析；仅查询业务库，结果由调用方持久化。"""

import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from data_analysis_agent.agents.review_prompts import PROMPT_VERSION, SYSTEM_PROMPT
from data_analysis_agent.agents.review_validation import result_summaries, validate_report
from data_analysis_agent.agents.runtime import (
    AgentExecutionError, FinalResponseFormatError, build_agent, execution_failure, create_llm, validate_final_result,
)
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import safe_json
from data_analysis_agent.reviews.parameters import BaselineScope
from data_analysis_agent.reviews.store import connect_review_database


def _rate(value):
    return "暂无占比" if value is None else f"{value * 100:.1f}%"


def grounded_answer(status, results):
    if status == "unsupported":
        return "当前问题超出所选范围或可用分析能力。请使用已选商品和时间，查询整体趋势、商品对比、六维度问题变化或原文证据；原因判断需要额外业务证据。"
    parts = []
    for result in results:
        data, reference = result["data"], f"[{result['result_id']}]"
        kind = result["kind"]
        if kind in ("context", "trend"):
            summary = data["summary"]
            parts.append(f"{reference} 当前窗口共 {summary['review_count']} 条评论，成功标注 {summary['successful_reviews']} 条，"
                         f"标注覆盖率 {_rate(summary['labeling_coverage'])}（{summary['successful_reviews']}/{summary['eligible_text_reviews']} 条有效正文）。")
            if summary["successful_reviews"]:
                parts.append(f"负面占比 {_rate(summary['negative_rate'])}（{summary['negative_review_count']}/{summary['successful_reviews']} 条成功标注）。")
            else:
                parts.append(f"暂无成功标注，待标注 {summary['unprocessed_reviews']} 条、失败 {summary['failed_reviews']} 条；"
                             "请检查标注版本或补齐数据后重新分析。没有有效正文时需补充带正文的评论。")
            if kind == "trend" and data["buckets"]:
                first, last = data["buckets"][0], data["buckets"][-1]
                parts.append(f"{reference} 按{'日' if data['period'] == 'day' else '月'}统计共 {len(data['buckets'])} 个时间桶。"
                             f"首桶 {first['bucket']}：评论 {first['review_count']} 条、负面 {first['negative_review_count']} 条；"
                             f"末桶 {last['bucket']}：评论 {last['review_count']} 条、负面 {last['negative_review_count']} 条。")
        elif kind == "comparison":
            products = data["products"]
            ready = sum(item["successful_reviews"] > 0 for item in products)
            limited = sum(0 < item["successful_reviews"] < 20 or (
                item["successful_reviews"] > 0 and item["labeling_coverage"] < 0.8) for item in products)
            missing = len(data["no_reviews_product_ids"])
            parts.append(f"{reference} 商品对比：共 {len(products) + missing} 款商品，{ready} 款可计算负面占比"
                         f"（其中 {limited} 款样本有限），{len(products) - ready} 款暂无成功标注，{missing} 款在窗口内无评论。"
                         "逐商品的计数、分母和覆盖率见对比表。")
        elif kind == "issues":
            parts.append(reference + " 六维度问题：" + "；".join(
                f"{item['aspect_label']}负面 {item['negative_review_count']} 条，占比 {_rate(item['negative_rate'])}，"
                + ("较基线无法计算" if item["negative_rate_change_pp"] is None
                   else f"较基线变化 {item['negative_rate_change_pp']:+.1f} 个百分点") for item in data["issues"]))
        else:
            parts.append(f"{reference} 找到 {data['total']} 条匹配证据，本页展示 {len(data['items'])} 条。")
            for item in data["items"]:
                quotes = "；".join(dict.fromkeys(opinion["quote"] for opinion in item["opinions"]))
                parts.append(f"{reference} 评论 #{item['review_id']}，商品 {item['product_id']}，"
                             f"{item['published_at'][:10]}：{quotes[:160]}{'…' if len(quotes) > 160 else ''}（完整引用见原文详情）")
    return "\n\n".join(parts)


def run_review_agent(db, question, *, scope, settings, progress=None):
    """模型仅选择只读工具及结果引用；所有数字和引文由代码整理。"""
    started = perf_counter()
    result = {"agent": True, "status": "error", "question": question, "snapshot_at": utc_now(),
              "agent_model_identity": {"model": settings.model, "base_url": settings.base_url,
                                       "prompt_version": PROMPT_VERSION}}
    context, agent, llm = None, None, None
    usage, model_responses = {}, 0
    progress_error = None

    def report(value):
        nonlocal progress_error
        if progress:
            try:
                progress(value)
            except Exception as exc:
                progress_error = exc
                raise
    try:
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise AgentExecutionError("问题必须为 1 至 4000 个字符的非空文本。")
        scope = BaselineScope.model_validate(scope).model_dump()
        if not scope["source"] or not scope["dataset_version"]:
            raise ReviewDataError("请选择明确的数据来源和数据集版本。")
        result["scope"] = scope
        from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry

        with TemporaryDirectory(prefix="review-agent-") as temporary, closing(
            connect_review_database(db, readonly=True)
        ) as connection, connection:
            connection.execute("BEGIN")
            context = ReviewToolContext(connection, scope, Path(temporary), settings.max_tool_calls, report)
            registry = create_review_registry(context)
            overview = registry.execute_tool("get_analysis_context", "{}")
            if "overview" not in context.results:
                raise ReviewDataError("无法读取当前评论范围，请检查标注表和数据集。")
            with create_llm(settings) as llm:
                agent = build_agent(settings, context, llm, system_prompt=SYSTEM_PROMPT,
                                     name="review_analyst", tool_registry=registry)

                async def record_step(event):
                    context.trace.append({"event": "model_response", "usage": event.data.get("usage"),
                                          "finish_reason": event.data.get("finish_reason")})
                    report({"stage": "agent_thinking", "completed": context.tool_calls,
                            "total": None, "unit": "tools"})

                prompt = json.dumps({"question": question, "bound_context": overview.to_dict()["data"]}, ensure_ascii=False)
                for attempt in range(2):
                    try:
                        text = agent.run(prompt, on_step=record_step)
                    finally:
                        if agent.last_run is not None:
                            model_responses += agent.last_run.model_calls
                            for key, value in (agent.last_run.usage or {}).items():
                                usage[key] = usage.get(key, 0) + value
                    if context.limit_reached:
                        raise AgentExecutionError("已达到本次工具调用上限，任务未确认完成。")
                    if agent.last_run.status != "completed":
                        raise AgentExecutionError("模型达到调用或输出上限，任务未确认完成。")
                    try:
                        status, ids = validate_final_result(text, context)
                        if status == "ok":
                            unresolved = set()
                            for event in context.trace:
                                if event["event"] == "tool_call":
                                    if event["status"] == "success":
                                        unresolved.discard(event["tool"])
                                    else:
                                        unresolved.add(event["tool"])
                            if unresolved or not set(ids) - {"overview"}:
                                raise AgentExecutionError("部分工具查询未完成，任务未确认完成。")
                            if set(context.results) - {"overview"} - set(ids):
                                raise AgentExecutionError("最终答复遗漏已完成的分析结果，任务未确认完成。")
                        break
                    except FinalResponseFormatError:
                        if attempt:
                            raise
                        prompt = "请只纠正最终 JSON 格式，不重复执行成功工具。只输出 status 和 result_ids，已有结果 ID：" + json.dumps(list(context.results))
                selected = [context.get_result(value) for value in ids]
                if status == "ok":
                    result["validation"] = validate_report(context.results, ids)
                    if result["validation"]["status"] != "passed":
                        raise AgentExecutionError("统计或引用校验未通过，请检查执行记录。")
                    result["answer_sections"] = [
                        {"result_id": item["result_id"], "kind": item["kind"], "text": grounded_answer(status, [item])}
                        for item in selected]
                result.update(status=status, result_ids=ids, answer=grounded_answer(status, selected))
            summaries = list(result_summaries(context.results.values()))
            warnings = ["负面占比以成功标注评论为分母；无成功标注时保留空值，可检查版本或准备数据后重查。零条证据不表示没有问题。",
                        "原文是示例证据，不能仅凭评论确认问题根因；不完整的首末时间桶不宜直接比较。"]
            if any(item["successful_reviews"] < 20 or (item["labeling_coverage"] or 0) < 0.8 for item in summaries):
                warnings.append("部分范围成功标注少于 20 条或覆盖率低于 80%，结论仅描述已有样本，请谨慎解释。")
            result["warnings"] = warnings
    except Exception as exc:
        if progress_error is not None:
            raise progress_error
        code, message = ("business_error", str(exc)) if isinstance(exc, ReviewDataError) else execution_failure(exc)
        result.update(status="error", error={"code": code, "message": message})
    finally:
        result.update(results=context.results if context else {}, trace=context.trace if context else [],
                      tool_calls=context.tool_calls if context else 0, model_calls=llm.budget.calls if llm else 0,
                      model_responses=model_responses, usage=usage or None,
                      elapsed_seconds=round(perf_counter() - started, 3))
    return json.loads(safe_json(result, settings.api_key))
