"""业务环境与独立模型环境之间的本地进程边界。"""

import json
import os
from pathlib import Path
import subprocess
import atexit
from queue import Queue, Empty
from threading import Lock, Thread, local
from time import monotonic
from uuid import uuid4

from data_analysis_agent.reviews.embedding_store import EmbeddingStoreError


class PersistentEmbeddingProcess:
    """Serial JSON-lines RPC. Fail closed on crash/timeout; no automatic restart."""
    def __init__(self, python, cache_dir, *, timeout=600, worker=None):
        from data_analysis_agent.reviews.embedding_store import SPACE
        if not 0 < timeout <= 600: raise ValueError('invalid_embedding_timeout')
        self.space = SPACE
        self.python, self.cache_dir = str(Path(python).resolve()), str(Path(cache_dir).resolve())
        self.worker = Path(worker) if worker else Path(__file__).resolve().parents[3]/'scripts/persistent_embedding_worker.py'
        self.timeout = timeout
        self.lock = Lock()
        self.process = None
        self.reader = None
        self.writer = None
        self.responses = Queue(maxsize=2)
        self.failed = self.closed = False
        self._metrics = local()

    @property
    def last_metrics(self):return getattr(self._metrics,'value',{})

    def _read(self, process):
        try:
            while True:
                line = process.stdout.readline(1024*1024)
                if not line or not line.endswith('\n'): break
                self.responses.put_nowait(json.loads(line))
        except Exception:
            pass
        finally:
            try:self.responses.put_nowait(None)
            except Exception:pass

    def _dispose(self):
        p = self.process
        if p is not None:
            if p.poll() is None: p.kill()
            p.wait(timeout=5)
            if self.reader is not None:self.reader.join(timeout=5)
            if self.writer is not None:self.writer.join(timeout=5)
            for pipe in (p.stdin,p.stdout):
                if pipe:pipe.close()
            self.process = None

    def _rpc(self, payload, deadline):
        request_id = uuid4().hex
        written=Queue(maxsize=1)
        def write():
            try:
                self.process.stdin.write(json.dumps(dict(payload,request_id=request_id),ensure_ascii=False)+'\n')
                self.process.stdin.flush()
                written.put(True)
            except Exception:written.put(False)
        self.writer=Thread(target=write,daemon=True)
        self.writer.start()
        if not written.get(timeout=max(0,deadline-monotonic())):raise ValueError('worker_pipe_failed')
        self.writer.join()
        result = self.responses.get(timeout=max(0,deadline-monotonic()))
        if (not isinstance(result,dict) or result.get('request_id')!=request_id
                or result.get('space_id')!=self.space.space_id or 'error' in result):
            raise ValueError('invalid_worker_response')
        return result

    def _call(self, operation, texts=None):
        deadline = monotonic()+self.timeout
        if not self.lock.acquire(timeout=self.timeout):
            raise EmbeddingStoreError('embedding_worker_busy')
        try:
            if self.closed or self.failed:raise ValueError('worker_unavailable')
            cold = self.process is None
            if cold:
                env={k:v for k,v in os.environ.items() if k.upper() in (
                    'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA',
                    'APPDATA','HOMEDRIVE','HOMEPATH','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')}
                env.update(PYTHONIOENCODING='utf-8',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                           HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
                self.process=subprocess.Popen([self.python,'-B',str(self.worker)],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env,
                    cwd=Path(__file__).resolve().parents[3],bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                self.reader=Thread(target=self._read,args=(self.process,),daemon=True)
                self.reader.start()
                ready=self._rpc(dict(operation='initialize',cache_dir=self.cache_dir),deadline)
                if ready.get('status')!='ready':raise ValueError('not_ready')
                load_ms=ready['model_load_ms']
            else:
                if self.process.poll() is not None:raise ValueError('worker_exited')
                load_ms=0
            result=self._rpc(dict(operation=operation,**({'texts':texts} if texts is not None else {})),deadline)
            self._metrics.value=dict(result.get('metrics',{}),cold_start=cold,model_load_ms=load_ms,
                                   process_id=self.process.pid)
            if operation=='encode':
                from data_analysis_agent.reviews.embedding_store import validate_vector
                vectors=result['vectors']
                if not isinstance(vectors,list) or len(vectors)!=len(texts):raise ValueError('vector_count')
                for vector in vectors:validate_vector(vector)
                return vectors
            if result.get('status')!='healthy':raise ValueError('unhealthy')
            return True
        except Exception:
            self.failed=True
            self._dispose()
            raise EmbeddingStoreError('local_embedding_process_failed') from None
        finally:self.lock.release()

    def embed_texts(self,texts):
        if not isinstance(texts,list) or len(texts)!=1 or not isinstance(texts[0],str) or not texts[0].strip() or len(texts[0])>4000:
            raise ValueError('invalid_query_text')
        return self._call('encode',texts)

    def health(self):return self._call('health')

    def close(self):
        with self.lock:
            self.closed=True
            self._dispose()


_processes = {}
_process_lock = Lock()

def shared_embedding_process(python,cache_dir,timeout=600):
    key=(str(Path(python).resolve()),str(Path(cache_dir).resolve()),timeout)
    with _process_lock:
        if key not in _processes:
            _processes[key]=PersistentEmbeddingProcess(python,cache_dir,timeout=timeout)
        return _processes[key]

def close_embedding_processes():
    with _process_lock:
        for process in _processes.values():process.close()
        _processes.clear()

atexit.register(close_embedding_processes)


class LocalEmbeddingProcess:
    def __init__(self, python, cache_dir):
        self.python = str(Path(python).resolve())
        self.cache_dir = str(Path(cache_dir).resolve())

    def encode(self, texts):
        root = Path(__file__).resolve().parents[3]
        env = {k: v for k, v in os.environ.items() if k.upper() in (
            'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA',
            'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')}
        env.update(PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TRANSFORMERS_OFFLINE='1')
        try:
            result = subprocess.run([self.python, '-B', str(root / 'scripts/local_embedding_worker.py')],
                input=json.dumps(dict(texts=texts, cache_dir=self.cache_dir), ensure_ascii=False),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', env=env,
                timeout=600, check=False, cwd=root)
            if result.returncode:
                raise ValueError
            value = json.loads(result.stdout)
            if 'error' in value:
                raise ValueError
            return value
        except Exception:
            raise EmbeddingStoreError('local_embedding_process_failed') from None
