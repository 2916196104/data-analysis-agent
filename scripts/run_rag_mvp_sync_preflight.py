"""Manual 053 sync budget only. Default: offline checks, no PostgreSQL connection."""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import ipaddress
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]

from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import PostgresConnection, database_identity
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.embedding_store import SPACE_ID, TEXT_BUILDER_VERSION, _range, _select
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus
from data_analysis_agent.reviews.scope import analysis_scope, review_scope
from data_analysis_agent.reviews.store import check_review_schema
import run_rag_mvp_d01 as safety

OUTPUT = Path('outputs/rag-semantic-retrieval/mvp-053-sync-budget-v1')
OWNER_SHA = {
    'src/data_analysis_agent/reviews/embedding_store.py': 'a2d39d9095cbd293ad07308db5c683b81ff138b4015d2a04135656f5321b1b63',
    'src/data_analysis_agent/reviews/rag_corpus.py': 'ed0ba5349caefbc1478116128ad397956c7e41ec5e8eb217b4da13fdcb877a79',
    'src/data_analysis_agent/reviews/scope.py': '673b0dc117c8b35cdae77b61c3565dc13aad795a40c81756b6c0287d74d82b32',
    'scripts/run_rag_mvp_d01.py': '860b5bae6c30b38c95b25a6e94dd65673b809c858749593d2b6dc559d4c480cd',
}
OWNER_FIELDS = ('source', 'dataset_version', 'product_id', 'review_id', 'annotation_id', 'label_profile_key',
                'opinion_source', 'model_opinion_id', 'human_opinion_id', 'human_revision_id',
                'aspect', 'sentiment', 'quote_start', 'quote_end', 'review_content_sha256',
                'embedding_input_sha256', 'text_builder_version', 'published_at')
ROW_FIELDS = ('id', 'stable_versioned_opinion_key', *OWNER_FIELDS, 'effective_snapshot_sha256',
              'embedding_space_id', 'invalidated_at', 'sync_run_id')
require = safety.require


class ReadonlyBudgetConnection(PostgresConnection):
    """Existing bridge/row types for the owner API; no migrations or vector search."""
    def __init__(self, dsn):
        self.statement_count = 0
        super().__init__(dsn, readonly=True)

    def _execute(self, query, parameters=None):
        command = query.lstrip().split(None, 1)[0].upper()
        require(command in ('SELECT', 'WITH', 'SHOW', 'SET', 'BEGIN'), 'non_readonly_command_forbidden')
        require('<=>' not in query, 'similarity_search_forbidden')
        self.statement_count += 1
        return super()._execute(query, parameters)


def preflight(root):
    require(Path(sys.executable).resolve() == (root/'.venv/Scripts/python.exe').resolve(), 'use_project_venv')
    require(not (root/OUTPUT).exists(), 'readonly_budget_already_claimed_do_not_rerun')
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--', str(OUTPUT/'plan.json')],
        cwd=root, capture_output=True, timeout=5, check=False).returncode == 0
    require(ignored, 'private_output_not_git_ignored')
    for name, digest in OWNER_SHA.items():
        require(sha256((root/name).read_bytes()).hexdigest() == digest, 'owner_implementation_changed')
    manifest = strict_json_loads((root/'tests/fixtures/rag_mvp_acceptance_v1.json').read_text(encoding='utf-8'))
    require(content_sha256(manifest) == safety.MANIFEST_SHA, 'frozen_manifest_changed')
    baseline = strict_json_loads((root/safety.BASELINE/'preflight.json').read_text(encoding='utf-8'))
    config_path = root/'.env'
    require(baseline['manifest_sha256'] == safety.MANIFEST_SHA, 'baseline_manifest_mismatch')
    require(sha256(config_path.read_bytes()).hexdigest() == baseline['protected_sha256']['.env'], 'frozen_config_changed')
    values = read_settings(config_path)
    dsn = values.get('DATABASE_URL', '')
    require(dsn.startswith(('postgresql://', 'postgres://')), 'postgres_required')
    require(dsn == dotenv_values(config_path, interpolate=False).get('DATABASE_URL'), 'database_environment_changed')
    require(not any(os.environ.get(k) for k in safety.PG_OVERRIDES), 'postgres_environment_not_frozen')
    require(database_identity(dsn) == safety.frozen_database_identity(root), 'frozen_database_target_mismatch')
    target = conninfo_to_dict(dsn)
    ipaddress.ip_address(target['host'])
    require(not target.get('hostaddr') and target.get('port', '5432').isdigit(), 'single_frozen_endpoint_required')
    profile = (manifest['annotation_model'], values['LLM_BASE_URL'], manifest['annotation_prompt_version'])
    require(content_sha256(list(profile)) == manifest['annotation_profile_sha256'], 'frozen_profile_changed')
    require(SPACE_ID == safety.SPACE and TEXT_BUILDER_VERSION == 'quote-utf8-v1', 'frozen_space_or_builder_changed')
    scope = manifest['scope']
    options = {k: scope[k] for k in ('source', 'dataset_version', 'date_from', 'date_to')}
    options.update(product_ids=[manifest['main_product_id']], as_of=None,
                   model=profile[0], base_url=profile[1], prompt_version=profile[2])
    return dsn, options, manifest


