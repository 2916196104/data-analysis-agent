"""基础聊天接口离线验收：真实 SDK + 模拟传输，数据库仅使用临时文件。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI

from data_analysis_agent.agents.chat import SYSTEM_PROMPT
from data_analysis_agent.api.app import create_app
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import init_database


VALUES = {"LLM_MODEL_ID": "SIMULATED-chat", "LLM_API_KEY": "SIMULATED-secret",
          "LLM_BASE_URL": "https://offline.invalid/v1", "LLM_TIMEOUT_SECONDS": "2",
          "LLM_MAX_RETRIES": "0"}


def completion(content="这是离线测试回答。", *, finish_reason="stop", **message):
    return {"id": "simulated-chat", "object": "chat.completion", "created": 1,
            "model": VALUES["LLM_MODEL_ID"], "choices": [{"index": 0, "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content, **message}}]}


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.task_db = self.root / "reviews.db", self.root / "tasks.db"
        init_database(self.db)
        init_labeling(self.db)
        init_workbench(self.db)
        init_monitoring(self.db)
        init_tasks(self.task_db, self.db)
        # 不允许开发机的真实模型环境变量覆盖测试用的独立 env-file。
        self.enterContext(patch.dict(os.environ))
        for name in (*VALUES, "AGENT_MAX_STEPS", "AGENT_MAX_TOOL_CALLS"):
            os.environ.pop(name, None)
        self.env_file = self.root / "chat.env"
        self.write_settings(self.env_file, VALUES)
        self.client = self.enterContext(TestClient(self.app(self.env_file), base_url="http://127.0.0.1",
                                                   raise_server_exceptions=False))
        self.requests, self.client_options, self.clients = [], [], []

    def write_settings(self, path, values):
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")

    def app(self, env_file):
        return create_app(self.db, self.task_db, import_root=self.root,
                          env_file=env_file, embedded_worker=False)

    def mock_model(self, handler):
        def transport(request):
            self.requests.append(json.loads(request.content))
            return handler(request)

        def client(**kwargs):
            self.client_options.append(kwargs)
            sdk = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            self.clients.append(sdk)
            return sdk

        return patch("openai.OpenAI", side_effect=client)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertNotIn("result", response.json())
        self.assertNotIn(VALUES["LLM_API_KEY"], response.text)
        self.assertNotIn("PRIVATE-provider-error", response.text)

    def test_chat_uses_bound_settings_without_scope_tools_or_database_writes(self):
        before = [path.read_bytes() for path in (self.db, self.task_db)]
        with self.mock_model(lambda request: httpx.Response(200, json=completion())):
            response = self.client.post("/api/v1/chat", json={"message": "  你能做什么？  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "ok", "result": {"answer": "这是离线测试回答。"}})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.requests, [{"model": VALUES["LLM_MODEL_ID"], "messages": [
            {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "你能做什么？"}]}])
        self.assertEqual(self.client_options, [{"api_key": VALUES["LLM_API_KEY"],
            "base_url": VALUES["LLM_BASE_URL"], "timeout": 2, "max_retries": 0}])
        self.assertTrue(self.clients[0].is_closed())
        self.assertEqual([path.read_bytes() for path in (self.db, self.task_db)], before)

    def test_invalid_or_extra_inputs_are_rejected_before_model_call(self):
        bodies = [{}, {"message": ""}, {"message": " \n\t"}, {"message": "问" * 4001},
                  {"message": None}, {"message": 123}, {"message": ["问题"]}]
        bodies += [{"message": "问题", field: "untrusted"} for field in
                   ("api_key", "model", "base_url", "system_prompt", "messages", "product_id")]
        with patch("openai.OpenAI") as model:
            for body in bodies:
                with self.subTest(body=body):
                    self.assert_error(self.client.post("/api/v1/chat", json=body), 422, "validation_error")
            model.assert_not_called()

    def test_maximum_length_unicode_question_is_accepted(self):
        message = "问" * 4000
        with self.mock_model(lambda request: httpx.Response(200, json=completion())):
            response = self.client.post("/api/v1/chat", json={"message": message})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.requests[0]["messages"][1]["content"], message)

    def test_missing_configuration_does_not_call_model(self):
        self.env_file.write_text("", encoding="utf-8")
        with patch("openai.OpenAI") as model:
            self.assert_error(self.client.post("/api/v1/chat", json={"message": "你好"}),
                              503, "configuration_error")
            model.assert_not_called()

    def test_provider_errors_are_translated_without_leaking_secrets(self):
        for upstream, status, code in ((401, 503, "authentication_error"), (429, 503, "rate_limit"),
                                       (500, 502, "model_api_error"), (404, 502, "model_api_error")):
            with self.subTest(upstream=upstream), self.mock_model(lambda request: httpx.Response(
                    upstream, json={"error": {"message": "PRIVATE-provider-error " + VALUES["LLM_API_KEY"]}})):
                self.assert_error(self.client.post("/api/v1/chat", json={"message": "你好"}), status, code)
        self.assertEqual(len(self.requests), 4)
        self.assertTrue(all(client.is_closed() for client in self.clients))

    def test_timeouts_and_connection_errors_end_with_safe_error(self):
        for exception, status, code in ((httpx.ReadTimeout, 504, "model_timeout"),
                                        (httpx.ConnectError, 503, "model_connection_error")):
            def fail(request):
                raise exception("PRIVATE-provider-error " + VALUES["LLM_API_KEY"], request=request)

            with self.subTest(error=exception.__name__), self.mock_model(fail):
                self.assert_error(self.client.post("/api/v1/chat", json={"message": "你好"}), status, code)

    def test_empty_truncated_malformed_and_tool_responses_are_not_success(self):
        payloads = [{"choices": []}, {"choices": {"invalid": True}}, {}, completion(None), completion(" \t"),
                    completion("未完成", finish_reason="length"),
                    completion(None, finish_reason="content_filter"),
                    completion("不能执行", tool_calls=[{"id": "call-1", "type": "function",
                        "function": {"name": "unregistered", "arguments": "{}"}}])]
        for payload in payloads:
            with self.subTest(payload=payload), self.mock_model(
                    lambda request: httpx.Response(200, json=payload)):
                self.assert_error(self.client.post("/api/v1/chat", json={"message": "你好"}),
                                  502, "invalid_model_response")

    def test_each_request_only_sends_its_own_question_and_redacts_key_in_answer(self):
        with self.mock_model(lambda request: httpx.Response(200, json=completion(VALUES["LLM_API_KEY"]))):
            for question in ("第一个问题", "第二个问题"):
                response = self.client.post("/api/v1/chat", json={"message": question})
                self.assertEqual(response.json()["result"]["answer"], "[REDACTED]")
        self.assertEqual([request["messages"][1]["content"] for request in self.requests],
                         ["第一个问题", "第二个问题"])
        self.assertTrue(all(len(request["messages"]) == 2 for request in self.requests))

    def test_app_instances_keep_their_own_env_file(self):
        other_env = self.root / "other.env"
        self.write_settings(other_env, {**VALUES, "LLM_MODEL_ID": "SIMULATED-other"})
        with TestClient(self.app(other_env), base_url="http://127.0.0.1") as other, self.mock_model(
                lambda request: httpx.Response(200, json=completion())):
            self.assertEqual(other.post("/api/v1/chat", json={"message": "你好"}).status_code, 200)
            self.assertEqual(self.client.post("/api/v1/chat", json={"message": "你好"}).status_code, 200)
        self.assertEqual([request["model"] for request in self.requests],
                         ["SIMULATED-other", VALUES["LLM_MODEL_ID"]])

    def test_existing_local_host_and_origin_restrictions_apply_to_chat(self):
        with patch("openai.OpenAI") as model:
            for headers, code in (({"Host": "untrusted.invalid"}, "host_not_allowed"),
                                  ({"Origin": "https://untrusted.invalid"}, "origin_not_allowed")):
                with self.subTest(headers=headers):
                    self.assert_error(self.client.post("/api/v1/chat", json={"message": "你好"},
                                                       headers=headers), 403, code)
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
