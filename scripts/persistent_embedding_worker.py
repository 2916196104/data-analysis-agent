"""Local query-only JSON-lines service; fixed BGE-M3, no downloads or DB access."""
import json
import os
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def serve(stream, output, load):
    model=None
    for line in stream:
        request={}
        try:
            request=json.loads(line)
            op=request['operation']
            if op=='initialize' and model is None:
                started=perf_counter();model=load(request['cache_dir'])
                response=dict(status='ready',model_load_ms=(perf_counter()-started)*1000)
            elif op=='health' and model is not None:response=dict(status='healthy')
            elif op=='encode' and model is not None:
                texts=request['texts']
                if not isinstance(texts,list) or len(texts)!=1 or not isinstance(texts[0],str) or not texts[0].strip() or len(texts[0])>4000:
                    raise ValueError('invalid_query')
                started=perf_counter();vectors=model.embed_texts(texts)
                response=dict(vectors=vectors,metrics=dict(encode_ms=(perf_counter()-started)*1000))
            else:raise ValueError('invalid_operation')
            response.update(request_id=request['request_id'],space_id=model.space.space_id)
            output.write(json.dumps(response,ensure_ascii=False,allow_nan=False)+'\n');output.flush()
        except Exception:
            output.write(json.dumps(dict(request_id=request.get('request_id'),error='local_embedding_worker_failed'))+'\n')
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
