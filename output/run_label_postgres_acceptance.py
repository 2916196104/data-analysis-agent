"""Run frontend acceptance in a temporary PostgreSQL database, never the business database."""
import os
import subprocess
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from data_analysis_agent.core.config import read_settings

settings = read_settings()
admin_url = os.environ.get('TEST_POSTGRES_ADMIN_URL') or settings['DATABASE_URL']
name = 'agent_test_' + uuid.uuid4().hex
with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as admin:
    admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
try:
    target = urlunsplit(urlsplit(admin_url)._replace(path='/' + name))
    result = subprocess.run(['node', 'tests/workbench.e2e.mjs'], cwd='frontend',
                            env={**os.environ, 'E2E_DATABASE_URL': target})
    raise SystemExit(result.returncode)
finally:
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as admin:
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    print('Isolated PostgreSQL acceptance database removed.')
