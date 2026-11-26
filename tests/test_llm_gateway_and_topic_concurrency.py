"""统一 LLM Gateway、原生 Agent Runtime（去 hello-agents 私有属性注入）与 review_topics 并发批处理专项测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI as RealOpenAI

from data_analysis_agent.agents import review_topics as topics
from data_analysis_agent.agents import runtime
from data_analysis_agent.core.config import AgentSettings, ConfigurationError
from data_analysis_agent.core.llm_gateway import (
    chat_completion,
    close_llm_clients,
    llm_gateway_stats,
    open_llm_client,
    provider_extra_body,
    redact_secret,
    reset_llm_gateway_stats,
    resolve_model,
)
from test_agent import FAKE_KEY, SETTINGS, completion, final, tools
from test_review_topic_budget import extracted


def _json_response(payload: dict, *, model: str = "offline-test-model", prompt_tokens: int = 12, completion_tokens: int = 8):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-gateway-test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


class LLMGatewayTests(unittest.TestCase):
    def setUp(self):
        close_llm_clients()
        reset_llm_gateway_stats()
        self.addCleanup(close_llm_clients)
        self.addCleanup(reset_llm_gateway_stats)
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("no real network")))

    def test_lazy_import_boundary_does_not_load_openai_or_httpx(self):
        code = (
            "import sys; "
            "import data_analysis_agent.core.llm_gateway; "
            "import data_analysis_agent.agents.runtime; "
            "print('openai' in sys.modules, 'httpx' in sys.modules, 'hello_agents' in sys.modules)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        self.assertEqual(proc.stdout.strip(), "False False False")

    def test_role_model_routing_and_topic_concurrency_config_validation(self):
        base = {
            "LLM_MODEL_ID": "default-model",
            "LLM_API_KEY": FAKE_KEY,
            "LLM_BASE_URL": "https://offline.invalid/v1",
        }
        defaults = AgentSettings.from_values(base)
        self.assertEqual(defaults.topic_classification_concurrency, 3)
        for role in ("default", "chat", "intent", "answer", "topic", "planner"):
            self.assertEqual(resolve_model(defaults, role), "default-model")

        routed = AgentSettings.from_values(
            {
                **base,
                "AGENT_TOPIC_CONCURRENCY": "4",
                "LLM_CHAT_MODEL_ID": "chat-fast",
                "LLM_INTENT_MODEL_ID": "intent-mini",
                "LLM_ANSWER_MODEL_ID": "answer-pro",
                "LLM_TOPIC_MODEL_ID": "topic-batch",
                "LLM_PLANNER_MODEL_ID": "planner-strong",
            }
        )
        self.assertEqual(routed.topic_classification_concurrency, 4)
        self.assertEqual(resolve_model(routed, "chat"), "chat-fast")
        self.assertEqual(resolve_model(routed, "intent"), "intent-mini")
        self.assertEqual(resolve_model(routed, "answer"), "answer-pro")
        self.assertEqual(resolve_model(routed, "topic"), "topic-batch")
        self.assertEqual(resolve_model(routed, "planner"), "planner-strong")

        for bad in ("0", "5", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ConfigurationError):
                AgentSettings.from_values({**base, "AGENT_TOPIC_CONCURRENCY": bad})

    def test_provider_extra_body_centralizes_siliconflow_thinking_rules(self):
        sf_cn = replace(SETTINGS, base_url="https://api.siliconflow.cn/v1", model="Pro/deepseek-ai/DeepSeek-V3.2")
        sf_com = replace(SETTINGS, base_url="https://api.siliconflow.com/v1", model="Pro/deepseek-ai/DeepSeek-V3.2")
        sf_non_pro = replace(SETTINGS, base_url="https://api.siliconflow.cn/v1", model="deepseek-ai/DeepSeek-V3.2")
        other = replace(SETTINGS, base_url="https://other.invalid/v1", model="Pro/deepseek-ai/DeepSeek-V3.2")

        self.assertEqual(provider_extra_body(sf_cn, role="answer"), {"enable_thinking": False})
        self.assertEqual(provider_extra_body(sf_com, role="answer"), {})
        self.assertEqual(provider_extra_body(sf_non_pro, role="answer"), {})
        self.assertEqual(provider_extra_body(other, role="answer"), {})

        self.assertEqual(provider_extra_body(sf_cn, role="topic"), {"enable_thinking": False})
        self.assertEqual(provider_extra_body(sf_com, role="topic"), {"enable_thinking": False})
        self.assertEqual(provider_extra_body(sf_non_pro, role="topic"), {"enable_thinking": False})
        self.assertEqual(provider_extra_body(other, role="topic"), {})
        self.assertEqual(provider_extra_body(sf_cn, role="chat"), {})

    def test_pooled_http_client_reuse_key_isolation_options_and_telemetry(self):
        created_clients: list[httpx.Client] = []
        seen_bodies: list[dict] = []
        seen_auth: list[str] = []
        lock = threading.Lock()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            with lock:
                seen_bodies.append(body)
                seen_auth.append(request.headers.get("authorization", ""))
            return _json_response({"ok": True}, model=body["model"], prompt_tokens=10, completion_tokens=5)

        def make_http_client() -> httpx.Client:
            client = httpx.Client(transport=httpx.MockTransport(handler))
            created_clients.append(client)
            return client

        s1 = replace(SETTINGS, chat_model="chat-v1", topic_model="topic-v1")
        s2 = replace(SETTINGS, api_key="another-offline-secret-key")

        # 并发 6 次相同凭据调用必须共享同一个 Keep-Alive httpx.Client，且各自 timeout/max_retries 独立生效。
        def worker(i: int):
            role = "chat" if i % 2 == 0 else "topic"
            with open_llm_client(
                s1,
                timeout=10 + i,
                max_retries=i % 2,
                pooled=True,
                http_client_factory=make_http_client,
                pool_tag="unit-pool",
            ) as client:
                self.assertEqual(client.timeout, 10 + i)
                self.assertEqual(client.max_retries, i % 2)
                return chat_completion(
                    s1,
                    role=role,
                    messages=[{"role": "user", "content": f"ping-{i}"}],
                    client=client,
                )

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(6)))

        self.assertEqual(len(results), 6)
        self.assertEqual(len(created_clients), 1)
        self.assertFalse(created_clients[0].is_closed)
        self.assertEqual(sorted(b["model"] for b in seen_bodies), ["chat-v1"] * 3 + ["topic-v1"] * 3)

        # 不同 API Key 必须严格隔离为独立池条目。
        chat_completion(
            s2,
            role="intent",
            messages=[{"role": "user", "content": "other-key"}],
            pooled=True,
            http_client_factory=make_http_client,
            pool_tag="unit-pool",
        )
        self.assertEqual(len(created_clients), 2)
        self.assertTrue(any("another-offline-secret-key" in auth for auth in seen_auth))

        stats = llm_gateway_stats()
        self.assertEqual(stats["calls"], 7)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(stats["pool_misses"], 2)
        self.assertEqual(stats["pool_hits"], 5)
        self.assertEqual(stats["pooled_clients"], 2)
        self.assertEqual(stats["prompt_tokens"], 70)
        self.assertEqual(stats["completion_tokens"], 35)
        self.assertEqual(stats["total_tokens"], 105)
        self.assertEqual(stats["by_role"], {"chat": 3, "topic": 3, "intent": 1})
        self.assertNotIn(FAKE_KEY, json.dumps(stats))
        self.assertNotIn("another-offline-secret-key", json.dumps(stats))
        self.assertEqual(redact_secret(f"leak:{FAKE_KEY}", FAKE_KEY), "leak:[REDACTED]")

        closed = close_llm_clients()
        self.assertEqual(closed, 2)
        self.assertTrue(all(c.is_closed for c in created_clients))
        self.assertEqual(llm_gateway_stats()["pooled_clients"], 0)


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        close_llm_clients()
        self.addCleanup(close_llm_clients)
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("no real network")))

    def test_native_gateway_llm_uses_planner_model_without_private_adapter_hack(self):
        routed = replace(SETTINGS, planner_model="planner-routed-v2", max_steps=2)
        sent_models: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent_models.append(body["model"])
            return completion(final("ok", "overview"))

        def fake_openai(**kwargs):
            return httpx_openai(kwargs, handler)

        with patch("openai.OpenAI", side_effect=fake_openai):
            with runtime.create_llm(routed) as llm:
                self.assertFalse(hasattr(llm, "_adapter"))
                agent = runtime.build_agent(
                    routed,
                    context=None,
                    llm=llm,
                    system_prompt="sys",
                    tool_registry=_EmptyRegistry(),
                )
                steps = []
                text = agent.run("hello", on_step=lambda ev: steps.append(ev.data))
                self.assertEqual(json.loads(text), {"status": "ok", "result_ids": ["overview"]})
                self.assertEqual(sent_models, ["planner-routed-v2"])
                self.assertEqual(llm.budget.calls, 1)
                self.assertEqual(agent.last_run.status, "completed")
                self.assertEqual(agent.last_run.model_calls, 1)
                self.assertEqual(steps[0]["step"], 1)

                # 第二次调用正常消费第 2 次预算；第 3 次调用触发 BudgetExceededError -> call_limit
                agent.run("follow-up")
                self.assertEqual(llm.budget.calls, 2)
                with self.assertRaises(runtime.BudgetExceededError) as caught:
                    agent.run("exceed")
                self.assertEqual(runtime.execution_failure(caught.exception)[0], "call_limit")
                # 触发预算异常时当次 last_run 不应重复携带上一次的 model_calls / usage
                self.assertEqual(agent.last_run.model_calls, 0)
                self.assertEqual(agent.last_run.usage, {})


def httpx_openai(kwargs: dict, handler):
    return RealOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


class _EmptyRegistry:
    def get_all_tools(self):
        return []

    def execute_tool(self, name, arguments):
        raise AssertionError(f"unexpected tool {name}")


class ReviewTopicsConcurrencyTests(unittest.TestCase):
    def setUp(self):
        close_llm_clients()
        self.addCleanup(close_llm_clients)
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("no real network")))

    def _wire_classify(self, payload: dict) -> topics.TopicBatch:
        return topics.TopicBatch(
            assignments=[
                topics.TopicAssignment(
                    opinion_id=row["opinion_id"],
                    state="assigned",
                    matches=[topics.TopicMatch(key="t1", start=0, end=len(row["quote"]))],
                )
                for row in payload["opinions"]
            ]
        )

    def _wire_discover(self) -> topics.TopicCatalog:
        return topics.TopicCatalog(
            themes=[
                topics.TopicProposal(
                    key="t1",
                    name="配送迟延",
                    definition="买家明确抱怨发货或配送慢",
                    support_ids=["u1"],
                )
            ]
        )

    def test_independent_batches_execute_truly_in_parallel_and_merge_deterministically(self):
        # 60 条唯一负向观点 -> 3 个批次；Barrier(3) 要求 3 个批次必须真正同时进入 _call，串行必然超时失败。
        barrier = threading.Barrier(3, timeout=5.0)
        batch2_done = threading.Event()
        batch3_done = threading.Event()
        caller_thread = threading.current_thread()
        progress_threads: list[threading.Thread] = []
        progress_completed: list[int] = []
        classify_threads: set[int] = set()
        lock = threading.Lock()

        def fake_call(settings, prompt, payload, schema, timeout, **kwargs):
            if schema is topics.TopicCatalog:
                return self._wire_discover()
            with lock:
                classify_threads.add(threading.get_ident())
            first_id = payload["opinions"][0]["opinion_id"]
            # 所有 3 个批次同时到达这里才能继续，直接证明并发执行。
            barrier.wait()
            # 刻意反序完成：第 3 批最先完成 -> 第 2 批其次 -> 第 1 批最后，验证合并顺序仍然严格升序。
            if first_id == "u41":
                batch3_done.set()
            elif first_id == "u21":
                batch3_done.wait(timeout=5.0)
                batch2_done.set()
            elif first_id == "u1":
                batch2_done.wait(timeout=5.0)
            return self._wire_classify(payload)

        def on_progress(event):
            progress_threads.append(threading.current_thread())
            progress_completed.append(event["completed"])

        routed = replace(SETTINGS, topic_model="topic-fast-model", topic_classification_concurrency=3)
        with patch.object(topics, "_call", side_effect=fake_call):
            data = topics.analyze_aspect_topics(extracted(60), settings=routed, progress=on_progress)

        self.assertEqual(data["analysis_status"], "complete")
        self.assertEqual(data["grouping_model"], "topic-fast-model")
        self.assertEqual(data["model_calls"], 4)
        self.assertEqual(len(classify_threads), 3)
        self.assertEqual([row["opinion_id"] for row in data["assignments"]], [f"u{i}" for i in range(1, 61)])
        self.assertTrue(topics.validate_topic_result(data))
        # progress 回调始终在调用方主线程触发，保证 SQLite 任务进度写入线程安全。
        self.assertTrue(all(t is caller_thread for t in progress_threads))
        self.assertEqual(progress_completed[0], 0)
        self.assertEqual(progress_completed[-1], 60)

    def test_parallel_partial_failure_retains_verified_batches_in_deterministic_order(self):
        def fake_call(settings, prompt, payload, schema, timeout, **kwargs):
            if schema is topics.TopicCatalog:
                return self._wire_discover()
            if payload["opinions"][0]["opinion_id"] == "u21":
                raise TimeoutError("batch 2 timeout")
            return self._wire_classify(payload)

        with patch.object(topics, "_call", side_effect=fake_call):
            data = topics.analyze_aspect_topics(
                extracted(60),
                settings=replace(SETTINGS, topic_classification_concurrency=3),
            )

        self.assertEqual(data["analysis_status"], "partial")
        self.assertEqual(data["model_calls"], 4)
        self.assertEqual([f["batch"] for f in data["failures"]], [2])
        self.assertEqual(data["failures"][0]["code"], "timeout")
        expected_ids = [f"u{i}" for i in range(1, 21)] + [f"u{i}" for i in range(41, 61)]
        self.assertEqual([row["opinion_id"] for row in data["assignments"]], expected_ids)
        self.assertTrue(topics.validate_topic_result(data))

    def test_parallel_respects_max_model_calls_deterministically(self):
        dispatched_first_ids: list[str] = []
        lock = threading.Lock()

        def fake_call(settings, prompt, payload, schema, timeout, **kwargs):
            if schema is topics.TopicCatalog:
                return self._wire_discover()
            with lock:
                dispatched_first_ids.append(payload["opinions"][0]["opinion_id"])
            return self._wire_classify(payload)

        # MAX_MODEL_CALLS=3：发现占 1 次，仅允许前 2 批并发派发，第 3 批在主线程预占时确定性截断。
        with (
            patch.object(topics, "MAX_MODEL_CALLS", 3),
            patch.object(topics, "_call", side_effect=fake_call),
        ):
            data = topics.analyze_aspect_topics(
                extracted(60),
                settings=replace(SETTINGS, topic_classification_concurrency=3),
            )

        self.assertEqual(data["analysis_status"], "partial")
        self.assertEqual(data["model_calls"], 3)
        self.assertEqual(sorted(dispatched_first_ids), ["u1", "u21"])
        self.assertEqual([f["batch"] for f in data["failures"]], [3])
        self.assertEqual(data["failures"][0]["code"], "request_budget_exceeded")
        self.assertEqual(data["failures"][0]["timeout_seconds"], 0)
        self.assertEqual([row["opinion_id"] for row in data["assignments"]], [f"u{i}" for i in range(1, 41)])
        self.assertTrue(topics.validate_topic_result(data))

    def test_progress_interruption_stops_parallel_dispatch_and_propagates(self):
        class Interrupted(RuntimeError):
            pass

        calls = 0

        def fake_call(settings, prompt, payload, schema, timeout, **kwargs):
            nonlocal calls
            calls += 1
            if schema is topics.TopicCatalog:
                return self._wire_discover()
            return self._wire_classify(payload)

        def abort_on_second(event):
            if event["completed"] > 0:
                raise Interrupted("cancelled by user")

        with patch.object(topics, "_call", side_effect=fake_call):
            with self.assertRaises(Interrupted):
                topics.analyze_aspect_topics(
                    extracted(80),
                    settings=replace(SETTINGS, topic_classification_concurrency=2),
                    progress=abort_on_second,
                )
        # 并发窗口为 2：发现(1) + 首批并发窗口(2) 后即中断，不会继续提交第 4 批。
        self.assertLessEqual(calls, 4)

    def test_max_concurrency_one_forces_serial_on_caller_thread(self):
        caller = threading.get_ident()
        seen_threads: set[int] = set()

        def fake_call(settings, prompt, payload, schema, timeout, **kwargs):
            seen_threads.add(threading.get_ident())
            if schema is topics.TopicCatalog:
                return self._wire_discover()
            return self._wire_classify(payload)

        with patch.object(topics, "_call", side_effect=fake_call):
            data = topics.analyze_aspect_topics(
                extracted(40),
                settings=replace(SETTINGS, topic_classification_concurrency=3),
                max_concurrency=1,
            )

        self.assertEqual(data["analysis_status"], "complete")
        self.assertEqual(seen_threads, {caller})
        self.assertTrue(topics.validate_topic_result(data))


if __name__ == "__main__":
    unittest.main()
