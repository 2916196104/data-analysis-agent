"""P3-B local synthetic processes and snapshot/Agent regression."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess, shared_embedding_process, close_embedding_processes
from data_analysis_agent.reviews.embedding_store import EmbeddingStoreError
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalError, PostgresSemanticRepository
from data_analysis_agent.core.config import AgentSettings, ConfigurationError
import test_semantic_retrieval as srtests

WORKER=Path(__file__).parent/'fixtures/synthetic_embedding_worker.py'

class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory())
        self.client=PersistentEmbeddingProcess(sys.executable,self.directory,timeout=10,worker=WORKER)
        self.addCleanup(self.client.close)

    def test_real_process_load_once_and_cold_hot_same_vector(self):
        with patch.dict('os.environ',{'LLM_API_KEY':'SYNTHETIC-SECRET','DATABASE_URL':'SYNTHETIC-SECRET'}):
            cold=self.client.embed_texts(['合成查询'])
        first=self.client.last_metrics.copy()
        hot=self.client.embed_texts(['合成查询'])
        self.assertEqual(cold,hot)
        self.assertTrue(first['cold_start']);self.assertFalse(self.client.last_metrics['cold_start'])
        self.assertEqual(first['process_id'],self.client.last_metrics['process_id'])
        self.assertTrue(self.client.health())
        self.assertEqual((Path(self.directory)/'loads.txt').read_text(),'loaded\n')
        process=self.client.process;reader=self.client.reader
        self.client.close()
        self.assertIsNotNone(process.poll());self.assertFalse(reader.is_alive())

    def test_concurrent_requests_keep_their_own_vectors(self):
        questions=['合成'+str(i) for i in range(8)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda q:self.client.embed_texts([q]),questions))
        for q,vectors in zip(questions,results):
            self.assertEqual(vectors[0][sum(map(ord,q))%1024],1.0)
        self.assertEqual((Path(self.directory)/'loads.txt').read_text(),'loaded\n')

    def test_timeout_kills_and_latches_failure_without_retry(self):
        self.client.health();process=self.client.process;self.client.timeout=.2
        with self.assertRaises(EmbeddingStoreError):self.client.embed_texts(['hang'])
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.client.reader.is_alive())
        with self.assertRaises(EmbeddingStoreError):self.client.embed_texts(['正常'])
        self.assertEqual((Path(self.directory)/'loads.txt').read_text(),'loaded\n')

    def test_crash_and_invalid_vectors_fail_without_output(self):
        for query in ('crash','badvector'):
            with self.subTest(query=query):
                client=PersistentEmbeddingProcess(sys.executable,self.directory,timeout=10,worker=WORKER)
                try:
                    with self.assertRaises(EmbeddingStoreError):client.embed_texts([query])
                    self.assertTrue(client.failed);self.assertIsNone(client.process)
                finally:client.close()

    def test_wrong_response_identity_and_space_are_rejected(self):
        for change in ("r['request_id']='wrong'", "r['space_id']='wrong'"):
            script=Path(self.directory)/'bad.py'
            script.write_text("import sys,json\nr=json.loads(sys.stdin.readline())\n"+change+"\nprint(json.dumps(r),flush=True)\n",encoding='utf-8')
            client=PersistentEmbeddingProcess(sys.executable,self.directory,timeout=5,worker=script)
            try:
                with self.assertRaises(EmbeddingStoreError):client.health()
            finally:client.close()

    def test_configuration_and_shared_lifetime(self):
        values={'LLM_MODEL_ID':'fake','LLM_API_KEY':'fake','LLM_BASE_URL':'https://offline.invalid/v1',
                'RAG_PYTHON':sys.executable,'RAG_MODEL_CACHE':self.directory,'RAG_TIMEOUT_SECONDS':'17'}
        settings=AgentSettings.from_values(values)
        self.assertEqual(settings.rag_timeout_seconds,17)
        for value in ('0','601','nan'):
            with self.assertRaises(ConfigurationError):AgentSettings.from_values(values|{'RAG_TIMEOUT_SECONDS':value})
        try:
            a=shared_embedding_process(sys.executable,self.directory,17)
            self.assertIs(a,shared_embedding_process(sys.executable,self.directory,17))
        finally:close_embedding_processes()
        self.assertTrue(a.closed)

    def test_worker_exit_releases_actual_synthetic_model_process(self):
        from data_analysis_agent.jobs.worker import TaskWorker
        process=shared_embedding_process(sys.executable,self.directory,10)
        process.worker=WORKER
        self.addCleanup(close_embedding_processes)
        process.health();child=process.process
        worker=TaskWorker(Path(self.directory)/'business.sqlite',Path(self.directory)/'tasks.sqlite',
                          import_root=self.directory,env_file=Path(self.directory)/'unused.env')
        worker.stop_requested.set()
        with patch('data_analysis_agent.jobs.worker.heartbeat'),patch.object(worker.lock,'release') as release:
            worker._loop()
        self.assertIsNotNone(child.poll());self.assertTrue(process.closed)
        release.assert_called_once()


class SnapshotReuseTests(unittest.TestCase):
    def test_actual_vector_sql_and_batch_adapter_match_legacy_documents(self):
        self.enterContext(patch('data_analysis_agent.reviews.analytics.check_labeling_schema'))
        from data_analysis_agent.reviews.embedding_store import search_effective_opinions, SPACE_ID
        from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalService
        helper=srtests.PostgresAdapterTests()
        _,_,corpus,matches,_,_=helper.setup_adapter()
        sources=[srtests.source(1),srtests.source(2)]
        class Rows:
            def __init__(self,rows):self.rows=rows
            def fetchall(self):return self.rows
        class Connection(srtests.FakePG):
            def execute(inner,query,parameters=None):
                if 'embedding <=>' in query:
                    inner.commands.append((query,parameters))
                    return Rows([dict(asdict(m.document),distance=m.cosine_distance) for m in matches])
                if 'FROM reviews r JOIN import_runs' in query:
                    inner.commands.append((query,parameters))
                    return Rows([dict(review_id=s.state.review_id,text=s.review_text,content_sha256=s.state.review_content_sha256,
                        **{k:getattr(s.opinions[0],k) for k in ('source','dataset_version','product_id','published_at','import_run_id','line_number','file_sha256')}) for s in sources])
                return super().execute(query,parameters)
        conn=Connection(active=True)
        with patch('data_analysis_agent.reviews.embedding_store.read_effective_corpus',return_value=corpus):
            legacy=search_effective_opinions(conn,srtests.VECTOR,options=srtests.OPTIONS,embedding_space_id=SPACE_ID)
        legacy_sql=next(x for x in conn.commands if isinstance(x,tuple))
        conn.commands=[]
        with patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus) as read:
            result=SemanticRetrievalService(encoder=srtests.FakeEncoder([]),repository=PostgresSemanticRepository(conn),scope=srtests.OPTIONS).search({'question':'合成'})
        read.assert_called_once()
        self.assertEqual([(e.document,e.cosine_distance) for e in result.evidence],[(m.document,m.cosine_distance) for m in legacy])
        queries=[x for x in conn.commands if isinstance(x,tuple)]
        self.assertEqual(queries[0],legacy_sql)
        self.assertEqual(len(queries),2)
        self.assertEqual(queries[1][1],('SYNTHETIC','synthetic-v1','SYNTHETIC-P1','2031-01-01','2031-01-02',1,2))

    def test_reuse_is_per_snapshot_and_old_handle_expires(self):
        helper=srtests.PostgresAdapterTests()
        service,conn,corpus,matches,context,enc=helper.setup_adapter(srtests.FakePG(active=True))
        with patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus) as read, \
             patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',return_value=matches), \
             patch('data_analysis_agent.reviews.semantic_retrieval.load_review_contexts',side_effect=context) as batch:
            repo=PostgresSemanticRepository(conn)
            with repo.snapshot(service.scope) as snap:
                snap.search(srtests.VECTOR,top_k=5,aspect=None,sentiment=None)
                sources=snap.load_sources(tuple(m.document.stable_versioned_opinion_key for m in matches))
                self.assertEqual(len(sources),2)
                read.assert_called_once();batch.assert_called_once()
                self.assertEqual(batch.call_args.args[2],[1,2])
            with self.assertRaisesRegex(SemanticRetrievalError,'expired'):snap.corpus()
            with repo.snapshot(service.scope) as fresh:fresh.corpus()
            self.assertEqual(read.call_count,2)

    def test_changed_transaction_identity_cannot_reuse_corpus(self):
        helper=srtests.PostgresAdapterTests()
        service,conn,corpus,_,_,_=helper.setup_adapter(srtests.FakePG(active=True))
        with patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus):
            with PostgresSemanticRepository(conn).snapshot(service.scope) as snap:
                snap.corpus()
                with patch.object(conn,'execute',return_value=srtests.Cursor(('OTHER',srtests.datetime(2031,3,1,tzinfo=srtests.timezone.utc)))):
                    with self.assertRaisesRegex(SemanticRetrievalError,'snapshot_identity_changed'):snap.corpus()

    def test_prepared_vector_is_bound_and_used_once(self):
        service,encoder,_,_,_=srtests.fixture()
        service.prepare(dict(question='合成'))
        service.search(dict(question='合成'))
        self.assertEqual(len(encoder.calls),1)
        service.search(dict(question='合成'));self.assertEqual(len(encoder.calls),2)
        service.prepare(dict(question='原问题'))
        with self.assertRaisesRegex(SemanticRetrievalError,'prepared_query_mismatch'):service.search(dict(question='改写'))

    def test_missing_duplicate_and_drifted_batch_contexts_fail(self):
        for mode in ('missing','duplicate','body','import'):
            helper=srtests.PostgresAdapterTests()
            service,conn,corpus,matches,context,_=helper.setup_adapter()
            def broken(db,scope,ids):
                rows=context(db,scope,ids)
                if mode=='missing':return rows[:1]
                if mode=='duplicate':return [rows[0],rows[0]]
                rows[0]['text' if mode=='body' else 'import_run_id']='DRIFT'
                return rows
            with self.subTest(mode=mode), \
                 patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',return_value=conn), \
                 patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus), \
                 patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',return_value=matches), \
                 patch('data_analysis_agent.reviews.semantic_retrieval.load_review_contexts',side_effect=broken):
                with self.assertRaises(SemanticRetrievalError):service.search({'question':'合成'})

if __name__=='__main__':unittest.main()
