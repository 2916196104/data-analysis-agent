"""Manual 055 business-chain acceptance; default is offline, never a TaskWorker."""
import argparse
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import socket
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from scripts import run_055_real_rag_once as rag
from data_analysis_agent.agents import reviews, review_tools, runtime
from data_analysis_agent.agents.review_answers import semantic_answer
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_retrieval import retrieval_request, validate_retrieval
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.reviews import store
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.parameters import BaselineScope
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticRetrievalService, PostgresSemanticRepository, require_source_validated_result, _scope,
)

QUESTION, RANGE = rag.QUESTION, rag.RANGE
OUTPUT = Path('docs/evaluation/agent-055-first-agent-e2e')
REPORT = Path('docs/agent-055-first-agent-e2e-acceptance.md')
TOOLS = ['get_analysis_context', 'search_semantic_evidence']
LIMIT = '结论仅限本次 Top-5 的合法买家自述；不代表全部评论或未索引观点。P6-E1B 阻断不变。'
IDENTITY_SQL = ("SELECT current_database() AS database_name, current_user AS effective_user, "
    "session_user AS login_user, current_setting('transaction_isolation') AS isolation_level, "
    "current_setting('transaction_read_only') AS read_only, current_setting('TimeZone') AS timezone, "
    "current_setting('statement_timeout') AS statement_timeout, current_setting('lock_timeout') AS lock_timeout")


def require(value, code):
    rag.require(value, code)


def new_record(authorized):
    record = rag.new_record(authorized)
    for key in ('category', 'candidates', 'assessment', 'final_llm_calls', 'full_agent_runs'):
        record.pop(key)
    record.update(version='agent-055-agent-e2e-once-v1', stage='offline_preparation', acceptance='NOT_EXECUTED',
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), limitation=LIMIT,
        preparation=None, state=None, intent_router=None, planner=None, validation=None,
        task_acceptance='NOT_APPLICABLE', task_acceptance_reason='本题旧式 request_goal/task_goal 不适用；使用 RuntimeQueryContract。',
        tools=None, answer_generation=None, model_calls=None, final_answer=None, public_answer=None,
        permitted_opinion_count=None, permitted_distinct_review_count=None, permitted_keys=None,
        sources=None, checks={}, transactions=[], database_mutation_attempts=0,
        cleanup=dict(rollback=None, connection_closed=None, encoder_closed=None, errors=[]))
    record['calls'].pop('adapter_calls')
    record['calls'].update(agent_calls=0, service_searches=0, blocked_llm_attempts=0,
        blocked_network_attempts=0, connection_peak=0)
    return record


def configuration(root, record):
    # Reuse the frozen scope/space/profile checks; this only reads local files.
    scope, encoder = rag.load_configuration(root, record)
    settings = AgentSettings.from_values(read_settings(root / '.env'))
    require(not any((settings.p6_preparation_enabled, settings.p6_execution_enabled,
                     settings.p6_publication_enabled)), 'unexpected_p6_configuration')
    require(settings.max_tool_calls >= 2, 'insufficient_tool_budget')
    scope = BaselineScope.model_validate(dict(scope, baseline_from='2021-09-10', baseline_to='2021-09-10')).model_dump()
    request = retrieval_request(QUESTION)
    require(request.model_dump() == dict(question=QUESTION, top_k=5, aspect=None, sentiment=None), 'query_contract_changed')
    return scope, settings, encoder


