"""Frozen 053 operational budget. This is not source/semantic publication permission."""
from collections import Counter
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import os
from pathlib import Path

from dotenv import dotenv_values
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.scope import review_scope

ROOT = Path(__file__).resolve().parents[3]
PLAN_PATH = Path('outputs/rag-semantic-retrieval/mvp-053-sync-budget-v1/plan.json')
FROZEN_PLAN_SHA = 'cc637bb5bf638c6a2883b66c99b28203c8abc952fd9baef42c2140906376ee71'
FROZEN_PROFILE = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
FROZEN_055_SHA = 'ed56e2419a43a60975cee5dace107523d8aeb67160bfe5b1111e494d44b44cc1'
SPACE = 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
BUILDER = 'quote-utf8-v1'
EXPECTED = dict(S=200, N=200, U=0, I=0, P=200, selected_review_groups=114)
PG_OVERRIDES = ('PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE '
                'PGSSLMODE PGSSLCERT PGSSLKEY PGSSLROOTCERT PGSSLCRL PGSSLCRLDIR PGSSLSNI '
                'PGSSLMINPROTOCOLVERSION PGSSLMAXPROTOCOLVERSION PGGSSENCMODE PGCHANNELBINDING '
                'PGREQUIREAUTH PGTARGETSESSIONATTRS PGLOADBALANCEHOSTS PGOPTIONS PGCONNECT_TIMEOUT').split()


class SyncBudgetError(RuntimeError):
    """Fixed safe codes only, no original text/keys/credentials."""


def require(value, code):
    if not value:
        raise SyncBudgetError(code)


def selected_metadata(documents):
    return [{k: v for k, v in asdict(d).items() if k not in ('base_url', 'quote', 'embedding_input')}
            for d in documents]


