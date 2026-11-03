"""Step 4A：仅限冻结业务范围的只读清点、检索和离线人工评分。"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import PostgresConnection, database_argument
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.analytics import _snapshot
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import SPACE_ID, search_effective_opinions, validate_vector
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS
from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION, read_effective_corpus
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.retrieval_evaluation import (
    APPROVED_SCOPE, PROFILE_KEY, EvaluationError, label_template, require, run_binding,
    score_run, validate_manifest, validate_options,
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def private_directory(path):
    path = Path(path).resolve()
    require(path.is_relative_to(ROOT / 'outputs/rag-retrieval-quality'), 'local_private_output_required')
    # 真实原文只能写到实际被 Git 忽略且没有已跟踪文件的目录。
    relative = path.relative_to(ROOT).as_posix()
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', relative + '/review.json'],
                             cwd=ROOT, capture_output=True)
    tracked = subprocess.run(['git', 'ls-files', '--', relative], cwd=ROOT, capture_output=True)
    require(ignored.returncode == 0 and tracked.returncode == 0 and not tracked.stdout, 'private_output_not_ignored')
    path.mkdir(parents=True, exist_ok=True)
    return path


def business_options(settings):
    options = dict(APPROVED_SCOPE, base_url=settings.get('LLM_BASE_URL', ''))
    validate_options(options)
    return options


PROVENANCE = ('source', 'dataset_version', 'product_id', 'review_id', 'annotation_id',
    'label_profile_key', 'opinion_source', 'model_opinion_id', 'human_opinion_id', 'human_revision_id',
    'aspect', 'sentiment', 'quote_start', 'quote_end', 'review_content_sha256',
    'embedding_input_sha256', 'text_builder_version', 'published_at')


def inventory(connection, options):
    validate_options(options)
    require(isinstance(connection, PostgresConnection), 'postgres_required')
    require(connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on', 'readonly_required')
    require(connection.execute('SHOW transaction_isolation').fetchone()[0] == 'repeatable read', 'snapshot_required')
    corpus = read_effective_corpus(connection, **options)
    require(corpus.label_profile_key == PROFILE_KEY, 'current_label_identity_mismatch')
    # 只读取已授权商品/日期/标注身份的派生行；这不是另一套向量检索 SQL。
    where, parameters, _ = review_scope(**{k: options[k] for k in
        ('source', 'dataset_version', 'product_ids', 'date_from', 'date_to')})
    rows = [dict(r) for r in connection.execute('''SELECT r.*, embedding::text vector_text,
        vector_dims(embedding) dimensions, vector_norm(embedding) norm
        FROM review_opinion_embeddings r WHERE ''' + where + ' AND label_profile_key=? ORDER BY id',
        (*parameters, PROFILE_KEY))]
    docs = {d.stable_versioned_opinion_key: d for d in corpus.documents}
    snapshots = {r.review_id: r.effective_snapshot_sha256 for r in corpus.reviews}
    valid, invalid = [], Counter()
    for r in rows:
        doc = docs.get(r['stable_versioned_opinion_key'])
        reason = ('explicitly_invalidated' if r['invalidated_at'] is not None else
            'space_or_builder' if r['embedding_space_id'] != SPACE_ID or r['text_builder_version'] != TEXT_BUILDER_VERSION else
            'stale_key_or_snapshot' if doc is None or r['effective_snapshot_sha256'] != snapshots[doc.review_id] else
            'metadata_mismatch' if any(r[f] != getattr(doc, f) for f in PROVENANCE) else None)
        if reason:
            invalid[reason] += 1
        else:
            require(r['dimensions'] == 1024 and abs(r['norm'] - 1) < 0.0001, 'stored_vector_invalid')
            validate_vector(json.loads(r['vector_text']))
            valid.append(doc)
    # 绑定源状态、完整有效观点版本、缓存元数据及向量；公开输出只有摘要。
    fingerprint = content_sha256(dict(
        reviews=[vars(r) for r in corpus.reviews], keys=sorted(docs),
        vectors=[{k: str(v) if k in ('created_at', 'synced_at', 'invalidated_at') and v is not None else v
                  for k, v in r.items() if k != 'embedding'} for r in rows]))
    by_aspect = Counter(d.aspect for d in valid)
    by_sentiment = Counter(d.sentiment for d in valid)
    distribution = {a: {s: sum(d.aspect == a and d.sentiment == s for d in valid) for s in SENTIMENTS}
                    for a in ASPECTS}
    result = dict(scope=APPROVED_SCOPE, label_profile_key=PROFILE_KEY, embedding_space_id=SPACE_ID,
        text_builder_version=TEXT_BUILDER_VERSION, transaction_read_only=True, isolation='repeatable read',
        stored_count=len(rows), valid_count=len(valid), invalid_count=sum(invalid.values()),
        invalid_reasons=dict(invalid), effective_opinion_count=len(docs),
        not_indexed_count=len(docs) - len(valid), indexed_review_groups=len({d.review_id for d in valid}),
        complete_review_groups=all({d.stable_versioned_opinion_key for d in corpus.documents if d.review_id == rid}
                                  == {d.stable_versioned_opinion_key for d in valid if d.review_id == rid}
                                  for rid in {d.review_id for d in valid}),
        by_aspect={a: by_aspect[a] for a in ASPECTS}, by_sentiment={s: by_sentiment[s] for s in SENTIMENTS},
        aspect_sentiment=distribution, opinion_sources=dict(Counter(d.opinion_source for d in valid)),
        dimensions=sorted({r['dimensions'] for r in rows}),
        norm_range=[min((r['norm'] for r in rows), default=None), max((r['norm'] for r in rows), default=None)],
        snapshot_sha256=fingerprint)
    return result, corpus, valid


def collect_run(db, options, manifest, encoder):
    validate_options(options)
    digest = validate_manifest(manifest)
    with _snapshot(db) as connection:
        before, _, _ = inventory(connection, options)
    require(before['snapshot_sha256'] == manifest['inventory_snapshot_sha256'], 'frozen_inventory_changed')
    require(before['stored_count'] == before['valid_count'] == 200 and before['invalid_count'] == 0,
            'approved_200_index_changed')
    # 编码期间不持有业务事务；只编码冻结问题，不重新编码任何真实观点。
    output = encoder.encode([q['text'] for q in manifest['queries']])
    require(output['space_id'] == SPACE_ID and len(output['vectors']) == len(manifest['queries']),
            'query_encoder_contract_mismatch')
    vectors = [validate_vector(v) for v in output['vectors']]
    searches = []
    with _snapshot(db) as connection:
        current, corpus, _ = inventory(connection, options)
        require(current == before, 'corpus_changed_during_encoding')
        snapshots = {r.review_id: r.effective_snapshot_sha256 for r in corpus.reviews}
        for query, vector in zip(manifest['queries'], vectors):
            for mode in ('unfiltered', 'filtered'):
                filters = query['filters'] if mode == 'filtered' else {}
                started = perf_counter()
                hits = search_effective_opinions(connection, vector, options=options,
                    embedding_space_id=SPACE_ID, top_k=5, **filters)
                elapsed = round((perf_counter() - started) * 1000, 2)
                # 在同一只读快照复跑并逐键、逐距离比较；不删改不利结果。
                repeat = search_effective_opinions(connection, vector, options=options,
                    embedding_space_id=SPACE_ID, top_k=5, **filters)
                require(hits == repeat, 'nonreproducible_ranking')
                search = dict(query_id=query['id'], mode=mode, filters=filters, elapsed_ms=elapsed,
                              repeat_identical=True, hits=[])
                for rank, hit in enumerate(hits, 1):
                    d = hit.document
                    require(d.label_profile_key == PROFILE_KEY and all(getattr(d, k) == options[k]
                        for k in ('source', 'dataset_version')) and d.product_id == options['product_ids'][0],
                        'unauthorized_result')
                    require(sha256(d.quote.encode('utf-8')).hexdigest() == d.embedding_input_sha256,
                            'evidence_hash_mismatch')
                    search['hits'].append(dict(rank=rank, key=d.stable_versioned_opinion_key,
                        review_id=d.review_id, annotation_id=d.annotation_id, opinion_source=d.opinion_source,
                        model_opinion_id=d.model_opinion_id, human_opinion_id=d.human_opinion_id,
                        human_revision_id=d.human_revision_id, aspect=d.aspect, sentiment=d.sentiment,
                        quote=d.quote, quote_start=d.quote_start, quote_end=d.quote_end,
                        embedding_input_sha256=d.embedding_input_sha256,
                        effective_snapshot_sha256=snapshots[d.review_id], evidence_ref=d.evidence_ref,
                        cosine_distance=hit.cosine_distance, evidence_valid=True))
                searches.append(search)
        # 评审只展示这 200 条已索引观点及所在的 105 条原文；不输出其他观点原文。
        review_ids = sorted({h['review_id'] for s in searches for h in s['hits']})
        originals = {r['id']: r['text'] for r in connection.execute(
            'SELECT id,text FROM reviews WHERE source=? AND dataset_version=? AND product_id=? AND id=ANY(?::int[])',
            (options['source'], options['dataset_version'], options['product_ids'][0], review_ids))}
        for s in searches:
            for h in s['hits']:
                text = originals[h['review_id']]
                require(text[h['quote_start']:h['quote_end']] == h['quote'], 'original_span_mismatch')
                h['review_text'] = text
    with _snapshot(db) as connection:
        after, _, _ = inventory(connection, options)
    require(before == after, 'corpus_changed_after_search')
    run = dict(manifest=manifest, manifest_sha256=digest,
        scope_sha256=content_sha256([APPROVED_SCOPE, PROFILE_KEY, SPACE_ID]),
        inventory=before, searches=searches, encoder_metrics=output.get('metrics', {}),
        query_vectors_sha256=content_sha256(vectors), authorized_snapshot_unchanged=True)
    run['run_id'] = run_binding(run)
    return run


def safe_summary(run):
    # 白名单公开结果，不输出 quote、原文、人工备注、地址、向量及凭据。
    return dict(run_id=run['run_id'], manifest_sha256=run['manifest_sha256'], inventory=run['inventory'],
        encoder_metrics=run['encoder_metrics'], query_vectors_sha256=run['query_vectors_sha256'],
        authorized_snapshot_unchanged=run['authorized_snapshot_unchanged'],
        searches=[dict(query_id=s['query_id'], mode=s['mode'], filters=s['filters'],
            returned=len(s['hits']), elapsed_ms=s['elapsed_ms'], repeat_identical=s['repeat_identical'],
            hits=[{k: h[k] for k in ('rank', 'key', 'review_id', 'annotation_id', 'aspect', 'sentiment',
                                      'cosine_distance', 'evidence_valid')} for h in s['hits']]) for s in run['searches']])


def write_review(directory, run):
    labels = label_template(run)
    write_json(directory / 'review.json', run)
    write_json(directory / 'labels.template.json', labels)
    payload = json.dumps(dict(run=run, labels=labels), ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    template = (ROOT / 'scripts/rag_retrieval_review.html').read_text(encoding='utf-8')
    (directory / 'review.html').write_text(template.replace('__REVIEW_PAYLOAD__', payload), encoding='utf-8')
    write_json(directory / 'summary.json', safe_summary(run))
    write_json(directory / 'metrics.pending.json', score_run(run, labels))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/rag-retrieval-quality/baseline-v1')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    run = sub.add_parser('run')
    run.add_argument('--manifest', default='evaluation/rag-retrieval-v1.json')
    run.add_argument('--embedding-python', default='.cache/embedding-eval/venv/Scripts/python.exe')
    run.add_argument('--cache-dir', default='.cache/embedding-eval/hf/hub')
    score = sub.add_parser('score')
    score.add_argument('--labels', required=True, help='本地用户实际评分并确认的 JSON')
    args = parser.parse_args(argv)
    try:
        directory = private_directory(args.output)
        if args.command == 'score':
            review = json.loads((directory / 'review.json').read_text(encoding='utf-8'))
            labels = json.loads(Path(args.labels).read_text(encoding='utf-8'))
            metrics = score_run(review, labels)
            write_json(directory / 'metrics.json', metrics)
            print(json.dumps(dict(status=metrics['status'], query_modes=len(metrics['query_details']))))
            return 0
        settings = read_settings()
        options = business_options(settings)
        db = database_argument('env:DATABASE_URL')
        if args.command == 'inventory':
            with _snapshot(db) as connection:
                result, _, _ = inventory(connection, options)
            write_json(directory / 'inventory.json', result)
            print(json.dumps(result, ensure_ascii=False))
        else:
            require(not (directory / 'review.json').exists(), 'existing_run_use_new_output_directory')
            manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
            result = collect_run(db, options, manifest, LocalEmbeddingProcess(args.embedding_python, args.cache_dir))
            write_review(directory, result)
            print(json.dumps(dict(status='automated_retrieval_complete_awaiting_human',
                queries=len(manifest['queries']), query_modes=len(result['searches']),
                returned=sum(len(s['hits']) for s in result['searches']),
                snapshot_unchanged=result['authorized_snapshot_unchanged'])))
        return 0
    except Exception:
        print('{"error":"retrieval_evaluation_failed; no private details logged"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
