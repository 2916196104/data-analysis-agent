"""显式的小范围 pgvector 同步；不被 Agent、API 或导入流程自动调用。"""

from contextlib import closing
from dataclasses import dataclass
import json
import math
from itertools import groupby
from pathlib import Path
from time import perf_counter

from data_analysis_agent.core.database import DatabaseError, PostgresConnection, is_postgres
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.analytics import _snapshot
from data_analysis_agent.reviews.embedding import EmbeddingSpace, MODEL_NAME, MODEL_REVISION
from data_analysis_agent.reviews.rag_corpus import RagOpinionDocument, TEXT_BUILDER_VERSION, read_effective_corpus
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.store import connect_review_database

SPACE = EmbeddingSpace(MODEL_NAME, MODEL_REVISION, 1024)
SPACE_ID = SPACE.space_id
assert SPACE_ID == 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
MIGRATION_ROOT = Path(__file__).resolve().parents[3] / 'migrations'
SOURCE_LOCK = ('products, import_runs, reviews, review_annotations, review_opinions, '
               'review_human_reviews, review_human_opinions')
RANGE_FIELDS = ('source', 'dataset_version', 'product_ids', 'date_from', 'date_to', 'as_of')


class EmbeddingStoreError(RuntimeError):
    """只含安全错误码，不回显 SQL 参数、模型输入或底层异常。"""


class SyncRetryRequired(EmbeddingStoreError):
    pass