class ReadOnlyOnceConnection(rag.OnceConnection):
    """Same PostgreSQL adapter, pinned credentials, two sequential native snapshots."""
    def __init__(self, record):
        super().__init__(record)
        record['calls']['connection_peak'] = 1
        try:
            for sql in ("SET default_transaction_isolation = 'repeatable read'",
                        "SET statement_timeout = '15s'", "SET lock_timeout = '3s'", "SET TIME ZONE 'UTC'"):
                self.execute(sql)
        except BaseException:
            self.close()
            raise

    def _execute(self, sql, parameters=None):
        command = sql.strip().upper()
        allowed_sets = {"SET DEFAULT_TRANSACTION_READ_ONLY = ON", "SET DEFAULT_TRANSACTION_ISOLATION = 'REPEATABLE READ'",
                        "SET STATEMENT_TIMEOUT = '15S'", "SET LOCK_TIMEOUT = '3S'", "SET TIME ZONE 'UTC'"}
        # Native SQL only. RO role + transaction are the database write barrier;
        # reject DML/DDL even in a CTE before forwarding it to the driver.
        tokens = re.sub(r"'(?:[^']|'')*'", "''", command)
        forbidden = re.search(r'\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|DO|GRANT|REVOKE)\b', tokens)
        if forbidden or ';' in tokens or (command.startswith('SET ') and command not in allowed_sets):
            self.record['database_mutation_attempts'] += 1
            raise rag.RunFailure('unexpected_sql_blocked')
        if command.startswith('BEGIN'):
            require(command == 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY', 'non_readonly_transaction_blocked')
            require(len(self.record['transactions']) < 2, 'extra_snapshot_blocked')
        for value in parameters or ():
            require(not str(value).startswith('2021-09-10'), 'baseline_data_read_blocked')
        cursor = super()._execute(sql, parameters)
        if command.startswith('BEGIN'):
            identity = dict(super()._execute(IDENTITY_SQL).fetchone())
            self.record['transactions'].append(identity)
            require(identity == dict(database_name='reviews', effective_user='rag_audit_ro', login_user='rag_audit_ro',
                isolation_level='repeatable read', read_only='on', timezone='UTC', statement_timeout='15s', lock_timeout='3s'),
                'transaction_identity_or_settings_mismatch')
        return cursor

    def executemany(self, *args, **kwargs):
        self.record['database_mutation_attempts'] += 1
        raise rag.RunFailure('bulk_sql_blocked')

    def rollback(self):
        super().rollback()
        self.record['cleanup']['rollback'] = True

    def __exit__(self, *args):
        self.rollback()

    def close(self):
        if self.raw.closed:
            self.record['cleanup']['connection_closed'] = True
            return
        try:
            self.rollback()
        finally:
            super().close()
            self.record['cleanup']['connection_closed'] = bool(self.raw.closed)


class OnceEncoder(rag.OnceEncoder):
    @property
    def last_metrics(self):
        return getattr(self.encoder, 'last_metrics', {})

    def embed_texts(self, texts):
        self.record['stage'] = 'query_embedding'
        try:
            return super().embed_texts(texts)
        except (Exception, KeyboardInterrupt) as exc:
            self.record['error'] = rag.error_details(exc, 'query_embedding')[1]
            raise


class RecordingService(SemanticRetrievalService):
    def __init__(self, *, record, **kwargs):
        self.record = record
        super().__init__(**kwargs)

    def search(self, request):
        require(self.record['calls']['service_searches'] == 0, 'second_search_blocked')
        require(request == retrieval_request(QUESTION), 'query_changed')
        self.record['calls']['service_searches'] += 1
        self.record['stage'] = 'production_retrieval'
        try:
            result = super().search(request)
            require_source_validated_result(result)
        except (Exception, KeyboardInterrupt) as exc:
            self.record['error'] = rag.error_details(exc, 'production_retrieval')[1]
            raise
        self.record['source_authentication'] = 'PASS'
        return result


def scope_matches(scope, expected):
    return all(scope.get(k) == v for k, v in expected.items())


def publishable_answer(result, evidence):
    """Keep the generated answer, withholding any quote that exposes a full body."""
    answer, public = result['answer'], deepcopy(result['public_answer'])
    redacted = []
    for row in evidence:
        quote = row['quote']
        if len(quote) > 160 or quote.strip() == row['review_text'].strip():
            replacement = f"[引文已脱敏；key={row['key']}，sha256={sha256(quote.encode('utf-8')).hexdigest()}]"
            answer = answer.replace(quote, replacement)
            for h in public.get('highlights', []):
                if h.get('finding_ref') == row['key']:
                    h['text'] = h['text'].replace(quote, replacement)
            for ref in public.get('evidence_refs', []):
                for q in ref['opinions']:
                    if q['opinion_key'] == row['key']:
                        q['quote'] = replacement
                        redacted.append(row['key'])
    return answer, public, redacted


def accept(result, scope, record):
    """Observe native results; never inject evidence, rewrite the answer or rerun tools."""
    checks = record['checks']
    def check(name, value):
        checks[name] = bool(value)
    record['result_status'] = result.get('status')
    record['state'] = dict(scope={k: result.get('scope', {}).get(k) for k in RANGE},
        resolved_product_ids=result.get('user_intent', {}).get('resolved_product_ids'),
        effective_time_range=result.get('user_intent', {}).get('effective_time_range'),
        analysis_state=result.get('analysis_state'))
    record['intent_router'] = dict(analysis_intent=result.get('analysis_intent'),
        method=result.get('intent_resolution', {}).get('method'),
        model_calls=result.get('intent_model_calls'))
    plan, contract = result.get('query_plan', {}), result.get('query_contract', {})
    record['planner'] = dict(analysis_type=plan.get('analysis_type'), requirements=plan.get('requirements'),
        required_tools=plan.get('required_tools'), required_facts=contract.get('required_facts'),
        original_question=contract.get('original_question'), scope={k: contract.get('scope', {}).get(k) for k in RANGE},
        support_status=contract.get('support_status'))
    record['validation'] = result.get('validation')
    record['model_calls'] = {key: result.get(key) for key in ('intent_model_calls', 'planning_model_calls',
        'answer_model_calls', 'topic_model_calls', 'total_model_calls')}
    record['agent_elapsed_seconds'] = result.get('elapsed_seconds')
    record['performance'] = result.get('performance')
    record['answer_generation'] = {k: result.get('answer_generation', {}).get(k) for k in (
        'strategy', 'attempts', 'generation_status', 'external_model_input', 'answer_outcome')}
    trace = [e for e in result.get('trace', []) if e.get('event') == 'tool_call']
    record['tools'] = [{k: e.get(k) for k in ('tool', 'status', 'result_id', 'cache_hit', 'elapsed_ms')} for e in trace]
    check('result_ok', result.get('status') == 'ok')
    check('state_scope', scope_matches(result.get('scope', {}), scope)
        and record['state']['resolved_product_ids'] == RANGE['product_ids']
        and record['state']['effective_time_range'] == {k: RANGE[k] for k in ('date_from', 'date_to')})
    check('router', result.get('analysis_intent') == 'semantic_evidence' and result.get('intent_model_calls') == 0)
    check('planner', plan.get('analysis_type') == 'semantic_evidence'
        and plan.get('requirements') == [dict(tool='search_semantic_evidence', parameters={})]
        and scope_matches(plan.get('scope', {}), scope) and plan.get('max_evidence_items') == 5)
    check('query_contract', contract.get('original_question') == QUESTION
        and contract.get('evidence_question') == '有没有送货慢的反馈？'
        and contract.get('support_status') == 'supported' and scope_matches(contract.get('scope', {}), scope)
        and [f['id'] for f in contract.get('required_facts', {}).get('items', [])] == ['delivery_slow'])
    check('tools', [e['tool'] for e in trace] == TOOLS and all(e['status'] == 'success' and not e['cache_hit'] for e in trace))
    check('validator', result.get('validation', {}).get('status') == 'passed')
    check('no_llm', all(v == 0 for v in record['model_calls'].values()) and record['calls']['blocked_llm_attempts'] == 0)
    check('legacy_not_applicable', result.get('request_goal') is None and result.get('task_goal') is None
        and 'task_acceptance' not in result)
    data_results = [item for item in result.get('results', {}).values() if item.get('kind') == 'semantic_evidence']
    check('one_semantic_result', len(data_results) == 1)
    if len(data_results) != 1:
        return
    data = data_results[0]['data']
    valid = validate_retrieval(data, QUESTION)
    check('retrieval_validation', valid and record['source_authentication'] == 'PASS')
    if not valid:
        return
    record['validator'] = 'PASS' if checks['validator'] else 'FAIL'
    permitted = data['assessment']['permitted_keys']
    evidence = data['retrieval']['evidence']
    record['retrieved_count'] = len(evidence)
    record['sources'] = rag.project_candidates(data)
    record['permitted_keys'] = permitted
    record['permitted_opinion_count'] = len(permitted)
    record['permitted_distinct_review_count'] = len({e['review_id'] for e in evidence if e['key'] in permitted})
    answer = result.get('public_answer', {})
    keys = [q['opinion_key'] for ref in answer.get('evidence_refs', []) for q in ref['opinions']]
    check('permitted_citations', keys == permitted and len(keys) == len(set(keys))
        and [h.get('finding_ref') for h in answer.get('highlights', [])] == permitted)
    check('explicit_delivery_slow_evidence', bool(permitted) and data['assessment']['status'] == 'sufficient')
    # The production template rechecks bindings, exact spans and scope. Equality
    # also detects unsupported counts/generalizations in any published section.
    expected_answer = semantic_answer(CompactAnalysisContext.model_validate(result['compact_context']))
    check('faithful_template_and_limits', answer == expected_answer
        and result.get('answer') == '\n\n'.join([answer['summary'], *(h['text'] for h in answer['highlights'])])
        and bool(answer.get('caveats')) and bool(answer.get('sample_note')))
    check('answer_template_no_model', record['answer_generation']['strategy'] == 'template'
        and record['answer_generation']['attempts'] == 0 and record['answer_generation']['external_model_input'] is False)
    # Never persist review_text. Quote content is represented by a hash and span;
    # final answer is published only after every gate and cleanup succeed below.
    if all(checks.values()):
        record['_answer'], record['_public_answer'], record['redacted_quote_keys'] = publishable_answer(result, evidence)
        record['answer_record_policy'] = '生产回答先以未删减值验收；记录仅脱敏长引文或等于完整正文的引文，key/span/hash 保留。'


def run_once(*, authorized=False, root=ROOT):
    record = new_record(authorized)
    started = perf_counter()
    connection = encoder = None
    try:
        scope, settings, encoding = configuration(root, record)
        record['preparation'] = dict(status='READY', local_files_only=True,
            compatibility_baseline='2021-09-10; Schema only, reads prohibited',
            expected_calls=dict(agent=1, embedding=1, top_k=1, llm=0))
        if not authorized:
            return record
        record['status'], record['stage'] = 'RUNNING', 'agent'

        def connect(target, *, readonly=False, **kwargs):
            nonlocal connection
            require(readonly and target == private_target, 'unexpected_database_target')
            record['stage'] = 'connect'
            connection = ReadOnlyOnceConnection(record)
            return connection

        def factory(conn, bound):
            nonlocal encoder
            require(conn is connection and encoder is None, 'extra_service_or_connection_blocked')
            current_scope = {key: value for key, value in scope.items()
                             if key not in ('baseline_from', 'baseline_to')}
            require(_scope(bound) == _scope(current_scope), 'retrieval_scope_changed')
            require(not conn.in_transaction, 'embedding_inside_snapshot')
            encoder = PersistentEmbeddingProcess(encoding.python, encoding.cache, timeout=encoding.timeout)
            return RecordingService(record=record, encoder=OnceEncoder(encoder, record),
                repository=PostgresSemanticRepository(conn), scope=bound)

        original_tool = review_tools.ReviewRegistry.execute_tool
        tool_attempts = []
        def tool(registry, name, text, *, automatic=False):
            require(len(tool_attempts) < 2 and name == TOOLS[len(tool_attempts)]
                and automatic and json.loads(text) == {}, 'unexpected_tool_call_blocked')
            tool_attempts.append(name)
            record['stage'] = name
            return original_tool(registry, name, text, automatic=automatic)

        def no_llm(*args, **kwargs):
            record['calls']['blocked_llm_attempts'] += 1
            record['error'] = dict(code='unexpected_llm_blocked', stage='model_guard')
            raise rag.RunFailure('unexpected_llm_blocked')

        def no_network(*args, **kwargs):
            record['calls']['blocked_network_attempts'] += 1
            record['error'] = dict(code='unexpected_python_network_blocked', stage='network_guard')
            raise rag.RunFailure('unexpected_python_network_blocked')

        # Resource factories/guards only: all business decisions remain native.
        # libpq's pinned local connection is C-level; Python model HTTP is denied.
        private_target = rag.urlunsplit(('postgresql', 'rag_audit_ro@127.0.0.1:5432', '/reviews', 'hostaddr=127.0.0.1', ''))
        with ExitStack() as stack:
            stack.enter_context(patch.object(store, 'connect_database', connect))
            stack.enter_context(patch.object(reviews, 'create_llm', no_llm))
            stack.enter_context(patch.object(runtime, 'create_llm', no_llm))
            stack.enter_context(patch.object(review_tools.ReviewRegistry, 'execute_tool', tool))
            stack.enter_context(patch.object(socket.socket, 'connect', no_network))
            stack.enter_context(patch.object(socket.socket, 'connect_ex', no_network))
            record['calls']['agent_calls'] += 1
            result = reviews.run_review_agent(private_target, QUESTION, scope=scope,
                settings=settings, semantic_service_factory=factory)
        record['stage'] = 'acceptance'
        accept(result, scope, record)
        if result.get('error') and record['error'] is None:
            record['error'] = dict(code=result['error'].get('code'), stage='production_agent')
    except (Exception, KeyboardInterrupt) as exc:
        _, detail = rag.error_details(exc, record['stage'])
        record['error'] = detail
        record['status'] = 'BLOCKED' if record['stage'] == 'offline_preparation' else 'FAILED'
    finally:
        for name, resource in (('connection_closed', connection), ('encoder_closed', encoder)):
            if resource is not None:
                try:
                    resource.close()
                    record['cleanup'][name] = True
                except (Exception, KeyboardInterrupt) as exc:
                    record['cleanup'][name] = False
                    record['cleanup']['errors'].append(dict(operation=name, exception_type=type(exc).__name__))
        if authorized and record['calls']['agent_calls']:
            calls, checks = record['calls'], record['checks']
            checks['once_budgets'] = all(calls[k] == 1 for k in ('agent_calls', 'connection_attempts',
                'query_embedding_attempts', 'query_embedding_successes', 'top_k_sql_attempts', 'top_k_sql_completed', 'service_searches'))
            checks['readonly_snapshots'] = len(record['transactions']) == 2 and record['database_mutation_attempts'] == 0
            checks['cleanup'] = all(record['cleanup'][k] is True for k in ('rollback', 'connection_closed', 'encoder_closed')) and not record['cleanup']['errors']
            checks['no_unapproved_operations'] = calls['blocked_llm_attempts'] == calls['blocked_network_attempts'] == 0
            passed = all(checks.values()) and record['error'] is None
            record['status'], record['acceptance'] = ('COMPLETED', 'PASS') if passed else ('FAILED', 'FAIL')
            if passed:
                record['final_answer'] = record.get('_answer')
                record['public_answer'] = record.get('_public_answer')
                record['publication_allowed'] = True
        record.pop('_answer', None)
        record.pop('_public_answer', None)
        record['elapsed_seconds'] = round(perf_counter() - started, 6)
        record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return record


def save_artifacts(record, root=ROOT):
    relative = OUTPUT / (('run-' if record['authorized'] else 'not-executed-') + record['run_id'] + '.json')
    (root / OUTPUT).mkdir(parents=True, exist_ok=True)
    (root / relative).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Keep the reviewed delivery documentation; update only the runtime section.
    report = root / REPORT
    marker = '<!-- agent-055-once-runtime -->'
    previous = report.read_text(encoding='utf-8') if report.exists() else '# 商品055：完整 Agent 业务链路验收记录\n'
    if not record['authorized'] and marker in previous and 'COMPLETED / PASS' in previous.split(marker)[-1]:
        return relative
    section = f"\n{marker}\n\n## 最新一次性入口记录\n\n状态：**{record['status']} / {record['acceptance']}**。真实 Agent 调用：{record['calls']['agent_calls']}。\n\n"
    section += f"[脱敏 JSON]({relative.relative_to(Path('docs')).as_posix()})。生产返回状态：{record.get('result_status')}；"
    section += f"许可观点：{record['permitted_opinion_count']}；不同评论：{record['permitted_distinct_review_count']}。\n\n"
    section += ('尚未执行业务链路，不能登记为真实业务闭环通过。\n' if record['status'] == 'NOT_EXECUTED' else
        '实际调用、逐项检查、清理及根因见 JSON；失败时禁止发布答案。\n')
    if record['final_answer'] is not None:
        section += '\n生产最终回答（许可短引用）：\n\n' + record['final_answer'] + '\n\n' + '\n'.join(record['public_answer']['caveats']) + '\n\n' + record['public_answer']['sample_note'] + '\n'
    section += '\n' + LIMIT + '\n'
    report.write_text(previous.split(marker)[0].rstrip() + '\n' + section, encoding='utf-8')
    return relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized', action='store_true', help='Manually authorize one real local Agent run.')
    args = parser.parse_args()
    # Third-party logging must not expose payloads/credentials on failures.
    logging.disable(logging.CRITICAL)
    record = run_once(authorized=args.execute_authorized)
    path = save_artifacts(record)
    print(json.dumps(dict(status=record['status'], acceptance=record['acceptance'], record=path.as_posix()), ensure_ascii=False))
    return 0 if record['status'] in ('NOT_EXECUTED', 'COMPLETED') else 1


if __name__ == '__main__':
    sys.exit(main())
