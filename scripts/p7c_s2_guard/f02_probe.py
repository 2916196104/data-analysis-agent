"""Offline test helper only. Not a service/model entry or execution authorization."""
import sys,os,json
from pathlib import Path

def main():
    mode,directory=sys.argv[1:3]
    out=Path(directory).resolve();os.environ['P7C_REAL_DIR']=str(out)
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
    def offline(event,args):
        if event in ('socket.connect','socket.sendto','socket.getaddrinfo','subprocess.Popen'):
            raise RuntimeError('offline_helper_IO_forbidden')
    sys.addaudithook(offline)
    if mode in ('consume','crash'):
        from f02_state import RunLedger
        a=json.loads((out/'claim.json').read_text(encoding='utf-8'))
        try:RunLedger(out,a).take('http_submission')
        except RuntimeError:
            print('DENIED');return 42
        if mode=='crash':os._exit(17)
        print('ADMITTED');return 0
    from f02_entry import verify_imports
    here=Path(__file__).parent
    import hashlib,types
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in here.glob('*.py')}
    if mode=='stale':sys.modules['p7c_real_guard']=types.ModuleType('p7c_real_guard')
    if mode=='hash':hashes['s2_guard.py']='bad'
    try:
        modules=verify_imports(here,hashes)
        if mode=='origin':
            print(json.dumps({k:str(m.__file__) for k,m in modules.items()}));return 0
        raise AssertionError('expected import refusal')
    except RuntimeError as exc:
        if mode not in ('stale','hash'):raise
        print(str(exc));return 0

if __name__=='__main__':raise SystemExit(main())
