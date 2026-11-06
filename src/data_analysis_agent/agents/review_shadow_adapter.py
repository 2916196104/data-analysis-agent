"""Explicit, default-off shadow invocation. Never imported by the production Agent.

Each injected judge runs in a disposable process, not an uncancellable thread.
This is lifecycle isolation, not an OS filesystem/network security sandbox.
"""
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import os
from queue import Queue, Empty
import subprocess
import sys
from threading import Thread
from time import monotonic

from pydantic import Field

from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from data_analysis_agent.reviews.query_contract import ContractModel
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalError
from data_analysis_agent.agents.review_structured_evidence import (
    JudgeOutput, ShadowOutcome, StructuredEvidenceError, prepare_input, model_payload, audit_output, parse,
)


class ShadowSettings(ContractModel):
    enabled: bool = False
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_input_bytes: int = Field(default=128000, ge=1024, le=128000)
    max_output_bytes: int = Field(default=65536, ge=1024, le=262144)


class JudgeFactory(ContractModel):
    """Trusted backend configuration, never accepted from a query or model response."""
    entrypoint: str = Field(pattern=r'^[A-Za-z_][\w.]*:[A-Za-z_]\w*$')
    config_json: str = Field(default='{}', max_length=16384, repr=False)


@dataclass(frozen=True)
class ShadowRun:
    outcome: ShadowOutcome
    diagnostics: dict
    judgments: JudgeOutput | None = field(default=None, repr=False)

    def debug_record(self):
        # No prompts, raw output/rationales, quotes, config, paths or exception text.
        return dict(outcome=asdict(self.outcome), execution=dict(self.diagnostics))


def worker_executable():
    if os.name != 'nt':
        return sys.executable
    # A Windows venv python.exe can redirect to a child; terminate the real image.
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetModuleFileNameW(None,buffer,len(buffer))
    if not 0 < size < len(buffer):
        raise OSError('interpreter_image_unavailable')
    return buffer.value


