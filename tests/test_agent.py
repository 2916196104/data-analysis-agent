"""Offline integration tests: real HelloAgents/SDK, scripted HTTP transport, no API calls."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import OpenAI

from data_analysis_agent.agent import run_agent_analysis
from data_analysis_agent.agent_tools import SalesToolContext, create_tool_registry
from data_analysis_agent.config import AgentSettings, ConfigurationError, read_settings, safe_json
from data_analysis_agent.data_loader import load_sales_csv

SAMPLE = Path(__file__).resolve().parents[1] / "data/samples/sales_mock.csv"
FAKE_KEY = "offline-test-key-not-a-real-credential"
SETTINGS = AgentSettings("offline-test-model", FAKE_KEY, "https://offline.invalid/v1")


def tools(*calls):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{index}_{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(calls)
    ]}


def final(status, *result_ids):
    return {"role": "assistant", "content": json.dumps({
        "status": status, "result_ids": list(result_ids),
    })}


def completion(message):
    return httpx.Response(200, json={
        "id": "chatcmpl-offline", "object": "chat.completion", "created": 0,
        "model": SETTINGS.model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


class AgentTests(unittest.TestCase):
    def run_script(self, root, messages, question="总销售额是多少？", settings=SETTINGS):
        requests = []
        sequence = iter(messages)

        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(str(request.url), "https://offline.invalid/v1/chat/completions")
            self.assertNotIn('"sales_amount": "120.50"', json.dumps(body))
            self.assertEqual({tool["function"]["name"] for tool in body.get("tools", [])},
                             {"inspect_dataset", "summarize_sales", "create_chart", "build_report"})
            requests.append(body)
            item = next(sequence)
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, httpx.Response) else completion(item)

        def client(**kwargs):
            self.assertEqual(kwargs["max_retries"], settings.max_retries)
            self.assertEqual(kwargs["timeout"], settings.timeout_seconds)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        with patch("openai.OpenAI", side_effect=client):
            result = run_agent_analysis(SAMPLE, question, root, settings=settings)
        return result, requests

    def test_configuration_and_secret_redaction(self):
        values = {"LLM_MODEL_ID": "test", "LLM_API_KEY": FAKE_KEY,
                  "LLM_BASE_URL": "https://offline.invalid/v1"}
        settings = AgentSettings.from_values(values)
        self.assertNotIn(FAKE_KEY, repr(settings))
        self.assertNotIn(FAKE_KEY, safe_json({"error": FAKE_KEY}, FAKE_KEY))
        escaped_secret = 'fake-"key\\value'
        self.assertNotIn(escaped_secret, json.loads(safe_json({"key": escaped_secret}, escaped_secret))["key"])
        self.assertEqual(json.loads(safe_json({"count": 1, "status": "ok"}, "1")),
                         {"count": 1, "status": "ok"})
        for key, value in (("LLM_API_KEY", ""), ("LLM_BASE_URL", "https://user:secret@example.com"),
                           ("AGENT_MAX_STEPS", "0"), ("AGENT_MAX_TOOL_CALLS", "-1"),
                           ("LLM_MAX_RETRIES", "4"), ("LLM_TIMEOUT_SECONDS", "nan")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ConfigurationError):
                    AgentSettings.from_values({**values, key: value})
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("LLM_MODEL_ID=file-model\nLLM_API_KEY=literal-${NAME}\n", encoding="utf-8")
            with patch.dict(os.environ, {"LLM_MODEL_ID": "process-model"}):
                self.assertEqual(read_settings(env_file)["LLM_MODEL_ID"], "process-model")

    def test_tool_contract_and_result_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = SalesToolContext(load_sales_csv(SAMPLE),
                                       {"sha256": "test-hash", "name": SAMPLE.name}, root)
            registry = create_tool_registry(context)
            schema = registry.get_tool("create_chart").to_openai_schema()
            self.assertIn("result_id", schema["function"]["parameters"]["properties"])
            invalid = registry.execute_tool("create_chart", {"result_id": "../../outside"})
            self.assertEqual(invalid.to_dict()["status"], "error")
            self.assertFalse((root / "sales_by_product.png").exists())
            invalid = registry.execute_tool("summarize_sales", {"sales_amount": "999.00"})
            self.assertEqual(invalid.to_dict()["status"], "error")
            result = registry.execute_tool("summarize_sales", {})
            self.assertEqual(result.data["data"]["sales_by_product"]["Coffee"], "400.00")
            returned = context.get_result("sales_by_product")
            returned["data"]["sales_by_product"]["Coffee"] = "999.00"
            self.assertEqual(context.get_result("sales_by_product")["data"]["sales_by_product"]["Coffee"], "400.00")
            with self.assertRaises(ValueError):
                context.create_chart("overview")

    def test_three_acceptance_questions_through_real_framework(self):
        cases = [
            ("总销售额是多少？", [tools(("inspect_dataset", {})), final("ok", "overview")], "ok"),
            ("按商品汇总并生成图表和报告。", [
                tools(("inspect_dataset", {}), ("summarize_sales", {})),
                tools(("create_chart", {"result_id": "sales_by_product"})),
                tools(("build_report", {"result_id": "sales_by_product", "chart_id": "chart"})),
                final("ok", "sales_by_product", "chart", "report"),
            ], "ok"),
            ("计算利润率。", [tools(("inspect_dataset", {})), final("unsupported", "overview")], "unsupported"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for question, script, status in cases:
                with self.subTest(question=question):
                    result, requests = self.run_script(directory, script, question)
                    self.assertEqual(result["status"], status, result)
                    root = Path(result["run_dir"])
                    self.assertTrue((root / "request.json").exists())
                    self.assertTrue((root / "outcome.json").exists())
                    trace = [json.loads(line) for line in (root / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(len([event for event in trace if event["event"] == "tool_call"]), result["tool_calls"])
                    self.assertEqual(len(requests), result["model_calls"])
                    self.assertEqual(result["results"]["overview"]["data"]["overview"]["total_sales"], "900.00")
                    if "report" in result["results"]:
                        report = Path(result["results"]["report"]["data"]["report_path"])
                        self.assertIn("| Coffee | 400.00 |", report.read_text(encoding="utf-8"))
                        self.assertIn("计算依据", report.read_text(encoding="utf-8"))
                        self.assertTrue((root / "sales_by_product.png").read_bytes().startswith(b"\x89PNG"))
                    if status == "unsupported":
                        self.assertIn("成本", result["answer"])
                        self.assertFalse((root / "report.md").exists())
                    self.assertNotIn(FAKE_KEY, "".join(
                        path.read_text(encoding="utf-8") for path in root.rglob("*")
                        if path.suffix in {".json", ".jsonl", ".md"}
                    ))

    def test_missing_configuration_and_bad_data_never_call_model(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "data_analysis_agent.agent._new_llm", side_effect=AssertionError("Network must not be called")
        ):
            with patch.dict(os.environ, {name: "" for name in ("LLM_MODEL_ID", "LLM_API_KEY", "LLM_BASE_URL")}):
                result = run_agent_analysis(SAMPLE, "总销售额", directory,
                                            env_file=Path(directory) / "absent.env")
                self.assertEqual(result["error"]["code"], "configuration_error")
            result = run_agent_analysis(Path(directory) / "missing.csv", "总销售额", directory, settings=SETTINGS)
            self.assertEqual(result["error"]["code"], "file_not_found")
            bad = Path(directory) / "bad.csv"
            bad.write_text("date,product,sales_amount\n2026-10-01,A,-1\n", encoding="utf-8")
            result = run_agent_analysis(bad, "总销售额", directory, settings=SETTINGS)
            self.assertEqual(result["error"]["code"], "data_error")

    def test_transient_retry_and_timeout_have_finite_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            retry = httpx.Response(500, headers={"retry-after-ms": "1"}, json={"error": {"message": "temporary"}})
            result, requests = self.run_script(directory, [
                retry, tools(("inspect_dataset", {})), final("ok", "overview"),
            ])
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(len(requests), 3)
            result, requests = self.run_script(directory, [httpx.ReadTimeout(FAKE_KEY), httpx.ReadTimeout(FAKE_KEY)])
            self.assertEqual(len(requests), 2)
            self.assertEqual(result["error"]["code"], "timeout", result)
            self.assertNotIn(FAKE_KEY, (Path(result["run_dir"]) / "outcome.json").read_text(encoding="utf-8"))

    def test_authentication_failure_does_not_leak_key_or_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            result, requests = self.run_script(directory, [httpx.Response(401, json={
                "error": {"message": f"bad credential: {FAKE_KEY}", "type": "invalid_api_key"},
            })])
            self.assertEqual(len(requests), 1)
            self.assertEqual(result["error"]["code"], "authentication_error", result)
            self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_model_cannot_supply_statistics_or_unknown_result_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            for message in (final("ok", "forged"), {"role": "assistant", "content": json.dumps({
                "status": "ok", "result_ids": ["overview"], "total_sales": "999.00",
            })}):
                with self.subTest(message=message):
                    result, _ = self.run_script(directory, [tools(("inspect_dataset", {})), message])
                    self.assertEqual(result["error"]["code"], "agent_error", result)

    def test_model_and_tool_limits_are_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result, requests = self.run_script(directory, [tools(("inspect_dataset", {}))] * 2,
                                               settings=replace(SETTINGS, max_steps=2))
            self.assertEqual(len(requests), 2)
            self.assertEqual(result["error"]["code"], "call_limit", result)
            result, _ = self.run_script(directory, [
                tools(("inspect_dataset", {}), ("summarize_sales", {})), final("ok", "overview"),
            ], settings=replace(SETTINGS, max_tool_calls=1))
            self.assertEqual(result["error"]["code"], "agent_error", result)
            self.assertNotIn("sales_by_product", result["results"])

    def test_cli_configuration_error_is_json_with_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8",
                   **{name: "" for name in ("LLM_MODEL_ID", "LLM_API_KEY", "LLM_BASE_URL")}}
            result = subprocess.run([
                sys.executable, "-m", "data_analysis_agent.agent_cli", "--input", str(SAMPLE),
                "--question", "总销售额", "--output-dir", directory,
                "--env-file", str(Path(directory) / "absent.env"),
            ], env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "configuration_error")
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
