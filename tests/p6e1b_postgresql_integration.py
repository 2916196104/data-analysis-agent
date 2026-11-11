"""真实 PostgreSQL 专项入口。无授权退出2；不建/删库，不迁移生产，不用SQLite或PG替身。"""
import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from p6e1b_environment import E1BBlocked, PURPOSE, load_authorization
from data_analysis_agent.agents.review_budget import P6CostAuthorization, P6OfflineTransport, P6SharedBudget
from data_analysis_agent.agents.review_execution import execute_p6_prepared
from data_analysis_agent.agents.review_multi_aspect import freeze_p6_goal
from data_analysis_agent.agents.review_plan import build_p6_query_plan
from data_analysis_agent.agents.review_preparation import prepare_p6_analysis, P6PreparationError
from data_analysis_agent.agents.review_prompts import PROMPT_VERSION
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.database import DatabaseError, PostgresConnection, connect_database, insert_id
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.analysis_state import has_saved_integrity
from data_analysis_agent.jobs.store import enqueue, claim_task, get_task, worker_started
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.human_review import save_human_review
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation, append_annotation_on_connection
from data_analysis_agent.reviews.loader import normalize_review
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl

COUNTS = {'service':5,'effectiveness':4,'logistics':4,'quality':2,'price':1,'packaging':0}
# 只读事务等待提交与线程退出共用2秒；低于正常角色的5秒idle上限。
COMMIT_BARRIER_SECONDS = 2.0
QUOTES = {'service':'客服回复缓慢','effectiveness':'降温速度慢','logistics':'配送延误',
          'quality':'外壳开裂','price':'价格偏高','packaging':'包装破损'}
SETTINGS = AgentSettings(model='P6-E1B-SYNTHETIC',api_key='not-a-real-key',base_url='https://offline.invalid/v1',
    semantic_parser_enabled=False,p6_preparation_enabled=True,p6_execution_enabled=True,p6_publication_enabled=True)
EVIDENCE = {'version':'p6-e1b-postgresql-evidence-v1','role_checks':{},'events':[], 'cases':{},
            'paid_model_calls':0,'embedding_calls':0,'production_database_operations':0}


def event(kind, **fields):
    EVIDENCE['events'].append({'kind':kind,'at':utc_now(),**fields})


