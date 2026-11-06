"""Opt-in worker-local failure retention. Never a source of validated evidence."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from data_analysis_agent.agents import review_structured_evidence as protocol

PRIVATE_ROOT = Path(__file__).resolve().parents[3]/'outputs'
MAX_RAW_BYTES = 65536
MAX_FILE_BYTES = 512 * 1024


def destination(value):
    path = Path(value)
    if not path.is_absolute() or path.exists():
        raise ValueError('diagnostic_destination_must_be_new')
    parent = path.parent.resolve(strict=True)
    root = PRIVATE_ROOT.resolve(strict=True)
    if parent != root and root not in parent.parents:
        raise ValueError('diagnostic_destination_outside_private_outputs')
    # Reject existing symlink/junction components; the worker is not an OS sandbox.
    for part in (path.parent, *path.parent.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('diagnostic_destination_redirected')
    return parent/path.name


def _private_mkdir(path):
    path.mkdir(mode=0o700)
    if os.name == 'nt':
        # Restrict a NEW EMPTY directory before writing any data. Files inherit its DACL.
        identity = subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],
            capture_output=True, timeout=5, creationflags=0x08000000, check=True)
        sid = re.search(rb'S-1-[0-9-]+', identity.stdout)
        if sid is None:
            raise OSError('diagnostic_owner_unavailable')
        subprocess.run(['icacls.exe',str(path),'/inheritance:r','/grant:r',
            '*'+sid.group().decode('ascii')+':(OI)(CI)F'], capture_output=True,
            timeout=5, creationflags=0x08000000, check=True)


def _redact(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]').replace(json.dumps(secret)[1:-1], '[REDACTED]')
    text = re.sub(r'(?i)(authorization["\s:]*)(?:bearer\s+)?[^\s"\\,}]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|database_url)["\s:=]+)[^"\s\\,}]+', r'\1[REDACTED]', text)
    return re.sub(r'(?i)(?:postgres(?:ql)?|mysql)://[^\s"\\]+', '[REDACTED_DATABASE_URL]', text)


def _atomic_write(path, value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(',',':')).encode('utf-8')
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError('diagnostic_file_too_large')
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        # Atomic publication with no overwrite, including concurrent writers.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class FailureCapture:
    def __init__(self, directory, envelope, max_output_bytes):
        self.directory = destination(directory)
        self.request = protocol.parse(envelope, protocol.JudgeInput)
        self.limit = min(max_output_bytes, MAX_RAW_BYTES)
        self.raw = None
        self.secrets = ()

    def capture(self, text, *, secrets=()):
        if isinstance(text,str) and len(text) <= self.limit and len(text.encode('utf-8')) <= self.limit:
            self.raw = text
            self.secrets = secrets

    def finish(self, *, provider_failed=False):
        if self.raw is None:
            return
        details = []
        outcome = protocol.audit_output(self.request, self.raw, failure_details=details)
        if not provider_failed and outcome.execution_state == 'validated_shadow':
            return
        redacted = _redact(self.raw,self.secrets)
        value = dict(request_id=self.request.request_id,
            input_sha256=sha256(protocol.canonical_json(self.request.model_dump(mode='json')).encode()).hexdigest(),
            raw_bytes=len(self.raw.encode('utf-8')), raw_sha256=sha256(self.raw.encode('utf-8')).hexdigest(),
            raw_model_text=redacted, redaction_applied=redacted != self.raw,
            provider_failed=provider_failed, error_codes=list(outcome.reason_codes), failure_details=details,
            evidence_status='unvalidated_diagnostic_only', publication_allowed=False, semantic_verified=False)
        _private_mkdir(self.directory)
        _atomic_write(self.directory/'failure.json', value)
