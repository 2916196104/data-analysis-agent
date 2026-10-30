"""生成可重复的隔离 SQL → 结构化 → 压缩 → 公开数据示例（不调用模型）。"""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_review_compression import CompressionTests
from data_analysis_agent.agents.review_compression import build_analysis_artifacts
from data_analysis_agent.core.serialization import canonical_json


def main():
    examples = {}
    with redirect_stdout(StringIO()):
        fixture = CompressionTests()
        fixture.setUp()
        try:
            for key, question in (
                ("positive", "美的空调好评主要集中在哪些方面？"),
                ("trend", "按月查看包装和物流问题变化"),
                ("trend_long", "按月查看包装和物流问题变化"),
                ("comparison", "比较两个商品的主要负面问题"),
            ):
                full, plan = fixture.result(question, product_ids=["P1", "P2"] if key == "comparison" else [],
                                            date_to="2025-12-31" if key == "trend_long" else "2023-03-31")
                artifacts = build_analysis_artifacts(full, plan)
                stages = {"raw_tool_results": full["results"], **artifacts}
                sizes = {name: len(canonical_json(data).encode("utf-8")) for name, data in stages.items()}
                examples[key] = {"question": question, "data_source": "controlled SQLite fixture; not real Midea data",
                                 "serialization": "canonical_json UTF-8 bytes", "sizes": sizes, **stages}
        finally:
            fixture.doCleanups()
    output = ROOT / "output" / "agent-refactor" / "phase3-example.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sizes": {key: item["sizes"] for key, item in examples.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