def summary_template():
    return dict(status='error', S=None, N=None, U=None, I=None, P=None, selected_review_groups=None,
        cross_product_unique_key_conflict=None, owner_identity_matches=None,
        baseline_055_count=None, baseline_055_sha256=None, n_plus_u_equals_s=None,
        can_enter_write_authorization=False, write_authorized=False, cli_safety_fixes_required=True,
        connection_attempts=0, automatic_retries=0, transaction_protection_confirmed=False,
        rolled_back=False, connection_closed=False)


def inventory(connection, options, manifest, summary, plan):
    corpus = read_effective_corpus(connection, **options)
    summary['current_reviews'] = len(corpus.reviews)
    summary['current_opinions'] = len(corpus.documents)
    require(corpus.scope == analysis_scope(**options)[3], 'corpus_scope_changed')
    require(corpus.label_profile_key == manifest['annotation_profile_sha256'], 'corpus_profile_changed')
    require((len(corpus.reviews), len(corpus.documents)) == (1699, 3591), 'current_corpus_counts_changed_stop')
    # Only hashes/identity metadata persist; full text/quotes stay in owner memory.
    plan['source_corpus_sha256'] = content_sha256(asdict(corpus))
    plan['scope'] = {k: v for k, v in corpus.scope.items() if k != 'base_url'}
    plan['profile_sha256'] = corpus.label_profile_key
    plan['complete_review_states'] = [asdict(r) for r in corpus.reviews]
    valid_keys = [d.stable_versioned_opinion_key for d in corpus.documents]
    require(len(valid_keys) == len(set(valid_keys)), 'duplicate_current_key')
    plan['complete_valid_keys'] = valid_keys
    selected = _select(corpus.documents, 200)
    summary['S'] = len(selected)
    require(0 < len(selected) <= 200, 'no_complete_group_or_selection_over_budget')
    all_groups = Counter(d.review_id for d in corpus.documents)
    chosen_groups = Counter(d.review_id for d in selected)
    require(all(chosen_groups[rid] == all_groups[rid] for rid in chosen_groups), 'selection_split_review_group')
    summary['selected_review_groups'] = len(chosen_groups)
    states = {r.review_id: r for r in corpus.reviews}
    plan['selected_review_groups'] = [dict(review_id=rid, opinion_count=count,
        effective_snapshot_sha256=states[rid].effective_snapshot_sha256) for rid, count in chosen_groups.items()]
    selected_by_key = {d.stable_versioned_opinion_key: d for d in selected}
    require(len(selected_by_key) == len(selected), 'duplicate_selected_key')
    plan['selected_documents'] = [{k: v for k, v in asdict(d).items()
                                   if k not in ('base_url', 'quote', 'embedding_input')} for d in selected]
    columns = ','.join(ROW_FIELDS)
    existing = [dict(r) for r in connection.execute('SELECT '+columns+''' FROM review_opinion_embeddings
        WHERE embedding_space_id=? AND text_builder_version=?
        AND stable_versioned_opinion_key=ANY(?::text[]) ORDER BY id''',
        (SPACE_ID, TEXT_BUILDER_VERSION, list(selected_by_key))).fetchall()]
    plan['existing_selected_rows'] = existing
    keys = [r['stable_versioned_opinion_key'] for r in existing]
    require(len(keys) == len(set(keys)) and set(keys) <= set(selected_by_key), 'existing_key_identity_invalid')
    summary.update(U=len(existing), N=len(selected)-len(existing), P=len(selected)-len(existing))
    summary['n_plus_u_equals_s'] = summary['N']+summary['U'] == summary['S']
    summary['cross_product_unique_key_conflict'] = any(r['product_id'] != options['product_ids'][0] for r in existing)
    summary['owner_identity_matches'] = all(
        all(r[k] == getattr(selected_by_key[r['stable_versioned_opinion_key']], k) for k in OWNER_FIELDS)
        and r['embedding_space_id'] == SPACE_ID for r in existing)
    require(not summary['cross_product_unique_key_conflict'] and summary['owner_identity_matches'],
            'existing_row_owner_mismatch_stop')
    # This is exactly _publish's predicate: no builder filter and ALL current keys.
    where, parameters = _range(corpus)
    invalidated = [dict(r) for r in connection.execute('SELECT '+columns+''' FROM review_opinion_embeddings c
        WHERE '''+where+''' AND label_profile_key=? AND embedding_space_id=? AND invalidated_at IS NULL
        AND NOT (stable_versioned_opinion_key=ANY(?::text[])) ORDER BY id''',
        (*parameters, corpus.label_profile_key, SPACE_ID, valid_keys)).fetchall()]
    plan['invalidation_rows'] = invalidated
    summary['I'] = len(invalidated)
    isolation = {k: options[k] for k in ('source', 'dataset_version', 'date_from', 'date_to', 'as_of')}
    isolation['product_ids'] = [manifest['isolation_product_id']]
    where55, parameters55, _ = review_scope(**isolation, alias='c')
    # Every stored column (including vector/timestamps) is hashed inside PG.
    # Fixed-length per-row digests, ordered by PK, avoid ambiguous concatenation.
    fingerprint_sql = '''SELECT count(*) AS row_count,
        encode(sha256(convert_to(coalesce(string_agg(row_sha256,'' ORDER BY id),''),'UTF8')),'hex') AS rowset_sha256
        FROM (SELECT c.id,encode(sha256(convert_to(to_jsonb(c)::text,'UTF8')),'hex') AS row_sha256
        FROM review_opinion_embeddings c WHERE '''+where55+'''
        AND label_profile_key=? AND embedding_space_id=? AND text_builder_version=?) fingerprints'''
    baseline = dict(connection.execute(fingerprint_sql,
        (*parameters55, corpus.label_profile_key, SPACE_ID, TEXT_BUILDER_VERSION)).fetchone())
    require(type(baseline['row_count']) is int and baseline['row_count'] >= 0, 'invalid_055_count')
    digest = baseline['rowset_sha256']
    require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'invalid_055_digest')
    summary.update(baseline_055_count=baseline['row_count'], baseline_055_sha256=digest)
    plan['055_baseline'] = dict(baseline, algorithm='sha256-pk-ordered-full-row-jsonb-v1',
        query_sha256=sha256(fingerprint_sql.encode()).hexdigest(), scope=isolation,
        profile=corpus.label_profile_key, space=SPACE_ID, builder=TEXT_BUILDER_VERSION)
    reasons = []
    if summary['U'] != 0: reasons.append('unexpected_existing_updates')
    if summary['I'] != 0: reasons.append('unexpected_invalidations')
    if baseline['row_count'] != 200: reasons.append('055_baseline_count_changed')
    summary['blocking_reasons'] = reasons
    summary['can_enter_write_authorization'] = not reasons and summary['n_plus_u_equals_s']
    summary['status'] = 'budget_ready_pending_separate_authorization' if summary['can_enter_write_authorization'] else 'blocked_unexpected_budget'