def validate_vector(vector):
    if (not isinstance(vector, (list, tuple)) or len(vector) != SPACE.dimension
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
        raise EmbeddingStoreError('invalid_vector_shape_or_value')
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or abs(norm - 1) > 1e-5:
        raise EmbeddingStoreError('invalid_vector_norm')
    return list(vector)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _connect(db, *, readonly=False):
    if not is_postgres(db):
        raise EmbeddingStoreError('postgres_required')
    return connect_review_database(db, readonly=readonly)


def migrate_embedding_store(db, *, rollback=False):
    """仅供已获 DDL 授权的显式命令调用；既不安装扩展，也不自动迁移。"""
    script = MIGRATION_ROOT / ('003_review_opinion_embeddings.down.sql' if rollback
                               else '003_review_opinion_embeddings.up.sql')
    try:
        with closing(_connect(db)) as c, c:
            c.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
            c.execute("SET LOCAL lock_timeout='3s'")
            c.execute("SET LOCAL statement_timeout='15s'")
            c.raw.execute(script.read_text(encoding='utf-8'), prepare=False)
    except DatabaseError:
        raise EmbeddingStoreError('migration_failed') from None
    except Exception:
        raise EmbeddingStoreError('migration_failed') from None


def _scope(options):
    # 本阶段只能单商品；让 Step 2A 继续负责所有身份、范围及时间验证。
    products = options.get('product_ids')
    if not isinstance(products, (list, tuple)) or len(products) != 1 or not products[0]:
        raise EmbeddingStoreError('one_explicit_product_required')
    return dict(options)


def _range(corpus, alias='c'):
    where, params, _ = review_scope(**{k: corpus.scope[k] for k in RANGE_FIELDS}, alias=alias)
    return where, params


def _run(db, corpus, metrics):
    # scope_json 不保存 base_url 或原文；profile 哈希绑定完整标注三元组。
    with closing(_connect(db)) as c, c:
        row = c.execute('''INSERT INTO rag_embedding_sync_runs
            (source,dataset_version,product_id,label_profile_key,embedding_space_id,scope_json,status,metrics)
            VALUES (?,?,?,?,?,?::jsonb,'running',?::jsonb) RETURNING id''',
            (corpus.scope['source'], corpus.scope['dataset_version'], corpus.scope['product_ids'][0],
             corpus.label_profile_key, SPACE_ID,
             _json({k: corpus.scope[k] for k in RANGE_FIELDS}), _json(metrics))).fetchone()
        return row[0]


def _finish(db, run_id, status, metrics, code=None):
    with closing(_connect(db)) as c, c:
        c.execute('''UPDATE rag_embedding_sync_runs SET status=?,metrics=?::jsonb,error_code=?,
            finished_at=clock_timestamp() WHERE id=?''', (status, _json(metrics), code, run_id))


def _select(documents, limit):
    # 上限按完整评论分组，不将一条评论的多观点拆成半个已索引集合。
    selected = []
    for _, rows in groupby(documents, key=lambda d: d.review_id):
        group = list(rows)
        if len(selected) + len(group) > limit:
            continue
        selected.extend(group)
    return selected


def _cached(db, documents):
    keys = [d.stable_versioned_opinion_key for d in documents]
    with closing(_connect(db, readonly=True)) as c:
        rows = c.execute('''SELECT stable_versioned_opinion_key,embedding::text FROM review_opinion_embeddings
            WHERE embedding_space_id=? AND text_builder_version=?
            AND stable_versioned_opinion_key=ANY(?::text[])''', (SPACE_ID, TEXT_BUILDER_VERSION, keys)).fetchall()
    return {r[0]: validate_vector(json.loads(r[1])) for r in rows}


def _publish(db, options, before, selected, vectors, run_id, metrics, started):
    publish_started = perf_counter()
    with closing(_connect(db)) as c, c:
        # 锁定后才在 READ COMMITTED 读取最新快照；不沿用编码前的 MVCC 快照。
        c.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        c.execute("SET LOCAL lock_timeout='3s'")
        c.execute("SET LOCAL statement_timeout='15s'")
        c.execute('LOCK TABLE review_opinion_embeddings IN SHARE ROW EXCLUSIVE MODE')
        # 同时阻止 SELECT FOR UPDATE；NOWAIT 避免与先持源锁的删除/标注事务形成等待环。
        c.execute('LOCK TABLE ' + SOURCE_LOCK + ' IN EXCLUSIVE MODE NOWAIT')
        try:
            current = read_effective_corpus(c, **options)
        except ReviewDataError:
            raise SyncRetryRequired('effective_corpus_unavailable') from None
        if current != before:
            raise SyncRetryRequired('effective_corpus_changed')
        where, params = _range(current)
        valid_keys = [d.stable_versioned_opinion_key for d in current.documents]
        metrics['invalidated_count'] = c.execute('''UPDATE review_opinion_embeddings c
            SET invalidated_at=clock_timestamp() WHERE ''' + where + '''
            AND label_profile_key=? AND embedding_space_id=? AND invalidated_at IS NULL
            AND NOT (stable_versioned_opinion_key=ANY(?::text[])) RETURNING id''',
            (*params, current.label_profile_key, SPACE_ID, valid_keys)).rowcount
        snapshots = {r.review_id: r.effective_snapshot_sha256 for r in current.reviews}
        fields = ('source','dataset_version','product_id','review_id','annotation_id','label_profile_key',
                  'opinion_source','model_opinion_id','human_opinion_id','human_revision_id',
                  'stable_versioned_opinion_key','aspect','sentiment','quote_start','quote_end',
                  'review_content_sha256','embedding_input_sha256','published_at','text_builder_version')
        for doc in selected:
            key = doc.stable_versioned_opinion_key
            if key not in vectors:
                continue
            c.execute('INSERT INTO review_opinion_embeddings (' + ','.join(fields) + ''',
                effective_snapshot_sha256,embedding_space_id,embedding,sync_run_id)
                VALUES (''' + ','.join('?' for _ in fields) + ''',?,?,?::vector(1024),?)
                ON CONFLICT (stable_versioned_opinion_key,embedding_space_id,text_builder_version)
                DO UPDATE SET embedding=EXCLUDED.embedding,effective_snapshot_sha256=EXCLUDED.effective_snapshot_sha256,
                sync_run_id=EXCLUDED.sync_run_id,synced_at=clock_timestamp(),invalidated_at=NULL''',
                (*[getattr(doc, f) for f in fields], snapshots[doc.review_id], SPACE_ID,
                 _json(validate_vector(vectors[key])), run_id))
        metrics['published_count'] = len(vectors)
        # 与检索同一套快照匹配，不能将未失效但已陈旧的行计为当前有效。
        eligible = _eligible(current)
        metrics['indexed_current_after'] = c.execute('''SELECT COUNT(*) FROM review_opinion_embeddings e
            JOIN jsonb_to_recordset(?::jsonb) v(key text,snapshot text) ON
                e.stable_versioned_opinion_key=v.key AND e.effective_snapshot_sha256=v.snapshot
            WHERE e.embedding_space_id=? AND e.invalidated_at IS NULL''', (_json(eligible), SPACE_ID)).fetchone()[0]
        metrics['not_indexed_count'] = len(current.documents) - metrics['indexed_current_after']
        status = 'partial' if metrics['not_indexed_count'] else 'succeeded'
        metrics['publish_ms'] = round((perf_counter() - publish_started) * 1000, 2)
        metrics['elapsed_ms'] = round((perf_counter() - started) * 1000, 2)
        c.execute('''UPDATE rag_embedding_sync_runs SET status=?,metrics=?::jsonb,finished_at=clock_timestamp()
                     WHERE id=?''', (status, _json(metrics), run_id))
    return status


def sync_effective_opinions(db, *, options, encoder, limit=200):
    """encoder.encode(texts) 返回同序 vectors（超限为 None）、space_id、metrics。

    一次最多 300 观点；编码器只在关闭所有数据库连接后执行。来源变化整批重试。
    """
    options = _scope(options)
    if type(limit) is not int or not 1 <= limit <= 300:
        raise EmbeddingStoreError('limit_must_be_1_to_300')
    started = perf_counter()
    before = read_effective_corpus(db, **options)
    selected = _select(before.documents, limit)
    metrics = dict(scope_opinion_count=len(before.documents), selected_opinion_count=len(selected),
                   attempted_count=0, successful_count=0, reused_count=0, overlength_count=0,
                   invalid_input_count=0, failed_count=0, published_count=0, invalidated_count=0)
    run_id = _run(db, before, metrics)
    try:
        vectors = _cached(db, selected)
        metrics['reused_count'] = len(vectors)
        pending = [d for d in selected if d.stable_versioned_opinion_key not in vectors]
        metrics['attempted_count'] = len(pending)
        if pending:
            output = encoder.encode([d.embedding_input for d in pending])
            if output['space_id'] != SPACE_ID or len(output['vectors']) != len(pending):
                raise EmbeddingStoreError('encoder_contract_mismatch')
            for doc, vector in zip(pending, output['vectors']):
                if vector is None:
                    metrics['overlength_count'] += 1
                else:
                    vectors[doc.stable_versioned_opinion_key] = validate_vector(vector)
                    metrics['successful_count'] += 1
            metrics['encoder'] = output.get('metrics', {})
        status = _publish(db, options, before, selected, vectors, run_id, metrics, started)
        return dict(run_id=run_id, status=status, metrics=metrics)
    except Exception as exc:
        # 底层 DB/模型异常可能携带原文，永远不向日志和调用方传播其消息。
        retry = isinstance(exc, (SyncRetryRequired, DatabaseError))
        code = 'effective_corpus_changed' if isinstance(exc, SyncRetryRequired) else (
            'database_retry_required' if isinstance(exc, DatabaseError) else 'encoding_or_publication_failed')
        metrics['failed_count'] = metrics['attempted_count'] if not retry else 0
        metrics['successful_count'] = 0
        metrics['published_count'] = 0
        metrics['elapsed_ms'] = round((perf_counter() - started) * 1000, 2)
        _finish(db, run_id, 'retry_required' if retry else 'failed', metrics, code)
        error = SyncRetryRequired if retry else EmbeddingStoreError
        raise error(code + f':run={run_id}') from None


def _eligible(corpus):
    snapshots = {r.review_id: r.effective_snapshot_sha256 for r in corpus.reviews}
    return [dict(key=d.stable_versioned_opinion_key, snapshot=snapshots[d.review_id]) for d in corpus.documents]


@dataclass(frozen=True)
class OpinionMatch:
    document: RagOpinionDocument
    cosine_distance: float


def search_effective_opinions(db, vector, *, options, embedding_space_id, top_k=5,
                              aspect=None, sentiment=None):
    return _search_effective_opinions(db,vector,options=options,embedding_space_id=embedding_space_id,
                                    top_k=top_k,aspect=aspect,sentiment=sentiment)


def _search_effective_opinions(db, vector, *, options, embedding_space_id, top_k=5,
                               aspect=None, sentiment=None, corpus=None):
    """同一个只读快照中回连有效语料，再由 PostgreSQL <=> 做精确排序。"""
    _scope(options)
    vector = validate_vector(vector)
    if embedding_space_id != SPACE_ID:
        raise EmbeddingStoreError('embedding_space_mismatch')
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise EmbeddingStoreError('invalid_top_k')
    if aspect is not None and aspect not in ASPECTS or sentiment is not None and sentiment not in SENTIMENTS:
        raise EmbeddingStoreError('invalid_filter')
    try:
        with _snapshot(db) as c:
            if not isinstance(c, PostgresConnection):
                raise EmbeddingStoreError('postgres_required')
            corpus = read_effective_corpus(c, **options) if corpus is None else corpus
            eligible = _eligible(corpus)
            documents = {d.stable_versioned_opinion_key: d for d in corpus.documents}
            where, filters = _range(corpus, alias='r')
            rows = c.execute('''SELECT r.*,r.embedding <=> ?::vector(1024) AS distance
                FROM review_opinion_embeddings r JOIN jsonb_to_recordset(?::jsonb) v(key text,snapshot text)
                ON r.stable_versioned_opinion_key=v.key AND r.effective_snapshot_sha256=v.snapshot
                WHERE ''' + where + ''' AND r.embedding_space_id=? AND r.text_builder_version=? AND r.label_profile_key=?
                AND r.invalidated_at IS NULL AND (?::text IS NULL OR r.aspect=?)
                AND (?::text IS NULL OR r.sentiment=?) ORDER BY distance,r.id LIMIT ?''',
                (_json(vector), _json(eligible), *filters, SPACE_ID, TEXT_BUILDER_VERSION, corpus.label_profile_key,
                 aspect, aspect, sentiment, sentiment, top_k)).fetchall()
            matches = []
            for row in rows:
                doc = documents[row['stable_versioned_opinion_key']]
                # 显式校验全部持久化来源元数据，手工篡改派生行时整次查询失败。
                fields = ('source','dataset_version','product_id','review_id','annotation_id','label_profile_key',
                          'opinion_source','model_opinion_id','human_opinion_id','human_revision_id','aspect',
                          'sentiment','quote_start','quote_end','review_content_sha256','embedding_input_sha256',
                          'text_builder_version','published_at')
                if any(row[f] != getattr(doc, f) for f in fields) or not math.isfinite(row['distance']):
                    raise EmbeddingStoreError('stored_provenance_mismatch')
                matches.append(OpinionMatch(doc, row['distance']))
            return tuple(matches)
    except DatabaseError:
        raise EmbeddingStoreError('search_database_error') from None
