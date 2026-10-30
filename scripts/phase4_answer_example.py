"""受控 SQL 与 Mock 模型回答示例；不访问生产库或真实模型。"""

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_review_answers as fixture_module
from test_agent import SETTINGS
from data_analysis_agent.core.serialization import canonical_json


def main():
    examples = {}
    with redirect_stdout(StringIO()):
        fixture = fixture_module.AnswerTests()
        fixture.setUp()
        try:
            positive = fixture.context("美的空调好评主要集中在哪些方面？", date_to="2023-03-31")
            sparse = fixture.context("按月查看包装和物流问题变化", date_to="2023-03-31")
            strong = fixture.strong_trend_context()
            comparison = fixture.context("比较两个商品的主要负面问题", product_ids=["P1", "P2"])
            for key, (full, context), failure in (("positive", positive, False), ("sparse_trend", sparse, False),
                    ("supported_trend", strong, False), ("comparison", comparison, False), ("timeout_fallback", strong, True)):
                with fixture.model(error=httpx.ReadTimeout("controlled timeout") if failure else None):
                    answer, diagnostics = fixture.generate(context)
                examples[key] = {"data_source": "controlled SQLite SQL + MockTransport; not production or real Midea data",
                    "question": context["question"], "raw_tool_results_bytes": len(canonical_json(full["results"]).encode("utf-8")),
                    "compact_context": context, "public_answer": answer, "answer_generation": diagnostics}
            with fixture.model():
                answer, diagnostics = fixture.generate(strong[1], replace(SETTINGS, answer_input_max_bytes=1024))
            examples["budget_fallback"] = {"public_answer": answer, "answer_generation": diagnostics}
        finally:
            fixture.doCleanups()
    output = ROOT / "output" / "agent-refactor" / "phase4-example.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sizes_and_calls": {key: {name: row["answer_generation"][name] for name in
        ("compact_bytes", "input_bytes", "sent_input_bytes", "attempts", "generation_status")} for key, row in examples.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
