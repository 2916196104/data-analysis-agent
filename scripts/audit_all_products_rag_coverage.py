"""All-product coverage audit. Default is offline; real reads need explicit authorization."""
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import csv
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
PROFILE_PATTERN = re.compile(r'^[0-9a-f]{64}$')
OUT = ROOT / 'docs' / 'evaluation'
FIELDS = ('source', 'dataset_version', 'product_id', 'review_id', 'annotation_id', 'label_profile_key',
          'opinion_source', 'model_opinion_id', 'human_opinion_id', 'human_revision_id', 'aspect',
          'sentiment', 'quote_start', 'quote_end', 'review_content_sha256', 'embedding_input_sha256',
          'text_builder_version', 'published_at')
COLUMNS = ('source', 'dataset_version', 'product_id', 'date_from', 'date_to', 'reviews', 'text_reviews',
           'successful_reviews', 'effective_opinions', 'model_opinions', 'human_opinions', 'opinion_reviews',
           'historical_vectors', 'active_vectors', 'current_member_vectors', 'provenance_matched_vectors',
           'snapshot_matched_vectors', 'usable_vectors', 'missing_usable_opinions', 'opinion_coverage',
           'covered_reviews', 'review_coverage', 'retrieval_status', 'coverage_status', 'anomalies', 'failure')


class AuditError(Exception):
    pass


def require(value, code):
    if not value:
        raise AuditError(code)


