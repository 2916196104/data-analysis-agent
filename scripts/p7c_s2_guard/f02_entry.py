"""Fresh interpreter entry. F02 never enables a real run without a verified OS boundary."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

GUARDS=('p7c_real_guard','s1_guard','s2_guard','f02_state','f02_sql','f02_network')


def verify_imports(directory,expected_hashes):
    directory=Path(directory).resolve()
    if any(name in sys.modules for name in GUARDS):
        raise RuntimeError('f02_stale_guard_cache')
    for name in GUARDS:
        p=directory/(name+'.py')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=expected_hashes.get(p.name):
            raise RuntimeError('f02_guard_hash_mismatch')
    sys.path.insert(0,str(directory))
    modules={name:importlib.import_module(name) for name in GUARDS}
    for name,m in modules.items():
        p=directory/(name+'.py')
        if (Path(m.__file__).resolve()!=p or Path(m.__spec__.origin).resolve()!=p
            or sys.modules[name] is not m):raise RuntimeError('f02_guard_origin')
    if modules['s1_guard'].base is not modules['p7c_real_guard'] or modules['s2_guard'].base is not modules['p7c_real_guard']:
        raise RuntimeError('f02_mixed_guard_graph')
    return modules


def wrapper_identity():
    from data_analysis_agent.jobs import store,submission
    from data_analysis_agent.jobs.worker import TaskWorker
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
    import subprocess
    directory=Path(__file__).resolve().parent
    functions=dict(enqueue=store.enqueue,submission=submission.enqueue,task=TaskWorker._execute,
                   sql=PostgresConnection._execute,popen=subprocess.Popen,rpc=PersistentEmbeddingProcess._rpc)
    records={}
    for key,fn in functions.items():
        p=Path(fn.__code__.co_filename).resolve()
        if p.parent!=directory:raise RuntimeError('f02_wrapper_origin')
        records[key]=dict(path=str(p),name=fn.__qualname__,closure_ids=[id(c.cell_contents) for c in (fn.__closure__ or ())])
    if store.enqueue is not submission.enqueue:raise RuntimeError('f02_route_capture')
    return records


def main(run_dir):
    if 'data_analysis_agent.api.app' in sys.modules or any(n.startswith('data_analysis_agent.api.routes.') for n in sys.modules):
        raise RuntimeError('f02_API_imported_before_guards')
    out=Path(run_dir).absolute()
    claim=json.loads((out/'claim.json').read_text(encoding='utf-8'))
    os.environ.update(P7C_REAL_DIR=str(out),LABEL_EXECUTION_MODE='local',PYTHONDONTWRITEBYTECODE='1')
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
    modules=verify_imports(Path(__file__).parent,claim['guard_hashes'])
    # Deliberately no override switch or unchecked attestation file.
    isolation=modules['f02_network'].isolation_status()
    if isolation['status']!='PASS':
        raise RuntimeError('f02_BLOCKED_OS_network_boundary_not_verified')
    modules['s2_guard'].install()
    wrapper_identity()
    from data_analysis_agent.jobs.store import init_tasks
    ledger=modules['p7c_real_guard'].LEDGER
    ledger.check_task_db(claim['task_db'])
    if Path(claim['task_db']).stat().st_size!=0:raise RuntimeError('f02_task_store_already_initialized')
    db='postgresql://rag_audit_ro@127.0.0.1:5432/reviews?hostaddr=127.0.0.1'
    init_tasks(claim['task_db'],db)
    from data_analysis_agent.api.app import create_app
    import uvicorn
    root=Path(__file__).resolve().parents[2]
    app=create_app(db,claim['task_db'],import_root=root/'data/private',env_file=root/'.env',
                   embedded_worker=True,allowed_origins=('http://127.0.0.1:5184',))
    uvicorn.run(modules['s2_guard'].S2Requests(app),host='127.0.0.1',port=8011,workers=1)


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('usage: f02_entry.py OWNED_NEW_RUN_DIR (currently BLOCKED)')
    main(sys.argv[1])
