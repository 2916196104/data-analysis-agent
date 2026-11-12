"""Manual, one-question 055 retrieval. No real work without --execute-authorized."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sys
from time import perf_counter
from types import SimpleNamespace
from urllib.parse import urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_reported_facts import target
from data_analysis_agent.agents.review_retrieval import execute_retrieval, retrieval_request, validate_retrieval
from data_analysis_agent.agents.review_task_contract import evidence_question
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import SPACE, SPACE_ID, EmbeddingStoreError, validate_vector
from data_analysis_agent.reviews.prompts import PROMPT_VERSION
from data_analysis_agent.reviews.rag_corpus import RagCorpusIntegrityError
from data_analysis_agent.reviews.scope import model_identity
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalError, SemanticRetrievalService, PostgresSemanticRepository

QUESTION = '商品100013588055有没有送货慢的反馈？'
PROFILE = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
SPACE_KEY = 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
RANGE = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588055'],
             date_from='2021-09-11', date_to='2022-08-26', as_of=None)
BASELINE = Path('docs/evaluation/agent-055-effective-corpus/effective_corpus-20261112T035403365378Z.json')
OUTPUT = Path('docs/evaluation/agent-055-first-real-rag')
REPORT = Path('docs/agent-055-first-real-rag-integration-result.md')
CONCLUSIONS = {
    'A': '技术链路成功，找到生产许可的 delivery_slow 明确证据。',
    'B': '技术链路成功，本次 Top-5 未找到足够的 delivery_slow 证据。',
    'C': '技术链路失败；请按记录的具体阶段定位，不自动重试。',
    'D': '来源认证或 Validator 失败；禁止发布候选检索结果。',
}
LIMIT = 'Top-5 不足不能证明全部 200 条已索引观点、891 条未覆盖观点或全部 510 条评论没有送货慢反馈。'


class RunFailure(RuntimeError):
    def __init__(self, code, category='C'):
        self.code, self.category = code, category
        super().__init__(code)


def require(condition, code, category='C'):
    if not condition:
        raise RunFailure(code, category)


def new_record(authorized):
    now = datetime.now(timezone.utc)
    return dict(version='agent-055-real-rag-once-v1', run_id=now.strftime('%Y%m%dT%H%M%S%fZ'),
        started_at_utc=now.isoformat(), authorized=authorized, status='NOT_EXECUTED', category=None,
        stage='not_executed', question=QUESTION,
        fixed_scope=dict(**RANGE, label_profile_key=PROFILE, embedding_space_id=SPACE_KEY,
                         top_k=5, aspect=None, sentiment=None),
        query_encoder=dict(model='BAAI/bge-m3', revision=REVISION, dimension=1024, pooling='cls',
                           normalization='l2', dtype='float32', max_input_tokens=512,
                           embedding_space_id=SPACE_KEY, local_only=True),
        baseline=dict(record=BASELINE.as_posix(), scoped_reviews=510, successful_reviews=505,
                      effective_opinions=1091, usable_vectors_U=200, uncovered_opinions=891,
                      opinion_coverage_percent=18.33, reaudited_this_run=False),
        calls=dict(connection_attempts=0, database_read_statements=0, query_embedding_attempts=0,
                   query_embedding_successes=0, top_k_sql_attempts=0, top_k_sql_completed=0, adapter_calls=0),
        cleanup=dict(rollback=None, connection_closed=None, encoder_closed=None, errors=[]),
        source_authentication='NOT_EXECUTED', validator='NOT_EXECUTED',
        retrieved_count=None, candidates=None, assessment=None, error=None,
        publication_allowed=False, limitation=LIMIT,
        final_llm_calls=0, full_agent_runs=0, database_mutations=0,
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest())


def load_configuration(root, record):
    raw = (root / BASELINE).read_bytes()
    baseline = strict_json_loads(raw.decode('utf-8'))
    require(baseline['status'] == 'COMPLETED', 'completed_baseline_required')
    expected = dict(**RANGE, label_profile_key=PROFILE, embedding_space_id=SPACE_KEY)
    require(baseline['scope'] == expected, 'baseline_scope_mismatch')
    require(all(baseline['metrics'][key] == value for key, value in (
        ('scoped_reviews', 510), ('successful_reviews', 505), ('effective_opinions', 1091),
        ('usable_vectors_U', 200), ('missing_usable_opinions', 891))), 'baseline_counts_mismatch')
    record['baseline']['record_sha256'] = sha256(raw).hexdigest()
    values = read_settings(root / '.env')
    identity = model_identity(values.get('LLM_MODEL_ID'), values.get('LLM_BASE_URL'), PROMPT_VERSION, required=True)
    require(content_sha256(list(identity)) == PROFILE, 'label_profile_mismatch')
    require(SPACE_ID == SPACE_KEY and SPACE.model_name == 'BAAI/bge-m3'
            and SPACE.model_revision == REVISION and SPACE.dimension == 1024, 'embedding_space_mismatch')
    python, cache = values.get('RAG_PYTHON'), values.get('RAG_MODEL_CACHE')
    require(bool(python) and Path(python).is_file() and bool(cache) and Path(cache).is_dir(),
            'local_encoder_configuration_missing')
    timeout = int(values.get('RAG_TIMEOUT_SECONDS', '600'))
    require(0 < timeout <= 600, 'invalid_encoder_timeout')
    scope = dict(**RANGE, model=identity[0], base_url=identity[1], prompt_version=identity[2])
    return scope, SimpleNamespace(python=python, cache=cache, timeout=timeout)


class OnceEncoder:
    def __init__(self, encoder, record):
        self.encoder, self.record = encoder, record

    @property
    def space(self):
        return self.encoder.space

    def embed_texts(self, texts):
        calls = self.record['calls']
        require(calls['query_embedding_attempts'] == 0, 'second_encoding_blocked')
        require(texts == [QUESTION], 'query_text_changed')
        calls['query_embedding_attempts'] += 1
        vectors = self.encoder.embed_texts(texts)
        require(isinstance(vectors, (list, tuple)) and len(vectors) == 1, 'invalid_query_vector_count')
        validate_vector(vectors[0])
        calls['query_embedding_successes'] += 1
        return vectors


class OnceConnection(PostgresConnection):
    def __init__(self, record):
        self.record = record
        require(record['calls']['connection_attempts'] == 0, 'second_connection_blocked')
        record['calls']['connection_attempts'] += 1
        # libpq supplies the password; no application read/write account or DSN from .env.
        private_target = urlunsplit(('postgresql', 'rag_audit_ro@127.0.0.1:5432', '/reviews',
                                    'hostaddr=127.0.0.1', ''))
        try:
            super().__init__(private_target, readonly=True)
        except (Exception, KeyboardInterrupt):
            raw = getattr(self, 'raw', None)
            if raw is not None:
                if raw.closed:
                    record['cleanup']['connection_closed'] = True
                else:
                    for operation, close in (('rollback', raw.rollback), ('connection_closed', raw.close)):
                        try:
                            close()
                            record['cleanup'][operation] = True
                        except (Exception, KeyboardInterrupt) as cleanup_error:
                            record['cleanup'][operation] = False
                            record['cleanup']['errors'].append(dict(
                                operation=operation, exception_type=type(cleanup_error).__name__))
            raise

    def _execute(self, statement, parameters=None):
        command = statement.lstrip().split(None, 1)[0].upper()
        require(command in ('SELECT', 'WITH', 'SHOW', 'BEGIN', 'SET'), 'non_read_statement_blocked')
        calls = self.record['calls']
        if command in ('SELECT', 'WITH', 'SHOW'):
            calls['database_read_statements'] += 1
        distance_query = '<=>' in statement
        if distance_query:
            require(command == 'SELECT' and calls['top_k_sql_attempts'] == 0, 'second_top_k_blocked')
            calls['top_k_sql_attempts'] += 1
        try:
            cursor = super()._execute(statement, parameters)
        except (Exception, KeyboardInterrupt):
            self.record['database_failure'] = dict(
                operation='top_k_distance_sql' if distance_query else 'production_read_or_transaction',
                statement_sha256=sha256(statement.encode('utf-8')).hexdigest())
            raise
        if distance_query:
            calls['top_k_sql_completed'] += 1
        return cursor


def error_details(exc, stage):
    chain, codes, seen = [], [], set()
    category = 'C'
    technical_codes = {'embedding_failed', 'invalid_query_vector', 'encoder_embedding_space_mismatch',
                       'repository_failed', 'invalid_retrieval_request', 'invalid_retrieval_scope'}
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        state = getattr(exc, 'sqlstate', None)
        state = state if isinstance(state, str) and re.fullmatch('[A-Z0-9]{5}', state) else None
        chain.append(dict(type=type(exc).__name__, module=type(exc).__module__, sqlstate=state))
        if isinstance(exc, RunFailure):
            codes.append(exc.code)
            if exc.category == 'D':
                category = 'D'
        elif isinstance(exc, SemanticRetrievalError):
            codes.append(exc.code)
            if exc.code not in technical_codes:
                category = 'D'
        elif isinstance(exc, (RagCorpusIntegrityError, ReviewDataError)) and stage == 'execute_retrieval':
            category = 'D'
        elif isinstance(exc, EmbeddingStoreError) and str(exc) == 'stored_provenance_mismatch':
            codes.append('stored_provenance_mismatch')
            category = 'D'
        exc = exc.__context__
    safe_codes = [c for c in codes if isinstance(c, str) and re.fullmatch('[a-z0-9_]+', c)]
    return category, dict(stage=stage, code=safe_codes[-1] if safe_codes else 'execution_failed',
                          sqlstate=next((e['sqlstate'] for e in chain if e['sqlstate']), None),
                          exception_chain=chain)


def project_candidates(data):
    assessment = data['assessment']
    allowed = set(assessment['permitted_keys'])
    judgments = {row['key']: row for row in assessment['candidate_judgments']}
    projected = []
    for row in data['retrieval']['evidence']:
        judgment = judgments[row['key']]
        opinion_id = row['model_opinion_id'] if row['opinion_source'] == 'model' else row['human_opinion_id']
        projected.append(dict(rank=row['rank'], distance=row['cosine_distance'], review_id=row['review_id'],
            annotation_id=row['annotation_id'], opinion_id=opinion_id, opinion_source=row['opinion_source'],
            model_opinion_id=row['model_opinion_id'], human_opinion_id=row['human_opinion_id'],
            human_revision_id=row['human_revision_id'], stable_versioned_opinion_key=row['key'],
            effective_snapshot_sha256=row['effective_snapshot_sha256'],
            source_authentication='PASS', validator='PASS',
            delivery_slow_supported='delivery_slow' in judgment['supported_fact_ids'],
            is_permitted_key=row['key'] in allowed, support_reason=judgment['reason'],
            quote_start=row['quote_start'], quote_end=row['quote_end'],
            quote_sha256=sha256(row['quote'].encode('utf-8')).hexdigest(), evidence_ref=row['evidence_ref'],
            manual_relevance='NOT_REVIEWED'))
    return projected


def run_once(*, authorized=False, root=ROOT):
    record = new_record(authorized)
    started = perf_counter()
    if not authorized:
        record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        record['elapsed_seconds'] = 0
        return record
    connection = encoder = None
    try:
        record['status'], record['stage'] = 'RUNNING', 'fixed_configuration'
        scope, settings = load_configuration(root, record)
        request = retrieval_request(QUESTION)
        require(request.model_dump(mode='json') == dict(question=QUESTION, top_k=5, aspect=None, sentiment=None),
                'retrieval_parameters_changed')
        diagnostics = {}
        # SKU comes from the already-certified fixed scope; no new catalog or permission audit.
        bound, plan, _ = resolve_review_request(QUESTION, scope,
            [dict(product_id=RANGE['product_ids'][0], title='', brand=None)], settings=None, diagnostics=diagnostics)
        require(bound == scope and plan.analysis_type == 'semantic_evidence'
                and [r['tool'] for r in plan.requirements] == ['search_semantic_evidence']
                and diagnostics.get('model_calls') == 0, 'intent_or_scope_mismatch')
        require(target(evidence_question(QUESTION, bound))['facts'] == ['delivery_slow'], 'fact_target_mismatch')
        record['intent'] = dict(type='semantic_evidence', product_ids=list(bound['product_ids']),
                                required_fact='delivery_slow', model_calls=0)
        encoder = PersistentEmbeddingProcess(settings.python, settings.cache, timeout=settings.timeout)
        record['stage'] = 'connect'
        connection = OnceConnection(record)
        service = SemanticRetrievalService(encoder=OnceEncoder(encoder, record),
            repository=PostgresSemanticRepository(connection), scope=bound)
        record['stage'] = 'query_embedding'
        service.prepare(request)  # Reused by this SAME service in execute_retrieval; no second encoding.
        record['stage'] = 'readonly_transaction'
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for sql in ("SET LOCAL statement_timeout='15s'", "SET LOCAL lock_timeout='3s'", "SET LOCAL TIME ZONE 'UTC'"):
            connection.execute(sql)
        identity = dict(connection.execute("""SELECT current_database() database_name, current_user effective_user,
            session_user login_user, current_setting('transaction_isolation') isolation_level,
            current_setting('transaction_read_only') read_only, current_setting('TimeZone') timezone,
            current_setting('statement_timeout') statement_timeout, current_setting('lock_timeout') lock_timeout""").fetchone())
        require(identity == dict(database_name='reviews', effective_user='rag_audit_ro', login_user='rag_audit_ro',
            isolation_level='repeatable read', read_only='on', timezone='UTC', statement_timeout='15s', lock_timeout='3s'),
            'readonly_identity_or_settings_mismatch')
        record['transaction'] = identity

        def factory(conn, requested_scope):
            require(conn is connection and requested_scope == bound, 'service_binding_mismatch', 'D')
            return service

        context = SimpleNamespace(question=QUESTION, connection=connection, settings=None, semantic_service_factory=factory)
        record['stage'] = 'execute_retrieval'
        record['calls']['adapter_calls'] += 1
        data = execute_retrieval(context, bound)
        record['source_authentication'] = 'PASS'  # Adapter required the in-memory production source proof.
        record['stage'] = 'validator'
        require(validate_retrieval(data, QUESTION), 'validator_rejected', 'D')
        require(data['assessment']['required_fact_ids'] == ['delivery_slow'], 'assessment_target_changed', 'D')
        record['validator'] = 'PASS'
        calls = record['calls']
        require(calls['query_embedding_attempts'] == calls['query_embedding_successes']
                == calls['top_k_sql_attempts'] == calls['top_k_sql_completed'] == calls['adapter_calls'] == 1,
                'single_call_contract_failed')
        record['candidates'] = project_candidates(data)
        record['retrieved_count'] = len(record['candidates'])
        record['recall_count_differs_from_baseline_expectation'] = record['retrieved_count'] != 5
        a = data['assessment']
        record['assessment'] = dict(policy=a['policy'], status=a['status'],
            required_fact_ids=a['required_fact_ids'], permitted_keys=a['permitted_keys'], reason=a['reason'])
        record['snapshot'] = data['retrieval']['snapshot']
        record['timings_ms'] = data['timings']
        record['category'] = 'A' if a['status'] == 'sufficient' and a['permitted_keys'] else 'B'
        record['status'], record['stage'] = 'COMPLETED', 'completed'
    except (Exception, KeyboardInterrupt) as exc:
        category, detail = error_details(exc, record['stage'])
        record.update(status='FAILED', category=category, error=detail)
        if category == 'D':
            if record['source_authentication'] != 'PASS':
                record['source_authentication'] = 'FAIL'
            if detail['code'] in ('validator_rejected', 'semantic_result_validation_failed', 'assessment_target_changed'):
                record['validator'] = 'FAIL'
            record['retrieved_count'] = None
    finally:
        operations = []
        if connection is not None:
            operations.extend((('rollback', connection.rollback), ('connection_closed', connection.close)))
        if encoder is not None:
            operations.append(('encoder_closed', encoder.close))
        for operation, close in operations:
            try:
                close()
                record['cleanup'][operation] = True
            except (Exception, KeyboardInterrupt) as exc:
                record['cleanup'][operation] = False
                record['cleanup']['errors'].append(dict(operation=operation, exception_type=type(exc).__name__))
                if record['category'] != 'D':
                    record['category'], record['status'] = 'C', 'FAILED'
                    if record['error'] is None:
                        record['error'] = dict(stage='cleanup', code='cleanup_failed', sqlstate=None,
                                               exception_chain=[dict(type=type(exc).__name__)])
        if record['category'] not in ('A', 'B'):
            record['candidates'], record['assessment'] = None, None
        record['publication_allowed'] = record['category'] in ('A', 'B')
        record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        record['elapsed_seconds'] = round(perf_counter() - started, 6)
    return record


def render_report(record, relative_record):
    fence = chr(96) * 3
    category = record['category']
    conclusion = CONCLUSIONS.get(category, 'NOT EXECUTED：入口已实现，尚未由用户授权触发真实编码或检索；A/B/C/D 均未判定。')
    calls = record['calls']
    rows = record['candidates']
    candidates = '未生成或未获准发布候选记录。'
    if rows is not None:
        candidates = '| rank | distance | review_id | annotation_id | opinion_id | 来源 | Validator | delivery_slow | permitted_key |\n|---|---|---|---|---|---|---|---|---|\n'
        candidates += '\n'.join('| {rank} | {distance:.8f} | {review_id} | {annotation_id} | {opinion_id} | {source_authentication} | {validator} | {delivery_slow_supported} | {is_permitted_key} |'.format(**r) for r in rows)
        if not rows:
            candidates += '\n实际返回 0 条；这不是商品不存在相关反馈的证明。'
    error = record['error']
    failure = '无实际执行错误。' if error is None else (
        f"失败阶段：{error['stage']}；安全错误码：{error['code']}；SQLSTATE：{error['sqlstate']}。异常类型见 JSON；不回显底层异常文本。")
    return fr'''# 商品 055：首次真实 RAG Top-5 联调结果

状态：**{record['status']}**；分类：**{category or '未判定'}**。记录时间 UTC：{record['started_at_utc']}。

{conclusion}

问题：{QUESTION}

## 实际执行与调用次数

| 项目 | 本次记录 |
|---|---|
| 用户执行授权参数 | {record['authorized']} |
| 数据库连接尝试 | {calls['connection_attempts']} |
| 查询编码尝试 / 成功 | {calls['query_embedding_attempts']} / {calls['query_embedding_successes']} |
| Top-5 距离 SQL 尝试 / 执行完成 | {calls['top_k_sql_attempts']} / {calls['top_k_sql_completed']} |
| execute_retrieval 调用 | {calls['adapter_calls']} |
| 实际召回数 | {record['retrieved_count'] if record['retrieved_count'] is not None else 'NOT EXECUTED / 未获认证'} |
| 来源认证 / Validator | {record['source_authentication']} / {record['validator']} |
| ROLLBACK / 关闭连接 / 关闭编码器 | {record['cleanup']['rollback']} / {record['cleanup']['connection_closed']} / {record['cleanup']['encoder_closed']} |
| 最终回答 LLM / 完整 Agent / 数据库修改 | 0 / 0 / 0 |

未创建连接或编码器时，清理值为 null/None，表示不适用，不是已完成回滚。距离 SQL 完成计数表示驱动 execute 已返回；来源认证和最终许可仍独立判断。清理失败会归 C，来源或 Validator 失败归 D 并隐藏候选。

{failure}

## A/B/C/D 判定口径

| 类别 | 条件 |
|---|---|
| A | 技术链路成功，生产事实许可明确支持 delivery_slow |
| B | 技术链路成功，Top-5 未找到足够支持，包括无候选或上下文无法许可 |
| C | 技术链路失败，记录配置、连接、编码、检索或清理的具体失败阶段 |
| D | 来源认证或 Validator 失败，禁止发布检索候选 |

未执行时不归入上述任何一类。技术链路成功不等于总体统计或完整 Agent 验收通过。

## 候选与事实许可

{candidates}

每条候选的完整 stable_versioned_opinion_key、effective_snapshot_sha256、来源 ID、quote 位置/摘要及许可原因仅从生产真实返回值投影，见[脱敏记录]({relative_record})；不保存完整评论、quote 正文、查询向量、密码、连接串或 endpoint。人工相关性默认 NOT_REVIEWED，不能冒称已人工判定。D 类不会输出未经认证的候选。

## 前置数据与结论边界

前置 [COMPLETED 有效语料记录](evaluation/agent-055-effective-corpus/effective_corpus-20261112T035403365378Z.json)：510 条评论、505 条成功标注评论、1091 条有效观点、U=200、891 条未覆盖、覆盖率 18.33%。这些是已完成核验的基线，**不是本次重新审计的实测统计**。不另跑权限、全量向量认证或同步；保留检索内部的生产当前成员/快照/来源保护。

{LIMIT}

## 本机唯一执行命令

[一次性脚本](../scripts/run_055_real_rag_once.py)默认不执行真实操作。用户明确授权后，在已能访问本机数据库的 PowerShell 手动触发：

{fence}powershell
Set-Location -LiteralPath 'E:\AllProject\data-analysis-agent'
& '.\.venv\Scripts\python.exe' '.\scripts\run_055_real_rag_once.py' --execute-authorized
{fence}

固定 reviews_csv / reviews-csv / 100013588055，UTC [2021-09-11, 2022-08-27)，Top-K=5、aspect=None、sentiment=None。使用既有 BGE-M3 固定修订、1024 维 CLS/L2 和本地缓存；空间/profile 不符即停止，不下载、不切换模型。

固定模型修订：{REVISION}。固定 Embedding 空间：{SPACE_KEY}。固定标注 profile：{PROFILE}。它们是不同身份，不用查询模型名称替换标注模型三元组。

实现复用同一 SemanticRetrievalService 的 prepare() 缓存，随后通过生产 execute_retrieval() 消费；编码器入口和实际距离 SQL 分别阻止第二次调用。RR READ ONLY、statement_timeout=15s、lock_timeout=3s、UTC；编码超时取现有 1～600 秒配置；不自动重试、增大 K 或关键词补搜。每次手动运行保存独立 JSON，并更新本报告；无授权的默认调用不会覆盖已有报告。仅状态 COMPLETED 且分类 A/B 表示技术链路完成；它不等于总体反馈结论或完整 Agent 验收。

当前任务到独立脚本、脱敏记录及本报告为止，不自动进入完整 Agent、最终回答、Gold/Oracle 修改或 P6-E1B 解除。
'''


def save_artifacts(record, root=ROOT):
    prefix = 'run' if record['authorized'] else 'not-executed'
    relative = OUTPUT / f"{prefix}-{record['run_id']}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    report = root / REPORT
    if record['authorized'] or not report.exists():
        report.write_text(render_report(record, relative.relative_to('docs').as_posix()), encoding='utf-8')
    return relative


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized', action='store_true',
                        help='Manually authorize exactly one real query encoding and one Top-5 retrieval.')
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    record = run_once(authorized=args.execute_authorized)
    relative = save_artifacts(record)
    print(json.dumps(dict(status=record['status'], category=record['category'],
        calls=record['calls'], retrieved_count=record['retrieved_count'],
        record_path=str(ROOT / relative), report_path=str(ROOT / REPORT)), ensure_ascii=False))
    return 0 if record['status'] in ('NOT_EXECUTED', 'COMPLETED') else 1


if __name__ == '__main__':
    raise SystemExit(main())

