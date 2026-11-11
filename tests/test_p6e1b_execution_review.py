"""执行前控制逻辑的离线反例；不模拟PostgreSQL事务或查询结果。"""
import ast
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('e1b_review_prepare',Path(__file__).resolve().parents[1]/'scripts/prepare_p6e1b_environment.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
import p6e1b_postgresql_integration as integration


class E1BExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','psycopg.connect','subprocess.run','subprocess.Popen'):
            blocked=self.enterContext(patch(target,side_effect=AssertionError('offline review only')))
            self.addCleanup(blocked.assert_not_called)
        self.folder=Path(self.enterContext(TemporaryDirectory(prefix='e1b-review-unit-')))
        self.plan=prepare.make_plan('b'*32,55441)
        self.plan_file=self.folder/'plan.json'
        self.plan_file.write_text(json.dumps(self.plan),encoding='utf-8')
        self.enterContext(patch.dict(integration.EVIDENCE,{'events':[],'cases':{},'role_checks':{
            'reader':{'timeouts_ms':{'idle_in_transaction_session_timeout':5000}}}},clear=True))

    def sql(self):
        manifest=prepare.draft_manifest(self.plan)
        manifest.update(authorized=True,synthetic_writes_authorized=True,approved_by='OFFLINE-UNIT-ONLY',
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        manifest['environment_preparation'].update(authorized=True,
            **{key:True for key in prepare.PERMISSIONS},expected_system_identifier='1234567890123456789')
        filename=self.folder/'unit-only.json';filename.write_text(json.dumps(manifest),encoding='utf-8')
        return prepare.render_sql(self.plan,prepare.check_approval(self.plan,filename,require_cluster=True))

    def test_guard_failure_is_a_sql_error_not_a_successful_quit(self):
        sql=self.sql()
        self.assertTrue(sql.startswith('\\set ON_ERROR_STOP on\n'))
        self.assertNotIn('\\quit',sql)
        self.assertEqual(sql.count('SELECT 1/0 AS p6_e1b_guard_rejected;'),4)

    def test_reader_barrier_wait_and_join_share_less_than_idle_timeout(self):
        waits,joins=[],[]
        class Event:
            def set(self):pass
            def wait(self,timeout):waits.append(timeout);return True
        class Thread:
            def __init__(self,**kwargs):pass
            def start(self):pass
            def join(self,timeout):joins.append(timeout)
            def is_alive(self):return False
        case=integration.P6E1BPostgresTests('test_PG02_append_between_statistics_and_prefetch')
        case.case='OFFLINE-SYNC-UNIT'
        with patch.object(integration.threading,'Event',Event),patch.object(integration.threading,'Thread',Thread), \
             patch.object(integration,'monotonic',side_effect=[0,0,1.9]):
            release,_,_=case.concurrent_commit(lambda:None);release()
        self.assertEqual(len(waits),1);self.assertEqual(len(joins),1)
        self.assertGreater(waits[0],0);self.assertGreater(joins[0],0)
        self.assertLessEqual(max(waits+joins),2.0)
        self.assertLessEqual(joins[0],0.101)

    def test_failure_stops_before_any_following_case(self):
        visited=[]
        class Dummy(unittest.TestCase):
            def test_first(self):visited.append('failure');self.fail('synthetic control failure')
            def test_next(self):visited.append('must-not-run')
        result=integration.SafeResult()
        unittest.TestSuite([Dummy('test_first'),Dummy('test_next')]).run(result)
        self.assertEqual(visited,['failure'])
        self.assertTrue(result.shouldStop)
        self.assertEqual(integration.EVIDENCE['cases']['test_first']['status'],'FAIL')

    def test_reader_failure_before_barrier_cannot_start_writer(self):
        calls=[]
        case=integration.P6E1BPostgresTests('test_PG02_append_between_statistics_and_prefetch')
        case.case='OFFLINE-CANCEL-UNIT'
        _,thread,abort=case.concurrent_commit(lambda:calls.append('must-not-write'))
        try:abort()
        finally:thread.join(10)
        self.assertEqual(calls,[]);self.assertFalse(thread.is_alive())

    def test_reviewed_injection_uses_explicit_plan_without_changing_database_targets(self):
        text=prepare.render_injection(self.plan,plan_file=self.plan_file)
        self.assertIn(self.plan_file.as_posix(),text)
        for target in prepare.target_values(self.plan).values():
            self.assertIn('p6_e1b_'+'b'*32,target)

    def test_error_stops_before_any_following_case(self):
        visited=[]
        class Dummy(unittest.TestCase):
            def test_first(self):visited.append('error');raise RuntimeError('synthetic control error')
            def test_next(self):visited.append('must-not-run')
        result=integration.SafeResult()
        unittest.TestSuite([Dummy('test_first'),Dummy('test_next')]).run(result)
        self.assertEqual(visited,['error']);self.assertTrue(result.shouldStop)

    def test_reparse_point_in_target_ancestry_is_rejected(self):
        actual=Path.lstat;redirected=Path(self.plan['cluster_directory']).parent
        def lstat(path,*args,**kwargs):
            if path==redirected:return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)
            return actual(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat),self.assertRaises(prepare.E1BBlocked):
            prepare.checked_plan(self.plan_file)

    def test_existing_non_directory_in_target_ancestry_is_rejected(self):
        actual=Path.lstat;blocked=Path(self.plan['cluster_directory']).parent
        def lstat(path,*args,**kwargs):
            if path==blocked:return SimpleNamespace(st_mode=stat.S_IFREG,st_file_attributes=0)
            return actual(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat),self.assertRaises(prepare.E1BBlocked):
            prepare.checked_plan(self.plan_file)

    def test_bootstrap_hba_includes_management_and_both_database_connections(self):
        rows=[line.split() for line in prepare.render_hba(self.plan).splitlines()]
        for database in ('postgres',*self.plan['databases'].values()):
            matching=[r for r in rows if r[0]=='host' and r[1] in ('all',database)
                      and self.plan['bootstrap_role'] in r[2].split(',') and r[3]=='127.0.0.1/32']
            self.assertTrue(matching)
            self.assertEqual(matching[0][-1],'scram-sha-256')
        self.assertNotIn('trust',prepare.render_hba(self.plan))

    def test_pg07_zero_call_and_failed_state_assertions_remain_strict(self):
        tree=ast.parse(Path(integration.__file__).read_text(encoding='utf-8'))
        pg07=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='test_PG07_timeouts_errors_disconnect_and_worker_abort')
        calls=[ast.unparse(node) for node in ast.walk(pg07) if isinstance(node,ast.Call)]
        self.assertIn('self.worker.stop_requested.set()',calls)
        self.assertIn("self.assertEqual(task['status'], 'failed')",calls)
        self.assertIn("self.assertEqual(self.calls, [], 'worker stop during prefetch must prevent classification')",calls)


if __name__=='__main__':unittest.main()
