"""Evaluation infrastructure assertions, independent of whether business cases pass."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
import sys
import unittest
from unittest.mock import patch

from agent_stage1_multiturn_evaluation import Harness, MemoryReadConnection, FIX, ROOT, verify_freeze
sys.path.insert(0,str(ROOT/'scripts'))
from summarize_agent_stage1_evaluation import assess
from data_analysis_agent.jobs.analysis_state import restore_ranking
from data_analysis_agent.jobs.store import analysis_result_integrity


class EvaluationBoundaryTests(unittest.TestCase):
    def test_frozen_files_and_production_sources_are_unchanged(self):
        self.assertTrue(verify_freeze()['frozen_before_execution'])

    def test_thirty_independent_groups_two_to_four_turns(self):
        groups=Harness().oracle['groups']
        self.assertEqual(len(groups),30)
        self.assertEqual(len({g['id'] for g in groups}),30)
        self.assertEqual(sum(len(g['turns']) for g in groups),62)
        self.assertTrue(all(2<=len(g['turns'])<=4 for g in groups))

    def test_every_turn_has_all_nine_independent_expectations(self):
        fields=('analysis_state','pending_goal','request_goal','user_intent','analysis_intent','query_plan',
                'required_tools','task_acceptance','final_status')
        for group in Harness().oracle['groups']:
            for t in group['turns']:
                self.assertTrue(all('expected_'+name in t for name in fields))

    def test_corpus_quotes_are_real_spans_and_not_production_sources(self):
        corpus=json.loads((FIX/'corpus.v1.json').read_text(encoding='utf-8'))
        self.assertIn('fictional',corpus['provenance'])
        for r in corpus['rows']:
            for o in r['opinions']:
                self.assertEqual(r['text'][o['quote_start']:o['quote_end']],o['quote'])

    def test_data_double_rejects_sql_writes_and_unknown_queries(self):
        h=Harness(); conn=MemoryReadConnection(h)
        for sql in ('INSERT INTO reviews VALUES (1)','DELETE FROM tasks','SELECT * FROM reviews'):
            with self.assertRaises(AssertionError):conn.execute(sql)

    def test_database_network_process_and_model_dependencies_are_blocked(self):
        import psycopg, openai, subprocess
        h=Harness()
        with ExitStack() as stack:
            h.patches(stack)
            for action in (lambda:sqlite3.connect(':memory:'),lambda:psycopg.connect('never-used'),
                           lambda:openai.OpenAI(),lambda:subprocess.Popen(['never-executed'])):
                with self.assertRaises(AssertionError):action()
            with socket.socket() as s:
                with self.assertRaises(AssertionError):s.connect(('127.0.0.1',1))
        self.assertEqual(len(h.forbidden_attempts),5)

    def test_memory_storage_seals_and_does_not_mutate_result(self):
        h=Harness(); result={'status':'unsupported','answer':'synthetic'}
        h.tasks['test']={'parameters':{'question':'test'},'progress':{}}
        h.finish(None,'test',None,status='succeeded',result=result)
        result['status']='changed'
        t=h.tasks['test']
        self.assertEqual(t['result']['status'],'unsupported')
        self.assertEqual(t['progress']['analysis_integrity'],analysis_result_integrity('test','succeeded',t['parameters'],t['result']))
        self.assertIn('finished_at',t)

    def test_oracle_authoring_does_not_import_agent(self):
        import ast
        module=ast.parse((ROOT/'scripts/freeze_agent_stage1_evaluation.py').read_text(encoding='utf-8'))
        imports=[n.module for n in ast.walk(module) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith('data_analysis_agent') for name in imports))

    def test_gold_is_not_rewritten_by_scoring(self):
        before={p.name:sha256(p.read_bytes()).hexdigest() for p in FIX.glob('*.json')}
        verify_freeze()
        self.assertEqual(before,{p.name:sha256(p.read_bytes()).hexdigest() for p in FIX.glob('*.json')})


if __name__=='__main__':unittest.main()