def verify_live_environment(config):
    """只访问已固定的两个专用库；全部身份/权限/标记通过前不插入夹具。"""
    for role, target in config.dsns.items():
        with closing(connect_database(target,readonly=True)) as connection:
            database, user, version = connection.execute(
                'SELECT current_database(), current_user, current_setting(\'server_version_num\')').fetchone()
            if (database != urlsplit(target).path[1:]
                    or hashlib.sha256(user.encode()).hexdigest() != config.manifest['role_sha256'][role]):
                raise E1BBlocked('live_database_or_role_mismatch')
            flags = connection.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                                       'FROM pg_roles WHERE rolname=current_user').fetchone()
            if any(flags): raise E1BBlocked('test_role_has_excessive_global_privileges')
            for target_role in ('creator','reader','writer','task'):
                other_user=urlsplit(config.dsns[target_role]).username
                if other_user!=user and connection.execute(
                        "SELECT pg_has_role(current_user,?,'MEMBER')",(other_user,)).fetchone()[0]:
                    raise E1BBlocked('cross_role_membership_present')
            marker = connection.execute('SELECT run_id,purpose,authorization_sha256 FROM public.p6_e1b_test_scope').fetchall()
            if [tuple(r) for r in marker] != [(config.run_id,PURPOSE,config.authorization_sha256)]:
                raise E1BBlocked('database_run_marker_mismatch')
            create = connection.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]
            if create != (role in ('creator','task_creator')):
                raise E1BBlocked('schema_role_separation_failed')
            other = 'reader' if role == 'task' else 'task'
            if role not in ('creator','task_creator') and connection.execute(
                    "SELECT has_database_privilege(current_user,?,'CONNECT')",
                    (urlsplit(config.dsns[other]).path[1:],)).fetchone()[0]:
                raise E1BBlocked('cross_database_connect_privilege_present')
            tables = ('tasks','worker_state') if role in ('task','task_creator') else (
                'reviews','products','import_runs','review_annotations','review_opinions','review_human_reviews','review_human_opinions')
            for table in tables:
                privileges = {p:bool(connection.execute('SELECT has_table_privilege(current_user,?,?)',
                    ('public.'+table,p)).fetchone()[0]) for p in ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE')}
                if (not privileges['SELECT'] or (role=='reader' and any(privileges[p] for p in privileges if p!='SELECT'))
                        or (role!='reader' and not all(privileges[p] for p in ('INSERT','UPDATE')))):
                    raise E1BBlocked('table_role_separation_failed')
            for row in connection.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                          "WHERE n.nspname='public' AND c.relkind='S'"):
                usage=connection.execute("SELECT has_sequence_privilege(current_user,?,'USAGE')",('public.'+row[0],)).fetchone()[0]
                if bool(usage) != (role!='reader'):raise E1BBlocked('sequence_role_separation_failed')
            if role == 'reader' and connection.execute(
                    "SELECT has_table_privilege(current_user,'public.reviews','UPDATE')").fetchone()[0]:
                raise E1BBlocked('reader_can_write')
            if role in ('task','writer','reader') and connection.execute(
                    "SELECT has_table_privilege(current_user,'public.p6_e1b_test_scope','INSERT,UPDATE,DELETE,TRUNCATE')").fetchone()[0]:
                raise E1BBlocked('runtime_role_can_rewrite_test_marker')
            limits = dict(connection.execute("SELECT name,setting::int FROM pg_settings WHERE name IN "
                "('statement_timeout','lock_timeout','idle_in_transaction_session_timeout')").fetchall())
            if len(limits) != 3 or any(not 0 < value <= 5000 for value in limits.values()):
                raise E1BBlocked('bounded_role_timeouts_required')
            EVIDENCE['role_checks'][role] = {'identity_pin_matches':True,'marker_matches':True,
                'global_privileges_restricted':True,'schema_create':bool(create),
                'server_version_num':int(version),'timeouts_ms':limits}
    # 独立库也不能夹带其他范围的数据；不读取/复制正文到报告。
    prefix = f'p6-e1b-{config.run_id}-'
    with closing(connect_database(config.dsns['reader'],readonly=True)) as connection:
        if connection.execute('SELECT COUNT(*) FROM reviews WHERE dataset_version NOT LIKE ?', (prefix+'%',)).fetchone()[0]:
            raise E1BBlocked('foreign_data_present_in_test_database')
    with closing(connect_database(config.dsns['task'],readonly=True)) as connection:
        for row in connection.execute('SELECT parameters_json,status FROM tasks'):
            if not json.loads(row[0]).get('dataset_version','').startswith(prefix) or row[1] in ('queued','running'):
                raise E1BBlocked('foreign_or_unfinished_tasks_present')


