"""Run only the existing no-database checks applicable to this audit boundary."""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
import openai
import psycopg
from test_agent_stage1_evaluation import EvaluationBoundaryTests
from test_review_router_p0a import RouterP0ATests
from test_semantic_retrieval import SemanticServiceTests, PostgresAdapterTests
from test_agent_stage1_p0_p1_fixes import MinimalBusinessFixTests
from agent_stage1_multiturn_evaluation import use_source_revision


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-revision',type=Path)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'.cache/agent-stage1')
    args=parser.parse_args()
    if args.source_revision:use_source_revision(args.source_revision)
    attempts=[]
    def blocked(*a,**kw):
        attempts.append('unexpected_external_dependency')
        raise AssertionError('stage1 forbids real database/network/process/model dependencies')
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in
        (EvaluationBoundaryTests,RouterP0ATests,SemanticServiceTests,PostgresAdapterTests,MinimalBusinessFixTests))
    out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        for module,name in ((sqlite3,'connect'),(psycopg,'connect'),(socket.socket,'connect'),
                (socket.socket,'connect_ex'),(subprocess,'Popen'),(openai,'OpenAI')):
            stack.enter_context(patch.object(module,name,blocked))
        with (out/'offline-checks.txt').open('x',encoding='utf-8') as log:
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                 skips=len(result.skipped),unexpected_dependency_attempts=attempts)
    with (out/'offline-checks.json').open('x',encoding='utf-8') as file:file.write(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return 0 if result.wasSuccessful() and not attempts and not result.skipped else 1


if __name__=='__main__':raise SystemExit(main())
