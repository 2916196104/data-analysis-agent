"""仅用合成观点评测固定 BGE-M3；下载和离线推理分开执行。"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
from threading import Event, Thread
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_analysis_agent.reviews.embedding import (  # noqa: E402
    MODEL_FILES, MODEL_NAME, MODEL_REVISION, LocalBgeM3Embedder,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare_model(cache_dir, output):
    from huggingface_hub import snapshot_download

    started = perf_counter()
    snapshot = Path(snapshot_download(
        MODEL_NAME, revision=MODEL_REVISION, cache_dir=str(cache_dir),
        allow_patterns=list(MODEL_FILES), token=False, max_workers=2,
    ))
    download_seconds = perf_counter() - started
    files = {}
    for name in MODEL_FILES:
        path = snapshot / name
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files[name] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
    manifest = {"model": MODEL_NAME, "revision": MODEL_REVISION, "snapshot": str(snapshot),
                "download_seconds": download_seconds, "download_and_hash_seconds": perf_counter() - started,
                "observed_utc": datetime.now(timezone.utc).isoformat(), "files": files}
    write_json(output / "model-download.json", manifest)
    print(json.dumps({"model_prepared": True, "revision": MODEL_REVISION,
                      "download_seconds": download_seconds, "bytes": sum(row["bytes"] for row in files.values())}), flush=True)


class Measurement:
    def __init__(self, label):
        import psutil

        self.psutil, self.process = psutil, psutil.Process()
        self.result = {"phase": label}
        self.stop = Event()
        self.peak_rss = 0
        self.min_available = psutil.virtual_memory().available

    def sample(self):
        self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
        self.min_available = min(self.min_available, self.psutil.virtual_memory().available)

    def monitor(self):
        while not self.stop.wait(0.05):
            self.sample()

    def __enter__(self):
        self.sample()
        self.result["rss_before_bytes"] = self.process.memory_info().rss
        cpu = self.process.cpu_times()
        self.cpu_before = cpu.user + cpu.system
        self.started = perf_counter()
        self.thread = Thread(target=self.monitor, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        self.thread.join()
        self.sample()
        elapsed = perf_counter() - self.started
        cpu = self.process.cpu_times()
        cpu_seconds = cpu.user + cpu.system - self.cpu_before
        info = self.process.memory_info()
        self.result.update(seconds=elapsed, cpu_seconds=cpu_seconds,
                           cpu_percent_one_core=100 * cpu_seconds / elapsed,
                           cpu_percent_machine=100 * cpu_seconds / elapsed / self.psutil.cpu_count(),
                           rss_after_bytes=info.rss, peak_rss_bytes=self.peak_rss,
                           process_lifetime_peak_rss_bytes=getattr(info, "peak_wset", self.peak_rss),
                           min_system_available_bytes=self.min_available,
                           success=kind is None)


def measure(label, function, phases):
    measurement = Measurement(label)
    try:
        with measurement:
            return function()
    finally:
        phases.append(measurement.result)
        print(json.dumps({"phase": label, "seconds": measurement.result.get("seconds"),
                          "peak_rss_bytes": measurement.result.get("peak_rss_bytes")}), flush=True)


def variant_text(document, variant):
    quote, context = document["quote"], document["context"]
    return quote if variant == "B" or quote == context else quote + "\n上下文：" + context


def retrieval_metrics(ranked_ids, qrels):
    relevant = {key for key, grade in qrels.items() if grade == 2}
    result = {f"recall_at_{k}": len(relevant.intersection(ranked_ids[:k])) / len(relevant) for k in (1, 3, 5)}
    result["precision_at_3"] = len(relevant.intersection(ranked_ids[:3])) / 3
    result["mrr"] = next((1 / index for index, key in enumerate(ranked_ids, 1) if key in relevant), 0.0)
    dcg = sum((2 ** qrels.get(key, 0) - 1) / math.log2(index + 2) for index, key in enumerate(ranked_ids[:5]))
    ideal = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(sorted(qrels.values(), reverse=True)[:5]))
    result["ndcg_at_5"] = dcg / ideal if ideal else 0.0
    return result


def evaluate_retrieval(documents, queries, document_vectors, query_vectors):
    import numpy as np

    scores = query_vectors @ document_vectors.T
    results = []
    for index, query in enumerate(queries):
        ordered = sorted(range(len(documents)), key=lambda position: (-float(scores[index, position]), documents[position]["id"]))
        ranked = [documents[position]["id"] for position in ordered]
        row = {"query_id": query["id"], "query": query["text"], "ambiguous": query.get("ambiguous", False),
               "ranking": [{"id": documents[position]["id"], "score": float(scores[index, position]),
                            "grade": query["qrels"].get(documents[position]["id"], 0)} for position in ordered]}
        # 同文跨批次可能相差约 1e-7，不能把浮点舍入当作语义区分。
        row["top_score_ties"] = int(np.isclose(scores[index], scores[index, ordered[0]], atol=1e-6, rtol=0).sum())
        if not row["ambiguous"]:
            row["metrics"] = retrieval_metrics(ranked, query["qrels"])
            top = [documents[position] for position in ordered[:3]]
            row["cross_aspect_at_3"] = sum(doc["aspect"] != query["aspect"] for doc in top) / 3
            row["opposite_sentiment_at_3"] = sum(doc["sentiment"] != query["sentiment"] for doc in top) / 3
            filtered = [documents[position]["id"] for position in ordered if
                        documents[position]["aspect"] == query["aspect"] and documents[position]["sentiment"] == query["sentiment"]]
            row["filtered_metrics"] = retrieval_metrics(filtered, query["qrels"])
        results.append(row)
    judged = [row for row in results if not row["ambiguous"]]
    mean = lambda key: {name: statistics.mean(row[key][name] for row in judged) for name in judged[0][key]}
    return {"queries": results, "macro_metrics": mean("metrics"), "filtered_macro_metrics": mean("filtered_metrics"),
            "cross_aspect_at_3": statistics.mean(row["cross_aspect_at_3"] for row in judged),
            "opposite_sentiment_at_3": statistics.mean(row["opposite_sentiment_at_3"] for row in judged)}


def validate_vectors(values, count, dimension):
    import numpy as np

    array = np.asarray(values, dtype=np.float32)
    if array.shape != (count, dimension) or not np.isfinite(array).all():
        raise RuntimeError("向量形状或有限值校验失败。")
    norms = np.linalg.norm(array, axis=1)
    if (norms <= 1e-12).any() or not np.allclose(norms, 1, atol=1e-5):
        raise RuntimeError("零向量或归一化校验失败。")
    return array


def evaluate(args, output):
    import numpy as np
    import psutil

    result = {"observed_utc": datetime.now(timezone.utc).isoformat(), "phases": [], "near_tie_atol": 1e-6,
              "fixture_sha256": sha256(args.fixture.read_bytes()).hexdigest(), "failures": []}
    phases = result["phases"]
    connections = []

    def refuse_network(event, arguments):
        if event == "socket.connect":
            connections.append(event)
            raise RuntimeError("离线评测禁止网络连接。")

    sys.addaudithook(refuse_network)
    try:
        torch = measure("torch_import", lambda: __import__("torch"), phases)
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        result["environment"] = {
            "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "cpu_logical": psutil.cpu_count(), "cpu_physical": psutil.cpu_count(logical=False),
            "memory_total_bytes": psutil.virtual_memory().total,
            "memory_available_bytes": psutil.virtual_memory().available,
            "threads": torch.get_num_threads(), "cuda_available": torch.cuda.is_available(),
            "xpu_available": bool(hasattr(torch, "xpu") and torch.xpu.is_available()),
            "packages": {name: importlib.metadata.version(name) for name in
                         ("torch", "transformers", "numpy", "psutil", "huggingface-hub", "tokenizers")},
        }
        encoder = measure("first_model_load", lambda: LocalBgeM3Embedder(
            args.cache_dir, batch_size=8, device="cpu"), phases)
        result["embedding_space"] = {**asdict(encoder.space), "space_id": encoder.space.space_id}
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        documents, queries = fixture["documents"], fixture["queries"]
        result.update(fixture_version=fixture["schema_version"], relevance_policy=fixture["policy"],
                      document_count=len(documents), scored_query_count=sum(not row.get("ambiguous") for row in queries),
                      ambiguous_query_count=sum(bool(row.get("ambiguous")) for row in queries))
        measure("first_single_encode", lambda: encoder.embed_texts([queries[0]["text"]]), phases)
        measure("warmup", lambda: encoder.embed_texts([row["quote"] for row in documents[:8]]), phases)
        single = [measure(f"single_{i}", lambda: encoder.embed_texts([queries[0]["text"]]), phases) for i in range(5)]
        assert all(len(value[0]) == encoder.space.dimension for value in single)
        query_vectors = validate_vectors(measure("queries", lambda: encoder.embed_texts([q["text"] for q in queries]), phases), len(queries), encoder.space.dimension)
        result["variants"], matrices = {}, {}
        for variant in ("B", "C"):
            texts = [variant_text(doc, variant) for doc in documents]
            values = measure(f"semantic_{variant}", lambda: encoder.embed_texts(texts), phases)
            matrices[variant] = validate_vectors(values, len(texts), encoder.space.dimension)
            tokens = encoder._tokenizer(texts, padding=False, truncation=False)["input_ids"]
            result["variants"][variant] = {
                "input_version": "quote-utf8-v1" if variant == "B" else "synthetic-quote-context-v1",
                "total_chars": sum(map(len, texts)), "mean_chars": statistics.mean(map(len, texts)),
                "total_tokens": sum(map(len, tokens)), "max_tokens": max(map(len, tokens)),
                "input_sha256": [sha256(text.encode("utf-8")).hexdigest() for text in texts],
                **evaluate_retrieval(documents, queries, matrices[variant], query_vectors),
            }
        result["validation"] = {"dimension": encoder.space.dimension, "finite": True, "nonzero": True,
                                "normalized": True, "order_and_batch_consistency": True}
        benchmark_texts = [documents[index % len(documents)]["quote"] for index in range(args.benchmark_texts)]
        result["benchmark"] = []
        reference = None
        for size in args.batch_sizes:
            encoder.batch_size = size
            times, max_difference = [], 0.0
            for repeat in range(args.repeats):
                values = measure(f"batch_{size}_repeat_{repeat}", lambda: encoder.embed_texts(benchmark_texts), phases)
                values = validate_vectors(values, len(benchmark_texts), encoder.space.dimension)
                if reference is None:
                    reference = values
                max_difference = max(max_difference, float(np.max(np.abs(reference - values))))
                if not np.allclose(reference, values, atol=1e-5, rtol=1e-5):
                    raise RuntimeError("输入顺序或不同 batch_size 输出一致性校验失败。")
                times.append(phases[-1]["seconds"])
            result["benchmark"].append({"batch_size": size, "texts": len(benchmark_texts),
                "repeats": args.repeats, "seconds": times, "median_seconds": statistics.median(times),
                "texts_per_second": len(benchmark_texts) / statistics.median(times), "max_abs_difference": max_difference})
        invalid = ["不是列表", [""], ["   "], [None], [1], ["\ud800"], ["测试" * 600]]
        rejected = 0
        for value in invalid:
            try:
                encoder.embed_texts(value)
            except ValueError:
                rejected += 1
        if rejected != len(invalid) or encoder.embed_texts([]) != []:
            raise RuntimeError("非法输入或空列表契约检查失败。")
        result["validation"]["invalid_inputs_rejected"] = rejected
        norms = np.linalg.norm(np.vstack([matrices["B"], matrices["C"], query_vectors]), axis=1)
        result["validation"].update(norm_min=float(norms.min()), norm_max=float(norms.max()),
                                     vectors_checked=len(norms))
        np.savez_compressed(output / "synthetic-vectors.npz", B=matrices["B"], C=matrices["C"], queries=query_vectors)
        result["status"] = "completed"
    except Exception as exc:
        result["status"] = "failed"
        result["failures"].append({"type": type(exc).__name__, "message": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "评测失败，参照异常类型排查。"})
    result["network_connection_attempts"] = len(connections)
    write_json(output / "evaluation.json", result)
    print(json.dumps({"status": result["status"], "output": str(output / "evaluation.json"),
                      "network_connection_attempts": len(connections)}, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="仅下载固定官方模型文件，不执行推理")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache/embedding-eval/hf/hub")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/rag-embedding")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/rag_embedding_cases.json")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 4, 8, 16])
    parser.add_argument("--benchmark-texts", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.threads < 1 or args.repeats < 1 or args.benchmark_texts < 1 or any(size < 1 for size in args.batch_sizes):
        parser.error("线程、重复次数、文本数和批大小必须为正整数。")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if args.prepare:
        prepare_model(args.cache_dir, args.output)
        return 0
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    return evaluate(args, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
