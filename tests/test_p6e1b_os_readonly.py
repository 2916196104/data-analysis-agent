"""Pure fault injection; no real OS, socket, database or model execution."""
from copy import deepcopy
import ctypes
import socket
import unittest
from unittest.mock import patch

from scripts import p6e1b_os_readonly as oscheck


class Reader:
    def __init__(self):self.pg=[];self.rows=[];self.fault=None;self.identities=0;self.snapshots=0
    def processes(self):
        self.snapshots+=1
        if self.fault=='process_snapshot':raise oscheck.ObservationUnknown('process_snapshot',5)
        if self.fault=='vanished' and self.snapshots>1:return []
        return deepcopy(self.pg)
    def tcp(self):
        if self.fault=='tcp_denied':raise oscheck.ObservationUnknown('tcp_2',5)
        if self.fault=='native_unexpected':raise RuntimeError('do not publish arbitrary exception text')
        return deepcopy(self.rows)
    def identity(self,pid):
        self.identities+=1
        if self.fault=='identity_denied':raise oscheck.ObservationUnknown('open_process',5)
        return {'pid':pid,'live':True,'image':'C:\\test\\process.exe','created_ticks':self.identities if self.fault=='reused' else 123}


class OSReadOnlyTests(unittest.TestCase):
    def setUp(self):
        for name in ('subprocess.Popen','subprocess.run','socket.socket.connect','socket.socket.bind','psycopg.connect','openai.OpenAI'):
            blocked=self.enterContext(patch(name,side_effect=AssertionError('unit test forbids real operations')))
            self.addCleanup(blocked.assert_not_called)
        self.reader=Reader()
    def test_free_at_both_observations_is_not_permanent_authority(self):
        r=oscheck.observe(self.reader);self.assertEqual(r['status'],'PASS');self.assertTrue(r['instantaneous_only']);self.assertFalse(r['recovery_authority'])
    def test_ipv4_listener_occupied_with_verified_owner(self):
        self.reader.rows=[{'port':55441,'pid':123,'family':2,'address':'0.0.0.0','listening':True}]
        r=oscheck.observe(self.reader);self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['owners'][0]['pid'],123)
    def test_ipv6_any_address_listener_blocks(self):
        self.reader.rows=[{'port':55441,'pid':123,'family':23,'address':'::','listening':True}]
        self.assertEqual(oscheck.observe(self.reader)['port_state'],'OCCUPIED_AT_OBSERVATION')
    def test_non_listening_bound_tcp_row_is_conservatively_blocked(self):
        self.reader.rows=[{'port':55441,'pid':123,'family':2,'listening':False}]
        self.assertEqual(oscheck.observe(self.reader)['status'],'BLOCKED')
    def test_process_identity_query_success(self):self.assertEqual(oscheck.stable_identity(self.reader,123)['created_ticks'],123)
    def test_permissions_denied_do_not_mean_no_process(self):
        self.reader.pg=[{'pid':123,'name':'postgres.exe'}];self.reader.fault='identity_denied'
        r=oscheck.observe(self.reader);self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['processes'][0]['error']['code'],5)
    def test_native_failure_is_not_zero_rows(self):
        self.reader.fault='tcp_denied';r=oscheck.observe(self.reader)
        self.assertEqual(r['port_state'],'UNKNOWN');self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['errors'][0]['code'],5)
    def test_cim_failure_alone_never_manufactures_success(self):
        # The independent probe has no CIM call or exception-to-PASS branch.
        self.reader.fault='tcp_denied'
        with patch('subprocess.run',side_effect=OSError('CIM unavailable')) as cim:
            self.assertEqual(oscheck.observe(self.reader)['status'],'BLOCKED');cim.assert_not_called()
    def test_unknown_native_exception_is_blocked(self):
        self.reader.fault='native_unexpected';r=oscheck.observe(self.reader)
        self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['port_state'],'UNKNOWN')
    def test_pid_reuse_blocks(self):
        self.reader.pg=[{'pid':123,'name':'postgres.exe'}];self.reader.fault='reused'
        self.assertEqual(oscheck.observe(self.reader)['status'],'BLOCKED')
    def test_process_disappearing_between_snapshots_blocks(self):
        self.reader.pg=[{'pid':123,'name':'postgres.exe'}];self.reader.fault='vanished'
        self.assertEqual(oscheck.observe(self.reader)['errors'][-1]['stage'],'os_snapshot_changed')
    def test_snapshot_access_denied_blocks(self):
        self.reader.fault='process_snapshot';self.assertEqual(oscheck.observe(self.reader)['status'],'BLOCKED')
    def test_pg_executable_without_directory_identity_stays_unknown(self):
        self.reader.pg=[{'pid':123,'name':'postgres.exe'}]
        r=oscheck.observe(self.reader);self.assertEqual(r['scope']['reasons'],['directory_scope_unknown'])
    def test_verified_other_directory_policy_does_not_require_stopping_business(self):
        # Policy test only; actual NativeReader cannot supply this proof in this environment.
        row={'pid':123,'identity':{'created_ticks':456},'directory_proof':{'pid':123,'created_ticks':456,'relation':'OTHER'}}
        self.assertEqual(oscheck.scope_decision([row])['status'],'PASS')
    def test_verified_target_directory_is_conflict(self):
        row={'pid':123,'identity':{'created_ticks':456},'directory_proof':{'pid':123,'created_ticks':456,'relation':'TARGET'}}
        self.assertIn('target_conflict',oscheck.scope_decision([row])['reasons'])
    def test_stale_directory_proof_is_unknown(self):
        row={'pid':123,'identity':{'created_ticks':456},'directory_proof':{'pid':123,'created_ticks':455,'relation':'OTHER'}}
        self.assertEqual(oscheck.scope_decision([row])['status'],'BLOCKED')
    def test_ipv4_wire_table_endianness(self):
        row=oscheck.Row4(state=2,local=0x0100007f,port=socket.htons(55441),pid=123)
        data=(1).to_bytes(4,'little')+bytes(row);r=oscheck.parse_table(data,2)[0]
        self.assertEqual((r['address'],r['port'],r['pid']),('127.0.0.1',55441,123))
    def test_ipv6_wire_table_endianness(self):
        row=oscheck.Row6();row.local[15]=1;row.port=socket.htons(55441);row.state=2;row.pid=123
        r=oscheck.parse_table((1).to_bytes(4,'little')+bytes(row),23)[0]
        self.assertEqual((r['address'],r['port'],r['pid']),('::1',55441,123))
    def test_truncated_wire_table_rejected(self):
        with self.assertRaises(oscheck.ObservationUnknown):oscheck.parse_table((10).to_bytes(4,'little'),2)
    def test_unknown_address_family_rejected(self):
        with self.assertRaises(oscheck.ObservationUnknown):oscheck.parse_table(b'\0'*4,999)
    def test_undefined_native_state_is_unknown_not_non_listening(self):
        row=oscheck.Row4(state=0,local=0x0100007f,port=socket.htons(55441),pid=123)
        r=oscheck.parse_table((1).to_bytes(4,'little')+bytes(row),2)[0]
        self.assertIsNone(r['listening'])
    def test_documented_ipv4_abi(self):
        self.assertEqual(ctypes.sizeof(oscheck.Row4),24);self.assertEqual(ctypes.alignment(oscheck.Row4),4)
        self.assertEqual([getattr(oscheck.Row4,k).offset for k in ('state','local','port','remote','remote_port','pid')],[0,4,8,12,16,20])
    def test_documented_ipv6_abi(self):
        self.assertEqual(ctypes.sizeof(oscheck.Row6),56);self.assertEqual(ctypes.alignment(oscheck.Row6),4)
        self.assertEqual([getattr(oscheck.Row6,k).offset for k in ('local','local_scope','port','remote','remote_scope','remote_port','state','pid')],[0,16,20,24,40,44,48,52])
    def test_ipv6_nonzero_scope_network_byte_order(self):
        row=oscheck.Row6();row.local_scope=socket.htonl(12);row.state=2
        self.assertEqual(oscheck.parse_table((1).to_bytes(4,'little')+bytes(row),23)[0]['scope_id'],12)
    def test_only_owner_pid_classes_are_accepted(self):
        self.assertEqual((oscheck.TCP_TABLE_OWNER_PID_LISTENER,oscheck.TCP_TABLE_OWNER_PID_ALL),(3,5))
        reader=object.__new__(oscheck.NativeReader)
        for family,kind in ((2,0),(23,6),(999,5)):
            with self.assertRaises(oscheck.ObservationUnknown):reader.tcp_table(family,kind)
    def test_raw_evidence_does_not_rewrite_zero_or_expose_other_rows(self):
        row=oscheck.Row4(state=0,local=0x0100007f,port=socket.htons(55441),pid=123)
        other=oscheck.Row4(state=2,port=socket.htons(5432),pid=456)
        reader=object.__new__(oscheck.NativeReader)
        raw=(2).to_bytes(4,'little')+bytes(row)+bytes(other)
        with patch.object(reader,'tcp_table',return_value=raw):e=reader.tcp_evidence(2,55441,123)
        self.assertEqual(len(e['matches']),1);self.assertEqual(e['matches'][0]['state_bytes_hex'],'00000000')
        self.assertEqual(e['matches'][0]['state_at_documented_offset'],0);self.assertIsNone(e['matches'][0]['parsed']['listening'])
        self.assertEqual(e['matches'][0]['raw_row_hex'],bytes(row).hex())
    def test_ipv6_raw_evidence_state_offset_and_listen(self):
        row=oscheck.Row6();row.state=2;row.port=socket.htons(55441);row.pid=123
        reader=object.__new__(oscheck.NativeReader)
        with patch.object(reader,'tcp_table',return_value=(1).to_bytes(4,'little')+bytes(row)):
            e=reader.tcp_evidence(23,55441,123,3)
        self.assertEqual((e['row_size'],e['state_offset']),(56,48))
        self.assertEqual(e['matches'][0]['state_bytes_hex'],'02000000');self.assertTrue(e['matches'][0]['parsed']['listening'])


if __name__=='__main__':unittest.main()
