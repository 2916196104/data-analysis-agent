"""离线生成 E1B 环境方案；不连接数据库、不启动程序、不代填批准人。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from p6e1b_environment import ENV_NAMES, E1BBlocked, PURPOSE, load_authorization
from data_analysis_agent.core.database import database_identity, postgres_schema
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.store import SCHEMA as BUSINESS_SCHEMA
from data_analysis_agent.reviews.label_store import LABEL_SCHEMA
from data_analysis_agent.reviews.human_review import SCHEMA as HUMAN_SCHEMA
from data_analysis_agent.jobs.store import SCHEMA as TASK_SCHEMA

PERMISSIONS = ('create_cluster', 'create_roles', 'create_databases', 'initialize_schema', 'test_connections')
SCHEMA_FILES = ('reviews/store.py', 'reviews/label_store.py', 'reviews/human_review.py',
                'jobs/store.py', 'core/database.py', 'core/serialization.py')


def check_directory_scope(cluster):
    cluster=Path(cluster)
    for path in reversed((cluster,*cluster.parents)):
        if path==ROOT or ROOT not in path.parents:
            continue
        try:
            info=path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info,'st_file_attributes',0) & 0x400):
            raise E1BBlocked('target_path_is_not_a_plain_directory')
    if cluster.resolve()!=cluster:
        raise E1BBlocked('target_path_resolves_elsewhere')


def make_plan(run_id, port):
    if not re.fullmatch('[a-f0-9]{32}', run_id) or type(port) is not int or not 1024 <= port <= 65535:
        raise E1BBlocked('invalid_run_id_or_nonprivileged_port')
    if port in (5432, 55432):
        raise E1BBlocked('reserved_existing_service_port')
    base = ROOT / '.cache' / 'p6e1b-env' / run_id
    check_directory_scope(base / 'cluster')
    return {'version': 'p6-e1b-environment-plan-v1', 'run_id': run_id, 'host': '127.0.0.1',
            'port': port, 'server_major': 18, 'cluster_directory': (base / 'cluster').as_posix(),
            'bootstrap_role': f'p6_e1b_{run_id}_bootstrap',
            'databases': {k: f'p6_e1b_{run_id}_{k}' for k in ('business', 'tasks')},
            'roles': {k: f'p6_e1b_{run_id}_{k}' for k in ('creator', 'reader', 'writer', 'task')},
            'schema_source_sha256': {p: hashlib.sha256((ROOT / 'src/data_analysis_agent' / p).read_bytes()).hexdigest()
                                     for p in SCHEMA_FILES},
            'preparation_source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
                'scripts/prepare_p6e1b_environment.py', 'tests/p6e1b_environment.py', 'tests/p6e1b_postgresql_integration.py')}}


def checked_plan(filename):
    plan = json.loads(Path(filename).read_text(encoding='utf-8'))
    if plan != make_plan(plan['run_id'], plan['port']):
        raise E1BBlocked('plan_or_schema_source_changed')
    return plan


def target_values(plan):
    # 无密码目标仅用于离线身份摘要；运行凭据由负责人在新进程中交互输入。
    values = {}
    for role, variable in ENV_NAMES.items():
        user = plan['roles']['creator' if role == 'task_creator' else role]
        database = plan['databases']['tasks' if role in ('task', 'task_creator') else 'business']
        values[variable] = f"postgresql://{user}@127.0.0.1:{plan['port']}/{database}?sslmode=disable"
    return values


def draft_manifest(plan):
    values = target_values(plan)
    return {'version': 'p6-e1b-authorization-v1', 'run_id': plan['run_id'], 'purpose': PURPOSE,
            'authorized': False, 'synthetic_writes_authorized': False, 'approved_by': '', 'expires_at': '',
            'allow_database_or_schema_drop': False,
            'identity_sha256': {r: hashlib.sha256(database_identity(values[v]).encode()).hexdigest()
                                for r, v in ENV_NAMES.items()},
            'role_sha256': {r: hashlib.sha256(plan['roles']['creator' if r == 'task_creator' else r].encode()).hexdigest()
                            for r in ENV_NAMES},
            'environment_preparation': {'plan_sha256': content_sha256(plan), 'authorized': False,
                                        **{p: False for p in PERMISSIONS}, 'expected_system_identifier': ''}}


def check_approval(plan, filename, *, require_cluster=False):
    config = load_authorization({**target_values(plan), 'P6_E1B_AUTHORIZATION_FILE': str(filename)})
    extra = config.manifest.get('environment_preparation', {})
    if (extra.get('plan_sha256') != content_sha256(plan) or extra.get('authorized') is not True
            or any(extra.get(p) is not True for p in PERMISSIONS)):
        raise E1BBlocked('environment_preparation_not_explicitly_approved')
    if require_cluster and not re.fullmatch('[0-9]{10,30}', extra.get('expected_system_identifier', '')):
        raise E1BBlocked('new_cluster_identity_not_confirmed')
    return config


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def cluster_guard(plan, database, system_id, expiry):
    return f"""SELECT current_database()={literal(database)}
 AND current_user={literal(plan['bootstrap_role'])}
 AND inet_server_addr()::text='127.0.0.1' AND inet_server_port()={plan['port']}
 AND current_setting('server_version_num')::int / 10000=18
 AND clock_timestamp() < {literal(expiry)}::timestamptz
 AND replace(current_setting('data_directory'),chr(92),'/')={literal(plan['cluster_directory'])}
 AND (SELECT system_identifier::text FROM pg_control_system())={literal(system_id)} AS cluster_matches \\gset
