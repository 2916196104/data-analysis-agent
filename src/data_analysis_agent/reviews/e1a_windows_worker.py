"""Windows AppContainer launcher. Grants apply only to a new staged runtime.

No account creation, reference ACL changes, ambient handles or network capabilities.
An unavailable sandbox is an error; there is no same-user subprocess fallback.
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor


def stage_python(root):
    """Copy public code/dependencies only. Never grant access to the installed venv."""
    import shutil
    import sys
    import sysconfig
    import zipfile
    root = Path(root).resolve(strict=True)
    target = root / 'python'; target.mkdir()
    base = Path(sys.base_prefix)
    for pattern in ('python.exe', 'python3.dll', 'python3*.dll', 'vcruntime*.dll'):
        for path in base.glob(pattern): shutil.copy2(path, target / path.name)
    shutil.copytree(base / 'DLLs', target / 'DLLs', ignore=shutil.ignore_patterns('__pycache__'))
    version = f'python{sys.version_info.major}{sys.version_info.minor}'
    with zipfile.ZipFile(target / f'{version}.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((base / 'Lib').rglob('*.py')):
            rel = path.relative_to(base / 'Lib')
            if any(p in ('site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip', 'turtledemo') for p in rel.parts):
                continue
            archive.write(path, str(rel))
    site = Path(sysconfig.get_paths()['purelib']); dest = target / 'packages'; dest.mkdir()
    for name in ('pydantic', 'pydantic_core', 'typing_extensions.py', 'typing_inspection', 'annotated_types', 'psycopg', 'psycopg_binary', 'psycopg_binary.libs'):
        source = site / name
        if source.is_dir(): shutil.copytree(source, dest / name, ignore=shutil.ignore_patterns('__pycache__'))
        else: shutil.copy2(source, dest / name)
    project_src = Path(__file__).resolve().parents[1]
    shutil.copytree(project_src, target / 'src' / 'data_analysis_agent',
                    ignore=lambda d,names: [n for n in names if n == '__pycache__' or (Path(d,n).is_file() and not n.endswith('.py'))])
    (target / f'{version}._pth').write_text(f'{version}.zip\nDLLs\npackages\nsrc\n', encoding='utf-8')
    return target / 'python.exe'


class IsolationError(RuntimeError):
    pass


def _api():
    if os.name != 'nt':
        raise IsolationError('windows_appcontainer_required')
    k = c.WinDLL('kernel32', use_last_error=True)
    a = c.WinDLL('advapi32', use_last_error=True)
    u = c.WinDLL('userenv', use_last_error=True)
    signatures = [
        (u, 'DeriveAppContainerSidFromAppContainerName', [w.LPCWSTR, c.POINTER(c.c_void_p)], c.c_long),
        (u, 'CreateAppContainerProfile', [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)], c.c_long),
        (u, 'DeleteAppContainerProfile', [w.LPCWSTR], c.c_long),
        (a, 'FreeSid', [c.c_void_p], c.c_void_p),
        (a, 'ConvertSidToStringSidW', [c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL),
        (a, 'OpenProcessToken', [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
        (a, 'GetTokenInformation', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
        (a, 'EqualSid', [c.c_void_p, c.c_void_p], w.BOOL),
        (k, 'LocalFree', [c.c_void_p], c.c_void_p),
        (k, 'CloseHandle', [w.HANDLE], w.BOOL),
        (k, 'InitializeProcThreadAttributeList', [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
        (k, 'UpdateProcThreadAttribute', [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p], w.BOOL),
        (k, 'DeleteProcThreadAttributeList', [c.c_void_p], None),
        (k, 'CreateProcessW', [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.c_void_p, c.c_void_p], w.BOOL),
        (k, 'CreateJobObjectW', [c.c_void_p, w.LPCWSTR], w.HANDLE),
        (k, 'SetInformationJobObject', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        (k, 'AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
        (k, 'ResumeThread', [w.HANDLE], w.DWORD),
        (k, 'WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
        (k, 'TerminateProcess', [w.HANDLE, w.UINT], w.BOOL),
        (k, 'GetExitCodeProcess', [w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
    ]
    for lib, name, args, result in signatures:
        fn = getattr(lib, name); fn.argtypes = args; fn.restype = result
    return k, a, u


def _check(ok, operation):
    if not ok:
        raise IsolationError(f'{operation}:winerror={c.get_last_error()}')


class _Startup(c.Structure):
    _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR),
                ('x', w.DWORD), ('y', w.DWORD), ('xs', w.DWORD), ('ys', w.DWORD),
                ('xc', w.DWORD), ('yc', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                ('show', w.WORD), ('reserved2size', w.WORD), ('reserved2', c.c_void_p),
                ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]


class _StartupEx(c.Structure):
    _fields_ = [('startup', _Startup), ('attributes', c.c_void_p)]


class _Process(c.Structure):
    _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]


class _Capabilities(c.Structure):
    _fields_ = [('sid', c.c_void_p), ('capabilities', c.c_void_p), ('count', w.DWORD), ('reserved', w.DWORD)]


class _JobBasic(c.Structure):
    _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong), ('flags', w.DWORD),
                ('min_working', c.c_size_t), ('max_working', c.c_size_t), ('active', w.DWORD),
                ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]


class _JobLimits(c.Structure):
    _fields_ = [('basic', _JobBasic), ('io', c.c_ulonglong * 6),
                ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]


class AppContainer:
    """A fresh temporary package profile per run, removed on exit; no Windows user."""
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.k, self.a, self.u = _api()
        self.sid = c.c_void_p()
        self.name = 'e1a.local.runtime.' + uuid.uuid4().hex
        hr = self.u.CreateAppContainerProfile(self.name, self.name, 'E1-A isolated offline worker', None, 0, c.byref(self.sid))
        if hr < 0:
            raise IsolationError(f'create_appcontainer_profile:hresult={hr}')
        value = w.LPWSTR()
        try:
            _check(self.a.ConvertSidToStringSidW(self.sid, c.byref(value)), 'sid_string')
            self.sid_string = value.value
        finally:
            if value: self.k.LocalFree(value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.a.FreeSid(self.sid)
        hr = self.u.DeleteAppContainerProfile(self.name)
        if hr < 0: raise IsolationError(f'delete_appcontainer_profile:hresult={hr}')

    def grant(self, path):
        path = Path(path).resolve(strict=True)
        if path != self.root and self.root not in path.parents:
            raise IsolationError('acl_target_outside_new_runtime')
        # Never grant on the repository, installed Python, or any reference directory.
        args = ['icacls.exe', str(path), '/grant', f'*{self.sid_string}:(OI)(CI)RX', '/Q']
        result = subprocess.run(args, capture_output=True, timeout=30, creationflags=0x08000000)
        if result.returncode: raise IsolationError(f'icacls_grant_failed:{result.returncode}')

    def run(self, executable, args, *, cwd, timeout_seconds):
        """Start suspended, verify OS token, contain in one-process job, then resume."""
        executable = Path(executable).resolve(strict=True); cwd = Path(cwd).resolve(strict=True)
        for path in (executable, cwd):
            if self.root not in path.parents and path != self.root:
                raise IsolationError('process_path_outside_new_runtime')
        if not 0 < timeout_seconds <= 120: raise IsolationError('invalid_worker_timeout')
        k, a = self.k, self.a
        size = c.c_size_t()
        import msvcrt
        k.InitializeProcThreadAttributeList(None, 3, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        _check(k.InitializeProcThreadAttributeList(attributes, 3, 0, c.byref(size)), 'init_attributes')
        caps = _Capabilities(self.sid, None, 0, 0)  # No network or other capability SIDs.
        child_policy = w.DWORD(1)  # PROCESS_CREATION_CHILD_PROCESS_RESTRICTED
        proc = _Process(); job = token = None; resumed = False
        out_r, out_w = os.pipe(); err_r, err_w = os.pipe()
        null = os.open(os.devnull, os.O_RDONLY)
        handles = (w.HANDLE * 3)(*[msvcrt.get_osfhandle(fd) for fd in (null, out_w, err_w)])
        for handle in handles: os.set_handle_inheritable(handle, True)
        pool = ThreadPoolExecutor(max_workers=2)
        # Drain concurrently so a large/error output cannot deadlock the child.
        def drain(fd):
            with os.fdopen(fd, 'rb') as stream: return stream.read(2 * 1024 * 1024 + 1)
        stdout = pool.submit(drain, out_r); stderr = pool.submit(drain, err_r)
        started = time.perf_counter()
        try:
            _check(k.UpdateProcThreadAttribute(attributes, 0, 0x20009, c.byref(caps), c.sizeof(caps), None, None), 'security_capabilities')
            _check(k.UpdateProcThreadAttribute(attributes, 0, 0x2000e, c.byref(child_policy), c.sizeof(child_policy), None, None), 'child_process_policy')
            _check(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, c.sizeof(handles), None, None), 'handle_allowlist')
            si = _StartupEx(); si.startup.cb = c.sizeof(si); si.attributes = c.cast(attributes, c.c_void_p)
            # The desktop app's noninteractive station can deny USER32 DLL startup.
            si.startup.desktop = 'winsta0\\default'
            si.startup.flags = 0x100; si.startup.stdin, si.startup.stdout, si.startup.stderr = handles
            # Windows needs profile locations to construct an AppContainer token.
            # Only OS location variables, never credentials, .env or PYTHONPATH.
            allowed_env = {n: os.environ[n] for n in ('SystemRoot', 'SystemDrive', 'windir', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA') if n in os.environ}
            allowed_env.update(TEMP=str(cwd), TMP=str(cwd), PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
            env = c.create_unicode_buffer('\0'.join(f'{n}={v}' for n,v in sorted(allowed_env.items(), key=lambda p:p[0].upper())) + '\0\0')
            command = c.create_unicode_buffer(subprocess.list2cmdline([str(executable), *map(str, args)]))
            _check(k.CreateProcessW(str(executable), command, None, None, True,
                0x00080000 | 0x08000000 | 0x00000004 | 0x00000400, env, str(cwd), c.byref(si), c.byref(proc)), 'create_appcontainer')
            token = w.HANDLE()
            _check(a.OpenProcessToken(proc.process, 8, c.byref(token)), 'open_worker_token')
            flag = w.DWORD(); needed = w.DWORD()
            _check(a.GetTokenInformation(token, 29, c.byref(flag), c.sizeof(flag), c.byref(needed)), 'token_is_appcontainer')
            a.GetTokenInformation(token, 31, None, 0, c.byref(needed))
            sid_info = c.create_string_buffer(needed.value)
            _check(a.GetTokenInformation(token, 31, sid_info, needed.value, c.byref(needed)), 'token_package_sid')
            if flag.value != 1 or not a.EqualSid(c.cast(sid_info, c.POINTER(c.c_void_p))[0], self.sid):
                raise IsolationError('worker_token_not_expected_appcontainer')
            job = k.CreateJobObjectW(None, None); _check(job, 'create_job')
            limits = _JobLimits(); limits.basic.flags = 0x2000 | 0x8; limits.basic.active = 1
            _check(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)), 'job_limits')
            _check(k.AssignProcessToJobObject(job, proc.process), 'assign_job')
            if k.ResumeThread(proc.thread) == 0xffffffff: _check(False, 'resume_worker')
            resumed = True
            wait = k.WaitForSingleObject(proc.process, int(timeout_seconds * 1000))
            timed_out = wait == 258
            if wait not in (0, 258): _check(False, 'wait_worker')
            if timed_out:
                _check(k.TerminateProcess(proc.process, 124), 'timeout_terminate')
                k.WaitForSingleObject(proc.process, 5000)
            code = w.DWORD(); _check(k.GetExitCodeProcess(proc.process, c.byref(code)), 'worker_exit_code')
            for fd in (null, out_w, err_w): os.close(fd)
            null = out_w = err_w = None
            return dict(pid=proc.pid, token_is_appcontainer=True, package_sid=self.sid_string,
                        network_capabilities=0, inherited_handles='only_null_stdin_stdout_stderr_pipes', child_processes_allowed=False,
                        timed_out=timed_out, exit_code=code.value, elapsed_seconds=time.perf_counter()-started,
                        stdout=stdout.result(timeout=5), stderr=stderr.result(timeout=5))
        finally:
            if proc.process and not resumed: k.TerminateProcess(proc.process, 125)
            for fd in (null, out_w, err_w):
                if fd is not None: os.close(fd)
            for handle in (token, proc.thread, proc.process, job):
                if handle: k.CloseHandle(handle)
            k.DeleteProcThreadAttributeList(attributes)
            pool.shutdown(wait=True)
