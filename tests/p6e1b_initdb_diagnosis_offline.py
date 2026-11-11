"""显式运行的历史诊断：需要本轮缓存执行器，不加入常规测试发现。无真实子进程。"""
import ast
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch

EXECUTOR=Path(__file__).resolve().parents[1]/'.cache/p6e1b-exec/execute.py'


class InitdbDiagnosisTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','psycopg.connect','subprocess._winapi.CreateProcess'):
            blocked=self.enterContext(patch(target,side_effect=AssertionError('read-only diagnosis')))
            self.addCleanup(blocked.assert_not_called)
        self.folder=Path(self.enterContext(TemporaryDirectory(prefix='initdb-diagnosis-unit-')))
        self.tree=ast.parse(EXECUTOR.read_text(encoding='utf-8'))
        function=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='command')
        scope={'subprocess':subprocess,'BIN':Path('C:/OFFLINE-TEST-NOT-A-PROGRAM'),
               'PRIVATE':self.folder,'STAGE':'unit','environment':lambda:{}}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<executor-command-only>','exec'),scope)
        self.command=scope['command']

    def fake_popen(self):
        instances=[]
        class FakeProcess:
            def __init__(self,args,**kwargs):
                self.args=args;self.options=kwargs;self.communications=[];self.kills=0;self.closed=False
                instances.append(self)
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def communicate(self,input=None,timeout=None):
                self.communications.append({'input':input,'timeout':timeout})
                if timeout is not None:raise subprocess.TimeoutExpired(self.args,timeout)
                return 'SYNTHETIC-PARTIAL-STDOUT','SYNTHETIC-PARTIAL-STDERR'
            def kill(self):self.kills+=1
        return FakeProcess,instances

    def test_actual_initializer_uses_password_file_without_password_prompt_flag(self):
        initialize=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='initialize')
        call=next(n for n in ast.walk(initialize) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                  and n.func.id=='command' and isinstance(n.args[0],ast.Constant) and n.args[0].value=='initdb')
        literals=[n.value for n in ast.walk(call.args[1]) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertIn('--pwfile',literals)
        self.assertNotIn('-W',literals);self.assertNotIn('--pwprompt',literals)
        self.assertEqual(next(k.value.value for k in call.keywords if k.arg=='timeout'),45)

    def test_windows_timeout_collects_output_after_direct_child_kill(self):
        fake,instances=self.fake_popen()
        with patch('subprocess.Popen',fake),self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.command('initdb',['--pwfile','unit-only'],timeout=45)
        process=instances[0]
        self.assertEqual(process.kills,1);self.assertTrue(process.closed)
        self.assertEqual([c['timeout'] for c in process.communications],[45,None])
        self.assertEqual(caught.exception.stdout,'SYNTHETIC-PARTIAL-STDOUT')
        self.assertEqual(caught.exception.stderr,'SYNTHETIC-PARTIAL-STDERR')

    def test_timeout_output_is_not_saved_by_current_command_wrapper(self):
        fake,_=self.fake_popen()
        with patch('subprocess.Popen',fake),self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.command('initdb',[],timeout=45)
        self.assertEqual(list(self.folder.iterdir()),[])
        exception_text=''.join(traceback.format_exception(caught.exception))
        self.assertNotIn('SYNTHETIC-PARTIAL-STDOUT',exception_text)
        self.assertNotIn('SYNTHETIC-PARTIAL-STDERR',exception_text)

    def test_initdb_stdin_is_unspecified_and_both_outputs_use_pipes(self):
        fake,instances=self.fake_popen()
        with patch('subprocess.Popen',fake),self.assertRaises(subprocess.TimeoutExpired):
            self.command('initdb',[],timeout=45)
        options=instances[0].options
        self.assertIsNone(options.get('stdin'))
        self.assertEqual(options['stdout'],subprocess.PIPE)
        self.assertEqual(options['stderr'],subprocess.PIPE)
        self.assertEqual(options['creationflags'],subprocess.CREATE_NO_WINDOW)
        self.assertEqual(options['encoding'],'utf-8');self.assertEqual(options['errors'],'replace')

    def test_normal_return_does_save_the_two_logs(self):
        reply=SimpleNamespace(returncode=0,stdout='SYNTHETIC VERSION',stderr='')
        with patch('subprocess.run',return_value=reply):
            self.assertEqual(self.command('initdb',['--version']),'SYNTHETIC VERSION')
        self.assertEqual((self.folder/'initdb-unit.stdout').read_text(),'SYNTHETIC VERSION')
        self.assertEqual((self.folder/'initdb-unit.stderr').read_text(),'')


if __name__=='__main__':unittest.main()
