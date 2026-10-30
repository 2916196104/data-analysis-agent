"""真实 HelloAgents/SDK 的评论问答离线验收；仅使用临时数据库和模拟模型。"""

import hashlib
import json
import sqlite3
import unittest
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI

from test_agent import SETTINGS, completion, final, tools
from test_review_workbench import IDENTITY, WorkbenchFixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_prompts import detect_sentiment_intent
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import TaskConflict, claim_task, get_task, init_tasks, worker_started
from data_analysis_agent.jobs.submission import prepare_parameters, retry_task
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.loader import DATASET_VERSION, SOURCE
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import connect_review_database


class ReviewAgentTests(WorkbenchFixture, unittest.TestCase):
    def scope(self, **changes):
        return {**IDENTITY, "source": SOURCE, "dataset_version": DATASET_VERSION, "product_ids": [],
                "date_from": "2023-01-01", "date_to": "2023-01-06", "baseline_from": "2022-12-01",
                "baseline_to": "2022-12-31", **changes}

    def mock_model(self, messages, requests):
        sequence = iter(messages)

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            user_prompt = next(item["content"] for item in requests[0]["messages"] if item["role"] == "user")
            plan = json.loads(user_prompt.split("<query_plan>\n")[1].split("\n</query_plan>")[0])
            self.assertEqual({item["function"]["name"] for item in body["tools"]},
                             {"get_analysis_context", *plan["required_tools"], *plan["optional_tools"]})
            item = next(sequence)
            if isinstance(item, Exception):
                raise item
            return completion(item)

        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        return patch("openai.OpenAI", side_effect=client)

    def run_script(self, messages, *, scope=None, settings=SETTINGS, question="查看趋势、商品对比和包装原文"):
        requests = []
        with self.mock_model(messages, requests):
            result = run_review_agent(self.db, question, scope=scope or self.scope(), settings=settings)
        return result, requests

    def test_trend_comparison_evidence_use_real_tools_and_never_write_business_database(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result, requests = self.run_script([
            tools(("get_trend", {"period": "day"}), ("compare_products", {}),
                  ("get_evidence", {"aspect": "packaging", "sentiment": "negative"})),
            final("ok", "r1", "r2", "r3"),
        ], question="按日查看整体趋势、商品对比、包装原文")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["results"]["r1"]["data"]["summary"]["review_count"], 6)
        self.assertEqual(len(result["results"]["r2"]["data"]["products"]), 3)
        evidence = result["results"]["r3"]["data"]["items"][0]
        self.assertEqual(evidence["opinions"][0]["quote"], "包装破了")
        self.assertIn("sha256:", evidence["evidence_ref"])
        self.assertNotIn("file_path", evidence)
        self.assertIn("6 条评论", result["answer"])
        self.assertIn("包装破了", result["answer"])
        self.assertNotIn(SETTINGS.api_key, json.dumps(result))
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_scoped_tools_reject_unknown_fields_products_and_write_tools_and_reuse_results(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            context = ReviewToolContext(connection, self.scope(product_ids=["P1"]), self.root, 20)
            registry = create_review_registry(context)
            for name, parameters in (
                ("get_trend", {"db_path": "other.db"}), ("get_evidence", {"date_from": "1999-01-01"}),
                ("compare_products", {"product_ids": ["P2"]}), ("get_evidence", {"page_size": True}),
                ("get_evidence", {"page_size": 100}), ("delete_reviews", {}),
            ):
                with self.subTest(name=name, parameters=parameters):
                    response = registry.execute_tool(name, json.dumps(parameters)).to_dict()
                    self.assertEqual(response["status"], "error", response)
            first = registry.execute_tool("get_trend", "{}").to_dict()["data"]
            again = registry.execute_tool("get_trend", "{}").to_dict()["data"]
            self.assertEqual(first, again)
            self.assertEqual(first["data"]["summary"]["review_count"], 3)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM reviews")

    def test_product_schema_lists_only_complete_ids_in_bound_scope(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            registry = create_review_registry(ReviewToolContext(connection, self.scope(product_ids=["P1"]), self.root, 20))
            for name in ("compare_products", "get_evidence", "get_rating_distribution"):
                schema = registry.get_tool(name).to_openai_schema()["function"]["parameters"]
                self.assertEqual(schema["properties"]["product_ids"]["items"]["enum"], ["P1"])
            rejected = registry.execute_tool("compare_products", json.dumps({"product_ids": ["P", "1"]})).to_dict()
            self.assertEqual(rejected["status"], "error")
            accepted = registry.execute_tool("compare_products", json.dumps({"product_ids": ["P1"]})).to_dict()
            self.assertEqual(accepted["status"], "success")
            empty = create_review_registry(ReviewToolContext(connection, self.scope(source="EMPTY"), self.root, 20))
            products = empty.get_tool("compare_products").to_openai_schema()["function"]["parameters"]["properties"]["product_ids"]
            self.assertNotIn("enum", products["items"])
            self.assertEqual(products["maxItems"], 0)

    def test_trend_schema_requires_period_and_success_response_guides_final_references(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            registry = create_review_registry(ReviewToolContext(connection, self.scope(), self.root, 20))
            for name in ("get_trend", "get_aspect_trend"):
                schema = registry.get_tool(name).to_openai_schema()["function"]["parameters"]
                self.assertIn("period", schema["required"])
                self.assertEqual(schema["properties"]["period"]["enum"], ["day", "month"])
            response = registry.execute_tool("get_aspect_trend", '{"aspect":"packaging","period":"day"}').to_dict()
            self.assertEqual(response["data"]["parameters"]["period"], "day")
            final_value = json.loads(response["text"].split("直接返回 ", 1)[1])
            self.assertEqual(final_value, {"status": "ok", "result_ids": [response["data"]["result_id"]]})

    def test_query_is_separate_from_bound_business_context(self):
        question = "按日查看包装趋势"
        result, requests = self.run_script([tools(("get_aspect_trend", {"aspect": "packaging", "period": "day"})),
                                           final("ok", "r1")], question=question)
        self.assertEqual(result["status"], "ok")
        prompt = next(item["content"] for item in requests[0]["messages"] if item["role"] == "user")
        self.assertEqual(prompt.split("\n\n<bound_context>")[0], question)
        self.assertEqual(json.loads(prompt.split("<bound_context>\n")[1].split("\n</bound_context>")[0])["result_id"], "overview")

    def test_sentiment_intent_handles_positive_negative_and_correction(self):
        self.assertEqual(detect_sentiment_intent("美的空调好评主要集中在哪些方面？"), "positive")
        self.assertEqual(detect_sentiment_intent("哪些方面存在问题和投诉？"), "negative")
        self.assertEqual(detect_sentiment_intent("不是问差评，我想知道好评"), "positive")
        self.assertIsNone(detect_sentiment_intent("好评和差评分别是什么？"))
        self.assertIsNone(detect_sentiment_intent("按月查看包装趋势"))

    def test_positive_question_requires_positive_tool_and_uses_positive_answer(self):
        result, requests = self.run_script(
            [tools(("get_positive_aspects", {})), final("ok", "r1")],
            question="美的空调好评主要集中在哪些方面？",
        )
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["sentiment_intent"], "positive")
        self.assertEqual(result["analysis_intent"], "positive_aspects")
        self.assertEqual(result["results"]["r1"]["kind"], "positive_aspects")
        self.assertEqual(result["structured_analysis"][0]["result_id"], "r1")
        self.assertEqual(result["compact_context"]["source_result_ids"], ["r1"])
        self.assertEqual(result["public_analysis"]["status"], "ok")
        self.assertIn("正向维度统计", result["answer"])
        self.assertNotIn("负面占比", result["answer"])
        self.assertEqual(len(requests), 2)

    def test_positive_question_rejects_negative_tool_result(self):
        result, _ = self.run_script(
            [tools(("get_trend", {"period": "month"})), final("ok", "r1")],
            question="不是问差评，我想知道好评",
        )
        self.assertEqual(result["status"], "error", result)
        self.assertNotIn("answer", result)

    def test_model_timeout_after_successful_tools_preserves_only_diagnostics(self):
        result, requests = self.run_script(
            [tools(("get_positive_aspects", {})), httpx.ReadTimeout("simulated timeout")],
            question="好评主要集中在哪些方面？", settings=replace(SETTINGS, max_retries=0),
        )
        self.assertEqual(result["status"], "error", result)
        self.assertEqual(result["error"]["code"], "timeout")
        self.assertIn("r1", result["results"])
        self.assertNotIn("answer", result)
        self.assertEqual(result["query_executions"], 2)
        self.assertEqual(result["model_tool_attempts"], 1)
        self.assertEqual(len(requests), 2)

    def test_unlabeled_failed_empty_and_zero_negative_are_distinct(self):
        cases = [("2023-01-05", "P2", "unprocessed_reviews"),
                 ("2023-01-03", "P1", "failed_reviews"),
                 ("2023-01-04", "P1", "empty_text_reviews")]
        for day, product, count_field in cases:
            with self.subTest(count_field=count_field):
                result, _ = self.run_script([tools(("compare_products", {})), final("ok", "r1")],
                                           scope=self.scope(date_from=day, date_to=day, product_ids=[product]), question="商品对比")
                row = result["results"]["r1"]["data"]["products"][0]
                self.assertEqual(row[count_field], 1)
                self.assertIsNone(row["negative_rate"])
                self.assertEqual(result["validation"]["status"], "passed", result)
        result, _ = self.run_script([tools(("compare_products", {})), final("ok", "r1")],
                                   scope=self.scope(product_ids=["P3"]), question="商品对比")
        self.assertEqual(result["results"]["r1"]["data"]["products"][0]["negative_rate"], 0)
        self.assertIn("1 款可计算", result["answer"])
        self.assertLess(len(result["answer"]), 200)

    def test_annotation_version_and_no_reviews_are_not_silently_omitted(self):
        result, _ = self.run_script([tools(("compare_products", {})), final("ok", "r1")],
                                   scope=self.scope(model="another-annotation-version"), question="商品对比")
        self.assertEqual(result["results"]["overview"]["data"]["summary"]["successful_reviews"], 0)
        self.assertEqual(result["validation"]["status"], "passed", result)
        self.assertIn("3 款暂无成功标注", result["answer"])
        result, _ = self.run_script([tools(("compare_products", {})), final("ok", "r1")],
                                   scope=self.scope(date_from="2024-01-01", date_to="2024-01-02"), question="商品对比")
        self.assertEqual(result["results"]["r1"]["data"]["no_reviews_product_ids"], ["P1", "P2", "P3"])

    def test_report_rejects_wrong_numbers_coverage_quotes_and_omitted_references(self):
        result, _ = self.run_script([
            tools(("get_trend", {}), ("get_evidence", {"aspect": "packaging"}), ("get_top_issues", {})),
            final("ok", "r1", "r2", "r3")], question="查看整体趋势、包装原文、六维度主要问题")
        self.assertEqual(result["validation"]["status"], "passed", result)
        for path, value in [
            (("r1", "data", "summary", "negative_rate"), 0.99),
            (("r1", "data", "summary", "labeling_coverage"), 1),
            (("r1", "data", "summary", "successful_reviews"), 900),
            (("r2", "data", "items", 0, "opinions", 0, "quote"), "虚构引文"),
            (("r2", "data", "items", 0, "evidence_ref"), "sha256:fake#line=1"),
            (("r3", "data", "issues", 0, "negative_rate_change_pp"), 0),
        ]:
            with self.subTest(path=path):
                changed = deepcopy(result["results"])
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assertEqual(validate_report(changed, result["result_ids"])["status"], "failed")
        self.assertEqual(validate_report(result["results"], ["r1"])["status"], "failed")

    def test_human_effective_opinions_and_empty_evidence_are_preserved(self):
        self.save(opinions=[], expected_revision_id=None)
        result, _ = self.run_script([tools(("get_evidence", {"aspect": "packaging"})), final("ok", "r1")], question="包装原文")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["results"]["r1"]["data"]["total"], 0)
        self.assertIn("找到 0 条匹配证据", result["answer"])
        self.assertTrue(result["warnings"])

    def test_invalid_final_missing_results_and_exhausted_budget_do_not_claim_success(self):
        cases = [
            ([final("ok", "invented")], SETTINGS),
            ([tools(("get_trend", {}), ("compare_products", {})), final("ok", "r1")], SETTINGS),
            ([tools(("get_trend", {}))], replace(SETTINGS, max_steps=1)),
            ([tools(("get_trend", {})), final("ok", "overview")], replace(SETTINGS, max_tool_calls=1)),
            ([tools(("compare_products", {"product_ids": ["OUTSIDE"]}), ("get_trend", {})),
              final("ok", "r1")], SETTINGS),
        ]
        for messages, settings in cases:
            with self.subTest(messages=messages):
                result, _ = self.run_script(messages, settings=settings)
                self.assertEqual(result["status"], "error", result)
                self.assertNotIn("answer", result)
                self.assertLessEqual(result["model_calls"], settings.max_steps)

    def test_unsupported_and_format_correction_are_explicit(self):
        result, _ = self.run_script([final("unsupported", "overview")], question="删除所有评论")
        self.assertEqual(result["status"], "unsupported", result)
        result, _ = self.run_script([tools(("get_trend", {})), {"role": "assistant", "content": "不是 JSON"},
                                     final("ok", "r1")], question="整体趋势")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["tool_calls"], 2)

    def test_http_queue_worker_restore_and_frozen_engine_identity(self):
        init_monitoring(self.db)
        task_db = self.root / "tasks.db"
        init_tasks(task_db, self.db)
        options = {"import_root": self.root, "env_file": self.root / "unused.env"}
        configuration = {"LLM_MODEL_ID": SETTINGS.model, "LLM_API_KEY": SETTINGS.api_key,
                         "LLM_BASE_URL": SETTINGS.base_url, "LLM_MAX_RETRIES": "0"}
        app = create_app(self.db, task_db, embedded_worker=False, **options)
        requests = []
        with patch("data_analysis_agent.jobs.submission.read_settings", return_value=configuration), \
             TestClient(app, base_url="http://127.0.0.1") as client:
            body = {"kind": "analysis", "parameters": {**self.scope(), "question": "查询包装原文"}}
            response = client.post("/api/v1/tasks", json=body, headers={"Idempotency-Key": "agent-question"})
            self.assertEqual(response.status_code, 202, response.text)
            task = response.json()["result"]["task"]
            self.assertEqual(task["parameters"]["model_identity"]["prompt_version"], "review-agent-v5")
            repeated = client.post("/api/v1/tasks", json=body, headers={"Idempotency-Key": "agent-question"})
            self.assertTrue(repeated.json()["result"]["reused"])
            worker = TaskWorker(self.db, task_db, **options)
            worker_started(task_db, worker.worker_id)
            claimed = claim_task(task_db, worker.worker_id)
            with self.mock_model([tools(("get_evidence", {"aspect": "packaging"})), final("ok", "r1")], requests):
                worker._execute(claimed)
            restored = client.get("/api/v1/tasks/" + task["task_id"]).json()["result"]
            self.assertEqual(restored["status"], "succeeded", restored)
            self.assertTrue(restored["result"]["agent"])
            self.assertNotIn("run_id", restored["result"])
            with closing(connect_review_database(self.db, readonly=True)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_analysis_runs").fetchone()[0], 0)
            changed = {**configuration, "LLM_MODEL_ID": "changed-model"}
            with patch("data_analysis_agent.jobs.submission.read_settings", return_value=changed):
                task = client.post("/api/v1/tasks", json=body).json()["result"]["task"]
            worker._execute(claim_task(task_db, worker.worker_id))
            self.assertEqual(get_task(task_db, task["task_id"])["error"]["code"], "configuration_error")
            with self.assertRaises(TaskConflict):
                retry_task(task_db, task["task_id"], **options)
            invalid = {**body, "parameters": {**body["parameters"], "model_identity": {}}}
            self.assertEqual(client.post("/api/v1/tasks", json=invalid).status_code, 422)
            legacy = prepare_parameters("analysis", self.scope(), **options)
            self.assertNotIn("question", legacy)
            self.assertNotIn("model_identity", legacy)


if __name__ == "__main__":
    unittest.main()