def _invoke(factory, payload, settings, *, deadline, cancel_event):
    config = strict_json_loads(factory.config_json)
    if type(config) is not dict:
        raise StructuredEvidenceError('invalid_factory_config')
    wire = canonical_json(dict(factory=factory.entrypoint, config=config, payload=payload,
                               max_output_bytes=settings.max_output_bytes)).encode('utf-8')
    if len(wire) > 1024 * 1024:
        raise StructuredEvidenceError('input_budget_exceeded')
    env = {k:v for k,v in os.environ.items() if k.upper() in (
        'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA',
        'HOMEDRIVE','HOMEPATH','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')}
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join(str(p) for p in sys.path if p),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    p = None
    threads = []
    events = Queue(maxsize=2)
    status, raw = 'worker_start_failed', b''
    diagnostics = dict(process_id=None, exit_code=None, reaped=True, threads_released=True,
                       termination_requested=False, cleanup_ms=0.0, output_bytes=0)
    try:
        p = subprocess.Popen([worker_executable(), '-B', '-m', 'data_analysis_agent.agents.review_shadow_worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        diagnostics['process_id'] = p.pid
        diagnostics['reaped'] = False

        def write():
            try:
                # FileIO.write may be partial; keep a single bounded writer.
                view = memoryview(wire)
                while view:
                    written = p.stdin.write(view)
                    if not written:
                        raise OSError('pipe_closed')
                    view = view[written:]
                p.stdin.close()
            except (OSError, ValueError):
                events.put_nowait(('pipe_failed', b''))

        def read():
            data = bytearray()
            try:
                while len(data) <= settings.max_output_bytes:
                    chunk = p.stdout.read(min(8192,settings.max_output_bytes+1-len(data)))
                    if not chunk:
                        events.put_nowait(('output', bytes(data)))
                        return
                    data.extend(chunk)
                events.put_nowait(('output_too_large', b''))
            except (OSError, ValueError):
                events.put_nowait(('pipe_failed', b''))

        threads = [Thread(target=write,name=f'shadow-write-{p.pid}',daemon=True),
                   Thread(target=read,name=f'shadow-read-{p.pid}',daemon=True)]
        for thread in threads:
            thread.start()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                status = 'cancelled'
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                status = 'deadline_exceeded'
                break
            try:
                status, raw = events.get(timeout=min(remaining,0.02))
                if status == 'output':
                    # Success requires EOF and a clean exit, not merely some bytes.
                    while p.poll() is None and monotonic()<deadline and not (cancel_event and cancel_event.is_set()):
                        try:
                            p.wait(timeout=min(0.02,max(0,deadline-monotonic())))
                        except subprocess.TimeoutExpired:
                            pass
                    status = ('deadline_exceeded' if p.poll() is None else
                        {70:'adapter_error',71:'invalid_output_type',72:'output_too_large',
                         73:'worker_input_invalid'}.get(p.returncode,
                            'completed' if p.returncode==0 and raw else 'missing_output' if p.returncode==0 else 'worker_exited'))
                break
            except Empty:
                pass
            except subprocess.TimeoutExpired:
                status = 'deadline_exceeded'
                break
        if monotonic() >= deadline and status == 'completed':
            status = 'deadline_exceeded'
        if cancel_event is not None and cancel_event.is_set():
            status = 'cancelled'
    except (OSError, ValueError):
        status = 'worker_start_failed'
    finally:
        cleanup = monotonic()
        if p is not None:
            try:
                if p.poll() is None:
                    diagnostics['termination_requested'] = True
                    p.kill()
                p.wait(timeout=2)
                diagnostics['reaped'] = True
            except (subprocess.TimeoutExpired, OSError):
                diagnostics['reaped'] = p.poll() is not None
            diagnostics['exit_code'] = p.poll()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=1)
            diagnostics['threads_released'] = not any(t.is_alive() for t in threads)
            if diagnostics['threads_released']:
                for pipe in (p.stdin,p.stdout):
                    pipe.close()
            if not diagnostics['reaped'] or not diagnostics['threads_released']:
                status = 'cleanup_failed'
        diagnostics['cleanup_ms'] = (monotonic()-cleanup)*1000
    diagnostics['output_bytes'] = len(raw)
    return status, raw, diagnostics


class StructuredEvidenceShadow:
    def __init__(self, *, settings=None, judge_factory=None):
        self.settings = settings or ShadowSettings()
        self.judge_factory = judge_factory

    def run(self, contract, result, *, trusted_contract_sha256, request_id, cancel_event=None):
        started = monotonic()
        diagnostics = dict(attempts=0, process_id=None, input_bytes=0, output_bytes=0,
                           input_sha256=None, output_sha256=None, total_ms=0.0,
                           reaped=True, threads_released=True, termination_requested=False)

        def finish(outcome, judgments=None):
            diagnostics['total_ms'] = (monotonic()-started)*1000
            return ShadowRun(outcome,dict(diagnostics),judgments)

        if not self.settings.enabled:
            return finish(ShadowOutcome('disabled', ('shadow_disabled',)))
        deadline = started + self.settings.timeout_seconds
        if cancel_event is not None and cancel_event.is_set():
            return finish(ShadowOutcome('cancelled', ('cancelled_before_start',)))
        try:
            request = prepare_input(contract,result,trusted_contract_sha256=trusted_contract_sha256,request_id=request_id)
            if not request.candidates:
                ids = tuple(f.id for f in request.contract.required_facts.items)
                return finish(ShadowOutcome('skipped_empty',('no_candidates_not_population_absence',),
                    missing_required_fact_ids=ids,unestablished_fact_ids=ids))
            payload = model_payload(request,max_bytes=self.settings.max_input_bytes)
            diagnostics.update(input_bytes=len(payload.encode('utf-8')), input_sha256=strict_json_loads(payload)['input_sha256'])
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError, SemanticRetrievalError) as exc:
            code = (str(exc) if isinstance(exc,StructuredEvidenceError) else
                    exc.code if isinstance(exc,SemanticRetrievalError) else 'invalid_input')
            return finish(ShadowOutcome('not_eligible' if code in ('not_eligible','unresolved_target','not_eligible_ambiguous_description')
                else 'input_invalid',(code,)))
        if monotonic() >= deadline:
            return finish(ShadowOutcome('model_failed',('deadline_exceeded',)))
        if not isinstance(self.judge_factory,JudgeFactory):
            return finish(ShadowOutcome('model_failed',('judge_not_configured',)))
        try:
            diagnostics['attempts'] = 1
            status, raw, process_info = _invoke(self.judge_factory,payload,self.settings,deadline=deadline,cancel_event=cancel_event)
            diagnostics.update(process_info)
            if status != 'completed':
                return finish(ShadowOutcome('cancelled' if status=='cancelled' else 'model_failed',(status,)))
            diagnostics['output_sha256'] = sha256(raw).hexdigest()
            outcome = audit_output(request,raw.decode('utf-8'))
            if monotonic() >= deadline:
                return finish(ShadowOutcome('model_failed',('deadline_exceeded',)))
            judgments = parse(raw.decode('utf-8'),JudgeOutput) if outcome.execution_state=='validated_shadow' else None
            if monotonic() >= deadline or (cancel_event is not None and cancel_event.is_set()):
                cancelled = cancel_event is not None and cancel_event.is_set()
                return finish(ShadowOutcome('cancelled' if cancelled else 'model_failed',
                                            ('cancelled' if cancelled else 'deadline_exceeded',)))
            return finish(outcome,judgments)
        except Exception:
            return finish(ShadowOutcome('model_failed',('adapter_or_transport_error',)))
