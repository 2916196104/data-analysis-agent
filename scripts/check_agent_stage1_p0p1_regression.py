"""Existing business regression targets with isolated synthetic SQLite only."""
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from agent_stage1_multiturn_evaluation import use_source_revision


def ids(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from ids(item)
        else:yield item.id()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-revision',type=Path,required=True)
    parser.add_argument('--targets',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();use_source_revision(args.source_revision)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    temp_root=(args.output_dir/'synthetic-temp').resolve();temp_root.mkdir()
    targets=json.loads(args.targets.read_text(encoding='utf-8'))['targets']
    suite=unittest.defaultTestLoader.loadTestsFromNames(targets);test_ids=list(ids(suite))
    original_connect,original_popen=sqlite3.connect,subprocess.Popen
    external=[];sqlite_calls=0
    def connect(target,*a,**kw):
        nonlocal sqlite_calls
        text=str(target)
        if text not in (':memory:','file::memory:?cache=shared'):
            path=Path(unquote(urlsplit(text).path.lstrip('/')) if text.startswith('file:') else text).resolve()
            if not path.is_relative_to(temp_root):
                external.append('sqlite_outside_test_directory');raise AssertionError('non-test SQLite target')
        sqlite_calls+=1
        return original_connect(target,*a,**kw)
    def blocked(*a,**kw):
        external.append('external_connection');raise AssertionError('no network or PostgreSQL connection')
    def popen(command,*a,**kw):
        if any(name in str(command).lower() for name in ('initdb','pg_ctl','psql','postgres.exe','pg_controldata')):
            external.append('postgres_command');raise AssertionError('no native PostgreSQL command')
        return original_popen(command,*a,**kw)
    started=time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch('tempfile.tempdir',str(temp_root)))
        stack.enter_context(patch('sqlite3.connect',connect))
        stack.enter_context(patch('psycopg.connect',blocked))
        stack.enter_context(patch('socket.socket.connect',blocked))
        stack.enter_context(patch('socket.socket.connect_ex',blocked))
        stack.enter_context(patch('subprocess.Popen',popen))
        with (args.output_dir/'tests.txt').open('x',encoding='utf-8') as log, redirect_stdout(log):
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skips=len(result.skipped),
        seconds=round(time.perf_counter()-started,3),failure_ids=[t.id() for t,_ in result.failures],
        error_ids=[t.id() for t,_ in result.errors],per_module=dict(Counter(v.split('.')[0] for v in test_ids)),
        test_ids=test_ids,sqlite_connections_to_synthetic_temp=sqlite_calls,external_attempts=external,
        postgres_connections=0,paid_provider_requests=0,real_embedding_calls=0)
    with (args.output_dir/'summary.json').open('x',encoding='utf-8') as file:json.dump(summary,file,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('test_ids','per_module')},ensure_ascii=False))
    return 0 if result.wasSuccessful() and not result.skipped and not external else 1


if __name__=='__main__':raise SystemExit(main())
