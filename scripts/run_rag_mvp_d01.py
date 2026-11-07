"""Manual Windows D01 only. Default is offline preflight; never retries."""
import argparse
from contextlib import closing
from datetime import date, timedelta
from hashlib import sha256
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from dotenv import dotenv_values
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import database_identity
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads

MANIFEST_SHA = 'a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29'
SQL_SHA = '62f15775b5ef4b8e841212453c1c853609856df467917470c12e66b41215a86c'
SPACE = 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
OPTIONS = '-c search_path=public -c timezone=UTC -c default_transaction_read_only=on'
OUTPUT = Path('outputs/rag-semantic-retrieval/mvp-d01-manual-v1')
BASELINE = Path('outputs/rag-semantic-retrieval/mvp-first-20261107-v1')
COUNT_FIELDS = ('reviews', 'declared_scope_rows', 'rows_linked_to_scoped_reviews',
                'stored_scope_mismatch', 'changed_content', 'changed_timestamp',
                'f_scope', 'f_profile', 'f_space_builder', 'f_active_before_current_key_join')
DIM_FIELDS = ('min_dims', 'max_dims')
NORM_FIELDS = ('min_norm', 'max_norm')
PG_OVERRIDES = ('PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE '
                'PGSERVICE PGSERVICEFILE PGSSLMODE PGSSLCERT PGSSLKEY PGSSLROOTCERT '
                'PGSSLCRL PGSSLCRLDIR PGSSLSNI PGSSLMINPROTOCOLVERSION PGSSLMAXPROTOCOLVERSION '
                'PGGSSENCMODE PGCHANNELBINDING PGREQUIREAUTH PGTARGETSESSIONATTRS '
                'PGLOADBALANCEHOSTS PGOPTIONS PGCONNECT_TIMEOUT').split()


class CheckFailed(ValueError):
    """Only fixed, public error codes may be displayed."""


def require(condition, code):
    if not condition:
        raise CheckFailed(code)


def frozen_database_identity(root):
    # Local read-only queue metadata; never call the business schema initializer.
    queue = (root/BASELINE/'tasks.sqlite3').resolve()
    require(queue.is_file(), 'frozen_database_identity_missing')
    with closing(sqlite3.connect(queue.as_uri()+'?mode=ro', uri=True)) as connection:
        rows = connection.execute('SELECT * FROM task_context').fetchall()
    require(len(rows) == 1 and len(rows[0]) == 1, 'frozen_database_identity_invalid')
    return rows[0][0]


