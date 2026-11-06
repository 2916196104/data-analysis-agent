"""P5-B2 opt-in *shadow-only* subprocess adapter.

Disabled by default. No model implementation, no API keys, and no connection to
the published answer/assessment path. Subprocess workers are explicitly supplied
by the caller, never taken from model text. Call only from a separate, approved
post-snapshot diagnostic path, not while holding a database transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import signal
import subprocess
import sys
import threading
from time import monotonic
from typing import Sequence

from data_analysis_agent.agents.p5b_shadow_contract import (
    ShadowOutcome, audit_output, model_payload, PrototypeError,
)

@dataclass(frozen=True)
class ShadowSettings:
    enabled: bool = False
    worker_argv: tuple[str, ...] = ()
    timeout_seconds: float = 8.0
    max_input_bytes: int = 128_000
    max_output_bytes: int = 128_000
    max_input_tokens_estimate: int = 32_000
    max_candidates: int = 5
    max_facts: int = 8

    def __post_init__(self):
        if not 0 < self.timeout_seconds <= 120:
            raise ValueError("invalid_timeout")
        if not 0 < self.max_input_bytes <= 128_000:
            raise ValueError("invalid_input_budget")
        if not 0 < self.max_output_bytes <= 128_000:
            raise ValueError("invalid_output_budget")
        if not 0 < self.max_input_tokens_estimate <= 32_000:
            raise ValueError("invalid_token_budget")
        if not 0 < self.max_candidates <= 5 or not 0 < self.max_facts <= 8:
            raise ValueError("invalid_contract_budget")

def _end_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP is not a security sandbox; taskkill attempts
        # to terminate descendants, then fall back to killing the direct child.
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           timeout=2.0, check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()

def invoke_worker(argv: Sequence[str], payload: bytes, *, timeout_seconds: float,
                  max_output_bytes: int) -> tuple[str, bytes]:
    """Read stdout in bounded chunks; kill workers (and descendants) on timeout.

    The worker is an independently trusted executable supplied by the host.
    This is resource-bound execution, NOT a malicious-code sandbox.
    """
    if not argv or any(not isinstance(x, str) or not x for x in argv):
        return ("worker_not_configured", b"")
    deadline = monotonic() + timeout_seconds
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = None
    output = bytearray()
    over_limit = threading.Event()
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False,
            creationflags=flags, start_new_session=(os.name != "nt"),
            close_fds=True,
        )
        def receive():
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                remaining = max_output_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > max_output_bytes or len(chunk) > remaining:
                    over_limit.set()
                    return

        reader = threading.Thread(target=receive, name="p5b-shadow-reader", daemon=True)
        reader.start()
        # Writing stdin may block indefinitely if a worker never consumes it.
        # A daemon writer lets the parent enforce the same wall-clock deadline.
        def send():
            try:
                proc.stdin.write(payload)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        writer = threading.Thread(target=send, name="p5b-shadow-writer", daemon=True)
        writer.start()
        while True:
            if over_limit.is_set():
                return ("output_budget_exceeded", bytes(output))
            if monotonic() >= deadline:
                return ("timeout", b"")
            if proc.poll() is not None and not reader.is_alive() and not writer.is_alive():
                break
            reader.join(min(0.025, max(0.0, deadline - monotonic())))
        if over_limit.is_set():
            return ("output_budget_exceeded", bytes(output))
        if proc.returncode != 0:
            return ("worker_failed", b"")
        return ("completed", bytes(output))
    except (OSError, ValueError, subprocess.SubprocessError):
        return ("worker_failed", b"")
    finally:
        if proc is not None:
            _end_process_tree(proc)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _end_process_tree(proc)
            if proc.stdin:
                try: proc.stdin.close()
                except OSError: pass
            if proc.stdout:
                try: proc.stdout.close()
                except OSError: pass

def run_shadow(request, *, settings: ShadowSettings = ShadowSettings()):
    """No result from this function can authorize a response."""
    if not settings.enabled:
        return ShadowOutcome("disabled", ("shadow_disabled",))
    if not settings.worker_argv:
        return ShadowOutcome("model_failed", ("worker_not_configured",))
    if not request.candidates:
        return ShadowOutcome("skipped_empty", ("no_candidates_not_population_absence",))
    if (len(request.candidates) > settings.max_candidates or
            len(request.contract.required_facts.items) > settings.max_facts):
        return ShadowOutcome("input_invalid", ("contract_budget_exceeded",))
    try:
        encoded = model_payload(request).encode("utf-8")
    except PrototypeError as exc:
        return ShadowOutcome("input_invalid", (str(exc),))
    except (UnicodeError, ValueError, TypeError):
        return ShadowOutcome("input_invalid", ("invalid_model_payload",))
    if len(encoded) > settings.max_input_bytes:
        return ShadowOutcome("input_invalid", ("input_budget_exceeded",))
    # Conservative byte-count ceiling, not a model-specific tokenizer measurement.
    if len(encoded) > settings.max_input_tokens_estimate:
        return ShadowOutcome("input_invalid", ("token_budget_unverified",))
    state, output = invoke_worker(settings.worker_argv, encoded,
                                  timeout_seconds=settings.timeout_seconds,
                                  max_output_bytes=settings.max_output_bytes)
    if state != "completed":
        return ShadowOutcome("model_failed", (state,))
    try:
        return audit_output(request, output.decode("utf-8", errors="strict"))
    except UnicodeError:
        return ShadowOutcome("output_invalid", ("invalid_output_encoding",))