def execute_readonly_once(root, dsn, options, manifest):
    summary = summary_template()
    plan = {'manifest_sha256': safety.MANIFEST_SHA, 'owner_implementation_sha256': OWNER_SHA,
            'space': SPACE_ID, 'builder': TEXT_BUILDER_VERSION}
    connection = None
    claimed = False
    phase = 'claim'
    start = perf_counter()
    try:
        directory = root/OUTPUT
        directory.mkdir(parents=True, exist_ok=False)
        claimed = True
        (directory/'execution.claim').write_text('single readonly budget batch\n', encoding='utf-8')
        phase = 'connect'
        summary['connection_attempts'] = 1
        connection = ReadonlyBudgetConnection(dsn)
        phase = 'readonly_transaction_setup'
        connection.execute('BEGIN')
        connection.execute("SET LOCAL statement_timeout='10s'")
        connection.execute("SET LOCAL lock_timeout='1s'")
        readonly = connection.execute('SHOW transaction_read_only').fetchone()[0]
        isolation = connection.execute('SHOW transaction_isolation').fetchone()[0]
        require(readonly == 'on' and isolation == 'repeatable read', 'readonly_transaction_not_confirmed')
        summary['transaction_protection_confirmed'] = True
        snapshot_sql = 'SELECT txid_current_snapshot()::text, transaction_timestamp()::text'
        snapshot = tuple(connection.execute(snapshot_sql).fetchone())
        plan['snapshot'] = list(snapshot)
        phase = 'readonly_schema_check'
        check_review_schema(connection)
        phase = 'readonly_inventory'
        inventory(connection, options, manifest, summary, plan)
        require(tuple(connection.execute(snapshot_sql).fetchone()) == snapshot, 'read_snapshot_changed')
    except (Exception, KeyboardInterrupt) as exc:
        summary['status'] = 'error'
        summary['can_enter_write_authorization'] = False
        summary['error'] = safety.safe_error(exc, phase)
    finally:
        if connection is not None:
            try:
                connection.rollback()
                summary['rolled_back'] = True
            except Exception as exc:
                summary['status'] = 'error'
                summary['can_enter_write_authorization'] = False
                summary['rollback_error'] = safety.safe_error(exc, 'rollback')
            finally:
                try:
                    connection.close()
                    summary['connection_closed'] = bool(connection.raw.closed)
                    require(summary['connection_closed'], 'connection_close_not_confirmed')
                except Exception as exc:
                    summary['status'] = 'error'
                    summary['can_enter_write_authorization'] = False
                    summary['close_error'] = safety.safe_error(exc, 'close')
            summary['statement_count'] = connection.statement_count
        summary['elapsed_ms'] = round((perf_counter()-start)*1000, 3)
        if claimed:
            try:
                plan['budget'] = {k: summary[k] for k in ('S', 'N', 'U', 'I', 'P', 'selected_review_groups')}
                plan['result_status'] = summary['status']
                # No corpus/quote/endpoint or vector values are serialized.
                with (root/OUTPUT/'plan.json').open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps(plan, ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')
                summary['private_plan_sha256'] = sha256((root/OUTPUT/'plan.json').read_bytes()).hexdigest()
                with (root/OUTPUT/'summary.json').open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
            except Exception as exc:
                summary['status'] = 'error'
                summary['can_enter_write_authorization'] = False
                summary['receipt_error'] = safety.safe_error(exc, 'save_receipt')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-readonly-once', action='store_true')
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        dsn, options, manifest = preflight(ROOT)
        if args.execute_readonly_once:
            summary = execute_readonly_once(ROOT, dsn, options, manifest)
        else:
            summary = {'status': 'offline_preflight_passed', 'connection_attempts': 0, 'write_authorized': False}
    except (Exception, KeyboardInterrupt) as exc:
        summary = summary_template()
        summary['error'] = safety.safe_error(exc, 'preflight')
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 0 if summary['status'] in ('offline_preflight_passed', 'budget_ready_pending_separate_authorization') else 1


if __name__ == '__main__':
    raise SystemExit(main())