def preflight(root):
    require(Path(sys.executable).resolve() == (root/'.venv/Scripts/python.exe').resolve(), 'use_project_venv')
    require(not (root/OUTPUT).exists(), 'manual_d01_already_claimed_do_not_rerun')
    manifest = strict_json_loads((root/'tests/fixtures/rag_mvp_acceptance_v1.json').read_text(encoding='utf-8'))
    require(content_sha256(manifest) == MANIFEST_SHA, 'frozen_manifest_changed')
    report = (root/'docs/rag-mvp-final-acceptance.md').read_text(encoding='utf-8')
    require(report.count('#### D01：') == report.count('#### D02：') == 1, 'd01_section_ambiguous')
    section = report.split('#### D01：', 1)[1].split('#### D02：', 1)[0]
    blocks = re.findall(r'```sql\n(.*?)```', section, re.S)
    require(len(blocks) == 1 and sha256(blocks[0].encode()).hexdigest() == SQL_SHA, 'frozen_d01_sql_changed')
    template = blocks[0]
    config_path = root/'.env'
    baseline = strict_json_loads((root/BASELINE/'preflight.json').read_text(encoding='utf-8'))
    require(baseline['manifest_sha256'] == MANIFEST_SHA, 'baseline_manifest_mismatch')
    require(sha256(config_path.read_bytes()).hexdigest() == baseline['protected_sha256']['.env'], 'frozen_config_file_changed')
    values = read_settings(config_path)
    dsn = values.get('DATABASE_URL', '')
    require(dsn.startswith(('postgresql://', 'postgres://')), 'postgres_configuration_required')
    require(dsn == dotenv_values(config_path, interpolate=False).get('DATABASE_URL'), 'database_environment_override_changed')
    require(not any(os.environ.get(k) for k in PG_OVERRIDES), 'postgres_environment_not_frozen')
    require(database_identity(dsn) == frozen_database_identity(root), 'frozen_database_target_mismatch')
    target = conninfo_to_dict(dsn)
    ipaddress.ip_address(target['host'])  # One numeric target; no DNS/multi-host fallback.
    require(not target.get('hostaddr') and target.get('port', '5432').isdigit(), 'single_frozen_endpoint_required')
    scope = manifest['scope']
    parameters = dict(p053=manifest['main_product_id'], p055=manifest['isolation_product_id'],
        source=scope['source'], dataset=scope['dataset_version'], profile=manifest['annotation_profile_sha256'],
        lower=scope['date_from']+'T00:00:00.000+00:00',
        upper=(date.fromisoformat(scope['date_to'])+timedelta(days=1)).isoformat()+'T00:00:00.000+00:00',
        space=SPACE, builder='quote-utf8-v1')
    require(set(re.findall(r':([a-z][a-z0-9_]*)', template)) == set(parameters), 'd01_bindings_changed')
    sql = re.sub(r'(?<!:):([a-z][a-z0-9_]*)', lambda m: '%('+m[1]+')s', template)
    require(re.sub(r'%\(([a-z][a-z0-9_]*)\)s', lambda m: ':'+m[1], sql) == template, 'sql_marker_conversion_changed')
    return dsn, sql, parameters


def safe_error(exc, phase):
    result = {'phase': phase, 'type': type(exc).__name__, 'sqlstate': None, 'category': 'unclassified', 'winsock_codes': []}
    if isinstance(exc, CheckFailed):
        result.update(code=str(exc), category='safety_check_failed')
        return result
    state = getattr(exc, 'sqlstate', None)
    if isinstance(state, str) and re.fullmatch('[A-Z0-9]{5}', state):
        result['sqlstate'] = state
    # Inspect in memory; never persist exception text, args, DSN or server message.
    text = str(exc).lower()
    codes = [n for n in (10013, 10061, 10060, 10065, 10051, 10049, 11001)
             if re.search(r'(?<![0-9])'+str(n)+r'(?![0-9])', text)]
    result['winsock_codes'] = codes
    for code, words, category in (
        (10013, ('permission denied', 'access denied'), 'os_access_denied'),
        (10061, ('connection refused', 'actively refused'), 'tcp_connection_refused'),
        (10060, ('timeout', 'timed out'), 'timeout'),
        (11001, ('could not translate host name',), 'name_resolution_failed'),
        (None, ('password authentication failed', 'no pg_hba.conf entry', 'no password supplied'), 'authentication_rejected'),
        (None, ('unrecognized configuration parameter', 'invalid value for parameter'), 'startup_parameter_rejected'),
        (None, ('ssl', 'tls', 'certificate'), 'tls_failure'),
    ):
        if code in codes or any(word in text for word in words):
            result['category'] = category
            break
    return result


def checked_rows(rows, parameters):
    expected = {'product_id', *COUNT_FIELDS, *DIM_FIELDS, *NORM_FIELDS}
    require(len(rows) == 2, 'unexpected_aggregate_row_count')
    require([r.get('product_id') for r in rows] == [parameters['p053'], parameters['p055']], 'result_scope_mismatch')
    for row in rows:
        require(set(row) == expected, 'unexpected_result_columns')
        require(all(type(row[k]) is int and row[k] >= 0 for k in COUNT_FIELDS), 'invalid_aggregate_count')
        require(all(row[k] is None or (type(row[k]) is int and row[k] > 0) for k in DIM_FIELDS), 'invalid_dimension_aggregate')
        require(all(row[k] is None or (type(row[k]) in (int, float) and math.isfinite(row[k]) and row[k] >= 0)
                    for k in NORM_FIELDS), 'invalid_norm_aggregate')
    return rows


