"""阶段 4：受控 SQL + Mock SDK，验证数字绑定、文字协议与安全降级。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
import test_review_compression as compression_fixture
from test_review_workbench import WorkbenchFixture
from data_analysis_agent.agents.review_compression import CompactAnalysisContext, build_analysis_artifacts
from data_analysis_agent.agents.review_answer_facts import build_answer_facts
from data_analysis_agent.agents.review_answers import (
    AnswerAssembler, NaturalLanguageAnswerGenerator, model_messages, read_public_answer, template_draft,
)
from data_analysis_agent.core.config import AgentSettings, ConfigurationError
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.labeling import validate_opinions
from test_review_workbench import IDENTITY


class AnswerTests(WorkbenchFixture, unittest.TestCase):
    scope = compression_fixture.CompressionTests.scope
    result = compression_fixture.CompressionTests.result

    def context(self, question, **scope):
        full, plan = self.result(question, **scope)
        return full, build_analysis_artifacts(full, plan)["compact_context"]

    def strong_trend_context(self):
        records = []
        for month, (packaging, logistics) in enumerate(zip([5, 10, 20, 10, 5, 10], [15, 12, 10, 8, 6, 5]), 1):
            for index in range(25):
                text = ("包装破损。" if index < packaging else "包装完整。") + ("物流延迟。" if index < logistics else "物流正常。")
                records.append({"parent_asin": "TREND-CONTROL", "user_id": f"{month}-{index}", "text": text,
                    "timestamp": int(datetime(2023, month, 1, tzinfo=timezone.utc).timestamp() * 1000), "rating": 3})
        path = self.root / "controlled-trend.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version="phase4-controlled")
        for review in pending_reviews(self.db, **IDENTITY, dataset_version="phase4-controlled", limit=1000):
            opinions = [{"aspect": aspect, "sentiment": "negative", "quote": quote}
                        for aspect, quote in (("packaging", "包装破损"), ("logistics", "物流延迟")) if quote in review["text"]]
            save_annotation(self.db, review, **IDENTITY, status="success",
                            opinions=validate_opinions(json.dumps({"opinions": opinions}), review["text"]))
        return self.context("按月查看包装和物流问题变化", dataset_version="phase4-controlled", product_ids=["TREND-CONTROL"], date_to="2023-06-30")

    def model(self, transform=None, error=None):
        self.requests = []
        def handler(request):
            body = json.loads(request.content)
            self.requests.append(body)
            self.assertNotIn("tools", body)
            self.assertNotIn("tool_choice", body)
            if error:
                raise error
            payload = json.loads(body["messages"][-1]["content"])
            context = CompactAnalysisContext.model_validate(self.current_context)
            facts, _ = build_answer_facts(context)
            draft = template_draft(context, facts).model_dump()
            value = transform(draft) if transform else draft
            return completion({"role": "assistant", "content": value if isinstance(value, str) else json.dumps(value)})
        def client(**kwargs):
            self.assertEqual(kwargs["max_retries"], 0)
            self.assertLessEqual(kwargs["timeout"], SETTINGS.answer_timeout_seconds)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        return patch("openai.OpenAI", side_effect=client)

    def generate(self, context, settings=SETTINGS):
        self.current_context = context
        return NaturalLanguageAnswerGenerator().generate(context["question"], context, settings=settings, validated=True)

    def test_positive_template_real_ranking_and_numbers_without_model(self):
        _, context = self.context("美的空调好评主要集中在哪些方面？")
        with patch("openai.OpenAI") as model:
            answer, diag = self.generate(context)
            model.assert_not_called()
        self.assertEqual(diag["attempts"], 0)
        self.assertEqual(answer["generation_status"], "template")
        self.assertIn("使用效果、物流、价格", answer["summary"])
        self.assertIn("2 条", answer["highlights"][0]["text"])
        self.assertIn("66.7%", answer["highlights"][0]["text"])
        self.assertIn("3 条包含正向观点", answer["sample_note"])
        self.assertNotIn("[r1]", canonical_json(answer))
        self.assertNotIn("负面占比", canonical_json(answer))

    def test_negative_template_keeps_success_denominator_and_current_window(self):
        _, context = self.context("这个商品主要有哪些问题？")
        answer, _ = self.generate(context)
        self.assertIn("包装", answer["summary"])
        self.assertIn("1/3 条成功标注评论", answer["highlights"][0]["text"])
        self.assertTrue(all(row["scope_ref"] == context["statistics"][0]["scope_ref"] for row in answer["highlights"]))

    def test_single_positive_dimension_below_default_top_three_is_not_lost(self):
        _, context = self.context("质量好评主要方面")
        answer, _ = self.generate(context)
        self.assertEqual([row["aspect_key"] for row in answer["highlights"]], ["quality"])
        self.assertIn("1 条", answer["highlights"][0]["text"])

    def test_no_positive_mentions_does_not_invent_ranking(self):
        for rid in (1, 2, 6):
            self.save(rid=rid, expected_revision_id=None, opinions=[])
        _, context = self.context("好评主要方面")
        answer, _ = self.generate(context)
        self.assertIn("数据不足", answer["summary"])
        self.assertIn("暂无匹配的有效正面观点", answer["highlights"][0]["text"])
        self.assertNotIn("0.0%", canonical_json(answer))

    def test_no_successful_annotations_is_explicit(self):
        _, context = self.context("好评主要方面", model="missing-version")
        answer, _ = self.generate(context)
        self.assertIn("暂无成功标注", answer["highlights"][0]["text"])
        self.assertNotIn("0.0%", canonical_json(answer))

    def test_coverage_multiple_mentions_and_source_refs_preserved(self):
        full, context = self.context("好评主要方面")
        before = canonical_json(full)
        answer, _ = self.generate(context)
        self.assertIn("部分样本量较少", "".join(answer["caveats"]))
        self.assertIn("不能相加", "".join(answer["caveats"]))
        self.assertEqual(answer["source_result_ids"], ["r1"])
        self.assertEqual(before, canonical_json(full))

    def test_trend_llm_separates_dimensions_and_sparse_buckets(self):
        _, context = self.context("按月查看包装和物流问题变化", date_to="2023-03-31")
        with self.model():
            answer, diag = self.generate(context)
        self.assertEqual(answer["generation_status"], "llm")
        self.assertEqual(diag["attempts"], 1)
        self.assertEqual([row["aspect_key"] for row in answer["highlights"]], ["packaging", "logistics"])
        self.assertEqual(answer["summary"].count("暂不能判断稳定趋势"), 2)
        self.assertIn("空数据不代表", "".join(answer["caveats"]))
        content = self.requests[0]["messages"][-1]["content"]
        for marker in ("base_url", "prompt_version", "annotation_profile_ref", "trace", "tool_calls", '"points"'):
            self.assertNotIn(marker, content)

    def test_product_comparison_binds_each_product_scope(self):
        _, context = self.context("比较两个商品的主要负面问题", product_ids=["P1", "P2"])
        with self.model():
            answer, _ = self.generate(context)
        self.assertEqual(answer["generation_status"], "llm")
        self.assertIn("商品 P1", answer["highlights"][0]["text"])
        self.assertIn("商品 P2", answer["highlights"][1]["text"])
        self.assertNotEqual(answer["highlights"][0]["scope_ref"], answer["highlights"][1]["scope_ref"])

    def test_supported_trend_explains_fluctuation_and_decline_from_real_sql(self):
        _, context = self.strong_trend_context()
        with self.model():
            answer, _ = self.generate(context)
        packaging, logistics = answer["highlights"]
        self.assertIn("呈现波动", answer["summary"])
        self.assertIn("2023-03", packaging["text"])
        self.assertIn("80.0%", packaging["text"])
        self.assertIn("呈下降变化", answer["summary"])
        self.assertIn("60.0%", logistics["text"])
        self.assertNotIn("暂不能判断", packaging["text"])

    def test_generation_retries_only_transport_with_fixed_limit(self):
        _, context = self.context("包装按月趋势")
        with self.model(error=httpx.ReadTimeout("simulated")):
            answer, diag = self.generate(context, replace(SETTINGS, answer_max_retries=1))
        self.assertEqual(diag["attempts"], 2)
        self.assertEqual(answer["generation_status"], "fallback")

    def test_timeout_uses_template_without_querying_again_or_modifying_data(self):
        _, context = self.context("包装按月趋势", date_to="2023-03-31")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with self.model(error=httpx.ReadTimeout("secret-provider-error")):
            answer, diag = self.generate(context)
        self.assertEqual(answer["generation_status"], "fallback")
        self.assertEqual(diag["error_code"], "generation_timeout")
        self.assertEqual(diag["attempts"], 1)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertNotIn("secret-provider", canonical_json(answer))

    def test_illegal_json_missing_fields_numbers_and_unsupported_claims_fall_back(self):
        _, context = self.context("包装按月趋势", date_to="2023-03-31")
        transforms = [lambda draft: "not-json", lambda draft: {"summary": "缺少字段"},
            lambda draft: {**draft, "count": 999},
            lambda draft: {**draft, "summary": "包装投诉999条，制冷问题最多。"},
            lambda draft: {**draft, "items": [{**draft["items"][0], "aspect_key": "cooling"}]},
            lambda draft: {**draft, "items": [{**draft["items"][0], "finding_ref": "invented"}]},
            lambda draft: {**draft, "items": [{**draft["items"][0], "explanation": "包装问题由安装人员导致。"}]},
            lambda draft: {**draft, "items": [{**draft["items"][0], "product_ids": ["OTHER-PRODUCT"]}]},
            lambda draft: {**draft, "items": [{**draft["items"][0], "date_from": "2000-01-01"}]},
            lambda draft: {**draft, "items": [{**draft["items"][0], "explanation": "包装负面占比持续上升。"}]},
            lambda draft: {**draft, "items": []}]
        for transform in transforms:
            with self.subTest(transform=transform), self.model(transform):
                answer, diag = self.generate(context)
                self.assertEqual(answer["generation_status"], "fallback")
                self.assertEqual(diag["error_code"], "invalid_generation")
                self.assertNotIn("999", canonical_json(answer))
                self.assertNotIn("cooling", canonical_json(answer))

    def test_input_budget_falls_back_before_client_creation(self):
        _, context = self.context("包装按月趋势", date_to="2030-12-31")
        with patch("openai.OpenAI") as client:
            answer, diag = self.generate(context, replace(SETTINGS, answer_input_max_bytes=1024))
            client.assert_not_called()
        self.assertEqual(answer["generation_status"], "fallback")
        self.assertEqual(diag["attempts"], 0)
        self.assertEqual(diag["error_code"], "input_budget_exceeded")

    def test_analysis_failure_cannot_be_relabelled_as_success(self):
        _, context = self.context("好评主要方面")
        with self.assertRaises(ValueError):
            NaturalLanguageAnswerGenerator().generate(context["question"], context, settings=SETTINGS, validated=False)
        with self.assertRaises(ValueError):
            NaturalLanguageAnswerGenerator().generate("另一个问题", context, settings=SETTINGS, validated=True)

    def test_assembler_rejects_changed_scope_or_direction(self):
        _, data = self.context("好评主要方面")
        context = CompactAnalysisContext.model_validate(data)
        facts, _ = build_answer_facts(context)
        draft = template_draft(context, facts)
        draft.items[0].aspect_key = "packaging"
        with self.assertRaises(ValueError):
            AnswerAssembler().assemble(context, draft)

    def test_history_read_is_safe_and_does_not_call_model(self):
        full, _ = self.context("好评主要方面")
        full["public_answer"] = {"summary": "[overview] private-base-url"}
        with patch("openai.OpenAI") as client:
            answer = read_public_answer({"status": "succeeded", "result": full})
            client.assert_not_called()
        self.assertEqual(answer["status"], "ok")
        self.assertIn("好评", answer["summary"])
        self.assertNotIn("private-base-url", canonical_json(answer))
        for status in ("partial", "failed", "queued", "running"):
            public = read_public_answer({"status": status, "result": full})
            self.assertEqual(public["highlights"], [])

    def test_answer_configuration_has_separate_limits(self):
        values = {"LLM_MODEL_ID": SETTINGS.model, "LLM_API_KEY": SETTINGS.api_key, "LLM_BASE_URL": SETTINGS.base_url,
                  "ANSWER_INPUT_MAX_BYTES": "2048", "ANSWER_TIMEOUT_SECONDS": "5", "ANSWER_MAX_RETRIES": "1"}
        settings = AgentSettings.from_values(values)
        self.assertEqual((settings.answer_input_max_bytes, settings.answer_timeout_seconds, settings.answer_max_retries), (2048, 5, 1))
        for key, value in (("ANSWER_INPUT_MAX_BYTES", "10"), ("ANSWER_TIMEOUT_SECONDS", "nan"), ("ANSWER_MAX_RETRIES", "9")):
            with self.assertRaises(ConfigurationError):
                AgentSettings.from_values({**values, key: value})


if __name__ == "__main__":
    unittest.main()
