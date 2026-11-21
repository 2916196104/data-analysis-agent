"""Freeze and measure the current 055 RAG baseline using local read-only data."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from urllib.parse import urlsplit

from evaluate_retrieval_quality import ROOT, business_options, inventory, private_directory, write_json
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_retrieval import execute_retrieval, permitted_references
from data_analysis_agent.agents.review_semantics import QuestionScopeError
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require, score_run
from data_analysis_agent.reviews.semantic_retrieval import (
    PostgresSemanticRepository, SemanticRetrievalError, SemanticRetrievalService, _scope, load_review_contexts,
)


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def historical_cases():
    archive = ROOT / 'outputs/rag-retrieval-quality/private-archive/step4a-20261104-human6'
    run = load(archive / 'review.json')
    labels = load(archive / 'labels.human.json')
    metrics = score_run(run, labels)
    require(labels['human_confirmed'], 'historical_human_confirmation_missing')
    require(metrics == load(archive / 'metrics.json'), 'historical_metrics_mismatch')
    require(run['manifest'] == load(ROOT / 'evaluation/rag-retrieval-v1.json'), 'historical_manifest_mismatch')
    grades = defaultdict(dict)
    by_rank = {(j['query_id'], j['mode'], j['rank']): j['grade'] for j in labels['judgments']}
    for search in run['searches']:
        query_id, mode = search['query_id'], search['mode']
        for hit in search['hits']:
            grade = by_rank[query_id, mode, hit['rank']]
            prior = grades[query_id].get(hit['key'])
            require(prior is None or prior['grade'] == grade, 'historical_grade_conflict')
            grades[query_id][hit['key']] = dict(hit, grade=grade)
    statuses = {q['id']: q['evidence_status'] for q in labels['queries']}
    cases = [dict(id=q['id'], split='historical', text=q['text'], criterion=q['criterion'],
                  expected_route='semantic_evidence', reference_status=statuses[q['id']])
             for q in run['manifest']['queries']]
    return run, metrics, cases, grades


def evaluate_case(case, options, catalog, repository, encoder):
    question = case['text']
    scope = dict(**options, as_of=None, baseline_from=None, baseline_to=None)
    row = dict(case, route=None, route_ok=False, retrieval=None)
    diagnostics = {}
    started = perf_counter()
    try:
        bound, plan, _ = resolve_review_request(question, scope, catalog,
            settings=SimpleNamespace(semantic_parser_enabled=False), diagnostics=diagnostics)
    except QuestionScopeError:
        row.update(route='rules_could_not_route', route_ms=(perf_counter()-started)*1000)
        return row
    row.update(analysis_type=plan.analysis_type, tools=plan.required_tools,
               route_ms=(perf_counter()-started)*1000, support_status=plan.task_contract.support_status)
    route = 'unsupported' if plan.unsupported_reason else (
        'sql_statistics' if plan.task_contract.operation == 'sql_statistics' else plan.analysis_type)
    row.update(route=route, route_ok=route == case['expected_route'])
    if route != 'semantic_evidence':
        return row
    query_scope = {key: bound[key] for key in options}
    query_scope['as_of'] = bound.get('as_of')
    require(_scope(query_scope) == _scope(options), 'evaluation_scope_changed')
    service = SemanticRetrievalService(encoder=encoder, repository=repository, scope=query_scope)
    context = SimpleNamespace(question=question, connection=None,
                              semantic_service_factory=lambda connection, scope: service)
    data = execute_retrieval(context, query_scope)
    permissions = data['assessment']
    refs = permitted_references(data, 'r1')
    ref_keys = [op.opinion_key for ref in refs for op in ref.opinions]
    require(ref_keys == permissions['permitted_keys'], 'citation_permission_order_mismatch')
    row['retrieval'] = dict(query=data['retrieval']['query'], evidence=data['retrieval']['evidence'],
                            assessment=permissions, citation_keys=ref_keys, timings=data['timings'],
                            encoder_metrics=encoder.last_metrics, total_ms=(perf_counter()-started)*1000)
    return row


def attribution(row, grades, sources, effective, indexed, snapshots):
    retrieval = row['retrieval']
    candidates = {hit['key'] for hit in retrieval['evidence']} if retrieval is not None else None
    permitted = set(retrieval['citation_keys']) if retrieval is not None else None
    standards = []
    for key, hit in grades.items():
        if hit['grade'] != 2:
            continue
        raw = sources.get(hit['review_id'])
        unchanged = raw is not None and raw['text'] == hit['review_text']
        standards.append(dict(key=key, review_id=hit['review_id'],
            raw_span_present=unchanged and raw['text'][hit['quote_start']:hit['quote_end']] == hit['quote'],
            effective_key_present=key in effective, indexed=key in indexed,
            in_candidates=None if candidates is None else key in candidates,
            licensed=None if permitted is None else key in permitted))
    if retrieval is not None:
        for hit in retrieval['evidence']:
            historical = grades.get(hit['key'])
            # A label was judged with full review context and a particular source snapshot.
            hit['historical_grade'] = historical['grade'] if (
                historical and historical['review_text'] == hit['review_text'] and
                historical['effective_snapshot_sha256'] == snapshots[hit['review_id']]) else None
    return standards


def run(output):
    settings = read_settings(ROOT / '.env')
    db = settings['DATABASE_URL']
    target = urlsplit(db)
    require(target.scheme in ('postgres', 'postgresql') and target.hostname in ('localhost', '127.0.0.1', '::1')
            and target.path == '/reviews', 'local_reviews_postgresql_required')
    options = business_options(settings)
    repository = PostgresSemanticRepository(db)
    history, metrics, cases, grades = historical_cases()
    additions = load(ROOT / 'evaluation/rag-055-baseline-cases.json')
    cases += additions['cases']
    require(len({case['id'] for case in cases}) == len(cases), 'duplicate_case_id')
    scope = _scope(options)
    with repository.snapshot(scope) as snapshot:
        connection = snapshot.connection
        before, corpus, vectors = inventory(connection, options)
        catalog = [dict(row) for row in connection.execute(
            'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
            (scope.source, scope.dataset_version))]
        sources = {row['review_id']: row for row in load_review_contexts(
            connection, scope, [r.review_id for r in corpus.reviews])}
    require(len(sources) == len(corpus.reviews) and bool(sources), 'incomplete_review_packet')
    effective = {doc.stable_versioned_opinion_key: doc for doc in corpus.documents}
    indexed = {doc.stable_versioned_opinion_key for doc in vectors}
    snapshots = {r.review_id: r.effective_snapshot_sha256 for r in corpus.reviews}
    require(indexed <= effective.keys(), 'invalid_inventory')
    source_digest_before = content_sha256({path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / 'src/data_analysis_agent').rglob('*.py'))})
    freeze = dict(question_set_sha256=content_sha256(cases), source_sha256=source_digest_before,
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        historical_run_id=history['run_id'], inventory=before,
        inventory_matches_history=before['snapshot_sha256'] == history['inventory']['snapshot_sha256'])
    write_json(output / 'freeze.json', freeze)

    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    try:
        results = [evaluate_case(case, options, catalog, repository, encoder) for case in cases]
    finally:
        encoder.close()
    with repository.snapshot(scope) as snapshot:
        after, _, _ = inventory(snapshot.connection, options)
    require(before == after, 'inventory_changed_during_run')
    for row in results:
        row['historical_grade_2_progress'] = attribution(
            row, grades.get(row['id'], {}), sources, effective, indexed, snapshots)

    write_json(output / 'results.json', dict(freeze=freeze, inventory_unchanged=True,
        historical_metrics={mode: metrics['groups'][f'mode:{mode}'] for mode in ('unfiltered', 'filtered')},
        cases=results))
    write_json(output / 'review-packet.json', dict(freeze=freeze,
        reviews=[dict(review_id=r.review_id, text=sources[r.review_id]['text'],
                      processing_status=r.processing_status) for r in corpus.reviews],
        effective_opinions=[dict(key=key, review_id=doc.review_id, quote=doc.quote,
            quote_start=doc.quote_start, quote_end=doc.quote_end, aspect=doc.aspect,
            sentiment=doc.sentiment, indexed=key in indexed) for key, doc in effective.items()],
        human_review=[dict(id=case['id'], corpus_status='unreviewed', accepted_quotes=[])
            for case in cases if case['expected_route'] == 'semantic_evidence']))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        output = private_directory(args.output)
        require(not any(output.iterdir()), 'use_empty_output_directory')
        results = run(output)
    except (EvaluationError, SemanticRetrievalError) as exc:
        print(f'baseline_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        # Exception messages may contain connection secrets or private review text.
        print(f'baseline_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    print(json.dumps(dict(cases=len(results), expected_routes=sum(row['route_ok'] for row in results),
                          live_retrievals=sum(row['retrieval'] is not None for row in results))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
