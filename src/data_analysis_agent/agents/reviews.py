"""复用 HelloAgents 的单轮评论分析；仅查询业务库，结果由调用方持久化。"""

import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from data_analysis_agent.agents.review_prompts import PROMPT_VERSION, SYSTEM_PROMPT, detect_sentiment_intent
from data_analysis_agent.agents.review_validation import result_summaries, validate_report
from data_analysis_agent.agents.review_semantics import QuestionScopeError
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_compression import build_analysis_artifacts, empty_public
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator, unavailable_answer
from data_analysis_agent.agents.runtime import (
    AgentExecutionError, FinalResponseFormatError, build_agent, execution_failure, create_llm, validate_final_result,
)
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import safe_json
from data_analysis_agent.reviews.parameters import BaselineScope
from data_analysis_agent.reviews.store import connect_review_database


def _rate(value):
    return "暂无占比" if value is None else f"{value * 100:.1f}%"


def grounded_answer(status, results):
    if status == "unsupported":
        return "当前问题超出所选范围或可用分析能力。可查询数据质量、整体或维度趋势、星级分布、商品或时间窗口对比及原文证据；扩大日期请先切换范围，原因判断需要额外业务证据。"
    parts = []
    if any(item["kind"] == "positive_aspects" for item in results):
        results = [item for item in results if item["kind"] != "context"]
    for result in results:
        data, reference = result["data"], f"[{result['result_id']}]"
        kind = result["kind"]
        if kind in ("context", "trend", "quality", "aspect_trend"):
            summary = data["summary"]
            parts.append(f"{reference} 查询窗口共 {summary['review_count']} 条评论，成功标注 {summary['successful_reviews']} 条，"
                         f"标注覆盖率 {_rate(summary['labeling_coverage'])}（{summary['successful_reviews']}/{summary['eligible_text_reviews']} 条有效正文）。")
            if summary["successful_reviews"]:
                parts.append(f"负面占比 {_rate(summary['negative_rate'])}（{summary['negative_review_count']}/{summary['successful_reviews']} 条成功标注）。")
            else:
                parts.append(f"暂无成功标注，待标注 {summary['unprocessed_reviews']} 条、失败 {summary['failed_reviews']} 条；"
                             "请检查标注版本或补齐数据后重新分析。没有有效正文时需补充带正文的评论。")
            if kind == "quality":
                parts.append(f"{reference} 缺失正文 {summary['empty_text_reviews']} 条，占全部评论 {_rate(data['missing_text_rate'])}；"
                             f"失败标注 {summary['failed_reviews']} 条，占有效正文 {_rate(data['failed_rate'])}；待标注 {summary['unprocessed_reviews']} 条。")
            if kind == "aspect_trend":
                parts.append(f"{reference} 已按{'日' if data['period'] == 'day' else '月'}统计 "
                             + "、".join(data["aspects"].values()) + f"，共 {len(data['buckets'])} 个时间桶；"
                             "各维度负面占比均以该时间桶成功标注评论为分母，详见维度趋势表。")
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
        elif kind in ("issues", "window_comparison"):
            if kind == "window_comparison":
                current, baseline = data["current"], data["baseline"]
                parts.append(f"{reference} 当前 {data['scope']['date_from']}—{data['scope']['date_to']}（{data['current_days']} 天）"
                             f"共 {current['review_count']} 条评论；基线 {data['baseline_scope']['date_from']}—{data['baseline_scope']['date_to']}"
                             f"（{data['baseline_days']} 天）共 {baseline['review_count']} 条评论。"
                             "窗口长度不同时，不宜仅按评论数量判断问题变化。")
            parts.append(reference + " 六维度问题：" + "；".join(
                f"{item['aspect_label']}负面 {item['negative_review_count']} 条，占比 {_rate(item['negative_rate'])}，"
                + ("未进行基线对比" if not data.get('baseline') else "较基线无法计算" if item["negative_rate_change_pp"] is None
                   else f"较基线变化 {item['negative_rate_change_pp']:+.1f} 个百分点") for item in data["issues"]))
        elif kind == "ratings":
            parts.append(f"{reference} 原始星级分布，按 " + {"product": "商品", "month": "月份", "all": "总体"}[data["group_by"]]
                         + f"汇总 {len(data['groups'])} 组。占比分母为有效 1–5 整数星级评论，独立于标注覆盖率。")
            for row in data["groups"]:
                parts.append(f"{reference} {row['group']}：有效星级 {row['rated_reviews']} 条，缺失 {row['missing_rating_reviews']} 条，"
                             f"其他评分 {row['other_rating_reviews']} 条；" + "；".join(
                                 f"{star['star']} 星 {star['review_count']} 条（{_rate(star['rate'])}）" for star in row["stars"]))
        elif kind == "positive_aspects":
            summary = data["summary"]
            if data["data_status"] == "insufficient_data":
                parts.append(f"{reference} 正向维度分析数据不足：范围共 {summary['review_count']} 条评论，成功标注 0 条，无法计算正向观点占比。")
            elif data["data_status"] == "no_positive_mentions":
                parts.append(f"{reference} 共 {summary['successful_reviews']} 条成功标注评论，但没有 sentiment=positive 的有效观点，正向维度占比均为暂无数据。")
            else:
                ranked = [item for item in data["aspects"] if item["positive_review_count"] > 0]
                details = "；".join(
                    f"{item['aspect_label']} {item['positive_review_count']} 条"
                    f"（{_rate(item['positive_rate'])}，分母为正向观点评论 {item['positive_rate_denominator']} 条；"
                    f"占成功标注 {_rate(item['positive_success_rate'])}）" for item in ranked[:3])
                parts.append(f"{reference} 正向维度统计：范围共 {summary['review_count']} 条评论，成功标注 {summary['successful_reviews']} 条，"
                             f"至少包含一个正向观点的评论 {summary['positive_review_count']} 条；主要方面：{details}。"
                             "各维度按评论 ID 去重，同一评论可计入多个维度。")
        elif kind == 'opinion_breakdown':
            parts.append(f"{reference} 匹配 {data['total']} 条评论、{data['total_opinions']} 条观点；按观点原文和情感精确归组，非标准细分标签。")
            for row in data['groups'][:3]:
                parts.append(f"{reference} “{row['quote']}”：{row['review_count']} 条评论、{row['opinion_count']} 条观点，{row['sentiment']}；评论 #{row['review_id']}。")
        elif kind == 'aspect_drilldown':
            parts.append(f"{reference} 二级主题{data['analysis_status']}，纳入 {data['coverage']['selected_reviews']} 条评论、"
                         f"{data['coverage']['selected_opinions']} 条观点；模型候选主题未经人工确认。")
        elif kind == "evidence":
            parts.append(f"{reference} 找到 {data['total']} 条匹配证据，本页展示 {len(data['items'])} 条。")
            for item in data["items"]:
                quotes = "；".join(dict.fromkeys(opinion["quote"] for opinion in item["opinions"]))
                parts.append(f"{reference} 评论 #{item['review_id']}，商品 {item['product_id']}，"
                             f"{item['published_at'][:10]}：{quotes[:160]}{'…' if len(quotes) > 160 else ''}（完整引用见原文详情）")
    return "\n\n".join(parts)


