"""Explicit native diagnostics using only test-owned Python listeners and Jobs."""
import json
import os
from datetime import datetime,timezone
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4
from scripts import p6e1b_os_readonly as probe
from scripts import p6e1b_windows_job as jobs

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=[]


class NativeOSDiagnostics(unittest.TestCase):
    def setUp(self):
        self.reader=probe.NativeReader();self.owned=[];self.audit={'test':self.id()};self.addCleanup(self.cleanup)
    def cleanup(self):
        for child in self.owned:
            child.terminate();child.wait(timeout=5);child.communicate(timeout=5)
            proof=child.verify_and_close(time.monotonic()+5)
            self.assertTrue(proof['all_exited'] and proof['handles_released']);self.audit['cleanup']=proof
        EVIDENCE.append(self.audit)
    def listener(self,host,port):
        self.assertFalse([r for r in self.reader.tcp() if r['port']==port] if port else [])
        token=uuid4().hex;folder=ROOT/'.cache/p6e1b-os-preflight/listeners'/token;folder.mkdir(parents=True)
        fixture=ROOT/'tests/fixtures/p6e1b_os_listener.py'
        argv=[sys.executable,str(fixture),'--token',token,'--host',host,'--port',str(port)]
        child=jobs.OwnedJobProcess(argv,allowed_prefix=[sys.executable,str(fixture)]);self.owned.append(child)
        deadline=time.monotonic()+10
        while not (folder/'ready.json').exists() and time.monotonic()<deadline:time.sleep(0.01)
        self.assertTrue((folder/'ready.json').exists())
        ready=json.loads((folder/'ready.json').read_text());self.assertEqual(ready['token'],token)
        self.assertIn(ready['pid'],child.members());return ready
    def test_ipv4_ipv6_tables_are_accessible(self):
        rows=self.reader.tcp();self.assertTrue(all(r['family'] in (2,23) for r in rows));self.audit['row_count']=len(rows)
    def test_55441_has_no_entry_before_owned_listener(self):
        rows=[r for r in self.reader.tcp() if r['port']==55441];self.assertEqual(rows,[]);self.audit['target_rows']=rows
    def test_own_process_image_and_creation_identity(self):
        identity=probe.stable_identity(self.reader,os.getpid());self.assertTrue(identity['live']);self.assertGreater(identity['created_ticks'],0);self.audit['identity']=identity
    def test_owned_ipv4_listener_and_pid_identity(self):
        ready=self.listener('127.0.0.1',55441)
        self.audit['raw_tables']=[self.reader.tcp_evidence(2,55441,ready['pid'],kind) for kind in (3,5)]
        self.assertEqual(ready['so_acceptconn'],1)
        rows=[r for r in self.reader.tcp() if r['port']==55441 and r['family']==2]
        self.audit.update(ready=ready,rows=rows)
        self.assertTrue(any(r['pid']==ready['pid'] and r['listening'] for r in rows))
        identity=probe.stable_identity(self.reader,ready['pid']);self.assertTrue(identity['live']);self.audit.update(rows=rows,owner=identity)
    def test_owned_ipv6_listener_and_pid_identity(self):
        ready=self.listener('::1',0)
        self.audit['raw_tables']=[self.reader.tcp_evidence(23,ready['port'],ready['pid'],kind) for kind in (3,5)]
        self.assertEqual(ready['so_acceptconn'],1)
        rows=[r for r in self.reader.tcp() if r['port']==ready['port'] and r['family']==23]
        self.audit.update(ready=ready,rows=rows)
        self.assertTrue(any(r['pid']==ready['pid'] and r['listening'] for r in rows));self.audit['rows']=rows
    def test_existing_pg_processes_are_reported_without_termination(self):
        rows=self.reader.processes();self.audit['pg_processes']=rows
        for row in rows:
            try:identity=probe.stable_identity(self.reader,row['pid']);self.audit.setdefault('identities',[]).append(identity)
            except probe.ObservationUnknown as e:self.audit.setdefault('unknowns',[]).append({'pid':row['pid'],'stage':e.stage,'code':e.code})
    def test_own_child_identity_is_readable(self):
        ready=self.listener('127.0.0.1',0)
        identity=probe.stable_identity(self.reader,ready['pid'])
        self.assertTrue(identity['live']);self.assertEqual(identity['pid'],ready['pid']);self.audit['child_identity']=identity


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(NativeOSDiagnostics)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    out=ROOT/'.cache/p6e1b-os-preflight/native-runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex)
    out.mkdir(parents=True,exist_ok=False)
    (out/'native-tests-evidence.json').write_text(json.dumps(EVIDENCE,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'native-tests-summary.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),
        'errors':len(result.errors),'skips':len(result.skipped),'status':'PASS' if result.wasSuccessful() else 'FAIL',
        'scope':'harmless listeners and read-only API; NOT recovery admission'},indent=2),encoding='utf-8')
    print('Native evidence: '+str(out))
    raise SystemExit(0 if result.wasSuccessful() else 1)