\\if :cluster_matches
\\else
\\echo BLOCKED: isolated cluster identity mismatch
SELECT 1/0 AS p6_e1b_guard_rejected;
\\endif
"""


def render_sql(plan, config):
    roles, databases = plan['roles'], plan['databases']
    q = {k: ident(v) for k, v in roles.items()}
    system_id = config.manifest['environment_preparation']['expected_system_identifier']
    expiry = config.manifest['expires_at']
    sql = '\\set ON_ERROR_STOP on\n' + cluster_guard(plan, 'postgres', system_id, expiry)
    sql += """SELECT NOT EXISTS(SELECT 1 FROM pg_database WHERE datname NOT IN ('postgres','template0','template1'))
 AND NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN (""" + ','.join(map(literal, roles.values())) + """)) AS fresh_cluster \\gset
\\if :fresh_cluster
\\else
\\echo BLOCKED: provisioning requires a new, empty, dedicated cluster
SELECT 1/0 AS p6_e1b_guard_rejected;
\\endif
"""
    for role in roles:
        sql += f'CREATE ROLE {q[role]} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8;\n'
        sql += f'\\password {roles[role]}\n'
        for setting, value in (('statement_timeout', '5000ms'), ('lock_timeout', '1000ms'),
                               ('idle_in_transaction_session_timeout', '5000ms'), ('timezone', 'UTC')):
            sql += f'ALTER ROLE {q[role]} SET {setting}={literal(value)};\n'
    sql += f"ALTER ROLE {q['reader']} SET default_transaction_read_only=on;\n"
    for database in databases.values():
        sql += f'CREATE DATABASE {ident(database)} OWNER {q["creator"]} TEMPLATE template0 ENCODING \'UTF8\' LC_COLLATE \'C\' LC_CTYPE \'C\';\n'
    for database in ('postgres', 'template0', 'template1', *databases.values()):
        sql += f'REVOKE ALL ON DATABASE {ident(database)} FROM PUBLIC;\n'
    sql += f'GRANT CONNECT ON DATABASE {ident(databases["business"])} TO {q["reader"]},{q["writer"]};\n'
    sql += f'GRANT CONNECT ON DATABASE {ident(databases["tasks"])} TO {q["task"]};\n'
    for kind, database in databases.items():
        sql += f"\\connect {database} {plan['bootstrap_role']} 127.0.0.1 {plan['port']}\n"
        sql += cluster_guard(plan, database, system_id, expiry)
        sql += f'BEGIN;\nREVOKE ALL ON SCHEMA public FROM PUBLIC;\nGRANT USAGE,CREATE ON SCHEMA public TO {q["creator"]};\nSET ROLE {q["creator"]};\n'
        sql += 'CREATE TABLE review_database_schema(namespace TEXT PRIMARY KEY,version INTEGER NOT NULL);\n'
        sql += f'INSERT INTO review_database_schema VALUES ({literal("business" if kind=="business" else "tasks")},1);\n'
        if kind == 'business':
            sql += postgres_schema(BUSINESS_SCHEMA + LABEL_SCHEMA + HUMAN_SCHEMA)
        else:
            sql += postgres_schema(TASK_SCHEMA)
            business_identity = database_identity(config.dsns['reader'])
            sql += f'INSERT INTO task_context VALUES ({literal(business_identity)});\n'
        sql += f"""
