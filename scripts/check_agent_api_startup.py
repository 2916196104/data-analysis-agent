"""Read-only MVP startup preflight. Never starts an API/Worker or submits tasks."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

DB = 'postgresql://rag_audit_ro@127.0.0.1:5432/reviews?hostaddr=127.0.0.1'
VERSION_TABLES = ('review_database_schema', 'review_labeling_schema',
                  'review_workbench_schema', 'review_monitor_schema')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--permissions-only', action='store_true',
                        help='Read version-table permissions; do not invoke the production API check.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'outputs/mvp-real-acceptance' / (
        'startup-permissions-before.json' if args.permissions_only else 'startup-check-after.json')
    result = dict(checked_at=datetime.now(timezone.utc).isoformat(), status='FAIL',
        mode='permissions_only' if args.permissions_only else 'production_startup_check',
        api_started=False, worker_started=False, task_submissions=0, task_database_writes=0,
        business_write_statements=0, acl_changes=0, model_calls=0, embedding_calls=0,
        production_api_check='NOT_RUN', missing_select=[], errors=[])
    connection = None
    try:
        connection = psycopg.connect(DB, connect_timeout=4, autocommit=True,
            row_factory=dict_row, application_name='agent-mvp-startup-preflight',
            options='-c search_path=public -c default_transaction_read_only=on '
                    '-c statement_timeout=5000 -c lock_timeout=1000')
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result['identity'] = dict(connection.execute("""SELECT current_database() AS database,
            current_user AS role, session_user AS login_role,
            current_setting('transaction_read_only') AS read_only,
            has_schema_privilege(current_user,'public','USAGE') AS schema_usage""").fetchone())
        if result['identity'] != dict(database='reviews', role='rag_audit_ro',
                login_role='rag_audit_ro', read_only='on', schema_usage=True):
            raise ValueError('unexpected_database_role_or_readonly_state')
        result['role'] = dict(connection.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,
            rolreplication,rolbypassrls,
            pg_has_role(current_user,'pg_write_all_data','MEMBER') AS write_all_data
            FROM pg_catalog.pg_roles WHERE rolname=current_user""").fetchone())
        if any(result['role'].values()):
            raise ValueError('unexpected_privileged_role')
        result['version_tables'] = [dict(row) for row in connection.execute("""SELECT t.name,
            c.oid IS NOT NULL AS exists,
            has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
            has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write,
            pg_has_role(current_user,c.relowner,'MEMBER') AS owner_member
            FROM unnest(%s::text[]) AS t(name)
            LEFT JOIN pg_catalog.pg_namespace n ON n.nspname='public'
            LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=t.name
            ORDER BY t.name""", (list(VERSION_TABLES),)).fetchall()]
        result['missing_select'] = [t['name'] for t in result['version_tables'] if not t['can_select']]
        if any(t['can_write'] or t['owner_member'] for t in result['version_tables']):
            raise ValueError('unexpected_version_table_write_privilege')
        result['direct_role_acl'] = [dict(row) for row in connection.execute("""SELECT c.relname,
            pg_get_userbyid(a.grantor) AS grantor,a.privilege_type,a.is_grantable
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            WHERE n.nspname='public' AND c.relname=ANY(%s)
            AND a.grantee=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
            ORDER BY c.relname,a.privilege_type""", (list(VERSION_TABLES),)).fetchall()]
        result['version_values'] = {}
        for table in result['version_tables']:
            if not table['can_select']:
                continue
            query = sql.SQL('SELECT version FROM public.{}').format(sql.Identifier(table['name']))
            parameters = ()
            if table['name'] == 'review_database_schema':
                query += sql.SQL(' WHERE namespace=%s')
                parameters = ('business',)
            values = [r['version'] for r in connection.execute(query, parameters).fetchall()]
            result['version_values'][table['name']] = values
            if values != [1]:
                result['errors'].append('unsupported_version:' + table['name'])
    except Exception as exc:
        result['errors'].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    finally:
        if connection is not None:
            try:
                connection.execute('ROLLBACK')
            except Exception as exc:
                result['errors'].append('rollback_failed:' + type(exc).__name__)
            finally:
                connection.close()
                result['connection_closed'] = True

    if not args.permissions_only:
        from data_analysis_agent.api.app import check_business_database
        try:
            # The original production check is mandatory, with the same read-only login.
            if result['errors']:
                raise ValueError('metadata_preflight_failed_stop')
            check_business_database(DB)
            result['production_api_check'] = 'PASS'
        except Exception as exc:
            result['production_api_check'] = 'FAIL'
            result['errors'].append(type(exc).__name__)
    result['status'] = 'PASS' if not result['errors'] and not result['missing_select'] else 'FAIL'
    result['worker_start'] = 'NOT_RUN: requires approved isolated task DB and available worker lock'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
