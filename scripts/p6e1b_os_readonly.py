"""Independent read-only OS diagnostics. NOT a recovery admission or CIM-success fallback."""
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import sys

PG_NAMES={'postgres.exe','pg_ctl.exe','initdb.exe'}
TCP_TABLE_OWNER_PID_LISTENER=3
TCP_TABLE_OWNER_PID_ALL=5


class ObservationUnknown(OSError):
    def __init__(self,stage,code=0):super().__init__(stage);self.stage=stage;self.code=code


class Row4(c.Structure):
    _fields_=[(name,c.c_uint32) for name in ('state','local','port','remote','remote_port','pid')]


class Row6(c.Structure):
    _fields_=[('local',c.c_ubyte*16),('local_scope',c.c_uint32),('port',c.c_uint32),
              ('remote',c.c_ubyte*16),('remote_scope',c.c_uint32),('remote_port',c.c_uint32),
              ('state',c.c_uint32),('pid',c.c_uint32)]


class Entry(c.Structure):
    _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',c.c_size_t),
        ('module',w.DWORD),('threads',w.DWORD),('ppid',w.DWORD),('priority',w.LONG),
        ('flags',w.DWORD),('exe',w.WCHAR*260)]


def parse_table(data,family):
    row_type=Row4 if family==2 else Row6 if family==23 else None
    if row_type is None or len(data)<4:raise ObservationUnknown('tcp_table_shape')
    count=c.c_uint32.from_buffer_copy(data[:4]).value;size=c.sizeof(row_type)
    if count>(len(data)-4)//size:raise ObservationUnknown('tcp_table_truncated')
    rows=[]
    for i in range(count):
        r=row_type.from_buffer_copy(data[4+i*size:4+(i+1)*size])
        raw=int(r.local).to_bytes(4,'little') if family==2 else bytes(r.local)
        rows.append({'family':family,'address':socket.inet_ntop(socket.AF_INET if family==2 else socket.AF_INET6,raw),
            'scope_id':0 if family==2 else socket.ntohl(r.local_scope),'port':socket.ntohs(r.port&65535),
            'state':int(r.state),'listening':(r.state==2) if 1<=r.state<=12 else None,'pid':int(r.pid)})
    return rows


class NativeReader:
    def __init__(self):
        if os.name!='nt':raise ObservationUnknown('windows_required')
        self.k=c.WinDLL('kernel32',use_last_error=True);self.ip=c.WinDLL('iphlpapi',use_last_error=True)
        bindings={
            'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            'CloseHandle':([w.HANDLE],w.BOOL),
            'CreateToolhelp32Snapshot':([w.DWORD,w.DWORD],w.HANDLE),
            'Process32FirstW':([w.HANDLE,c.POINTER(Entry)],w.BOOL),
            'Process32NextW':([w.HANDLE,c.POINTER(Entry)],w.BOOL),
            'QueryFullProcessImageNameW':([w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)],w.BOOL),
            'GetProcessTimes':([w.HANDLE,*([c.POINTER(w.FILETIME)]*4)],w.BOOL),
            'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD)}
        for name,(args,restype) in bindings.items():
            function=getattr(self.k,name);function.argtypes=args;function.restype=restype
        self.ip.GetExtendedTcpTable.argtypes=[c.c_void_p,c.POINTER(w.DWORD),w.BOOL,w.ULONG,c.c_int,w.ULONG]
        self.ip.GetExtendedTcpTable.restype=w.DWORD
    @staticmethod
    def check(value,stage):
        if not value:raise ObservationUnknown(stage,c.get_last_error())
        return value
    def close(self,handle):self.check(self.k.CloseHandle(handle),'close_query_handle')
    def tcp_table(self,family,table_class=TCP_TABLE_OWNER_PID_ALL):
        if family not in (2,23) or table_class not in (TCP_TABLE_OWNER_PID_LISTENER,TCP_TABLE_OWNER_PID_ALL):
            raise ObservationUnknown('tcp_table_parameters')
        size=w.DWORD();code=self.ip.GetExtendedTcpTable(None,c.byref(size),False,family,table_class,0)
        if code not in (0,122):raise ObservationUnknown('tcp_'+str(family),int(code))
        for _ in range(3):
            if not 4<=size.value<=8*1024*1024:raise ObservationUnknown('tcp_buffer_bound')
            buffer=c.create_string_buffer(size.value)
            code=self.ip.GetExtendedTcpTable(buffer,c.byref(size),False,family,table_class,0)
            if code==122:continue
            if code!=0:raise ObservationUnknown('tcp_'+str(family),int(code))
            return buffer.raw[:size.value]
        raise ObservationUnknown('tcp_table_keeps_changing')
    def tcp(self):
        return [row for family in (2,23) for row in parse_table(self.tcp_table(family),family)]
    def tcp_evidence(self,family,port,pid,table_class=TCP_TABLE_OWNER_PID_ALL):
        # Keep only the owned fixture row, never unrelated endpoints or remote addresses.
        raw=self.tcp_table(family,table_class);row_type=Row4 if family==2 else Row6
        stride=c.sizeof(row_type);offset=row_type.state.offset;matches=[]
        for index,row in enumerate(parse_table(raw,family)):
            if row['port']==port and row['pid']==pid:
                data=raw[4+index*stride:4+(index+1)*stride]
                matches.append({'parsed':row,'raw_row_hex':data.hex(),
                    'state_bytes_hex':data[offset:offset+4].hex(),
                    'state_at_documented_offset':int.from_bytes(data[offset:offset+4],'little')})
        return {'utc':datetime.now(timezone.utc).isoformat(),'family':family,'table_class':table_class,
            'row_size':stride,'state_offset':offset,'header_hex':raw[:4].hex(),'matches':matches}
    def processes(self):
        snapshot=self.k.CreateToolhelp32Snapshot(2,0)
        if snapshot==w.HANDLE(-1).value:raise ObservationUnknown('process_snapshot',c.get_last_error())
        rows=[]
        try:
            item=Entry();item.size=c.sizeof(item)
            ok=self.k.Process32FirstW(snapshot,c.byref(item))
            while ok:
                if item.exe.casefold() in PG_NAMES:rows.append({'pid':int(item.pid),'ppid':int(item.ppid),'name':item.exe.casefold()})
                ok=self.k.Process32NextW(snapshot,c.byref(item))
            if c.get_last_error()!=18:raise ObservationUnknown('process_enumeration',c.get_last_error())
        finally:self.close(snapshot)
        return rows
    def identity(self,pid):
        handle=self.check(self.k.OpenProcess(0x101000,False,pid),'open_process')
        try:
            if self.k.WaitForSingleObject(handle,0)!=258:raise ObservationUnknown('process_not_live')
            length=w.DWORD(32768);image=c.create_unicode_buffer(length.value)
            self.check(self.k.QueryFullProcessImageNameW(handle,0,image,c.byref(length)),'query_process_image')
            times=[w.FILETIME() for _ in range(4)]
            self.check(self.k.GetProcessTimes(handle,*[c.byref(t) for t in times]),'query_process_creation')
            if self.k.WaitForSingleObject(handle,0)!=258:raise ObservationUnknown('process_disappeared')
            return {'pid':pid,'image':image.value,'created_ticks':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime,'live':True}
        finally:self.close(handle)


