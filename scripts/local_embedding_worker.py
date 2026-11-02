"""一次作业一次加载；仅通过本地 stdin/stdout 传递语料/向量，无数据库凭据。"""

import json
import os
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TRANSFORMERS_OFFLINE='1')
    connections = 0

    def audit(event, args):
        nonlocal connections
        if event == 'socket.connect':
            connections += 1
            raise RuntimeError('network_disabled')
    sys.addaudithook(audit)
    try:
        request = json.load(sys.stdin)
        texts = request['texts']
        if (not isinstance(texts, list) or len(texts) > 300
                or any(not isinstance(t, str) or not t.strip() for t in texts)):
            raise ValueError('invalid_input')
        import psutil
        import torch
        from data_analysis_agent.reviews.embedding import LocalBgeM3Embedder
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        process = psutil.Process()
        cpu_before = sum(process.cpu_times()[:2])
        started = perf_counter()
        model = LocalBgeM3Embedder(request['cache_dir'], batch_size=8, max_input_tokens=512, device='cpu')
        load_ms = (perf_counter() - started) * 1000
        lengths = [len(model._tokenizer(t, truncation=False)['input_ids']) for t in texts]
        valid_indices = [i for i, n in enumerate(lengths) if n <= 512]
        started = perf_counter()
        encoded = model.embed_texts([texts[i] for i in valid_indices])
        encode_ms = (perf_counter() - started) * 1000
        vectors = [None] * len(texts)
        for i, vector in zip(valid_indices, encoded):
            vectors[i] = vector
        memory = process.memory_info()
        result = dict(space_id=model.space.space_id, vectors=vectors, metrics=dict(
            model_load_ms=round(load_ms, 2), encode_ms=round(encode_ms, 2),
            token_counts=lengths, batch_size=8, threads=4, device='cpu',
            cpu_seconds=round(sum(process.cpu_times()[:2]) - cpu_before, 2),
            peak_rss_bytes=getattr(memory, 'peak_wset', memory.rss),
            outbound_connection_attempts=connections))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    except Exception:
        # 不输出异常消息、堆栈、输入内容或路径中的凭据。
        print('{"error":"local_embedding_worker_failed"}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
