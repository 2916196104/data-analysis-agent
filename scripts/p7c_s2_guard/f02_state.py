"""One-run durable admission. No reset, resume, task creation or business IO."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

LIMITS = dict(http_submission=1, submission=1, worker_task=1, query_embedding=1,
              vector_search=1, model_process_create=1, model_initialize=1, model_encode_rpc=1)


class AdmissionError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def guard_hashes():
    here=Path(__file__).resolve().parent
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(here.iterdir())
            if p.is_file() and p.suffix in ('.py','.json','.mjs')}


def git_head():
    root=Path(__file__).resolve().parents[2]
    git=root/'.git'
    if git.is_file():git=(root/git.read_text().strip().removeprefix('gitdir: ')).resolve()
    head=(git/'HEAD').read_text().strip()
    if head.startswith('ref: '):
        ref=head[5:];p=git/ref
        if p.is_file():head=p.read_text().strip()
        else:
            head=next(line.split()[0] for line in (git/'packed-refs').read_text().splitlines()
                      if line.endswith(' '+ref))
    return head


def safe_path(value):
    p = Path(value).absolute()
    # resolve() alone hides symlinks/junctions. Inspect the original ancestors first.
    for part in (p, *p.parents):
        if part.exists() or part.is_symlink():
            st = part.lstat()
            if part.is_symlink() or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise AdmissionError('redirected_path')
    if p != p.resolve():
        raise AdmissionError('noncanonical_path')
    return p


class RunLedger:
    @classmethod
    def create(cls, directory, authorization):
        out = safe_path(directory)
        if not out.name.startswith('p7c-s2-r1b-'):
            raise AdmissionError('not_a_new_r1b_run')
        # Existing directories, even empty/crashed ones, are never rearmed.
        out.mkdir(exist_ok=False)
        cls.validate(authorization, out)
        with (out/'claim.json').open('x', encoding='utf-8') as f:
            json.dump(authorization, f, ensure_ascii=False, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        task_db = out/'tasks.sqlite3'
        with task_db.open('xb') as f:
            f.flush(); os.fsync(f.fileno())
        ledger = out/'quota.sqlite3'
        with ledger.open('xb'):
            pass
        con = sqlite3.connect(ledger, isolation_level=None)
        try:
            con.execute('PRAGMA synchronous=FULL')
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE run (binding TEXT NOT NULL, task_id TEXT, vector_hash TEXT, stopped INTEGER NOT NULL DEFAULT 0)')
            con.execute('CREATE TABLE quota (operation TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used BETWEEN 0 AND 1))')
            binding = dict(authorization=authorization, task_file_id=task_db.stat().st_ino)
            con.execute('INSERT INTO run(binding) VALUES (?)', (json.dumps(binding,sort_keys=True),))
            con.executemany('INSERT INTO quota VALUES (?,0)', [(k,) for k in LIMITS])
            con.execute('COMMIT')
        finally:
            con.close()
        return cls(out, authorization)

    @staticmethod
    def validate(a, out):
        from p7c_real_guard import QUESTION, SCOPE, IDENTITY
        now = time.time()
        if (a.get('stage') != 'P7-C-S2-R1B' or a.get('approved') is not True
            or not isinstance(a.get('authorization_id'), str) or len(a['authorization_id']) < 16
            or a.get('run_dir') != str(out) or a.get('task_db') != str(out/'tasks.sqlite3')
            or a.get('question') != QUESTION or a.get('scope') != SCOPE or a.get('identity') != IDENTITY
            or a.get('limits') != LIMITS or a.get('observation_seconds') != 900
            or not isinstance(a.get('conversation_id'), str) or not a['conversation_id']
            or a.get('git_head') != git_head()
            or a.get('guard_hashes') != guard_hashes()
            or any(a.get(k) != 0 for k in ('paid_llm','vector_sync','pg_business_writes','automatic_retries'))
            or type(a.get('started_at')) not in (int,float) or type(a.get('deadline')) not in (int,float)
            or a['deadline']-a['started_at'] != 900 or now < a['started_at'] or now >= a['deadline']):
            raise AdmissionError('authorization_binding')

    def __init__(self, directory, authorization):
        self.out = safe_path(directory)
        self.authorization = authorization
        self.validate(authorization, self.out)
        for name in ('claim.json','tasks.sqlite3','quota.sqlite3'):
            if not safe_path(self.out/name).is_file():
                raise AdmissionError('missing_owned_file')
        claim = json.loads((self.out/'claim.json').read_text(encoding='utf-8'))
        if claim != authorization:
            raise AdmissionError('claim_mismatch')
        with self.connection() as c:
            self._check(c)

    def _connect(self):
        # mode=rw prevents silently creating a replacement ledger after file loss.
        return sqlite3.connect((self.out/'quota.sqlite3').as_uri()+'?mode=rw', uri=True,
                               timeout=5, isolation_level=None)

    @contextmanager
    def connection(self):
        c=self._connect()
        try:yield c
        finally:c.close()

    def _check(self, c):
        self.validate(self.authorization, safe_path(self.out))
        for name in ('claim.json','tasks.sqlite3','quota.sqlite3'):
            safe_path(self.out/name)
        if json.loads((self.out/'claim.json').read_text(encoding='utf-8')) != self.authorization:
            raise AdmissionError('claim_changed')
        row = c.execute('SELECT binding,task_id,vector_hash,stopped FROM run').fetchone()
        binding = json.loads(row[0])
        if (binding['authorization'] != self.authorization
            or binding['task_file_id'] != (self.out/'tasks.sqlite3').stat().st_ino or row[3]):
            raise AdmissionError('ledger_binding_or_stopped')
        return row

    def check_task_db(self, path):
        if safe_path(path) != self.out/'tasks.sqlite3':
            raise AdmissionError('task_database_mismatch')

    def check_task(self, task_id):
        with self.connection() as c:
            row = self._check(c)
            if not task_id or row[1] != task_id:
                raise AdmissionError('task_identity_mismatch')

    def take(self, operation, *, task_id=None):
        if operation not in LIMITS:
            raise AdmissionError('unknown_quota')
        c = self._connect()
        try:
            c.execute('PRAGMA synchronous=FULL')
            c.execute('BEGIN IMMEDIATE')
            row = self._check(c)
            if operation not in ('http_submission','submission') and (not task_id or row[1] != task_id):
                raise AdmissionError('task_identity_mismatch')
            prerequisites = dict(submission='http_submission',worker_task='submission',query_embedding='worker_task',
                                 model_process_create='query_embedding',model_initialize='model_process_create',
                                 model_encode_rpc='model_initialize',vector_search='model_encode_rpc')
            pre = prerequisites.get(operation)
            if pre and c.execute('SELECT used FROM quota WHERE operation=?',(pre,)).fetchone()[0] != 1:
                raise AdmissionError('quota_order')
            changed = c.execute('UPDATE quota SET used=1 WHERE operation=? AND used=0',(operation,)).rowcount
            if changed != 1:
                raise AdmissionError('duplicate_'+operation)
            c.execute('COMMIT')
        except BaseException:
            if c.in_transaction: c.execute('ROLLBACK')
            raise
        finally:
            c.close()

    def bind_task(self, task_id, conversation_id):
        if not isinstance(task_id,str) or not task_id or conversation_id != self.authorization['conversation_id']:
            raise AdmissionError('task_binding')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE');self._check(c)
            used = c.execute("SELECT used FROM quota WHERE operation='submission'").fetchone()[0]
            if used != 1 or c.execute('UPDATE run SET task_id=? WHERE task_id IS NULL',(task_id,)).rowcount != 1:
                c.execute('ROLLBACK');raise AdmissionError('task_already_bound')
            c.execute('COMMIT')

    def bind_vector(self, vector):
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE');self._check(c)
            if c.execute('UPDATE run SET vector_hash=? WHERE vector_hash IS NULL',(digest(vector),)).rowcount != 1:
                c.execute('ROLLBACK');raise AdmissionError('vector_already_bound')
            c.execute('COMMIT')

    def check_vector(self, vector):
        with self.connection() as c:
            row=self._check(c)
            if row[2] != digest(vector): raise AdmissionError('vector_binding')

    def counts(self):
        with self.connection() as c:
            return dict(c.execute('SELECT operation,used FROM quota'))

    def stop(self):
        with self.connection() as c:
            c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE run SET stopped=1');c.execute('COMMIT')
