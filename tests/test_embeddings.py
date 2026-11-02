"""接口防护使用模拟本地编码器；真实模型效果另由评测脚本执行。"""

from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from data_analysis_agent.reviews.embedding import (
    EmbeddingError, EmbeddingSpace, LocalBgeM3Embedder, MODEL_NAME, MODEL_REVISION,
)
from scripts.evaluate_embeddings import evaluate_retrieval, retrieval_metrics, variant_text


class EmbeddingContractTests(unittest.TestCase):
    def test_space_identity_changes_with_model_revision_dimension_and_normalization(self):
        space = EmbeddingSpace(MODEL_NAME, MODEL_REVISION, 1024)
        self.assertEqual(space.space_id, replace(space).space_id)
        for changes in ({"model_name": "other"}, {"model_revision": "other"}, {"dimension": 768},
                        {"normalization": "none"}, {"max_input_tokens": 1024}, {"pooling": "mean"}):
            self.assertNotEqual(space.space_id, replace(space, **changes).space_id)

    def test_invalid_settings_fail_before_model_load(self):
        for options in ({"batch_size": 0}, {"batch_size": True}, {"max_input_tokens": 1},
                        {"max_input_tokens": 8193}, {"device": "remote"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                LocalBgeM3Embedder("missing", **options)

    def test_relevance_metrics_have_hand_checkable_denominators(self):
        values = retrieval_metrics(["a", "x", "b", "c"], {"a": 2, "b": 2, "c": 1})
        self.assertEqual(values["recall_at_1"], 0.5)
        self.assertEqual(values["recall_at_3"], 1)
        self.assertAlmostEqual(values["precision_at_3"], 2 / 3)
        self.assertEqual(values["mrr"], 1)
        self.assertLess(values["ndcg_at_5"], 1)

    def test_float32_near_ties_are_not_semantic_distinctions(self):
        import numpy as np

        docs = [{"id": key, "aspect": "service", "sentiment": "negative"} for key in ("a", "b")]
        query = {"id": "q", "text": "测试", "aspect": "service", "sentiment": "negative", "qrels": {"a": 2}}
        result = evaluate_retrieval(docs, [query], np.array([[1.0], [1.0000001]], dtype=np.float32),
                                    np.ones((1, 1), dtype=np.float32))
        self.assertEqual(result["queries"][0]["top_score_ties"], 2)

    def test_fixture_and_context_are_fixed_before_model_scoring(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/rag_embedding_cases.json").read_text(encoding="utf-8"))
        docs = {row["id"]: row for row in fixture["documents"]}
        self.assertEqual(len(docs), len(fixture["documents"]))
        for row in docs.values():
            self.assertIn(row["quote"], row["context"])
            self.assertEqual(variant_text(row, "B"), row["quote"])
            self.assertIn(row["quote"], variant_text(row, "C"))
        for query in fixture["queries"]:
            if query.get("ambiguous"):
                self.assertEqual(query["qrels"], {})
            else:
                self.assertIn(2, query["qrels"].values())
                for key, grade in query["qrels"].items():
                    self.assertIn(grade, (1, 2))
                    self.assertEqual((docs[key]["aspect"], docs[key]["sentiment"]),
                                     (query["aspect"], query["sentiment"]))


@unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("transformers"),
                     "requires isolated embedding evaluation dependencies")
class LocalEncoderTests(unittest.TestCase):
    def setUp(self):
        import torch

        self.torch, self.tokenized = torch, []

        def tokenize(texts, **kwargs):
            self.tokenized.extend(texts)
            return {"input_ids": torch.tensor([[len(text), 1] for text in texts])}

        def forward(**tokens):
            ids = tokens["input_ids"]
            vectors = torch.zeros((len(ids), 1, 1024))
            vectors[:, 0, 0], vectors[:, 0, 1] = ids[:, 0], 1
            return SimpleNamespace(last_hidden_state=vectors)

        self.model = Mock(side_effect=forward)
        self.model.to.return_value = self.model
        self.model.eval.return_value = self.model
        config = SimpleNamespace(model_type="xlm-roberta", hidden_size=1024)
        factories = [patch("transformers.AutoConfig.from_pretrained", return_value=config),
                     patch("transformers.AutoTokenizer.from_pretrained", return_value=tokenize),
                     patch("transformers.AutoModel.from_pretrained", return_value=self.model)]
        self.factories = [factory.start() for factory in factories]
        for factory in factories:
            self.addCleanup(factory.stop)
        self.encoder = LocalBgeM3Embedder("isolated-cache", batch_size=2)

    def test_loading_is_local_pinned_and_does_not_trust_remote_code(self):
        for factory in self.factories:
            self.assertEqual(factory.call_args.args[0], MODEL_NAME)
            kwargs = factory.call_args.kwargs
            self.assertEqual(kwargs["revision"], MODEL_REVISION)
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])
            self.assertFalse(kwargs["token"])

    def test_batch_order_and_raw_input_are_preserved(self):
        texts = [" 好用\r\n", "很慢", "不错", "太差了", "e\u0301"]
        vectors = self.encoder.embed_texts(texts)
        self.assertEqual(self.tokenized, texts)
        self.assertEqual(self.model.call_count, 3)
        for text, vector in zip(texts, vectors):
            self.assertEqual(len(vector), 1024)
            self.assertAlmostEqual(vector[0], len(text) / math.sqrt(len(text) ** 2 + 1), places=6)
            self.assertAlmostEqual(sum(value * value for value in vector), 1, places=6)

    def test_invalid_inputs_and_empty_list_do_not_run_inference(self):
        for value in ("hello", ("hello",), [None], [1], [""], ["  "], ["\ud800"]):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.encoder.embed_texts(value)
        self.assertEqual(self.encoder.embed_texts([]), [])
        self.model.assert_not_called()

    def test_overlength_is_rejected_without_truncation(self):
        self.encoder._tokenizer = Mock(return_value={"input_ids": self.torch.ones((1, 513), dtype=self.torch.long)})
        with self.assertRaisesRegex(ValueError, "截断"):
            self.encoder.embed_texts(["超长合成文本"])
        self.model.assert_not_called()

    def test_nan_inf_zero_and_wrong_dimension_are_rejected(self):
        for kind in ("nan", "inf", "zero", "shape"):
            with self.subTest(kind=kind):
                vector = self.torch.ones((1, 1, 3 if kind == "shape" else 1024))
                if kind == "zero":
                    vector.zero_()
                elif kind in ("nan", "inf"):
                    vector[0, 0, 0] = float(kind)
                self.model.side_effect = None
                self.model.return_value = SimpleNamespace(last_hidden_state=vector)
                with self.assertRaises(EmbeddingError):
                    self.encoder.embed_texts(["好用"])

    def test_inference_failure_is_explicit_and_does_not_echo_input(self):
        self.model.side_effect = RuntimeError("private-synthetic-marker")
        with self.assertRaises(EmbeddingError) as caught:
            self.encoder.embed_texts(["private-synthetic-marker"])
        self.assertNotIn("private-synthetic-marker", str(caught.exception))

    def test_wrong_model_configuration_cannot_enter_this_space(self):
        self.factories[0].return_value = SimpleNamespace(model_type="other", hidden_size=1024)
        with self.assertRaisesRegex(EmbeddingError, "不一致"):
            LocalBgeM3Embedder("isolated-cache")


if __name__ == "__main__":
    unittest.main()