def static_check():
    path = ROOT / 'src/data_analysis_agent/reviews/embedding_store.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_search_effective_opinions')
    fields = next(ast.literal_eval(n.value) for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'fields' for t in n.targets))
    require(tuple(fields) == FIELDS, 'PRODUCTION_PROVENANCE_CONTRACT_CHANGED')
    return {'status': 'OFFLINE_CHECK_PASS', 'database_connections': 0,
            'production_provenance_fields': len(fields), 'runner_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'requires_explicit_authorization': True, 'real_inventory': 'NOT_EXECUTED'}


def classify_vectors(docs, states, eligible, vectors, space_id, text_version, profile_key, validate_vector):
    """Deduplicate opinion keys; production membership and source checks happen before this call."""
    counts, usable, bad_eligible = Counter(), set(), False
    for row in vectors:
        if row['invalidated_at'] is not None:
            counts['invalidated'] += 1
            continue
        counts['active_vectors'] += 1
        if row['label_profile_key'] != profile_key:
            counts['profile_mismatch'] += 1
            continue
        if row['embedding_space_id'] != space_id or row['text_builder_version'] != text_version:
            counts['space_or_text_version_mismatch'] += 1
            continue
        key = row['stable_versioned_opinion_key']
        doc = docs.get(key)
        if doc is None:
            state = states.get(row['review_id'])
            if state is None:
                counts['unexplained_nonmember'] += 1
            elif state.annotation_id != row['annotation_id']:
                counts['annotation_mismatch_unverified'] += 1
            elif state.human_revision_id != row['human_revision_id']:
                counts['revision_mismatch_unverified'] += 1
            else:
                counts['unexplained_nonmember'] += 1
            continue
        counts['current_member_vectors'] += 1
        if not all(row[f] == getattr(doc, f) for f in FIELDS):
            counts['source_metadata_mismatch'] += 1
            if row['effective_snapshot_sha256'] == eligible[key]:
                bad_eligible = True
            continue
        counts['provenance_matched_vectors'] += 1
        if row['effective_snapshot_sha256'] != eligible[key]:
            counts['snapshot_mismatch'] += 1
            continue
        counts['snapshot_matched_vectors'] += 1
        try:
            validate_vector(json.loads(row['stored_vector']))
        except (ValueError, TypeError, RuntimeError):
            counts['invalid_vector'] += 1
            bad_eligible = True
            continue
        require(key not in usable, 'DUPLICATE_CURRENT_VECTOR')
        usable.add(key)
    require(len(usable) <= len(docs), 'USABLE_EXCEEDS_EFFECTIVE')
    return counts, usable, bad_eligible


def grouped_coverage(docs, usable):
    groups = defaultdict(Counter)
    for key, doc in docs.items():
        for kind, value in (('aspect_sentiment', (doc.aspect, doc.sentiment)), ('month', doc.published_at[:7])):
            groups[(kind, value)]['effective'] += 1
            groups[(kind, value)]['usable'] += key in usable
    return [{'kind': k[0], 'group': k[1], **dict(v)} for k, v in sorted(groups.items(), key=str)]


def audit_product(connection, item, identity, profile, services, deadline):
    from data_analysis_agent.reviews.scope import review_scope
    from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, _verified_sources
    from data_analysis_agent.reviews.embedding_store import _eligible, validate_vector, SPACE_ID
    from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION
    source, dataset, product = item['source'], item['dataset_version'], item['product_id']
    base = dict(source=source, dataset_version=dataset, product_id=product, reviews=item['reviews'],
                text_reviews=item['text_reviews'], historical_vectors=item['historical_vectors'])
    require(time.monotonic() < deadline, 'AUDIT_DEADLINE')
    if item['reviews'] == 0:
        require(item['historical_vectors'] == 0, 'VECTORS_WITHOUT_REVIEWS')
        return {**base, 'retrieval_status': 'NO_EFFECTIVE_OPINIONS', 'coverage_status': 'N/A', 'opinion_coverage': None,
                'successful_reviews': 0, 'effective_opinions': 0, 'model_opinions': 0, 'human_opinions': 0,
                'opinion_reviews': 0, 'covered_reviews': 0, 'review_coverage': None,
                'active_vectors': 0, 'current_member_vectors': 0, 'provenance_matched_vectors': 0,
                'snapshot_matched_vectors': 0, 'usable_vectors': 0, 'missing_usable_opinions': 0,
                'groups': [], 'anomalies': {}}
    require(item['first_day'] is not None and item['last_day'] is not None, 'DATE_RANGE_UNKNOWN')
    start, end = str(item['first_day']), str(item['last_day'])
    options = dict(source=source, dataset_version=dataset, product_ids=[product], date_from=start, date_to=end,
                   as_of=None, model=identity[0], base_url=identity[1], prompt_version=identity[2])
    scope = _scope(options)
    where, filters, _ = review_scope(source=source, dataset_version=dataset, product_ids=[product],
                                     date_from=start, date_to=end, alias='r')
    with PostgresSemanticRepository(connection).snapshot(scope) as snapshot:
        require(item['historical_vectors'] <= 20000, 'PRODUCT_VECTOR_LIMIT')
        corpus = snapshot.corpus()
        docs = {d.stable_versioned_opinion_key: d for d in corpus.documents}
        states = {s.review_id: s for s in corpus.reviews}
        require(len(docs) == len(corpus.documents) and len(states) == item['reviews'], 'CORPUS_SCOPE_OR_KEY_MISMATCH')
        require(corpus.label_profile_key == profile, 'PROFILE_MISMATCH')
        keys = tuple(docs)
        # Bound per-product work; oversized products fail closed instead of exhausting the host.
        require(len(keys) <= 10000, 'PRODUCT_OPINION_LIMIT')
        verified = _verified_sources(snapshot.load_sources(keys), keys, scope) if keys else {}
        require(set(verified) == set(keys), 'SOURCE_VERIFICATION_FAILED')
        eligible = {v['key']: v['snapshot'] for v in _eligible(corpus)}
        require(set(eligible) == set(keys), 'ELIGIBLE_KEY_MISMATCH')
        rows = connection.execute('SELECT r.id,' + ','.join('r.' + f for f in FIELDS) + ''' ,
            r.stable_versioned_opinion_key,r.effective_snapshot_sha256,r.embedding_space_id,
            r.invalidated_at,r.embedding::text stored_vector FROM review_opinion_embeddings r
            WHERE r.source=? AND r.dataset_version=? AND r.product_id=? ORDER BY r.id''', (source, dataset, product))
        vectors = []
        for row in rows:
            require(len(vectors) < 20000, 'PRODUCT_VECTOR_LIMIT')
            vectors.append(dict(row))
        require(len(vectors) == item['historical_vectors'], 'VECTOR_INVENTORY_MISMATCH')
        counts, usable, bad = classify_vectors(docs, states, eligible, vectors, SPACE_ID, TEXT_BUILDER_VERSION,
                                                profile, validate_vector)
        E, U = len(docs), len(usable)
        reviews = {d.review_id for d in docs.values()}
        covered_reviews = {docs[key].review_id for key in usable}
        uncertain = bad or any(counts[name] for name in ('unexplained_nonmember',
            'annotation_mismatch_unverified', 'revision_mismatch_unverified'))
        retrieval = ('UNKNOWN' if uncertain else 'NO_EFFECTIVE_OPINIONS' if E == 0 else
                     'READY_FOR_RETRIEVAL' if U else 'NO_VECTORS' if not vectors else 'STALE_OR_INVALID')
        result = {**base, 'date_from': start, 'date_to': end,
                  'successful_reviews': sum(s.processing_status == 'success' for s in states.values()),
                  'effective_opinions': E, 'model_opinions': sum(d.opinion_source == 'model' for d in docs.values()),
                  'human_opinions': sum(d.opinion_source == 'human' for d in docs.values()),
                  'opinion_reviews': len(reviews), 'covered_reviews': len(covered_reviews),
                  'review_coverage': len(covered_reviews)/len(reviews) if reviews else None,
                  'usable_vectors': U, 'missing_usable_opinions': E-U, 'opinion_coverage': U/E if E else None,
                  'retrieval_status': retrieval, 'coverage_status': 'N/A' if not E else 'PARTIAL_COVERAGE' if U<E else 'FULL_COVERAGE',
                  'anomalies': dict(counts), 'groups': grouped_coverage(docs, usable)}
        for name in ('active_vectors', 'current_member_vectors', 'provenance_matched_vectors', 'snapshot_matched_vectors'):
            result[name] = counts[name]
        return result


def execute(profile):
    from urllib.parse import urlunsplit
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.core.serialization import content_sha256
    from data_analysis_agent.reviews.scope import model_identity
    from data_analysis_agent.reviews.store import check_review_schema
    from data_analysis_agent.reviews.embedding_store import SPACE_ID
    require(PROFILE_PATTERN.fullmatch(profile) is not None, 'INVALID_PROFILE_KEY')
    started = time.monotonic()
    deadline = started + 270
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    result = {'status': 'RUNNING', 'run_id': run_id, 'profile_key': profile, 'embedding_space_id': SPACE_ID,
              'connection_attempts': 0, 'products': [], 'database_writes': 0, 'embedding_calls': 0,
              'llm_calls': 0, 'agent_calls': 0, 'rollback': None, 'closed': None, 'failures': []}
    connection = None
    try:
        result['connection_attempts'] = 1
        connection = PostgresConnection(urlunsplit(('postgresql', 'rag_audit_ro@127.0.0.1:5432', '/reviews', 'hostaddr=127.0.0.1', '')), readonly=True)
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for sql in ("SET LOCAL statement_timeout='15s'", "SET LOCAL lock_timeout='3s'", "SET LOCAL TIME ZONE 'UTC'",
                    "SET LOCAL transaction_timeout='280s'"):
            connection.execute(sql)
        info = connection.execute("""SELECT current_database(),current_user,session_user,
            current_setting('transaction_isolation'),current_setting('transaction_read_only'),current_setting('TimeZone'),
            txid_current_snapshot()::text,transaction_timestamp()::text""").fetchone()
        require(tuple(info[:6]) == ('reviews', 'rag_audit_ro', 'rag_audit_ro', 'repeatable read', 'on', 'UTC'), 'IDENTITY_MISMATCH')
        result['snapshot'] = {'id': info[6], 'observed_at': info[7]}
        role = connection.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
            pg_has_role(current_user,'pg_write_all_data','MEMBER') FROM pg_catalog.pg_roles WHERE rolname=current_user""").fetchone()
        require(role is not None and not any(role), 'ROLE_NOT_READ_ONLY')
        ownership = connection.execute("""SELECT pg_has_role(current_user,datdba,'MEMBER')
            FROM pg_catalog.pg_database WHERE datname=current_database()""").fetchone()
        schema_write = connection.execute("""SELECT count(*) FROM pg_catalog.pg_namespace n
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
              AND n.nspname NOT LIKE 'pg_temp_%' AND (pg_has_role(current_user,n.nspowner,'MEMBER')
              OR has_schema_privilege(current_user,n.oid,'CREATE'))""").fetchone()[0]
        require(ownership is not None and not ownership[0] and schema_write == 0, 'OWNER_OR_SCHEMA_WRITE_ACCESS')
        check_review_schema(connection)
        vector_type = connection.execute("""SELECT format_type(a.atttypid,a.atttypmod)
            FROM pg_catalog.pg_attribute a WHERE a.attrelid='public.review_opinion_embeddings'::regclass
            AND a.attname='embedding' AND NOT a.attisdropped""").fetchone()
        extension = connection.execute("SELECT extversion FROM pg_catalog.pg_extension WHERE extname='vector'").fetchone()
        require(vector_type is not None and vector_type[0] == 'vector(1024)' and extension is not None, 'VECTOR_EXTENSION_OR_TYPE_MISMATCH')
        result['pgvector'] = {'version': extension[0], 'type': vector_type[0]}
        indexes = connection.execute("""SELECT i.relname,x.indisvalid,x.indisready,pg_get_indexdef(i.oid)
            FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class c ON c.oid=x.indrelid
            JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='review_opinion_embeddings' ORDER BY i.relname""")
        result['indexes'] = [{'name': row[0], 'valid': row[1], 'ready': row[2], 'definition': row[3]} for row in indexes]
        objects = connection.execute("""SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
            has_table_privilege(current_user,c.oid,'SELECT') FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
            AND c.relname=ANY(?::text[])""", (['products','reviews','import_runs','review_annotations','review_opinions',
               'review_workbench_schema','review_human_reviews','review_human_opinions','review_opinion_embeddings'],)).fetchall()
        require(len(objects) == 9 and all(not row[1] and not row[2] and row[3] for row in objects), 'PERMISSION_OR_RLS_UNKNOWN')
        write_access = connection.execute("""SELECT count(*) FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
            AND c.relkind IN ('r','p','v','m','f') AND (
              pg_has_role(current_user,c.relowner,'MEMBER') OR
              has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR
              has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES'))""").fetchone()[0]
        require(write_access == 0, 'ROLE_WRITE_ACCESS_UNKNOWN_OR_PRESENT')
        triads = connection.execute('SELECT DISTINCT requested_model,base_url,prompt_version FROM review_annotations').fetchall()
        matches = set()
        for row in triads:
            try:
                value = model_identity(*tuple(row), required=True)
                if content_sha256(list(value)) == profile:
                    matches.add(value)
            except Exception:
                continue
        require(len(matches) == 1, 'PROFILE_NOT_UNIQUELY_BOUND')
        identity = next(iter(matches))
        catalog = connection.execute("""SELECT p.source,p.dataset_version,p.product_id,
            count(r.id) reviews,count(r.id) FILTER (WHERE r.has_text<>0) text_reviews,
            min((r.published_at::timestamptz AT TIME ZONE 'UTC')::date) first_day,
            max((r.published_at::timestamptz AT TIME ZONE 'UTC')::date) last_day,
            (SELECT count(*) FROM review_opinion_embeddings e WHERE e.source=p.source
               AND e.dataset_version=p.dataset_version AND e.product_id=p.product_id) historical_vectors
            FROM products p LEFT JOIN reviews r ON r.source=p.source AND r.dataset_version=p.dataset_version
               AND r.product_id=p.product_id GROUP BY p.source,p.dataset_version,p.product_id
            ORDER BY p.source,p.dataset_version,p.product_id""").fetchall()
        result['catalog_count'] = len(catalog)
        orphans = connection.execute("""SELECT count(*) FROM reviews r LEFT JOIN products p ON
            p.source=r.source AND p.dataset_version=r.dataset_version AND p.product_id=r.product_id
            WHERE p.product_id IS NULL""").fetchone()[0]
        require(orphans == 0, 'REVIEW_PRODUCT_ORPHANS')
        for row in catalog:
            item = dict(row)
            try:
                result['products'].append(audit_product(connection, item, identity, profile, None, deadline))
            except Exception as exc:
                result['failures'].append({'source': item['source'], 'dataset_version': item['dataset_version'],
                                           'product_id': item['product_id'], 'type': type(exc).__name__,
                                           'code': str(exc) if isinstance(exc, AuditError) else 'PRODUCT_AUDIT_FAILED'})
                break
        result['status'] = 'COMPLETED' if len(result['products']) == len(catalog) and not result['failures'] else 'BLOCKED'
    except Exception as exc:
        result['status'] = 'BLOCKED'
        result['failures'].append({'type': type(exc).__name__, 'code': str(exc) if isinstance(exc, AuditError) else 'AUDIT_FAILED'})
    finally:
        if connection is not None:
            try:
                connection.rollback()
                result['rollback'] = True
            except Exception:
                result['status'] = 'BLOCKED'
                result['rollback'] = False
            try:
                connection.close()
                result['closed'] = bool(connection.raw.closed)
            except Exception:
                result['status'] = 'BLOCKED'
                result['closed'] = False
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['scanned_products'] = len(result['products'])
    result['unscanned_products'] = result.get('catalog_count', 0) - len(result['products']) if 'catalog_count' in result else None
    rows = result['products']
    result['summary'] = {
        'with_reviews': sum(r['reviews'] > 0 for r in rows),
        'with_successful_annotations': sum((r.get('successful_reviews') or 0) > 0 for r in rows),
        'with_effective_opinions': sum((r.get('effective_opinions') or 0) > 0 for r in rows),
        'with_historical_vectors': sum(r['historical_vectors'] > 0 for r in rows),
        'with_usable_vectors': sum((r.get('usable_vectors') or 0) > 0 for r in rows),
        'ready': sum(r['retrieval_status'] == 'READY_FOR_RETRIEVAL' for r in rows),
        'no_effective_opinions': sum(r['retrieval_status'] == 'NO_EFFECTIVE_OPINIONS' for r in rows),
        'no_vectors': sum(r['retrieval_status'] == 'NO_VECTORS' for r in rows),
        'stale_or_invalid': sum(r['retrieval_status'] == 'STALE_OR_INVALID' for r in rows),
        'unknown': sum(r['retrieval_status'] == 'UNKNOWN' for r in rows) + (result['unscanned_products'] or 0),
        'business_demand': 'UNKNOWN',
    }
    eligible = [r for r in rows if r['retrieval_status'] != 'UNKNOWN' and (r.get('effective_opinions') or 0) > 0
                and r['missing_usable_opinions'] > 0]
    eligible.sort(key=lambda r: (r['usable_vectors'] != 0, r['opinion_coverage'], -r['effective_opinions']))
    result['provisional_candidates'] = [{'source': r['source'], 'dataset_version': r['dataset_version'],
        'product_id': r['product_id'], 'effective_opinions': r['effective_opinions'], 'usable_vectors': r['usable_vectors'],
        'opinion_coverage': r['opinion_coverage'], 'business_demand': 'UNKNOWN', 'sync_cost_and_risk': 'UNKNOWN'}
        for r in eligible[:5]] if result['status'] == 'COMPLETED' else []
    return result


def save(result):
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = result['run_id']
    json_path = OUT / f'rag-all-products-coverage-{run_id}.json'
    csv_path = OUT / f'rag-all-products-coverage-{run_id}.csv'
    with json_path.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    with csv_path.open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for row in result['products']:
            safe = dict(row, anomalies=json.dumps(row.get('anomalies', {}), ensure_ascii=False))
            for key in ('source', 'dataset_version', 'product_id'):
                if str(safe.get(key, '')).lstrip().startswith(('=', '+', '-', '@')):
                    safe[key] = "'" + safe[key]
            writer.writerow(safe)
    report_path = OUT / f'rag-all-products-coverage-{run_id}.md'
    with report_path.open('x', encoding='utf-8') as handle:
        handle.write('# 全商品 RAG 覆盖盘点\n\n')
        handle.write(f"状态：{result['status']}；目录商品：{result.get('catalog_count', 'UNKNOWN')}；"
                     f"完成：{result['scanned_products']}；未完成：{result['unscanned_products']}。\n\n")
        handle.write('按 source + dataset_version + product_id 分组；百分比分母为当前有效观点，'
                     '评论覆盖率单独记录。UNKNOWN 不代表无向量。\n\n')
        handle.write('| 来源 | 数据集 | 商品 | 有效观点 E | 可用 U | 观点覆盖 | 检索状态 |\n|---|---|---|---:|---:|---:|---|\n')
        for r in result['products']:
            safe = lambda v: str(v).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
            rate = 'N/A' if r.get('opinion_coverage') is None else f"{r['opinion_coverage']:.2%}"
            handle.write(f"| {safe(r['source'])} | {safe(r['dataset_version'])} | {safe(r['product_id'])} | "
                         f"{r.get('effective_opinions', 'UNKNOWN')} | {r.get('usable_vectors', 'UNKNOWN')} | {rate} | {r['retrieval_status']} |\n")
        handle.write('\n## 商品维度与月份覆盖\n\n')
        for r in result['products']:
            handle.write(f"- {safe(r['source'])} / {safe(r['dataset_version'])} / {safe(r['product_id'])}: ")
            handle.write('; '.join(f"{safe(g['kind'])} {safe(g['group'])}: {g['usable']}/{g['effective']}"
                                   for g in r.get('groups', [])) or 'N/A')
            handle.write('\n')
        handle.write('\n## 暂定补齐候选\n\n')
        if result['provisional_candidates']:
            for r in result['provisional_candidates']:
                handle.write(f"- {safe(r['source'])} / {safe(r['dataset_version'])} / {safe(r['product_id'])}: "
                             f"{r['usable_vectors']}/{r['effective_opinions']}；业务需求、同步成本与风险待确认。\n")
        else:
            handle.write('无可确认候选；部分扫描或异常不能形成全库排序。\n')
        handle.write('\n来源/维度/月度明细及异常见同运行 JSON。先排查 UNKNOWN 和异常，'
                     '再考虑无可用向量且有有效观点的商品及低覆盖商品；业务需求、成本与风险未知，'
                     '不得据缺口数量自动决定最终优先级或同步。没有执行向量同步。\n')
    return json_path, csv_path, report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized', action='store_true', help='One separately authorized local read-only inventory.')
    parser.add_argument('--profile-key', help='Explicitly approved 64-character label profile digest.')
    args = parser.parse_args()
    try:
        preflight = static_check()
        if not args.execute_authorized:
            if args.profile_key is not None:
                require(PROFILE_PATTERN.fullmatch(args.profile_key) is not None, 'INVALID_PROFILE_KEY')
            print(json.dumps(preflight, ensure_ascii=False))
            return 0
        require(args.profile_key is not None, 'PROFILE_KEY_REQUIRED')
        result = execute(args.profile_key)
        json_path, csv_path, report_path = save(result)
        print(json.dumps({'status': result['status'], 'scanned_products': result['scanned_products'],
                          'json': str(json_path.relative_to(ROOT)), 'csv': str(csv_path.relative_to(ROOT)),
                          'report': str(report_path.relative_to(ROOT))}, ensure_ascii=False))
        return 0 if result['status'] == 'COMPLETED' else 2
    except (AuditError, OSError, StopIteration, SyntaxError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': str(exc) if isinstance(exc, AuditError) else type(exc).__name__}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
