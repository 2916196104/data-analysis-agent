"""E1B 显式授权门；只读取指定测试变量，不回退 .env / DATABASE_URL。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from data_analysis_agent.core.database import database_identity
from data_analysis_agent.core.serialization import content_sha256

ENV_NAMES = {role: 'P6_E1B_' + role.upper() + '_DSN'
             for role in ('creator', 'task_creator', 'reader', 'writer', 'task')}
PURPOSE = 'synthetic-p6-postgresql-integration'


class E1BBlocked(ValueError):
    """消息仅含固定安全原因，不包含 DSN、角色名或原始异常。"""


@dataclass(frozen=True)
class AuthorizedEnvironment:
    run_id: str
    authorization_sha256: str
    manifest: dict = field(repr=False)
    dsns: dict = field(repr=False)


def load_authorization(values, *, now=None):
    try:
        filename = values.get('P6_E1B_AUTHORIZATION_FILE')
        if not filename or any(not values.get(name) for name in ENV_NAMES.values()):
            raise E1BBlocked('isolated_environment_or_authorization_missing')
        manifest = json.loads(Path(filename).read_text(encoding='utf-8'))
        run_id = manifest['run_id']
        if (manifest['version'] != 'p6-e1b-authorization-v1' or not re.fullmatch('[a-f0-9]{32}', run_id)
                or manifest['purpose'] != PURPOSE or manifest['authorized'] is not True
                or manifest['synthetic_writes_authorized'] is not True or not manifest['approved_by'].strip()
                or manifest['allow_database_or_schema_drop'] is not False):
            raise E1BBlocked('authorization_scope_invalid')
        current = now or datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(manifest['expires_at'])
        if expiry.tzinfo is None or expiry <= current:
            raise E1BBlocked('authorization_expired_or_undated')
        dsns = {role: values[name] for role, name in ENV_NAMES.items()}
        identities, users, endpoints = {}, {}, set()
        for role, dsn in dsns.items():
            parts = urlsplit(dsn)
            database = 'tasks' if role in ('task', 'task_creator') else 'business'
            if (parts.scheme not in ('postgres', 'postgresql') or not parts.hostname or not parts.username
                    or parts.path != f'/p6_e1b_{run_id}_{database}' or parts.fragment
                    or set(parse_qs(parts.query)) - {'sslmode', 'sslrootcert'}):
                raise E1BBlocked('database_is_not_the_authorized_unique_test_target')
            identity = database_identity(dsn)
            identities[role] = hashlib.sha256(identity.encode()).hexdigest()
            if identities[role] != manifest['identity_sha256'][role]:
                raise E1BBlocked('test_identity_pin_mismatch')
            users[role] = parts.username
            if hashlib.sha256(parts.username.encode()).hexdigest() != manifest['role_sha256'][role]:
                raise E1BBlocked('test_role_pin_mismatch')
            endpoints.add((parts.hostname.lower(), parts.port or 5432))
        if (len(endpoints) != 1 or len({users[r] for r in ('creator', 'reader', 'writer', 'task')}) != 4
                or users['creator'] != users['task_creator']
                or identities['creator'] != identities['reader'] or identities['writer'] != identities['reader']
                or identities['task_creator'] != identities['task'] or identities['task'] == identities['reader']):
            raise E1BBlocked('roles_or_databases_not_isolated')
        return AuthorizedEnvironment(run_id, content_sha256(manifest), manifest, dsns)
    except E1BBlocked:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        raise E1BBlocked('authorization_or_target_could_not_be_verified') from None
