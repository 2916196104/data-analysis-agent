"""Read-only cross-product holdout for the 055 keyword and evidence experiment."""

import argparse
from collections import Counter
import json
from statistics import median
import sys
from time import perf_counter

from audit_all_products_rag_coverage import FIELDS, classify_vectors
from baseline_rag_055 import evaluate_case, load
from evaluate_rag_055_coverage import setup
from evaluate_rag_055_hybrid import Candidate, answer_candidates, keyword_candidates
from evaluate_rag_055_keyword_evidence import POLICY, eligible, experimental_refs
from evaluate_rag_055_topk import replay_vector
from evaluate_retrieval_quality import private_directory, write_json
from data_analysis_agent.agents.review_retrieval import assess_retrieval, retrieval_request
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import _eligible, SPACE_ID, validate_vector
from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, load_review_contexts


PRODUCT = '100013588053'
ARMS = ('top5', 'proof_only', 'keyword_only', 'joint')


def scoped_settings():
    settings, db, options = setup()
    return settings, db, dict(options, product_ids=[PRODUCT])


def read_packet(snapshot, scope):
    connection = snapshot.connection
    corpus = snapshot.corpus()
    options = scope.options()
    where, params, _ = review_scope(**{k: options[k] for k in
        ('source', 'dataset_version', 'product_ids', 'date_from', 'date_to')}, alias='r')
    vectors = [dict(r) for r in connection.execute(
        'SELECT ' + ','.join('r.' + name for name in FIELDS) + ''',
        r.stable_versioned_opinion_key,r.effective_snapshot_sha256,r.embedding_space_id,r.invalidated_at,
        r.embedding::text stored_vector FROM review_opinion_embeddings r WHERE ''' + where + ' ORDER BY r.id', params)]
    docs = {d.stable_versioned_opinion_key: d for d in corpus.documents}
    states = {s.review_id: s for s in corpus.reviews}
    counts, usable, anomalies = classify_vectors(docs, states, {r['key']:r['snapshot'] for r in _eligible(corpus)},
        vectors, SPACE_ID, TEXT_BUILDER_VERSION, corpus.label_profile_key, validate_vector)
    require(not anomalies and len(usable) > 0, 'usable_index_not_verified')
    review_ids = list(states)
    reviews = {}
    for start in range(0, len(review_ids), 250):
        for row in load_review_contexts(connection, scope, review_ids[start:start+250]):
            reviews[row['review_id']] = row['text']
    require(set(reviews) == set(states) and all(reviews[d.review_id][d.quote_start:d.quote_end] == d.quote
            for d in docs.values()), 'raw_source_span_mismatch')
    coverage = dict(product=PRODUCT, review_count=len(reviews), status_counts=dict(Counter(s.processing_status for s in states.values())),
        effective_opinions=len(docs), usable_vectors=len(usable), coverage=len(usable)/len(docs), coverage_diagnostics=dict(counts))
    opinions = [dict(key=key, review_id=d.review_id, quote=d.quote, quote_start=d.quote_start,
        quote_end=d.quote_end, aspect=d.aspect, sentiment=d.sentiment, indexed=key in usable) for key,d in docs.items()]
    packet = dict(scope=scope.model_dump(mode='json', exclude={'base_url'}), coverage=coverage,
        reviews=[dict(review_id=i,text=reviews[i],state=vars(states[i])) for i in sorted(reviews)], opinions=opinions,
        vector_fingerprint=content_sha256([[r['stable_versioned_opinion_key'], r['effective_snapshot_sha256'],
                                           str(r['invalidated_at']), r['stored_vector']] for r in vectors]))
    return packet, usable


def collect(output):
    _, db, options = scoped_settings()
    scope = _scope(options)
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        packet, _ = read_packet(snapshot, scope)
    write_json(output / 'source-packet.json', packet)
    return dict(packet_sha256=content_sha256(packet), **packet['coverage'])