def stable_identity(reader,pid):
    first=reader.identity(pid);second=reader.identity(pid)
    if first!=second or first.get('live') is not True:raise ObservationUnknown('pid_disappeared_or_reused')
    return first


def scope_decision(rows):
    """Policy-only proof requirement. Native diagnosis never fabricates directory proofs."""
    reasons=[]
    for row in rows:
        proof=row.get('directory_proof')
        if row.get('identity') is None:reasons.append('process_identity_unknown')
        elif not isinstance(proof,dict) or proof.get('pid')!=row['pid'] or proof.get('created_ticks')!=row['identity']['created_ticks']:
            reasons.append('directory_scope_unknown')
        elif proof.get('relation')=='TARGET':reasons.append('target_conflict')
        elif proof.get('relation')!='OTHER':reasons.append('directory_scope_unknown')
    return {'status':'BLOCKED' if reasons else 'PASS','reasons':sorted(set(reasons)),
        'note':'Directory proofs are required; this standalone diagnostic does not supply them or authorize recovery.'}


def observe(reader=None,port=55441):
    result={'utc':datetime.now(timezone.utc).isoformat(),'port':port,'status':'BLOCKED',
        'port_state':'UNKNOWN','processes':[],'errors':[],'recovery_authority':False,'instantaneous_only':True}
    try:
        reader=reader or NativeReader()
        before=reader.processes();tcp_before=[r for r in reader.tcp() if r['port']==port]
        for row in before:
            item={**row,'identity':None,'directory_proof':None}
            try:item['identity']=stable_identity(reader,row['pid'])
            except ObservationUnknown as exc:item['error']={'stage':exc.stage,'code':exc.code}
            result['processes'].append(item)
        owners=[]
        for pid in sorted({r['pid'] for r in tcp_before}):
            try:owners.append(stable_identity(reader,pid))
            except ObservationUnknown as exc:result['errors'].append({'stage':exc.stage,'code':exc.code})
        tcp_after=[r for r in reader.tcp() if r['port']==port];after=reader.processes()
        result.update(tcp_before=tcp_before,tcp_after=tcp_after,owners=owners)
        if before!=after or tcp_before!=tcp_after:raise ObservationUnknown('os_snapshot_changed')
        result['port_state']='OCCUPIED_AT_OBSERVATION' if tcp_after else 'NO_ENTRY_AT_OBSERVATION'
        scoped=scope_decision(result['processes']);result['scope']=scoped
        if not tcp_after and not result['errors'] and scoped['status']=='PASS':result['status']='PASS'
    except ObservationUnknown as exc:result['errors'].append({'stage':exc.stage,'code':exc.code})
    except Exception as exc:result['errors'].append({'stage':'unexpected_os_observation_error','error_type':type(exc).__name__})
    return result


if __name__=='__main__':
    # No execution/repair/stop switches and no approved-plan writer.
    print(json.dumps(observe(),ensure_ascii=False))