class P6E1BPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_authorization(os.environ)
        EVIDENCE.update(run_id=cls.config.run_id,authorization_sha256=cls.config.authorization_sha256,
            identity_pins=cls.config.manifest['identity_sha256'],cleanup_policy='close_connections_only; no database/schema/data drop')
        verify_live_environment(cls.config)

    def setUp(self):
        self.handles, self.calls, self.snapshots = [], [], []
        self.addCleanup(self.close_handles)
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='p6-e1b-fixture-')))
        self.case = self._testMethodName.split('_')[1]
        self.dataset = f'p6-e1b-{self.config.run_id}-{self.case}-{uuid4().hex[:8]}'
        self.conversation = self.dataset
        self.identity = {'model':'P6-E1B-LABELS','base_url':'https://labels.invalid/v1','prompt_version':'synthetic-v1'}
        self.scope = {**self.identity,'source':'amazon_reviews_2023','dataset_version':self.dataset,
            'product_ids':['100013588053'],'date_from':'2023-02-01','date_to':'2023-02-28',
            'baseline_from':'2023-01-01','baseline_to':'2023-01-31','as_of':None}
        self.agent_identity = {'model':SETTINGS.model,'base_url':SETTINGS.base_url,'prompt_version':PROMPT_VERSION}
        allowed = set(self.config.dsns.values())
        original = connect_database
        def tracked(target, **kwargs):
            if target not in allowed: raise E1BBlocked('unexpected_database_target')
            if datetime.now(timezone.utc) >= datetime.fromisoformat(self.config.manifest['expires_at']):
                raise E1BBlocked('authorization_expired')
            connection = original(target, **kwargs)
            self.handles.append(connection)
            connection.execute("SELECT set_config('application_name',?,false)", ('p6e1b_'+self.config.run_id,))
            return connection
        # 记录真实连接，未替换 PostgreSQL 对象/SQL/行结果。
        for name in ('data_analysis_agent.reviews.store.connect_database',
                     'data_analysis_agent.jobs.store.connect_database',
                     'data_analysis_agent.jobs.worker.connect_database'):
            self.enterContext(patch(name,side_effect=tracked))
        for name in ('openai.OpenAI','data_analysis_agent.agents.reviews.create_llm',
                     'data_analysis_agent.agents.review_intent._llm_intent',
                     'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            blocked = self.enterContext(patch(name,side_effect=AssertionError('E1B forbids model/embedding')))
            self.addCleanup(blocked.assert_not_called)
        self.enterContext(patch('data_analysis_agent.jobs.worker.configured_agent_identity',return_value=(SETTINGS,self.agent_identity)))
        self.enterContext(patch('data_analysis_agent.agents.review_budget.make_execution_budget',side_effect=lambda _:self.budget()))
        rows = [{'parent_asin':'100013588053','user_id':self.dataset+str(i),
            'text':'。'.join(QUOTES[a] for a,n in COUNTS.items() if i < n) or '暂无明确反馈',
            'timestamp':1675987200000} for i in range(6)]
        path = self.root/'initial.jsonl';path.write_text('\n'.join(canonical_json(r) for r in rows),encoding='utf-8')
        import_amazon_jsonl(self.config.dsns['writer'],reviews_path=path,dataset_version=self.dataset)
        for row in pending_reviews(self.config.dsns['writer'],**self.identity,dataset_version=self.dataset,limit=100):
            opinions = [dict(aspect=a,sentiment='negative',quote=q) for a,q in QUOTES.items() if q in row['text']]
            save_annotation(self.config.dsns['writer'],row,**self.identity,status='success',
                opinions=validate_opinions(json.dumps({'opinions':opinions}),row['text']))
        self.worker = TaskWorker(self.config.dsns['reader'],self.config.dsns['task'],import_root=self.root,
                                 env_file=self.root/'unused.env')
        worker_started(self.config.dsns['task'],self.worker.worker_id)
        self.ranking = self.work('100013588053的差评主要集中在哪些方面？')
        self.assertEqual(self.ranking['status'],'succeeded')
        context = self.context()
        self.auth = {'conversation_id':self.conversation,'agent_identity':self.agent_identity,
                     'ranking_task':self.ranking,'context':context}
        self.goal = freeze_p6_goal('具体体现在哪里？',scope=self.ranking['result']['scope'],**self.auth).goal
        self.assertIsNotNone(self.goal)
        self.plan = build_p6_query_plan(self.goal)

    def tearDown(self):
        if getattr(self,'worker',None) and self.worker.thread is not None:self.worker.stop()
        leaked = [connection for connection in self.handles if not connection.raw.closed]
        for connection in leaked:
            try: connection.rollback()
            except DatabaseError: pass
            finally: connection.close()
        self.assertFalse(leaked, 'application connection was left open before test cleanup')
        self.assert_released()

    def close_handles(self):
        if getattr(self,'worker',None) and self.worker.thread is not None:self.worker.stop()
        for connection in self.handles:
            if not connection.raw.closed:
                try:connection.rollback()
                except DatabaseError:pass
                finally:connection.close()

    def work(self, question, parent=None, *, background=False):
        params = {**self.scope,'selection_scope':self.scope,'question':question,'rules':{},
                  'conversation_id':self.conversation,'model_identity':self.agent_identity}
        if parent:params['context_task_id']=parent
        task_id = enqueue(self.config.dsns['task'],'analysis',params)['task']['task_id']
        if not background:self.worker._execute(claim_task(self.config.dsns['task'],self.worker.worker_id))
        deadline = monotonic()+15
        while monotonic()<deadline:
            task = get_task(self.config.dsns['task'],task_id)
            if task['status'] not in ('queued','running'):return task
            if not background:break
        self.fail('isolated task did not terminate within the test bound')

    def context(self):
        return load_analysis_context(self.config.dsns['task'],{**self.scope,'selection_scope':self.scope,
            'context_task_id':self.ranking['task_id'],'conversation_id':self.conversation})

    def budget(self):
        def respond(request):
            self.calls.append((request['aspect'],request['stage']))
            body = json.loads(request['messages'][1]['content'])
            if request['stage']=='topic_discovery':
                content={'themes':[{'n':QUOTES[request['aspect']],'d':QUOTES[request['aspect']],
                    'q':[body['opinions'][0]['opinion_id']]}]}
            else:
                content={'rows':[{'i':r['opinion_id'],'s':'a','m':[['t1',{'quote_id':r['opinion_id']}]]} for r in body['opinions']]}
            return SimpleNamespace(model=SETTINGS.model,id='e1b-fixed-response',usage={'prompt_tokens':50,'completion_tokens':20},
                choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(tool_calls=None,content=json.dumps(content,ensure_ascii=False)))])
        policy=P6CostAuthorization(authorized=True,model=SETTINGS.model,base_url=SETTINGS.base_url,currency='TEST',
            amount_limit=Decimal('100'),input_price=Decimal('0.0001'),output_price=Decimal('0.0001'),
            billing_categories=('input','output'),token_upper_bound_id='synthetic-only',token_upper_bound=lambda p,b:1000)
        return P6SharedBudget(policy,offline_transport=P6OfflineTransport(respond))

    def snapshot(self, connection, label):
        row = connection.execute('SELECT pg_backend_pid(),txid_current_snapshot()::text,'
            "current_setting('transaction_isolation'),current_setting('transaction_read_only')").fetchone()
        value={'label':label,'pid':row[0],'snapshot':row[1],'isolation':row[2],'readonly':row[3]}
        self.snapshots.append(value);event('read_snapshot',case=self.case,**value)

    def prepare(self):
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as connection, connection:
            self.primary_connection=connection
            connection.execute('BEGIN');self.snapshot(connection,'before_prepare')
            prepared=prepare_p6_analysis(connection,self.goal,self.plan,**self.auth)
            self.snapshot(connection,'after_prepare')
        return prepared

    def same_snapshot(self):
        self.assertEqual(len({r['pid'] for r in self.snapshots}),1)
        self.assertEqual(len({r['snapshot'] for r in self.snapshots}),1)
        self.assertTrue(all(r['isolation']=='repeatable read' and r['readonly']=='on' for r in self.snapshots))

    def concurrent_commit(self, action):
        ready, committed, cancelled = threading.Event(), threading.Event(), threading.Event(); errors=[]
        idle_ms=EVIDENCE['role_checks']['reader']['timeouts_ms']['idle_in_transaction_session_timeout']
        barrier_seconds=min(COMMIT_BARRIER_SECONDS,idle_ms/2000)
        def writer():
            if not ready.wait(10):errors.append('barrier_timeout');committed.set();return
            if cancelled.is_set():committed.set();return
            try:action();event('writer_committed',case=self.case)
            except Exception:errors.append('writer_failed')
            finally:committed.set()
        thread=threading.Thread(target=writer);thread.start()
        def release():
            deadline=monotonic()+barrier_seconds
            event('reader_at_barrier',case=self.case);ready.set()
            self.assertTrue(committed.wait(max(0,deadline-monotonic())),'writer did not commit at barrier')
            thread.join(max(0,deadline-monotonic()));self.assertFalse(thread.is_alive());self.assertFalse(errors)
        def abort_and_join():
            cancelled.set();ready.set();thread.join(10)
            self.assertFalse(thread.is_alive(),'writer did not terminate after reader cleanup')
        return release, thread, abort_and_join

    def append_review(self):
        """同一真实W事务提交新来源、评论和合法标注；不调用LLM。"""
        raw={'parent_asin':'100013588053','user_id':self.dataset+'added','text':QUOTES['packaging'],'timestamp':1676073600000}
        path=self.root/'added.jsonl';path.write_text(canonical_json(raw)+'\n',encoding='utf-8')
        row=normalize_review(raw);digest=content_sha256(row)
        with closing(connect_review_database(self.config.dsns['writer'])) as w, w:
            w.execute('BEGIN')
            source=insert_id(w,'INSERT INTO import_runs(source,dataset_version,kind,file_path,file_sha256,started_at,completed_at) VALUES (?,?,?,?,?,?,?)',
                ('amazon_reviews_2023',self.dataset,'reviews',str(path),hashlib.sha256(path.read_bytes()).hexdigest(),utc_now(),utc_now()))
            review=insert_id(w,'INSERT INTO reviews(source,dataset_version,source_record_id,product_id,asin,user_id,title,text,has_text,rating,published_at,fetched_at,raw_json,content_sha256,import_run_id,line_number) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('amazon_reviews_2023',self.dataset,digest,row['product_id'],row['asin'],row['user_id'],row['title'],row['text'],1,
                 row['rating'],row['published_at'],utc_now(),canonical_json(raw),digest,source,1))
            append_annotation_on_connection(w,{'id':review,'text':row['text'],'content_sha256':digest},**self.identity,
                status='success',opinions=validate_opinions(json.dumps({'opinions':[{'aspect':'packaging','sentiment':'negative','quote':row['text']}]}),row['text']))

    def revise(self, aspect, *, change_counts):
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as connection, connection:
            connection.execute('BEGIN')
            data=analytics.get_aspect_opinions(connection,**{k:v for k,v in self.scope.items() if not k.startswith('baseline_')},aspect=aspect,sentiment='negative')
            item=data['items'][0]
        # 合法人工修订提交完整观点集合，不能把当前维度筛选后的列表当作整条评论全部观点。
        opinions=[{'aspect':a,'sentiment':'negative',
                   'quote':'回复缓慢' if not change_counts and a=='service' else q}
                  for a,q in QUOTES.items() if q in item['text'] and not (change_counts and a==aspect)]
        save_human_review(self.config.dsns['writer'],item['review_id'],annotation_id=item['annotation_id'],
            expected_revision_id=item['revision_id'],action='corrected',reviewer='P6-E1B-SYNTHETIC',opinions=opinions)

    def assert_released(self):
        for role in ('creator','task_creator'):
            with closing(connect_database(self.config.dsns[role],readonly=True)) as monitor:
                deadline=monotonic()+5
                while monotonic()<deadline:
                    count=monitor.execute('SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() '
                        'AND application_name=? AND pid<>pg_backend_pid()',('p6e1b_'+self.config.run_id,)).fetchone()[0]
                    if count==0:break
                self.assertEqual(count,0,'test sessions remain on the PostgreSQL server')

    def test_PG01_readonly_protocol_and_release(self):
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as connection:
            connection.execute('BEGIN');self.snapshot(connection,'protocol')
            self.assertEqual(connection.execute('SHOW timezone').fetchone()[0],'UTC')
            with self.assertRaises(DatabaseError):
                connection.execute('UPDATE reviews SET text=text WHERE dataset_version=?',(self.dataset,))
            connection.rollback();self.assertFalse(connection.in_transaction)
        self.assertTrue(connection.raw.closed);self.same_snapshot()

    def test_PG02_append_between_statistics_and_prefetch(self):
        original=analytics.get_top_issues
        release,thread,abort=self.concurrent_commit(self.append_review)
        def issues(connection,**scope):
            data=original(connection,**scope);self.snapshot(connection,'statistics');release();return data
        try:
            with patch.object(analytics,'get_top_issues',side_effect=issues):prepared=self.prepare()
        finally:abort()
        self.same_snapshot()
        self.assertEqual(prepared.validate()['current_snapshot']['denominator']['value'],6)
        self.assertEqual([prepared.get_input(a)['total'] for a in self.goal.required_aspects],[5,4,4])
        current=analytics.get_top_issues(self.config.dsns['reader'],**{k:v for k,v in self.scope.items() if not k.startswith('baseline_')})
        self.assertEqual(current['current']['successful_reviews'],7)

    def test_PG03_revision_between_aspect_reads(self):
        original=analytics.get_aspect_opinions
        release,thread,abort=self.concurrent_commit(lambda:self.revise('effectiveness',change_counts=True))
        def opinions(connection,**scope):
            data=original(connection,**scope)
            if connection is self.primary_connection:
                self.snapshot(connection,scope['aspect'])
                if scope['aspect']=='service':release()
            return data
        try:
            with patch.object(analytics,'get_aspect_opinions',side_effect=opinions):prepared=self.prepare()
        finally:abort()
        self.same_snapshot();self.assertEqual(prepared.get_input('effectiveness')['total'],4)
        self.assertTrue(all(r['sentiment']=='negative' for r in prepared.get_input('effectiveness')['opinions']))
        current=analytics.get_top_issues(self.config.dsns['reader'],**{k:v for k,v in self.scope.items() if not k.startswith('baseline_')})
        self.assertEqual(current['current']['aspects']['effectiveness']['negative_review_count'],3)

    def test_PG04_changed_count_stops_without_prefetch_or_reselection(self):
        self.append_review();self.calls.clear()
        with patch.object(analytics,'get_aspect_opinions',side_effect=AssertionError('stale ranking must not prefetch')) as blocked:
            task=self.work('具体体现在哪里？',self.ranking['task_id'])
        blocked.assert_not_called();self.assertEqual(self.calls,[])
        self.assertEqual(task['result']['p6_preparation']['reason'],'stale_ranking')
        self.assertEqual(task['result']['query_executions'],1)
        self.assertEqual(task['result']['request_goal']['required_aspects'],list(self.goal.required_aspects))
        self.ranking=self.work('100013588053的差评主要集中在哪些方面？')
        refreshed=self.work('具体体现在哪里？',self.ranking['task_id'])
        self.assertTrue(refreshed['result']['task_acceptance']['complete'])

    def test_PG05_same_counts_content_revision_boundary(self):
        old=self.prepare();self.revise('service',change_counts=False);new=self.prepare()
        self.assertEqual(old.validate()['current_snapshot'],new.validate()['current_snapshot'])
        self.assertNotEqual(old.get_input('service')['opinions'],new.get_input('service')['opinions'])
        self.assertNotEqual(old.payload_sha256,new.payload_sha256)
        event('count_only_freshness_boundary',case=self.case,old_ranking_still_accepted=True)

    def test_PG06_frozen_input_after_release_never_requeries(self):
        prepared=self.prepare();before=prepared.get_input('service');self.append_review()
        self.assertEqual(prepared.get_input('service'),before)
        self.assert_released()
        with patch('data_analysis_agent.reviews.store.connect_review_database',side_effect=AssertionError('no reconnect')), \
             patch('psycopg.connect',side_effect=AssertionError('no classification database connection')), \
             patch.object(analytics,'get_aspect_opinions',side_effect=AssertionError('no prefetch')), \
             patch.object(analytics,'get_top_issues',side_effect=AssertionError('no statistics')):
            result=execute_p6_prepared(prepared,settings=SETTINGS,budget=self.budget(),**self.auth)
            self.assertTrue(result.acceptance['complete'])
            broken=replace(prepared,payload_json='{}')
            with self.assertRaises(ValueError):broken.validate()

    def test_PG07_timeouts_errors_disconnect_and_worker_abort(self):
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as connection:
            connection.execute('BEGIN');connection.execute("SET LOCAL statement_timeout='50ms'")
            with self.assertRaises(DatabaseError) as caught:connection.execute('SELECT pg_sleep(0.2)')
            self.assertEqual(getattr(caught.exception.__context__,'sqlstate',None),'57014');connection.rollback()
            connection.execute('BEGIN')
            with self.assertRaises(DatabaseError):connection.execute('SELECT unknown_p6_e1b_column FROM reviews')
            connection.rollback()
        with closing(connect_review_database(self.config.dsns['writer'])) as writer, writer:
            writer.execute('BEGIN');writer.execute('LOCK TABLE review_opinions IN ACCESS EXCLUSIVE MODE')
            with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as reader:
                reader.execute('BEGIN');reader.execute("SET LOCAL lock_timeout='50ms'")
                with self.assertRaises(DatabaseError) as caught:reader.execute('SELECT COUNT(*) FROM review_opinions')
                self.assertEqual(getattr(caught.exception.__context__,'sqlstate',None),'55P03');reader.rollback()
            writer.rollback()
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as idle:
            idle.execute("SET idle_in_transaction_session_timeout='100ms'");idle.execute('BEGIN');idle.execute('SELECT 1')
            pid=idle.raw.info.backend_pid
            with closing(connect_database(self.config.dsns['creator'],readonly=True)) as monitor:
                deadline=monotonic()+5
                while monotonic()<deadline and monitor.execute('SELECT COUNT(*) FROM pg_stat_activity WHERE pid=?',(pid,)).fetchone()[0]:pass
                self.assertEqual(monitor.execute('SELECT COUNT(*) FROM pg_stat_activity WHERE pid=?',(pid,)).fetchone()[0],0)
            with self.assertRaises(DatabaseError):idle.execute('SELECT 1')
        with closing(connect_review_database(self.config.dsns['reader'],readonly=True)) as disconnected:
            disconnected.execute('BEGIN');disconnected.raw.close()
            with self.assertRaises(DatabaseError):disconnected.execute('SELECT 1')
        def timeout_in_prepare(connection,**scope):
            connection.execute("SET LOCAL statement_timeout='50ms'")
            connection.execute('SELECT pg_sleep(0.2)')
        self.calls.clear()
        with patch.object(analytics,'get_top_issues',side_effect=timeout_in_prepare):
            timed_out=self.work('具体体现在哪里？',self.ranking['task_id'])
        self.assertNotEqual(timed_out['result']['status'],'ok');self.assertEqual(self.calls,[])
        original=analytics.get_top_issues
        def stop_inside_prepare(connection,**scope):
            data=original(connection,**scope);self.worker.stop_requested.set();return data
        self.calls.clear()
        with patch.object(analytics,'get_top_issues',side_effect=stop_inside_prepare):
            task=self.work('具体体现在哪里？',self.ranking['task_id'])
        event('worker_stop_outcome',case=self.case,task_status=task['status'],classification_calls=len(self.calls))
        self.assertEqual(task['status'],'failed')
        self.assertEqual(self.calls,[], 'worker stop during prefetch must prevent classification')

    def test_PG08_background_worker_publication_and_api_read(self):
        from data_analysis_agent.api.routes.public_analysis import create_router
        self.worker.start()
        try:task=self.work('具体体现在哪里？',self.ranking['task_id'],background=True)
        finally:self.worker.stop()
        result=task['result'];self.assertEqual(task['status'],'succeeded')
        self.assertTrue(has_saved_integrity(task));self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['p6_execution']['budget']['attempts'],6)
        self.assertEqual(len(self.calls),6)
        public=create_router(self.config.dsns['task']).routes[0].endpoint(task['task_id'])['result']
        self.assertEqual(public['p6_publication'],result['p6_publication'])
        self.assertEqual(public['public_answer']['status'],'ok')
        self.assertEqual(public['p6_publication']['publishable_aspects'],list(self.goal.required_aspects))