def compare_case(snapshot, scope, row, documents, indexed, available, labels, proof):
    question = row['text']
    retrieved = row['retrieval']
    vector = retrieved['evidence']
    baseline = retrieved['citation_keys']
    enabled = row['expect_trigger']
    legacy_probe = enabled and not baseline
    joint_probe = enabled and not proof
    keyword, elapsed = [], None
    if legacy_probe or joint_probe:
        started = perf_counter()
        request = retrieval_request(question)
        candidates = keyword_candidates(documents, request, available, {r['key'] for r in vector})
        keyword = answer_candidates(snapshot, scope, request, candidates, indexed)['evidence']
        elapsed = (perf_counter() - started) * 1000
    combined = vector + keyword
    require(len(vector) <= 5 and len(keyword) <= 5 and len({r['key'] for r in combined}) == len(combined),
            'candidate_budget_or_duplicate')
    legacy = assess_retrieval(question, combined)['permitted_keys'][:5] if legacy_probe else baseline
    joint = experimental_refs(question, combined)['citation_keys'] if joint_probe else proof
    citations = dict(top5=baseline, proof_only=proof, keyword_only=legacy, joint=joint)
    require(enabled or all(keys == baseline for keys in citations.values()), 'control_changed')
    return dict(id=row['id'], question=question, route=row['route'], eligible=enabled,
        legacy_probe=legacy_probe, joint_probe=joint_probe, keyword_ms=elapsed,
        vector=retrieved, keyword=keyword, citation_keys=citations,
        known_vector_hits=[r['key'] for r in vector if labels.get(r['key']) == 'direct'],
        known_keyword_hits=[r['key'] for r in keyword if labels.get(r['key']) == 'direct'])


def summarize(rows, labels):
    targets = [row for row in rows if row['eligible']]
    timings = [r['keyword_ms'] for r in targets if r['keyword_ms'] is not None]
    arms = {}
    for arm in ARMS:
        published = {key for row in targets for key in row['citation_keys'][arm]}
        arms[arm] = dict(questions_with_citations=sum(bool(r['citation_keys'][arm]) for r in targets),
            questions_with_known_direct_citations=sum(any(labels.get(k) == 'direct' for k in r['citation_keys'][arm])
                                                     for r in targets),
            distinct_known_direct_citations=sum(labels.get(k) == 'direct' for k in published),
            distinct_known_reject_citations=sum(labels.get(k) == 'reject' for k in published),
            distinct_unadjudicated_citations=sum(k not in labels for k in published))
    return dict(target_questions=len(targets), arms=arms,
        joint_fallback_questions=[r['id'] for r in targets if r['joint_probe']],
        controls_unchanged=[r['id'] for r in rows if not r['eligible']],
        distinct_known_vector_hits=len({key for r in targets for key in r['known_vector_hits']}),
        distinct_known_keyword_hits=len({key for r in targets for key in r['known_keyword_hits']}),
        keyword_median_ms=median(timings) if timings else None)