CREATE TABLE public.p6_e1b_test_scope (
 run_id TEXT PRIMARY KEY CHECK(run_id={literal(plan['run_id'])}),
 purpose TEXT NOT NULL CHECK(purpose={literal(PURPOSE)}),
 authorization_sha256 TEXT NOT NULL CHECK(authorization_sha256 ~ '^[a-f0-9]{{64}}$')
);
INSERT INTO public.p6_e1b_test_scope VALUES ({literal(plan['run_id'])},{literal(PURPOSE)},{literal(config.authorization_sha256)});
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
"""
        runtime = ['reader', 'writer'] if kind == 'business' else ['task']
        for role in runtime:
            sql += f'GRANT USAGE ON SCHEMA public TO {q[role]};\nGRANT SELECT ON ALL TABLES IN SCHEMA public TO {q[role]};\n'
        if kind == 'business':
            tables = 'import_runs,products,reviews,import_errors,review_annotations,review_opinions,review_human_reviews,review_human_opinions'
            sql += f'GRANT INSERT,UPDATE ON {tables} TO {q["writer"]};\nGRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {q["writer"]};\n'
        else:
            sql += f'GRANT INSERT,UPDATE ON tasks,worker_state TO {q["task"]};\n'
        sql += 'RESET ROLE;\nCOMMIT;\n'
    return sql


def render_injection(plan, *, plan_file=None):
    # 只设置当前进程环境，不运行测试/SQL，不保存密码，不修改 .env。
    plan_file=Path(plan_file) if plan_file is not None else Path(plan['cluster_directory']).parent/'plan.json'
    lines = ["param([Parameter(Mandatory)][string]$AuthorizationFile)",
             "$ErrorActionPreference='Stop'",
             "foreach ($p6e1bName in @(" + ','.join("'"+n+"'" for n in [*ENV_NAMES.values(), 'P6_E1B_AUTHORIZATION_FILE']) + ")) { [Environment]::SetEnvironmentVariable($p6e1bName,$null,'Process') }",
             "$p6e1bAuth=(Resolve-Path -LiteralPath $AuthorizationFile).Path",
             f"$p6e1bPython='{(ROOT / '.venv/Scripts/python.exe').as_posix()}'",
             f"& $p6e1bPython '{(ROOT / 'scripts/prepare_p6e1b_environment.py').as_posix()}' check --plan '{plan_file.as_posix()}' --authorization $p6e1bAuth --require-cluster",
             "if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: approval check failed' }",
             "$p6e1bPasswords=@{}", 'try {']
    for role in plan['roles']:
        lines += [f" $p6e1bSecure=Read-Host '本次隔离库 {role} 密码' -AsSecureString",
                  ' $p6e1bPtr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p6e1bSecure)',
                  f" try {{ $p6e1bPasswords['{role}']=[uri]::EscapeDataString([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p6e1bPtr)) }} finally {{ [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p6e1bPtr); $p6e1bSecure.Dispose() }}"]
    for role, variable in ENV_NAMES.items():
        user = 'creator' if role == 'task_creator' else role
        database = plan['databases']['tasks' if role in ('task', 'task_creator') else 'business']
        lines += [f" $env:{variable}='postgresql://{plan['roles'][user]}:'+$p6e1bPasswords['{user}']+'@127.0.0.1:{plan['port']}/{database}?sslmode=disable'"]
    lines += [" $env:P6_E1B_AUTHORIZATION_FILE=$p6e1bAuth", "} catch {",
              " foreach ($p6e1bName in @(" + ','.join("'"+n+"'" for n in [*ENV_NAMES.values(), 'P6_E1B_AUTHORIZATION_FILE']) + ")) { [Environment]::SetEnvironmentVariable($p6e1bName,$null,'Process') }",
              " throw 'BLOCKED: configuration injection failed'", "} finally { $p6e1bPasswords.Clear() }",
              "Write-Output 'E1B process variables prepared; no database connection made.'"]
    return '\n'.join(lines) + '\n'


def render_hba(plan):
    roles, dbs = plan['roles'], plan['databases']
    return (f"host all {plan['bootstrap_role']} 127.0.0.1/32 scram-sha-256\n"
            f"host {dbs['business']} {roles['creator']},{roles['reader']},{roles['writer']} 127.0.0.1/32 scram-sha-256\n"
            f"host {dbs['tasks']} {roles['creator']},{roles['task']} 127.0.0.1/32 scram-sha-256\n"
            'host all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n')


def write_new(path, content):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    draft = commands.add_parser('draft')
    draft.add_argument('--run-id', default=None)
    draft.add_argument('--port', type=int, required=True)
    for command in ('check', 'render'):
        p = commands.add_parser(command)
        p.add_argument('--plan', type=Path, required=True)
        p.add_argument('--authorization', type=Path, required=True)
        p.add_argument('--require-cluster', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'draft':
            plan = make_plan(args.run_id or uuid4().hex, args.port)
            folder = Path(plan['cluster_directory']).parent
            folder.mkdir(parents=True, exist_ok=False)
            write_new(folder / 'plan.json', json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
            write_new(folder / 'authorization.pending.json', json.dumps(draft_manifest(plan), ensure_ascii=False, indent=2)+'\n')
            write_new(folder / 'inject.ps1', render_injection(plan))
            write_new(folder / 'pg_hba.template.conf', render_hba(plan))
            result = {'status': 'DRAFT_ONLY', 'plan_file': str(folder / 'plan.json'), 'database_connections': 0}
        else:
            plan = checked_plan(args.plan)
            config = check_approval(plan, args.authorization, require_cluster=args.command == 'render' or args.require_cluster)
            if args.command == 'render':
                write_new(args.plan.parent / 'provision.psql', render_sql(plan, config))
            result = {'status': 'OFFLINE_APPROVAL_CHECKED' if args.command == 'check' else 'SQL_RENDERED_NOT_EXECUTED',
                      'authorization_sha256': config.authorization_sha256, 'database_connections': 0}
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (E1BBlocked, ValueError, KeyError, TypeError, OSError):
        print(json.dumps({'status': 'BLOCKED', 'reason': 'plan_approval_or_output_invalid', 'database_connections': 0})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
