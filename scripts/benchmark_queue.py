"""只在隔离测试库中进行模拟模型基准；输出评论数、请求数和 RSS 时间序列。"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from psycopg import sql
from openai import OpenAI
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.jobs import label_queue as store
from data_analysis_agent.jobs.label_limiter import GlobalLimiter
from data_analysis_agent.jobs.label_metrics import run_metrics
from data_analysis_agent.jobs.label_stream import LabelStream
from data_analysis_agent.jobs.label_worker import LabelWorker
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.store import init_database

ROOT = Path(__file__).resolve().parents[1]


def rss_mb():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.ws / 1024 / 1024


def run_case(admin, redis_url, count, worker_count, delay_ms):
    name = "agent_test_" + uuid.uuid4().hex
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    db = urlunsplit(urlsplit(admin)._replace(path="/" + name))
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream = LabelStream(redis, name="benchmark-labels:" + name, reclaim_ms=120000, max_backlog=1000)
    clients, workers, limiters = [], [], []
    try:
        init_database(db)
        init_labeling(db)
        store.init_queue(db)
        with store.queue_connection(db) as c, c:
            c.execute("""INSERT INTO import_runs(id,source,dataset_version,kind,file_path,file_sha256,started_at)
                VALUES (1,'SIMULATED','benchmark','reviews','SIMULATED','SIMULATED','2026-10-24T00:00:00+00:00')""")
            c.execute("INSERT INTO products(source,dataset_version,product_id) VALUES ('SIMULATED','benchmark','P1')")
            c.execute("""INSERT INTO reviews(id,source,dataset_version,source_record_id,product_id,asin,user_id,title,
                text,has_text,rating,published_at,fetched_at,raw_json,content_sha256,import_run_id,line_number)
                SELECT n,'SIMULATED','benchmark',n::text,'P1','P1',n::text,'',
                '质量很好，模拟评论 ' || n::text,1,5,'2026-10-24T00:00:00+00:00','2026-10-24T00:00:00+00:00',
                '{}',repeat(md5(n::text),2),1,n FROM generate_series(1,?) n""", (count,))
        settings = AgentSettings("SIMULATED", "not-a-real-key", "https://mock.invalid/v1", timeout_seconds=10)
        run_id = store.create_run(db, settings)
        lock = threading.Lock()
        active, peak, calls = 0, 0, 0
        def model(request):
            nonlocal active, peak, calls
            with lock:
                active += 1
                peak = max(peak, active)
                calls += 1
            try:
                time.sleep(delay_ms / 1000)
                user = json.loads(json.loads(request.content)["messages"][1]["content"])
                opinion = [{"aspect":"quality","sentiment":"positive","quote":"质量很好"}]
                data = ({"items":[{"review_id":item["review_id"],"opinions":opinion} for item in user["items"]]}
                        if "items" in user else {"opinions":opinion})
                return httpx.Response(200, json={"id":"SIMULATED","object":"chat.completion","created":0,"model":"SIMULATED",
                    "choices":[{"index":0,"message":{"role":"assistant","content":json.dumps(data)},"finish_reason":"stop"}],
                    "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}})
            finally:
                with lock:
                    active -= 1
        for _ in range(worker_count):
            client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(model)))
            clients.append(client)
            limiter = GlobalLimiter(redis, scope=name, rpm=1000000, tpm=10000000000,
                                    concurrency=2, cold_start_seconds=0)
            limiters.append(limiter)
            workers.append(LabelWorker(db, stream, settings, client=client, limiter=limiter, chunk_size=100, batch_size=4))
        started = time.perf_counter()
        samples = [{"elapsed_seconds":0, "rss_mb":rss_mb(), "successful_reviews":0}]
        last_report = started
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(w.run, run_id=run_id, max_seconds=3600) for w in workers]
            try:
                while not all(f.done() for f in futures):
                    time.sleep(1)
                    if any(f.done() and f.exception() for f in futures):
                        for w in workers:
                            w.stop.set()
                        break
                    if time.perf_counter() - last_report >= 10:
                        with store.queue_connection(db) as c:
                            completed = c.execute("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'").fetchone()[0]
                        sample = {"elapsed_seconds":time.perf_counter()-started, "rss_mb":rss_mb(),
                                  "successful_reviews":completed, "stream_entries":redis.xlen(stream.name)}
                        samples.append(sample)
                        print(json.dumps({"reviews":count,"workers":worker_count,**sample}), flush=True)
                        last_report = time.perf_counter()
                reasons = [f.result() for f in futures]
            finally:
                for w in workers:
                    w.stop.set()
        result = run_metrics(db, run_id, finish=True)
        samples.append({"elapsed_seconds":time.perf_counter()-started,"rss_mb":rss_mb(),"successful_reviews":result["successful_reviews"]})
        result.update(reviews=count, workers=worker_count, global_concurrency=2, batch_size=4, db_chunk_size=100,
                      mock_delay_ms=delay_ms, actual_mock_requests=calls, observed_max_concurrency=peak,
                      rss_samples=samples, peak_sampled_rss_mb=max(s["rss_mb"] for s in samples),
                      stop_reasons=reasons, real_model_requests=0, redis_version=redis.info("server")["redis_version"])
        if result["successful_reviews"] != count or calls != (count + 3) // 4 or peak > 2:
            raise AssertionError("benchmark did not complete cleanly: " + json.dumps(result))
        return result
    finally:
        for client in clients:
            client.close()
        redis.delete(stream.name)
        if limiters:
            redis.delete(*limiters[0].keys)
        redis.close()
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=int, required=True)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--mock-delay-ms", type=float, default=2)
    args = parser.parse_args()
    admin = os.environ.get("TEST_POSTGRES_ADMIN_URL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not admin or not redis_url or not 1 <= args.reviews <= 1000000 or any(w not in (1,2,4) for w in args.workers):
        parser.error("需要隔离 TEST_POSTGRES_ADMIN_URL、TEST_REDIS_URL 和有效的评论数/Worker 数。")
    output = ROOT / "outputs/queue-validation"
    output.mkdir(parents=True, exist_ok=True)
    for worker_count in args.workers:
        result = run_case(admin, redis_url, args.reviews, worker_count, args.mock_delay_ms)
        path = output / f"benchmark-{args.reviews}-{worker_count}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report":str(path), "successful_reviews":result["successful_reviews"],
                          "model_requests":result["model_requests"],"elapsed_seconds":result["elapsed_seconds"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