class UnsupportedReviewQuery(ValueError):
    """明确不支持的任务在模型调用前结束，不发布替代方向的统计。"""


def run_review_agent(db, question, *, scope, settings, progress=None, conversation_context=None):
    """模型仅选择只读工具及结果引用；所有数字和引文由代码整理。"""
    started = perf_counter()
    timings = {key: 0.0 for key in (
        "planning_duration_ms", "query_duration_ms", "validation_duration_ms",
        "compression_duration_ms", "answer_duration_ms", "total_duration_ms",
    )}
    sentiment_intent = detect_sentiment_intent(question)
    analysis_intent = {"positive": "positive_aspects", "negative": "negative_aspects"}.get(sentiment_intent)
    result = {"agent": True, "status": "error", "question": question, "snapshot_at": utc_now(),
              "sentiment_intent": sentiment_intent, "analysis_intent": analysis_intent,
              "agent_model_identity": {"model": settings.model, "base_url": settings.base_url,
                                       "prompt_version": PROMPT_VERSION}}
    result.update(user_intent={}, intent_resolution={'method': 'rules', 'model_calls': 0})
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
            if isinstance(connection, PostgresConnection):
                # 直接追问也受数据库超时保护，且保留服务器已有的更严格上限。
                connection.execute("SELECT set_config('statement_timeout', "
                    "LEAST(COALESCE(NULLIF(setting::int,0),30000),30000)::text,true) "
                    "FROM pg_settings WHERE name='statement_timeout'")
            products = [dict(row) for row in connection.execute('SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
                                          (scope['source'], scope['dataset_version'])).fetchall()]
            planning_started = perf_counter()
            scope, plan, semantic_direct = resolve_review_request(question, scope, products, settings=settings,
                context=conversation_context, diagnostics=result['intent_resolution'], intent_state=result['user_intent'])
            result['user_intent'].update(resolved_product_ids=list(scope['product_ids']),
                effective_time_range={'date_from': scope['date_from'], 'date_to': scope['date_to']})
            sentiment_intent = plan.sentiment
            result.update(scope=scope, query_plan=plan.to_dict(), sentiment_intent=sentiment_intent,
                          analysis_intent=plan.analysis_type, analysis_question=(plan.answer_question or question)
                          if question.startswith('执行本轮已确认的只读查询') else question)
            if plan.analysis_type == 'aspect_drilldown':
                result['user_intent'].update(query_type='aspect_drilldown', sub_intent='drill_down')
            timings['planning_duration_ms'] = (perf_counter() - planning_started) * 1000
            if plan.unsupported_reason:
                raise UnsupportedReviewQuery(plan.unsupported_reason)
            if plan.analysis_type == "pros_cons" and len(plan.requirements) + 1 > settings.max_tool_calls:
                raise UnsupportedReviewQuery("本轮商品数量超过工具调用预算，请减少商品后分析优缺点。")
            context = ReviewToolContext(connection, scope, Path(temporary), settings.max_tool_calls, report,
                                        sentiment_intent=sentiment_intent, query_plan=plan, settings=settings)
            registry = create_review_registry(context)
            overview = registry.execute_tool("get_analysis_context", "{}", automatic=True)
            if "overview" not in context.results:
                raise ReviewDataError("无法读取当前评论范围，请检查标注表和数据集。")
            status, ids = None, []
            direct_query = semantic_direct or question.startswith("执行本轮已确认的只读查询") or plan.analysis_type in {'opinion_breakdown', 'aspect_drilldown', 'pros_cons'}
            if direct_query:
                # 已确认追问或确定的文本归组计划直接执行；仍走同一注册表、预算与发布校验。
                for request in plan.requirements:
                    response = registry.execute_tool(request["tool"], json.dumps(request["parameters"], ensure_ascii=False), automatic=True)
                    payload = response.to_dict()
                    if payload.get("status") != "success":
                        raise ReviewDataError(payload.get("message") or "已确认的追问执行失败。")
                status, ids = "ok", [key for key in context.results if key != "overview"]
            else:
                with create_llm(settings) as llm:
                    agent = build_agent(settings, context, llm, system_prompt=SYSTEM_PROMPT,
                                         name="review_analyst", tool_registry=registry)

                    async def record_step(event):
                        context.trace.append({"event": "model_response", "usage": event.data.get("usage"),
                                              "finish_reason": event.data.get("finish_reason")})
                        report({"stage": "agent_thinking", "completed": context.tool_calls,
                                "total": None, "unit": "tools"})

                    # 查询请求独立于不可信业务上下文，避免被当作待分析的数据而忽略。
                    prompt = (question + "\n\n<bound_context>\n"
                              + json.dumps(overview.to_dict()["data"], ensure_ascii=False) + "\n</bound_context>"
                              + "\n<query_plan>\n" + json.dumps(plan.to_dict(), ensure_ascii=False) + "\n</query_plan>")
                    for attempt in range(2):
                        try:
                            model_started = perf_counter()
                            queries_before = context.query_duration_ms
                            text = agent.run(prompt, on_step=record_step)
                        finally:
                            timings["planning_duration_ms"] += (perf_counter() - model_started) * 1000 - (context.query_duration_ms - queries_before)
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
                            if status == "unsupported" and ids != ["overview"]:
                                raise AgentExecutionError("能力不足的答复不能发布未校验的统计结果。")
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
            if any(item['kind'] == 'aspect_drilldown' and item['data']['analysis_status'] == 'failed' for item in selected):
                raise ReviewDataError('二级主题归并未完成；原始观点与失败原因已保留，未发布主题结论。')
            if status == "ok":
                validation_started = perf_counter()
                result["validation"] = validate_report(
                    context.results, ids, sentiment_intent=sentiment_intent, query_plan=plan,
                    expected_scopes={key: (scope if key == "overview" else {
                        **context.query_scope(item["parameters"].get("product_ids", []),
                                              baseline=item["parameters"].get("window") == "baseline"),
                        **{k: v for k, v in item["parameters"].items()
                           if k in ("date_from", "date_to") and v is not None},
                    }) for key, item in context.results.items()},
                )
                timings["validation_duration_ms"] = round((perf_counter() - validation_started) * 1000, 3)
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
            if result["status"] == "ok":
                compression_started = perf_counter()
                result.update(build_analysis_artifacts({**result, "results": context.results}, plan))
                timings["compression_duration_ms"] = round((perf_counter() - compression_started) * 1000, 3)
        # 关闭只读事务和规划客户端后才解释已核验快照，生成阶段没有查询入口。
        if result['status'] == 'ok' and any(item['kind'] == 'aspect_drilldown' and item['data']['analysis_status'] == 'partial' for item in selected):
            result['status'] = result['public_analysis']['status'] = 'partial'
            result['public_answer'] = unavailable_answer('partial')
            result['answer_generation'] = {'strategy': 'blocked', 'attempts': 0, 'generation_status': 'unavailable', 'error_code': 'analysis_not_complete'}
        if result["status"] == "ok":
            report({"stage": "answer_generating", "completed": 0, "total": 1, "unit": "answers"})
            answer_started = perf_counter()
            result["public_answer"], result["answer_generation"] = NaturalLanguageAnswerGenerator().generate(
                result["analysis_question"], result["compact_context"], settings=settings, validated=result["validation"]["status"] == "passed")
            timings["answer_duration_ms"] = round((perf_counter() - answer_started) * 1000, 3)
    except QuestionScopeError as exc:
        result['user_intent'].update(needs_clarification=True, clarification_question=str(exc))
        result.update(status="unsupported", answer=str(exc), result_ids=[])
    except UnsupportedReviewQuery as exc:
        result.update(status="unsupported", answer=str(exc), result_ids=[])
    except Exception as exc:
        if progress_error is not None:
            raise progress_error
        code, message = ("business_error", str(exc)) if isinstance(exc, ReviewDataError) else execution_failure(exc)
        if result['intent_resolution'].get('error_code'):
            timed_out = result['intent_resolution']['error_code'] == 'timeout'
            code = 'intent_timeout' if timed_out else 'invalid_intent'
            message = '本轮语义解析超时，尚未执行数据查询。' if timed_out else '本轮语义解析未通过结构校验，尚未执行数据查询。'
        result.update(status="error", error={"code": code, "message": message})
        result.pop("answer", None)
        result.pop("answer_sections", None)
        result.pop("public_answer", None)
    finally:
        timings["query_duration_ms"] = round(context.query_duration_ms if context else 0, 3)
        timings["planning_duration_ms"] = round(timings["planning_duration_ms"], 3)
        timings["total_duration_ms"] = round((perf_counter() - started) * 1000, 3)
        result["performance"] = {**timings, "answer_input_bytes": result.get("answer_generation", {}).get("input_bytes", 0)}
        result.update(results=context.results if context else {}, trace=context.trace if context else [],
                      model_tool_attempts=context.model_tool_attempts if context else 0,
                      cache_hits=context.cache_hits if context else 0,
                      query_executions=context.query_executions if context else 0,
                      tool_calls=context.tool_calls if context else 0, model_calls=llm.budget.calls if llm else 0,
                      model_responses=model_responses, usage=usage or None,
                      answer_model_calls=result.get("answer_generation", {}).get("attempts", 0),
                      topic_model_calls=context.topic_model_calls if context else 0,
                      intent_model_calls=result['intent_resolution'].get('model_calls', 0),
                      total_model_calls=(llm.budget.calls if llm else 0) + result.get("answer_generation", {}).get("attempts", 0)
                                        + result['intent_resolution'].get('model_calls', 0) + (context.topic_model_calls if context else 0),
                      elapsed_seconds=round(timings["total_duration_ms"] / 1000, 3))
    result.setdefault("public_analysis", empty_public(result["status"]).model_dump())
    result.setdefault("public_answer", unavailable_answer("unsupported" if result["status"] == "unsupported" else "failed", reason=result.get("answer")))
    return json.loads(safe_json(result, settings.api_key))