class SafeResult(unittest.TestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.failfast=True
    def startTest(self,test):
        super().startTest(test);self.start=monotonic()
    def addSuccess(self,test):
        super().addSuccess(test);EVIDENCE['cases'][test._testMethodName]={'status':'PASS','seconds':round(monotonic()-self.start,3)}
    def addFailure(self,test,err):
        super().addFailure(test,err);EVIDENCE['cases'][test._testMethodName]={'status':'FAIL','error_type':err[0].__name__}
    def addError(self,test,err):
        super().addError(test,err);EVIDENCE['cases'][getattr(test,'_testMethodName','environment')]={'status':'ERROR','error_type':err[0].__name__}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='requires explicit unique databases, roles and authorization')
    parser.add_argument('--output',type=Path,default=Path('.cache/p6e1b/postgresql-result.json'))
    args=parser.parse_args()
    try:
        config=load_authorization(os.environ)
        if not args.run:
            result={'status':'READY_FOR_LIVE_IDENTITY_CHECK','postgresql_cases_executed':0,'authorization_loaded':True}
            code=2
        else:
            suite=unittest.defaultTestLoader.loadTestsFromTestCase(P6E1BPostgresTests)
            outcome=SafeResult();suite.run(outcome)
            result={**EVIDENCE,'status':'PASS' if outcome.wasSuccessful() and outcome.testsRun==8 else 'FAIL',
                    'postgresql_cases_executed':outcome.testsRun,'failures':len(outcome.failures),'errors':len(outcome.errors)}
            code=0 if result['status']=='PASS' else 1
    except E1BBlocked as exc:
        result={'status':'BLOCKED','reason':str(exc),'postgresql_cases_executed':0,
                'production_database_operations':0,'paid_model_calls':0,'embedding_calls':0};code=2
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));return code


if __name__=='__main__':raise SystemExit(main())
