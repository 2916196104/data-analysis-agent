"""显式隔离管理库中的合成数据验收；绝不读取业务 DATABASE_URL。"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

import psycopg
from psycopg import sql
from test_review_workbench import WorkbenchFixture, IDENTITY
from data_analysis_agent.cli.migrate import migrate_sqlite
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import (
    SPACE_ID, migrate_embedding_store, sync_effective_opinions, search_effective_opinions,
)
from data_analysis_agent.reviews.label_store import save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus
from data_analysis_agent.reviews.store import import_amazon_jsonl, connect_review_database

# 编码前固定人工可检查的相关性标准，不用模型分数生成正确答案。
CASES = [
    ('service','negative','客服半天都没有回应我的问题。'),
    ('service','negative','售后联系了几次才有人答复。'),
    ('service','positive','客服很快就回复了问题，态度耐心。'),
    ('logistics','positive','第二天就准时送到了，配送很及时。'),
    ('logistics','negative','快递等了好多天，一直没有送到。'),
    ('packaging','negative','收到时外包装已经压坏。'),
    ('effectiveness','negative','开机很久房间温度才降下来。'),
    ('effectiveness','positive','打开空调一会儿就凉快了。'),
    ('effectiveness','negative','睡觉时机器的声音很大。'),
    ('quality','negative','才用了几天机器就出现故障。'),
    ('quality','positive','外壳做工扎实，没有毛刺。'),
    ('price','positive','这个价格买到很划算。'),
]
QUERIES = [
    dict(name='service_synonym', text='客服一直不回复。', aspect=None, sentiment=None,
         expected_aspect='service', expected_sentiment='negative'),
    dict(name='logistics_positive', text='配送速度很快。', aspect='logistics', sentiment='positive',
         expected_aspect='logistics', expected_sentiment='positive'),
    dict(name='logistics_negative', text='物流迟迟没有送到。', aspect='logistics', sentiment='negative',
         expected_aspect='logistics', expected_sentiment='negative'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-env', default='TEST_PGVECTOR_ADMIN_URL')
    args = parser.parse_args()
    admin_url = os.environ[args.admin_env]
    # 此工具只能连接显式隔离实例，业务默认 5432 / reviews 被拒绝。
    parts = urlsplit(admin_url)
    if parts.hostname not in ('127.0.0.1','localhost') or parts.port != 55439 or parts.path != '/postgres':
        raise ValueError('isolated_local_cluster_55439_required')
    name = 'rag_vector_acceptance_' + uuid.uuid4().hex[:12]
    db = urlunsplit(parts._replace(path='/' + name))
    output = ROOT / 'outputs/rag-pgvector'
    output.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    import unittest
    class Fixture(WorkbenchFixture, unittest.TestCase):
        pass
    fixture = Fixture()
    fixture.setUp()
    try:
        migrate_sqlite(fixture.db, db)
    finally:
        fixture.doCleanups()
    with psycopg.connect(db, autocommit=True) as c:
        c.execute('CREATE EXTENSION vector')
    migrate_embedding_store(db)
    now_ms = int(datetime(2023, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
    # 120 条短观点 + 1 条明确超限观点，另有原夹具 P1 的 3 条。
    synthetic = CASES * 10 + [('service','negative','客服没有及时回复。' * 200)]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'synthetic.jsonl'
        path.write_text('\n'.join(json.dumps(dict(parent_asin='P1',user_id=f'synthetic-{i}',
            text=text,rating=3,timestamp=now_ms), ensure_ascii=False)
            for i, (_, _, text) in enumerate(synthetic)), encoding='utf-8')
        import_amazon_jsonl(db, reviews_path=path)
    with closing(connect_review_database(db, readonly=True)) as c:
        inputs = [dict(r) for r in c.execute('SELECT id,text,content_sha256 FROM reviews WHERE id>6 ORDER BY id')]
    for review, (aspect, sentiment, text) in zip(inputs, synthetic):
        save_annotation(db, review, **IDENTITY, status='success', opinions=validate_opinions(
            json.dumps(dict(opinions=[dict(aspect=aspect,sentiment=sentiment,quote=text)])), text))
    options = dict(**IDENTITY, source='amazon_reviews_2023', dataset_version='amazon-reviews-2023',
                   product_ids=['P1'], date_from='2023-01-01', date_to='2023-01-06')
    encoder = LocalEmbeddingProcess(ROOT / '.cache/embedding-eval/venv/Scripts/python.exe',
                                    ROOT / '.cache/embedding-eval/hf/hub')
    first = sync_effective_opinions(db, options=options, encoder=encoder, limit=200, execute=True, encoding_authorized=True)
    second = sync_effective_opinions(db, options=options, encoder=encoder, limit=200, execute=True, encoding_authorized=True)
    # 第二轮仍会拒绝尚未索引的超限观点；已成功的 123 条全部复用。
    query_output = encoder.encode([q['text'] for q in QUERIES])
    results = []
    for query, vector in zip(QUERIES, query_output['vectors']):
        started = perf_counter()
        matches = search_effective_opinions(db, vector, options=options, embedding_space_id=SPACE_ID,
            aspect=query['aspect'], sentiment=query['sentiment'], top_k=5)
        elapsed = (perf_counter() - started) * 1000
        correct = sum(h.document.aspect == query['expected_aspect'] and
                      h.document.sentiment == query['expected_sentiment'] for h in matches)
        results.append(dict(name=query['name'], elapsed_ms=round(elapsed,2),
            precision_at_5=correct / 5, returned=len(matches),
            hits=[dict(review_id=h.document.review_id, aspect=h.document.aspect, sentiment=h.document.sentiment,
                       cosine_distance=h.cosine_distance, evidence_ref=h.document.evidence_ref) for h in matches]))
    vector = query_output['vectors'][0]
    no_match = search_effective_opinions(db, vector, options=options, embedding_space_id=SPACE_ID,
                                        aspect='service', sentiment='unknown')
    other_product = search_effective_opinions(db, vector, options=options | dict(product_ids=['P2']), embedding_space_id=SPACE_ID)
    other_profile = search_effective_opinions(db, vector, options=options | dict(model='other-model'), embedding_space_id=SPACE_ID)
    corpus = read_effective_corpus(db, **options)
    with closing(connect_review_database(db, readonly=True)) as c:
        original = {r['id']:r['text'] for r in c.execute('SELECT id,text FROM reviews')}
        cte = __import__('data_analysis_agent.reviews.scope',fromlist=['current_reviews_sql']).current_reviews_sql
        head, params = cte(c, tuple(IDENTITY.values()))
        count = c.execute(head + "SELECT COUNT(*) FROM effective_opinions o JOIN current c ON c.id=o.review_id "
                          "WHERE c.product_id='P1'", params).fetchone()[0]
    assert all(original[d.review_id][d.quote_start:d.quote_end] == d.quote for d in corpus.documents)
    assert count == len(corpus.documents) == 124
    assert first['metrics']['successful_count'] == 123 and first['metrics']['overlength_count'] == 1
    assert second['metrics']['reused_count'] == 123
    assert not no_match and not other_product and not other_profile
    with psycopg.connect(db, autocommit=True) as c:
        schema = c.execute("SELECT column_name,data_type,udt_name,is_nullable FROM information_schema.columns "
                           "WHERE table_name='review_opinion_embeddings' ORDER BY ordinal_position").fetchall()
        constraints = c.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
                                "WHERE conrelid='review_opinion_embeddings'::regclass ORDER BY conname").fetchall()
        indexes = c.execute("SELECT indexname,indexdef FROM pg_indexes WHERE tablename='review_opinion_embeddings'").fetchall()
        check = c.execute('SELECT count(*),min(vector_dims(embedding)),max(vector_dims(embedding)), '
                          'min(vector_norm(embedding)),max(vector_norm(embedding)) FROM review_opinion_embeddings').fetchone()
        size = c.execute("SELECT pg_total_relation_size('review_opinion_embeddings')").fetchone()[0]
    report = dict(status='completed_synthetic_only', database=name, observed_at=datetime.now(timezone.utc).isoformat(),
        embedding_space_id=SPACE_ID, first_sync=first, second_sync=second, queries=results,
        sql_effective_opinion_count=count, corpus_count=len(corpus.documents),
        original_slice_check=True, no_match_count=len(no_match), other_product_count=len(other_product),
        other_profile_count=len(other_profile), vector_checks=check, table_total_bytes=size,
        schema=schema, constraints=constraints, indexes=indexes)
    (output / 'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','database','vector_checks','table_total_bytes')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