@dataclass(frozen=True)
class Frozen053Budget:
    raw_plan: bytes = field(repr=False)
    root: Path = ROOT

    def __post_init__(self):
        require(sha256(self.raw_plan).hexdigest() == FROZEN_PLAN_SHA, 'budget_plan_digest_mismatch')
        p = self.plan()
        require(p['budget'] == EXPECTED and p['result_status'] == 'budget_ready_pending_separate_authorization', 'budget_not_ready')
        require(p['profile_sha256'] == FROZEN_PROFILE and (p['space'], p['builder']) == (SPACE, BUILDER), 'budget_profile_or_space_changed')
        scope = p['scope']
        require((scope['source'], scope['dataset_version'], scope['product_ids'], scope['date_from'], scope['date_to'], scope['as_of'])
                == ('reviews_csv', 'reviews-csv', ['100013588053'], '2021-09-11', '2022-08-26', None), 'budget_scope_changed')
        require(len(p['selected_documents']) == 200 and len(p['selected_review_groups']) == 114, 'budget_selection_count_changed')
        require(len(p['complete_valid_keys']) == 3591 and len(p['complete_review_states']) == 1699, 'budget_corpus_count_changed')
        require(not p['existing_selected_rows'] and not p['invalidation_rows'], 'budget_contains_existing_changes')
        require(p['055_baseline']['row_count'] == 200 and p['055_baseline']['rowset_sha256'] == FROZEN_055_SHA, 'budget_055_baseline_changed')

    @classmethod
    def load(cls, root=ROOT):
        return cls((root/PLAN_PATH).read_bytes(), root)

    def plan(self):
        return strict_json_loads(self.raw_plan.decode('utf-8'))

    def check_target(self, db):
        config = self.root/'.env'
        baseline = strict_json_loads((self.root/'outputs/rag-semantic-retrieval/mvp-first-20261107-v1/preflight.json').read_text(encoding='utf-8'))
        require(sha256(config.read_bytes()).hexdigest() == baseline['protected_sha256']['.env'], 'budget_config_changed')
        manifest = strict_json_loads((self.root/'tests/fixtures/rag_mvp_acceptance_v1.json').read_text(encoding='utf-8'))
        require(content_sha256(manifest) == self.plan()['manifest_sha256'] == baseline['manifest_sha256'], 'budget_manifest_changed')
        for name, digest in self.plan()['owner_implementation_sha256'].items():
            # The sync implementation is intentionally revised here; source/selection owners stay frozen.
            if name != 'src/data_analysis_agent/reviews/embedding_store.py':
                require(sha256((self.root/name).read_bytes()).hexdigest() == digest, 'budget_source_owner_changed')
        values = read_settings(config)
        require(db == values.get('DATABASE_URL') == dotenv_values(config, interpolate=False).get('DATABASE_URL'), 'budget_database_target_changed')
        require(not any(os.environ.get(k) for k in PG_OVERRIDES), 'budget_pg_environment_changed')

    def check_options(self, options, limit):
        p = self.plan()
        require(limit == 200, 'budget_limit_changed')
        require(options.get('timezone', 'UTC') == 'UTC', 'budget_timezone_changed')
        clean = {k: v for k, v in options.items() if k not in ('base_url', 'timezone')}
        expected = {k: v for k, v in p['scope'].items() if k != 'timezone'}
        # The legacy CLI omits optional as_of; its meaning remains null.
        clean.setdefault('as_of', None)
        require(clean == expected, 'budget_request_scope_changed')
        require(content_sha256([options['model'], options['base_url'], options['prompt_version']]) == FROZEN_PROFILE, 'budget_request_profile_changed')

    def check_corpus(self, corpus, selected):
        p = self.plan()
        require(content_sha256(asdict(corpus)) == p['source_corpus_sha256'], 'budget_source_corpus_changed')
        by_key = {d.stable_versioned_opinion_key: d for d in corpus.documents}
        require(all(d == by_key.get(d.stable_versioned_opinion_key) for d in selected), 'budget_selected_document_not_in_corpus')
        require(selected_metadata(selected) == p['selected_documents'], 'budget_selected_keys_or_identity_changed')
        require([d.stable_versioned_opinion_key for d in corpus.documents] == p['complete_valid_keys'], 'budget_complete_keys_changed')
        require([asdict(r) for r in corpus.reviews] == p['complete_review_states'], 'budget_complete_snapshots_changed')
        states = {r.review_id: r for r in corpus.reviews}
        counts = Counter(d.review_id for d in selected)
        complete_counts = Counter(d.review_id for d in corpus.documents)
        require(len(counts) == 114 and all(n == complete_counts[rid] == states[rid].opinion_count
                for rid, n in counts.items()), 'budget_incomplete_review_group')
        groups = [dict(review_id=rid, opinion_count=n, effective_snapshot_sha256=states[rid].effective_snapshot_sha256)
                  for rid, n in counts.items()]
        require(groups == p['selected_review_groups'] and len(selected) == 200, 'budget_complete_groups_changed')

    def check_database(self, connection, corpus, selected, where, parameters):
        keys = [d.stable_versioned_opinion_key for d in selected]
        rows = connection.execute('''SELECT id,product_id,source,dataset_version,review_id,stable_versioned_opinion_key
            FROM review_opinion_embeddings WHERE embedding_space_id=? AND text_builder_version=?
            AND stable_versioned_opinion_key=ANY(?::text[]) ORDER BY id''', (SPACE, BUILDER, keys)).fetchall()
        require(not any(r['product_id'] != '100013588053' for r in rows), 'budget_cross_product_key_conflict')
        require(not rows, 'budget_U_or_N_changed')
        valid_keys = [d.stable_versioned_opinion_key for d in corpus.documents]
        invalidated = connection.execute('SELECT id FROM review_opinion_embeddings c WHERE '+where+'''
            AND label_profile_key=? AND embedding_space_id=? AND invalidated_at IS NULL
            AND NOT (stable_versioned_opinion_key=ANY(?::text[])) ORDER BY id''',
            (*parameters, corpus.label_profile_key, SPACE, valid_keys)).fetchall()
        require(not invalidated, 'budget_I_or_change_rows_changed')
        actual = dict(S=len(keys), N=len(keys)-len(rows), U=len(rows), I=len(invalidated), P=len(keys)-len(rows))
        require(actual == {k:self.plan()['budget'][k] for k in actual} and actual['P'] <= 200, 'budget_actual_counts_changed')
        self.check_055(connection)
        return actual

    def check_055(self, connection):
        baseline = self.plan()['055_baseline']
        where, parameters, _ = review_scope(**baseline['scope'], alias='c')
        fingerprint_sql = '''SELECT count(*) AS row_count,
        encode(sha256(convert_to(coalesce(string_agg(row_sha256,'' ORDER BY id),''),'UTF8')),'hex') AS rowset_sha256
        FROM (SELECT c.id,encode(sha256(convert_to(to_jsonb(c)::text,'UTF8')),'hex') AS row_sha256
        FROM review_opinion_embeddings c WHERE '''+where+'''
        AND label_profile_key=? AND embedding_space_id=? AND text_builder_version=?) fingerprints'''
        require(sha256(fingerprint_sql.encode()).hexdigest() == baseline['query_sha256'], 'budget_055_query_changed')
        row = connection.execute(fingerprint_sql, (*parameters, FROZEN_PROFILE, SPACE, BUILDER)).fetchone()
        require(row['row_count'] == 200 and row['rowset_sha256'] == FROZEN_055_SHA, 'budget_055_rows_changed')
