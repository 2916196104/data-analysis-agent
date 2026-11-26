"""业务环境与独立模型环境之间的本地进程边界。"""

import json
import logging
import os
from pathlib import Path
import subprocess
import atexit
from queue import Queue, Empty
from threading import Lock, Thread, local
from time import monotonic, sleep
from uuid import uuid4

from data_analysis_agent.reviews.embedding_store import EmbeddingStoreError

logger = logging.getLogger(__name__)

MAX_QUERY_BATCH = 8
DEFAULT_MAX_RESTARTS = 3
DEFAULT_RESTART_BACKOFF_SECONDS = 0.5
MAX_RESTART_BACKOFF_SECONDS = 30.0
DEFAULT_RESTART_COOLDOWN_SECONDS = 60.0


class _RecoverableWorkerError(ValueError):
    """子进程返回的单请求输入错误；进程本身仍然健康，不触发销毁或故障计数。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PersistentEmbeddingProcess:
    """Serial JSON-lines RPC with optional bounded self-healing and batch encoding."""

    supports_batch_encode = True

    def __init__(
        self,
        python,
        cache_dir,
        *,
        timeout=600,
        worker=None,
        max_restarts=0,
        restart_backoff_seconds=DEFAULT_RESTART_BACKOFF_SECONDS,
        restart_cooldown_seconds=DEFAULT_RESTART_COOLDOWN_SECONDS,
    ):
        from data_analysis_agent.reviews.embedding_store import SPACE
        if not 0 < timeout <= 600: raise ValueError('invalid_embedding_timeout')
        if type(max_restarts) is not int or max_restarts < 0: raise ValueError('invalid_max_restarts')
        if isinstance(restart_backoff_seconds, bool) or restart_backoff_seconds < 0:
            raise ValueError('invalid_restart_backoff')
        if (
            restart_cooldown_seconds is not None
            and (isinstance(restart_cooldown_seconds, bool) or restart_cooldown_seconds < 0)
        ):
            raise ValueError('invalid_restart_cooldown')
        self.space = SPACE
        self.python, self.cache_dir = str(Path(python).resolve()), str(Path(cache_dir).resolve())
        self.worker = Path(worker) if worker else Path(__file__).resolve().parents[3]/'scripts/persistent_embedding_worker.py'
        self.timeout = timeout
        self.max_restarts = max_restarts
        self.restart_backoff_seconds = float(restart_backoff_seconds)
        self.restart_cooldown_seconds = None if restart_cooldown_seconds is None else float(restart_cooldown_seconds)
        self.lock = Lock()
        self.process = None
        self.reader = None
        self.writer = None
        self.responses = Queue(maxsize=2)
        self.failed = self.closed = False
        self.consecutive_failures = 0
        self.restart_count = 0
        self._last_failure_at = None
        self._metrics = local()

    @property
    def last_metrics(self):return getattr(self._metrics,'value',{})

    def _can_restart(self):
        if self.closed or self.max_restarts <= 0:
            return False
        if self.consecutive_failures <= self.max_restarts:
            return True
        if (
            self.restart_cooldown_seconds is not None
            and self.restart_cooldown_seconds > 0
            and self._last_failure_at is not None
            and monotonic() - self._last_failure_at >= self.restart_cooldown_seconds
        ):
            return True
        return False

    def _wait_restart_backoff(self, deadline):
        if self._last_failure_at is None or self.restart_backoff_seconds <= 0:
            return
        delay = min(
            MAX_RESTART_BACKOFF_SECONDS,
            self.restart_backoff_seconds * (2 ** max(0, self.consecutive_failures - 1)),
        )
        remaining_wait = delay - (monotonic() - self._last_failure_at)
        if remaining_wait > 0:
            if monotonic() + remaining_wait >= deadline:
                raise TimeoutError('restart_backoff_exceeds_deadline')
            sleep(remaining_wait)

    def _read(self, process, responses):
        try:
            while True:
                line = process.stdout.readline(1024*1024)
                if not line or not line.endswith('\n'): break
                responses.put_nowait(json.loads(line))
        except Exception:
            pass
        finally:
            try:responses.put_nowait(None)
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
        if (isinstance(result, dict) and result.get('request_id') == request_id
                and result.get('recoverable') is True and 'error' in result):
            raise _RecoverableWorkerError(str(result.get('error') or 'query_input_rejected'))
        if (not isinstance(result,dict) or result.get('request_id')!=request_id
                or result.get('space_id')!=self.space.space_id or 'error' in result):
            raise ValueError('invalid_worker_response')
        return result

    def _call(self, operation, texts=None):
        deadline = monotonic()+self.timeout
        if not self.lock.acquire(timeout=self.timeout):
            raise EmbeddingStoreError('embedding_worker_busy')
        already_latched = False
        try:
            if self.closed:
                already_latched = True
                raise ValueError('worker_unavailable')
            if self.failed:
                if not self._can_restart():
                    already_latched = True
                    raise ValueError('worker_unavailable')
                cooldown_probe = self.consecutive_failures > self.max_restarts
                if not cooldown_probe:
                    self._wait_restart_backoff(deadline)
                self.failed = False
                self.restart_count += 1
                logger.warning(
                    "语义检索编码子进程自动重建 attempt=%d/%d cooldown_probe=%s",
                    self.consecutive_failures,
                    self.max_restarts,
                    cooldown_probe,
                )
            cold = self.process is None
            if cold:
                env={k:v for k,v in os.environ.items() if k.upper() in (
                    'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA',
                    'APPDATA','HOMEDRIVE','HOMEPATH','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')}
                env.update(PYTHONIOENCODING='utf-8',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                           HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
                self.responses = Queue(maxsize=2)
                self.process=subprocess.Popen([self.python,'-B',str(self.worker)],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env,
                    cwd=Path(__file__).resolve().parents[3],bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                self.reader=Thread(target=self._read,args=(self.process,self.responses),daemon=True)
                self.reader.start()
                ready=self._rpc(dict(operation='initialize',cache_dir=self.cache_dir),deadline)
                if ready.get('status')!='ready':raise ValueError('not_ready')
                load_ms=ready['model_load_ms']
            else:
                if self.process.poll() is not None:raise ValueError('worker_exited')
                load_ms=0
            result=self._rpc(dict(operation=operation,**({'texts':texts} if texts is not None else {})),deadline)
            self.consecutive_failures = 0
            self._last_failure_at = None
            self._metrics.value=dict(result.get('metrics',{}),cold_start=cold,model_load_ms=load_ms,
                                   process_id=self.process.pid,restarts=self.restart_count)
            if operation=='encode':
                from data_analysis_agent.reviews.embedding_store import validate_vector
                vectors=result['vectors']
                if not isinstance(vectors,list) or len(vectors)!=len(texts):raise ValueError('vector_count')
                for vector in vectors:validate_vector(vector)
                return vectors
            if result.get('status')!='healthy':raise ValueError('unhealthy')
            return True
        except _RecoverableWorkerError:
            # 单次输入超长或非法不破坏常驻模型进程，直接返回安全错误码。
            raise EmbeddingStoreError('local_embedding_query_rejected') from None
        except Exception:
            if not already_latched:
                self.consecutive_failures += 1
                self._last_failure_at = monotonic()
            self.failed=True
            self._dispose()
            raise EmbeddingStoreError('local_embedding_process_failed') from None
        finally:self.lock.release()

    def embed_texts(self,texts):
        if (not isinstance(texts,list) or not 1<=len(texts)<=MAX_QUERY_BATCH
                or any(not isinstance(text,str) or not text.strip() or len(text)>4000 for text in texts)):
            raise ValueError('invalid_query_text')
        return self._call('encode',texts)

    def health(self):return self._call('health')

    def close(self):
        with self.lock:
            self.closed=True
            self._dispose()


_processes = {}
_process_lock = Lock()

def shared_embedding_process(
    python,
    cache_dir,
    timeout=600,
    *,
    max_restarts=DEFAULT_MAX_RESTARTS,
    restart_backoff_seconds=DEFAULT_RESTART_BACKOFF_SECONDS,
):
    key=(str(Path(python).resolve()),str(Path(cache_dir).resolve()),timeout)
    with _process_lock:
        if key not in _processes:
            _processes[key]=PersistentEmbeddingProcess(
                python,
                cache_dir,
                timeout=timeout,
                max_restarts=max_restarts,
                restart_backoff_seconds=restart_backoff_seconds,
            )
        return _processes[key]


def evict_failed_embedding_process(python, cache_dir, timeout=600):
    """移除已失败的共享实例，避免预热阶段瞬时失败污染后续首次检索。"""
    key = (str(Path(python).resolve()), str(Path(cache_dir).resolve()), timeout)
    removed = None
    with _process_lock:
        candidate = _processes.get(key)
        if candidate is not None and candidate.failed:
            removed = _processes.pop(key)
    if removed is not None:
        removed.close()
        return True
    return False


def close_embedding_processes():
    with _process_lock:
        for process in _processes.values():process.close()
        _processes.clear()


def warmup_embedding_process(python, cache_dir, *, timeout=600):
    """同步预热持久嵌入子进程（加载本地模型并执行健康检查），降低首次语义检索冷启动耗时。"""
    started = monotonic()
    process = shared_embedding_process(python, cache_dir, timeout=timeout)
    ok = process.health()
    elapsed_ms = round((monotonic() - started) * 1000, 1)
    logger.info("语义检索编码进程预热完成 elapsed_ms=%.1f metrics=%s", elapsed_ms, process.last_metrics)
    return bool(ok)


def start_embedding_warmup(python, cache_dir, *, timeout=600):
    """在后台守护线程中异步预热编码进程，不阻塞 API/Worker 启动；失败仅记日志并清理失败实例，不中断主服务。"""
    def _run():
        try:
            warmup_embedding_process(python, cache_dir, timeout=timeout)
        except Exception as exc:
            evict_failed_embedding_process(python, cache_dir, timeout=timeout)
            logger.warning("语义检索编码进程启动预热跳过/失败（%s），首次检索将按需重建。", type(exc).__name__)

    thread = Thread(target=_run, name="rag-embedding-warmup", daemon=True)
    thread.start()
    return thread

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