def diagnose(rows):
    row = rows[0]
    if [r['reviews'] for r in rows] != [1699, 510]:
        return 'scope_counts_drifted_stop'
    if row['declared_scope_rows'] == row['rows_linked_to_scoped_reviews'] == 0:
        return '053_vector_coverage_missing_in_current_snapshot'
    if row['rows_linked_to_scoped_reviews'] == 0:
        return 'stored_rows_need_review_identity_verification'
    for field in ('f_scope', 'f_profile', 'f_space_builder', 'f_active_before_current_key_join'):
        if row[field] == 0:
            return '053_excluded_at_'+field
    return 'current_key_and_complete_snapshot_verification_still_required'


def execute_once(root, dsn, sql, parameters):
    outcome = {'status': 'error', 'connection_attempts': 0, 'd01_attempts': 0, 'd01_completed': 0,
               'automatic_retries': 0, 'rolled_back': False, 'connection_closed': False}
    connection = None
    phase = 'claim'
    claimed = False
    started = perf_counter()
    try:
        # Atomic directory creation is the permanent one-shot guard, including failures.
        destination = root/OUTPUT
        destination.mkdir(parents=True, exist_ok=False)
        claimed = True
        (destination/'execution.claim').write_text('D01 manual single attempt\n', encoding='utf-8')
        phase = 'connect'
        outcome['connection_attempts'] = 1
        connection = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10, options=OPTIONS)
        phase = 'readonly_transaction_setup'
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY', prepare=False)
        connection.execute("SET LOCAL statement_timeout='10s'", prepare=False)
        connection.execute("SET LOCAL lock_timeout='1s'", prepare=False)
        readonly = connection.execute('SHOW transaction_read_only', prepare=False).fetchone()['transaction_read_only']
        isolation = connection.execute('SHOW transaction_isolation', prepare=False).fetchone()['transaction_isolation']
        require(readonly == 'on' and isolation == 'repeatable read', 'readonly_transaction_not_confirmed')
        outcome['transaction_protection_confirmed'] = True
        phase = 'D01'
        outcome['d01_attempts'] = 1
        rows = connection.execute(sql, parameters, prepare=False).fetchall()
        outcome['d01_completed'] = 1
        rows = checked_rows(rows, parameters)
        outcome.update(status='completed', rows=rows, diagnosis=diagnose(rows))
    except (Exception, KeyboardInterrupt) as exc:
        outcome['error'] = safe_error(exc, phase)
    finally:
        if connection is not None:
            try:
                connection.rollback()
                outcome['rolled_back'] = True
            except Exception as exc:
                outcome['status'] = 'error'
                outcome['rollback_error'] = safe_error(exc, 'rollback')
            finally:
                try:
                    connection.close()
                    outcome['connection_closed'] = bool(connection.closed)
                except Exception as exc:
                    outcome['status'] = 'error'
                    outcome['close_error'] = safe_error(exc, 'close')
        outcome['elapsed_ms'] = round((perf_counter()-started)*1000, 3)
        if claimed:
            try:
                with (root/OUTPUT/'result.json').open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps(outcome, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
            except Exception as exc:
                outcome['status'] = 'error'
                outcome['receipt_error'] = safe_error(exc, 'save_receipt')
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-once', action='store_true', help='Manually attempt one connection and original D01; no retries.')
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        dsn, sql, parameters = preflight(ROOT)
        if args.execute_once:
            result = execute_once(ROOT, dsn, sql, parameters)
        else:
            result = {'status': 'offline_preflight_passed', 'connection_attempts': 0, 'd01_attempts': 0,
                      'manifest_sha256': MANIFEST_SHA, 'd01_sql_sha256': SQL_SHA}
    except (Exception, KeyboardInterrupt) as exc:
        result = {'status': 'error', 'connection_attempts': 0, 'd01_attempts': 0, 'error': safe_error(exc, 'preflight')}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
