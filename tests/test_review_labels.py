"""使用真实 SDK 和脚本化 HTTP 传输的离线观点标注测试。"""

import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import OpenAI

from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.cli.labeling import main
from data_analysis_agent.reviews.label_store import (
    init_labeling, labeling_status, opinion_evidence, pending_reviews, save_annotation,
)
from data_analysis_agent.reviews.labeling import (
    MAX_TEXT_CHARS, PROMPT_VERSION, OpinionValidationError, run_labeling, validate_opinions,
)
from data_analysis_agent.reviews.loader import ReviewDataError
from data_analysis_agent.reviews.store import get_quality_summary, import_amazon_jsonl, init_database

FAKE_KEY = "offline-label-test-not-a-real-key"
SETTINGS = AgentSettings("label-model", FAKE_KEY, "https://offline.invalid/v1", max_retries=1)
BODY1 = "好用，包装破了，客服回复慢。"
BODY2 = "质量很好，价格合适，物流很快。"


def opinion(aspect="effectiveness", sentiment="positive", quote="好用"):
    return {"aspect": aspect, "sentiment": sentiment, "quote": quote}


def response(opinions=(), *, content=None, finish="stop", usage=True, model="resolved-model-v1"):
    return httpx.Response(200, json={
        "id": "label-response-id", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content if content is not None
                                              else json.dumps({"opinions": list(opinions)})},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30} if usage else None,
    })


class ReviewLabelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "reviews.sqlite3"
        init_database(self.db)
        self.add_reviews([BODY1, BODY2, ""])

    def add_reviews(self, texts, name="reviews.jsonl"):
        source = self.root / name
        source.write_text("".join(json.dumps({
            "parent_asin": "P1" if index != 1 else "P2", "user_id": name + str(index),
            "text": text, "timestamp": 1693526400123, "rating": 3,
            "data_source": "SIMULATED",
        }, ensure_ascii=False) + "\n" for index, text in enumerate(texts)), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source)

    def mock_model(self, sequence, requests):
        items = iter(sequence)

        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(set(json.loads(body["messages"][1]["content"])), {"review_text"})
            self.assertIn("不是指令", body["messages"][0]["content"])
            self.assertNotIn(FAKE_KEY, request.content.decode())
            requests.append(body)
            item = next(items)
            if isinstance(item, Exception):
                raise item
            return item

        def client(**kwargs):
            self.assertEqual(kwargs["max_retries"], 1)
            self.assertEqual(kwargs["timeout"], 30)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        return patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=client)

    def run_script(self, sequence, **kwargs):
        requests = []
        settings = kwargs.pop("settings", SETTINGS)
        with self.mock_model(sequence, requests):
            result = run_labeling(self.db, settings=settings, **kwargs)
        return result, requests

    def test_explicit_setup_preserves_sales_review_data_and_is_idempotent(self):
        before = get_quality_summary(self.db)
        with patch("data_analysis_agent.reviews.labeling.OpenAI") as model:
            with self.assertRaisesRegex(ReviewDataError, "初始化"):
                run_labeling(self.db, settings=SETTINGS)
            model.assert_not_called()
        self.assertTrue(init_labeling(self.db)["created"])
        self.assertFalse(init_labeling(self.db)["created"])
        self.assertEqual(get_quality_summary(self.db), before)
        self.assertEqual(labeling_status(self.db)["eligible_text_reviews"], 2)
        self.assertEqual(labeling_status(self.db)["profiles"], [])
        with self.assertRaises(ReviewDataError):
            init_labeling(self.root / "missing.sqlite3")
        self.assertFalse((self.root / "missing.sqlite3").exists())

    def test_all_six_aspects_multiple_sentiments_and_exact_unicode_spans(self):
        text = "🙂质量很好，但后来断了。好用，价格一般，包装破了，物流很快，客服不确定。"
        items = [opinion("quality", "positive", "质量很好"), opinion("quality", "negative", "后来断了"),
                 opinion(), opinion("price", "neutral", "价格一般"),
                 opinion("packaging", "negative", "包装破了"), opinion("logistics", "positive", "物流很快"),
                 opinion("service", "unknown", "客服不确定")]
        result = validate_opinions(json.dumps({"opinions": items + [items[0]]}), text)
        self.assertEqual(len(result), 7)
        self.assertEqual(result[0]["quote_start"], 1)
        self.assertEqual({item["aspect"] for item in result},
                         {"quality", "effectiveness", "price", "packaging", "logistics", "service"})
        for item in result:
            self.assertEqual(text[item["quote_start"]:item["quote_end"]], item["quote"])
        fenced = '```json\n' + json.dumps({"opinions": [opinion()]}) + '\n```'
        self.assertEqual(validate_opinions(fenced, BODY1)[0]["quote"], "好用")

    def test_invalid_output_and_fabricated_or_noncontinuous_quotes_are_rejected(self):
        cases = ["not json", "[]", '{}', '{"opinions":[],"opinions":[]}',
                 '{"opinions":[],"value":NaN}', json.dumps({"opinions": "wrong"}),
                 json.dumps({"opinions": [opinion(aspect="other")]}),
                 json.dumps({"opinions": [opinion(sentiment="mixed")]}),
                 json.dumps({"opinions": [opinion(quote="改写的片段")]}),
                 json.dumps({"opinions": [opinion(quote="Works well")]}),
                 json.dumps({"opinions": [opinion(quote="好用…客服回复慢")]}),
                 json.dumps({"opinions": [opinion(quote=" ")]}),
                 json.dumps({"opinions": [opinion(quote=1)]}),
                 json.dumps({"opinions": [{**opinion(), "quote_start": 0}]}),
                 json.dumps({"opinions": [opinion()] * 31}), None, "x" * 100001]
        for content in cases:
            with self.subTest(content=str(content)[:100]), self.assertRaises(OpinionValidationError):
                validate_opinions(content, BODY1)

    def test_end_to_end_incremental_results_model_versions_and_original_evidence(self):
        init_labeling(self.db)
        before = get_quality_summary(self.db)
        script = [response([opinion(), opinion("packaging", "negative", "包装破了"),
                            opinion("service", "negative", "客服回复慢")]),
                  response([opinion("quality", "positive", "质量很好"),
                            opinion("price", "positive", "价格合适"),
                            opinion("logistics", "positive", "物流很快")])]
        result, requests = self.run_script(script)
        self.assertEqual((result["successful_reviews"], result["opinion_count"], result["model_calls"]), (2, 6, 2))
        self.assertEqual(len(requests), 2)
        with patch("data_analysis_agent.reviews.labeling.OpenAI") as model:
            self.assertEqual(run_labeling(self.db, settings=SETTINGS)["selected_reviews"], 0)
            model.assert_not_called()
        progress = labeling_status(self.db)
        self.assertEqual(progress["empty_text_reviews"], 1)
        self.assertEqual(progress["profiles"][0]["unprocessed_reviews"], 0)
        self.assertEqual(progress["profiles"][0]["opinion_count"], 6)
        evidence = opinion_evidence(self.db, 1)
        annotated = evidence["annotations"][0]
        self.assertEqual(annotated["model_version"], "resolved-model-v1")
        self.assertEqual(annotated["requested_model"], SETTINGS.model)
        self.assertEqual(annotated["prompt_version"], PROMPT_VERSION)
        self.assertEqual(annotated["total_tokens"], 30)
        self.assertEqual(evidence["text"], BODY1)
        self.assertEqual(evidence["line_number"], 1)
        self.assertTrue(evidence["evidence_ref"].endswith("#line=1"))
        self.assertEqual(get_quality_summary(self.db), before)

    def test_limit_new_imports_and_product_filter_process_only_pending_records(self):
        init_labeling(self.db)
        first, _ = self.run_script([response([opinion()])], limit=1)
        second, _ = self.run_script([response([])], product_ids=["P2"])
        self.assertEqual(first["results"][0]["review_id"], 1)
        self.assertEqual(second["results"][0]["review_id"], 2)
        self.add_reviews(["新评论"], name="extra.jsonl")
        third, _ = self.run_script([response([])])
        self.assertEqual(third["selected_reviews"], 1)
        self.assertEqual(third["results"][0]["review_id"], 4)
        self.assertEqual(labeling_status(self.db)["profiles"][0]["successful_reviews"], 3)

    def test_failure_is_not_automatic_retry_and_explicit_retry_preserves_history(self):
        init_labeling(self.db)
        first, _ = self.run_script([response([opinion(quote="编造片段")]), response([])])
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["failed_reviews"], 1)
        self.assertEqual(labeling_status(self.db)["profiles"][0]["failed_reviews"], 1)
        no_retry, _ = self.run_script([])
        self.assertEqual(no_retry["model_calls"], 0)
        retry, _ = self.run_script([response([opinion()])], retry_failed=True)
        self.assertEqual(retry["successful_reviews"], 1)
        history = opinion_evidence(self.db, 1)["annotations"]
        self.assertEqual([item["status"] for item in history], ["failed", "success"])
        self.assertEqual(history[0]["opinions"], [])
        progress = labeling_status(self.db)["profiles"][0]
        self.assertEqual((progress["failed_attempts"], progress["failed_reviews"], progress["successful_reviews"]), (1, 0, 2))

    def test_success_with_no_opinions_is_cached_and_missing_usage_is_null(self):
        init_labeling(self.db)
        self.run_script([response([], usage=False), response([])])
        evidence = opinion_evidence(self.db, 1)["annotations"][0]
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(evidence["opinions"], [])
        self.assertIsNone(evidence["input_tokens"])
        self.assertEqual(self.run_script([])[0]["model_calls"], 0)

    def test_model_endpoint_and_prompt_changes_create_separate_profiles(self):
        init_labeling(self.db)
        self.run_script([response([]), response([])])
        self.run_script([response([]), response([])], settings=replace(SETTINGS, model="new-model"))
        self.run_script([response([]), response([])], settings=replace(SETTINGS, base_url="https://other.invalid/v1"))
        with patch("data_analysis_agent.reviews.labeling.PROMPT_VERSION", "changed-prompt"):
            self.run_script([response([]), response([])])
        profiles = labeling_status(self.db)["profiles"]
        self.assertEqual(len(profiles), 4)
        self.assertTrue(all(item["successful_reviews"] == 2 for item in profiles))

    def test_authentication_failure_stops_and_does_not_persist_secret(self):
        init_labeling(self.db)
        denied = httpx.Response(401, json={"error": {"message": FAKE_KEY, "type": "authentication_error"}})
        result, requests = self.run_script([denied])
        self.assertEqual(result["stopped_reason"], "authentication_error")
        self.assertEqual((len(requests), result["processed_reviews"]), (1, 1))
        self.assertEqual(labeling_status(self.db)["profiles"][0]["unprocessed_reviews"], 1)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertNotIn(FAKE_KEY, "\n".join(connection.iterdump()))
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_rate_limit_exhaustion_stops_after_configured_retries(self):
        init_labeling(self.db)
        limited = httpx.Response(429, json={"error": {"message": "rate limited"}},
                                headers={"retry-after-ms": "1"})
        result, requests = self.run_script([limited, limited])
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["stopped_reason"], "rate_limit")
        self.assertEqual(result["processed_reviews"], 1)

    def test_changed_content_is_reprocessed_and_old_history_remains_separate(self):
        init_labeling(self.db)
        self.run_script([response([]), response([])])
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE reviews SET text=?, content_sha256=? WHERE id=1",
                               ("更新后的好用评论", "changed-content-hash"))
        result, requests = self.run_script([response([opinion()])])
        self.assertEqual((result["selected_reviews"], len(requests)), (1, 1))
        history = opinion_evidence(self.db, 1)["annotations"]
        self.assertEqual([item["matches_current_content"] for item in history], [False, True])
        profile = labeling_status(self.db)["profiles"][0]
        self.assertEqual(profile["successful_reviews"], 2)
        self.assertEqual(profile["successful_attempts"], 3)

    def test_transient_retry_is_bounded_and_one_timeout_does_not_stop_batch(self):
        init_labeling(self.db)
        bad = httpx.Response(500, json={"error": {"message": "temporary failure"}}, headers={"retry-after-ms": "1"})
        result, requests = self.run_script([bad, response([])], limit=1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["model_calls"], 1)
        timeout = httpx.ReadTimeout("must not persist " + FAKE_KEY)
        self.add_reviews(["另一条评论"], name="extra.jsonl")
        continued, requests = self.run_script([timeout, timeout, response([])])
        self.assertEqual(len(requests), 3)
        self.assertEqual(continued["stopped_reason"], None)
        self.assertEqual((continued["failed_reviews"], continued["successful_reviews"]), (1, 1))
        self.assertEqual(continued["results"][0]["error_code"], "model_timeout")
        self.assertNotIn(FAKE_KEY, json.dumps(continued))

    def test_repeated_server_errors_stop_batch(self):
        init_labeling(self.db)
        self.add_reviews(["第三条", "第四条"], name="extra.jsonl")
        bad = httpx.Response(503, json={"error": {"message": "temporary failure"}},
                             headers={"retry-after-ms": "1"})
        result, requests = self.run_script([bad] * 6 + [response([])])
        self.assertEqual(len(requests), 6)
        self.assertEqual(result["processed_reviews"], 3)
        self.assertEqual(result["stopped_reason"], "repeated_model_api_errors")
        self.assertEqual([item["error_code"] for item in result["results"]], ["model_server_error"] * 3)

    def test_truncated_output_and_overlong_input_are_failed_without_partial_opinions(self):
        init_labeling(self.db)
        result, _ = self.run_script([response([opinion()], finish="length")], limit=1)
        self.assertEqual(result["failed_reviews"], 1)
        self.assertEqual(opinion_evidence(self.db, 1)["annotations"][0]["opinions"], [])
        self.add_reviews(["长" * (MAX_TEXT_CHARS + 1)], name="long.jsonl")
        outcome, requests = self.run_script([response([])])
        self.assertEqual(len(requests), 1)
        self.assertEqual(outcome["results"][-1]["error_code"], "input_too_long")

    def test_storage_rolls_back_invalid_opinions_and_rejects_changed_source(self):
        init_labeling(self.db)
        identity = {"model": SETTINGS.model, "base_url": SETTINGS.base_url, "prompt_version": PROMPT_VERSION}
        review = pending_reviews(self.db, **identity, limit=1)[0]
        invalid = {**opinion(aspect="other"), "quote_start": 0, "quote_end": 2}
        with self.assertRaises(sqlite3.IntegrityError):
            save_annotation(self.db, review, **identity, status="success", opinions=[invalid])
        self.assertEqual(opinion_evidence(self.db, 1)["annotations"], [])
        with self.assertRaises(ReviewDataError):
            save_annotation(self.db, {**review, "text": "changed"}, **identity, status="success")
        with self.assertRaises(ReviewDataError):
            save_annotation(self.db, review, **identity, status="success", opinions=[
                {**opinion(), "quote_start": 1, "quote_end": 3}])

    def test_embedded_null_in_original_fragment_is_preserved(self):
        init_labeling(self.db)
        self.add_reviews(["\x00好用"], name="control-character.jsonl")
        identity = {"model": SETTINGS.model, "base_url": SETTINGS.base_url, "prompt_version": PROMPT_VERSION}
        review = next(item for item in pending_reviews(self.db, **identity, limit=10) if item["id"] == 4)
        opinions = validate_opinions(json.dumps({"opinions": [opinion(quote="\x00好用")]}), review["text"])
        save_annotation(self.db, review, **identity, status="success", opinions=opinions)
        stored = opinion_evidence(self.db, 4)["annotations"][0]["opinions"][0]
        self.assertEqual(stored["quote"], "\x00好用")
        self.assertEqual(review["text"][stored["quote_start"]:stored["quote_end"]], stored["quote"])

    def test_metadata_is_redacted_and_input_guards_make_no_api_calls(self):
        init_labeling(self.db)
        self.run_script([response([], model="model-" + FAKE_KEY)], limit=1)
        model = opinion_evidence(self.db, 1)["annotations"][0]["model_version"]
        self.assertNotIn(FAKE_KEY, model)
        self.assertIn("[REDACTED]", model)
        for value in (0, 1001, True, "20"):
            with self.assertRaises(ReviewDataError):
                run_labeling(self.db, settings=SETTINGS, limit=value)
        for value in (0, 5, True, "4"):
            with self.assertRaises(ReviewDataError):
                run_labeling(self.db, settings=SETTINGS, workers=value)
        for value in ([""], "P1", [1]):
            with self.assertRaises(ReviewDataError):
                run_labeling(self.db, settings=SETTINGS, product_ids=value)
        for value in (0, True, "1", 999):
            with self.assertRaises(ReviewDataError):
                opinion_evidence(self.db, value)

    def test_cli_setup_run_status_evidence_and_missing_configuration(self):
        def cli(*arguments):
            output = io.StringIO()
            with patch.object(sys, "argv", ["review-label", *arguments]), redirect_stdout(output):
                code = main()
            return code, json.loads(output.getvalue())

        self.assertEqual(cli("init", "--db", str(self.db))[0], 0)
        with patch("data_analysis_agent.reviews.labeling.read_settings", return_value={}):
            code, output = cli("run", "--db", str(self.db))
        self.assertEqual(code, 1)
        requests = []
        with patch("data_analysis_agent.reviews.labeling.read_settings", return_value={
            "LLM_MODEL_ID": SETTINGS.model, "LLM_API_KEY": FAKE_KEY, "LLM_BASE_URL": SETTINGS.base_url,
        }), self.mock_model([response([opinion(quote="fabricated")]), response([])], requests):
            self.assertEqual(cli("run", "--db", str(self.db))[0], 3)
        with patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("must not load .env")):
            self.assertEqual(cli("status", "--db", str(self.db))[0], 0)
            self.assertEqual(cli("evidence", "--db", str(self.db), "--review-id", "1")[0], 0)

    def test_parallel_requests_keep_one_writer_and_report_progress(self):
        init_labeling(self.db)
        self.add_reviews([f"新的评论 {index}" for index in range(6)], name="more.jsonl")
        active, maximum = 0, 0
        lock = threading.Lock()
        requests = []

        def handler(request):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                requests.append(request)
            time.sleep(0.03)
            with lock:
                active -= 1
            return response([])

        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        updates = []
        with patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=client):
            result = run_labeling(self.db, settings=SETTINGS, workers=4, progress=updates.append)
        self.assertEqual(maximum, 4)
        self.assertEqual((result["selected_reviews"], result["successful_reviews"], result["model_calls"]),
                         (8, 8, 8))
        self.assertEqual([item["completed"] for item in updates], list(range(9)))
        self.assertEqual(len({item["review_id"] for item in result["results"]}), 8)
        self.assertEqual(labeling_status(self.db)["profiles"][0]["successful_reviews"], 8)

    def test_label_batch_reuses_one_database_writer_connection(self):
        init_labeling(self.db)
        connections = []

        def open_connection(path):
            from data_analysis_agent.reviews.store import connect_review_database

            connection = connect_review_database(path)
            connections.append(connection)
            return connection

        with self.mock_model([response([]), response([])], []), \
                patch("data_analysis_agent.reviews.labeling.connect_review_database", side_effect=open_connection):
            result = run_labeling(self.db, settings=SETTINGS, limit=2, workers=2)
        self.assertEqual(result["successful_reviews"], 2)
        self.assertEqual(len(connections), 1)

    def test_parallel_auth_failure_stops_submitting_new_requests(self):
        init_labeling(self.db)
        self.add_reviews([f"新的评论 {index}" for index in range(4)], name="more.jsonl")
        lock = threading.Lock()
        seen = 0

        def handler(request):
            nonlocal seen
            with lock:
                seen += 1
                number = seen
            if number == 1:
                return httpx.Response(401, json={"error": {"message": "denied", "type": "authentication_error"}})
            time.sleep(0.03)
            return response([])

        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        with patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=client):
            result = run_labeling(self.db, settings=SETTINGS, workers=4)
        self.assertEqual((seen, result["processed_reviews"], result["model_calls"]), (4, 4, 4))
        self.assertEqual(result["stopped_reason"], "authentication_error")
        self.assertEqual(labeling_status(self.db)["profiles"][0]["unprocessed_reviews"], 2)

    def test_cli_progress_is_visible_before_final_json(self):
        init_labeling(self.db)
        output, progress = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["review-label", "run", "--db", str(self.db), "--workers", "2"]), \
                patch("data_analysis_agent.reviews.labeling.read_settings", return_value={
                    "LLM_MODEL_ID": SETTINGS.model, "LLM_API_KEY": FAKE_KEY, "LLM_BASE_URL": SETTINGS.base_url,
                }), self.mock_model([response([]), response([])], []), \
                redirect_stdout(output), redirect_stderr(progress):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())["successful_reviews"], 2)
        self.assertIn("标注进度 0/2", progress.getvalue())
        self.assertIn("标注进度 2/2", progress.getvalue())


if __name__ == "__main__":
    unittest.main()
