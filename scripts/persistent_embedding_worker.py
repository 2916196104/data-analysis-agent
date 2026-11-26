"""Local query-only JSON-lines service; fixed BGE-M3, no downloads or DB access."""
import json
import os
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

MAX_QUERY_BATCH = 8


class _RecoverableRequestError(ValueError):
    """单次查询输入非法或超长等可恢复错误，返回安全错误码但不杀死常驻模型进程。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def serve(stream, output, load):
    model = None
    for line in stream:
        request = {}
        try:
            request = json.loads(line)
            op = request['operation']
            if op == 'initialize' and model is None:
                started = perf_counter()
                model = load(request['cache_dir'])
                response = dict(status='ready', model_load_ms=(perf_counter() - started) * 1000)
            elif op == 'health' and model is not None:
                response = dict(status='healthy')
            elif op == 'encode' and model is not None:
                texts = request['texts']
                if (not isinstance(texts, list) or not 1 <= len(texts) <= MAX_QUERY_BATCH
                        or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts)):
                    raise _RecoverableRequestError('invalid_query')
                started = perf_counter()
                try:
                    vectors = model.embed_texts(texts)
                except ValueError:
                    raise _RecoverableRequestError('query_input_rejected') from None
                response = dict(
                    vectors=vectors,
                    metrics=dict(encode_ms=(perf_counter() - started) * 1000, batch_size=len(texts)),
                )
            elif model is not None:
                raise _RecoverableRequestError('invalid_operation')
            else:
                raise ValueError('invalid_operation')
            response.update(request_id=request['request_id'], space_id=model.space.space_id)
            output.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
            output.flush()
        except _RecoverableRequestError as exc:
            output.write(json.dumps(dict(
                request_id=request.get('request_id'),
                error=exc.code,
                recoverable=True,
            )) + '\n')
            output.flush()
            continue
        except Exception:
            output.write(json.dumps(dict(request_id=request.get('request_id'), error='local_embedding_worker_failed')) + '\n')
            output.flush()
            return 1
    return 0

def main():
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    def audit(event,args):
        if event=='socket.connect':raise RuntimeError('network_disabled')
    sys.addaudithook(audit)
    def load(cache):
        import torch
        from data_analysis_agent.reviews.embedding import LocalBgeM3Embedder
        torch.set_num_threads(4);torch.set_num_interop_threads(1)
        return LocalBgeM3Embedder(cache,batch_size=8,max_input_tokens=512,device='cpu')
    return serve(sys.stdin,sys.stdout,load)

if __name__=='__main__':raise SystemExit(main())
