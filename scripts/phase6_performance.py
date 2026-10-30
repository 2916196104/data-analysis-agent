"""可复现的阶段 6 绝对测量：临时 SQLite + MockTransport，非生产延迟。"""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_review_context_performance import ContextPerformanceTests, confirmed
from test_review_plan import QueryPlanTests
from test_review_answers import AnswerTests
from test_agent import SETTINGS
from data_analysis_agent.agents.reviews import run_review_agent


def main():
    measured = {}
    with redirect_stdout(StringIO()):
        fixture = ContextPerformanceTests(); fixture.setUp()
        planner = QueryPlanTests(); planner.setUp()
        answers = AnswerTests(); answers.setUp()
        try:
            for name, question in (
                ("positive_dimensions", confirmed("get_positive_aspects")),
                ("negative_dimensions", confirmed("get_top_issues")),
                ("logistics_followup", confirmed("get_top_issues", aspect="logistics")),
                ("evidence_page_1", confirmed("get_evidence", aspect="packaging", sentiment="negative", window="current", page=1, page_size=1)),
                ("evidence_page_2", confirmed("get_evidence", aspect="packaging", sentiment="negative", window="current", page=2, page_size=1)),
            ):
                rows = []
                for _ in range(5):
                    result = run_review_agent(fixture.db, question, scope=fixture.scope(), settings=SETTINGS)
                    assert result["status"] == "ok", result
                    rows.append({**result["performance"], **{key: result[key] for key in ("model_calls", "query_executions", "cache_hits", "answer_model_calls")}})
                measured[name] = {"runs": rows, "median_total_ms": statistics.median(row["total_duration_ms"] for row in rows),
                                  "public_answer": result["public_answer"]}
            result = planner.run_sync("按月查看包装和物流问题变化")
            assert result["status"] == "ok", result
            measured["two_aspect_trend_orchestration"] = {"performance": result["performance"], "query_executions": result["query_executions"],
                "note": "同步受控规划器；回答客户端未连接，属于可靠降级路径"}
            _, context = answers.strong_trend_context()
            for name, error in (("complex_explanation", None), ("timeout_fallback", httpx.ReadTimeout("controlled timeout"))):
                with answers.model(error=error):
                    start = perf_counter(); answer, diagnostics = answers.generate(context)
                    elapsed = (perf_counter() - start) * 1000
                measured[name] = {"measured_duration_ms": round(elapsed, 3), "answer_generation": diagnostics,
                                  "generation_status": answer["generation_status"]}
        finally:
            fixture.doCleanups(); planner.doCleanups(); answers.doCleanups()
    output = ROOT / "output" / "agent-refactor" / "phase6-performance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source": "isolated SQLite SQL + synchronous controlled planner + in-memory MockTransport; no production supplier timing",
        "baseline": "no reliable same-environment wall-time baseline; absolute values only", "measurements": measured}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