def evaluate(output):
    frozen = load(output / 'holdout.json')
    packet_sha = frozen['source_packet_sha256']
    cases, refs = frozen['queries'], frozen['references']
    labels = {row['key']: row['label'] for row in refs}
    require(frozen['product'] == PRODUCT and frozen['rankings_seen'] is False and
            len(labels) == len(refs) == len({r['review_id'] for r in refs}) and
            Counter(labels.values()) == {'direct': 6, 'reject': 6}, 'invalid_frozen_references')
    require(len({c['id'] for c in cases}) == len({c['text'] for c in cases}) == 5 and
            all(eligible(c['text']) == c['expect_trigger'] for c in cases), 'holdout_trigger_mismatch')
    require(content_sha256(load(output / 'source-packet.json')) == packet_sha, 'frozen_packet_changed')

    settings, db, options = scoped_settings()
    scope = _scope(options)
    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    replay = {}
    try:
        for case in cases:
            q = case['text']
            replay[q] = replay_vector(encoder.space, q, encoder.embed_texts([q])[0])
            replay[q].last_metrics = encoder.last_metrics
    finally:
        encoder.close()

    strata = {'same_index': [], 'all_effective': []}
    gate_checks = []
    repository = PostgresSemanticRepository(db)
    with repository.snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        packet, indexed = read_packet(snapshot, scope)
        require(content_sha256(packet) == packet_sha, 'live_packet_differs_from_freeze')
        catalog = [dict(row) for row in snapshot.connection.execute(
            'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
            (scope.source, scope.dataset_version))]
        scoped_repository = PostgresSemanticRepository(snapshot.connection)
        documents = snapshot.corpus().documents
        by_key = {doc.stable_versioned_opinion_key: doc for doc in documents}
        by_packet_key = {row['key']: row for row in packet['opinions']}
        require(all(all(row[k] == v for k, v in by_packet_key[row['key']].items()) for row in refs),
                'frozen_reference_binding_mismatch')
        ref_candidates = [Candidate(by_key[r['key']], 'frozen_reference', i) for i, r in enumerate(refs, 1)]
        for case in cases:
            q = case['text']
            row = evaluate_case(case, options, catalog, scoped_repository, replay[q])
            require(row['route_ok'] and row['retrieval'] is not None, f"holdout_route_changed_{case['id']}")
            baseline = row['retrieval']['citation_keys']
            proof = experimental_refs(q, row['retrieval']['evidence'])['citation_keys'] if eligible(q) else baseline
            strata['same_index'].append(compare_case(snapshot, scope, row, documents, indexed, indexed, labels, proof))
            strata['all_effective'].append(compare_case(snapshot, scope, row, documents, indexed, by_key, labels, proof))
            if case['expect_trigger']:
                reference_evidence = answer_candidates(snapshot, scope, retrieval_request(q), ref_candidates, indexed)['evidence']
                for ref in reference_evidence:
                    legacy = assess_retrieval(q, [ref])
                    experiment = experimental_refs(q, [ref])['assessment']
                    gate_checks.append(dict(id=case['id'], key=ref['key'], review_id=ref['review_id'],
                        label=labels[ref['key']], legacy=legacy['candidate_judgments'][0],
                        experiment=experiment['candidate_judgments'][0]))
    with repository.snapshot(scope) as snapshot:
        require(content_sha256(read_packet(snapshot, scope)[0]) == packet_sha, 'live_packet_changed_during_run')

    gate_counts = {gate: {label: len({r['key'] for r in gate_checks if r['label'] == label and
                                     r[gate]['support'] == 'direct_report'}) for label in ('direct', 'reject')}
                   for gate in ('legacy', 'experiment')}
    summary = dict(coverage=packet['coverage'], source_packet_sha256=packet_sha,
        holdout_sha256=content_sha256(frozen), human_confirmed=frozen['human_confirmed'], policy=POLICY,
        direct_reference_count=6, direct_refs_indexed=sum(r['indexed'] for r in refs if r['label'] == 'direct'),
        budget=dict(vector=5, keyword=5, citations=5), database_scope_unchanged=True,
        strata={name: summarize(rows, labels) for name, rows in strata.items()}, standalone_gate_passes=gate_counts)
    write_json(output / 'results.json', dict(summary=summary, strata=strata, standalone_gate_checks=gate_checks))
    write_json(output / 'comparison.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--evaluate', action='store_true', help='Run comparison after freezing holdout.json')
    args = parser.parse_args()
    try:
        output = private_directory(args.output)
        if args.evaluate:
            require({entry.name for entry in output.iterdir()} == {'source-packet.json', 'holdout.json'},
                    'frozen_inputs_required_or_already_evaluated')
            print(json.dumps(evaluate(output)))
        else:
            require(not any(output.iterdir()), 'use_empty_output_directory')
            print(json.dumps(collect(output)))
    except EvaluationError as exc:
        print(f'cross_product_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'cross_product_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
