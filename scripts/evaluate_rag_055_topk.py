"""Compare single-query Top 5 and Top 15 on the frozen 055 business snapshot."""

import argparse
from collections import defaultdict
import json
from statistics import median
import sys
from time import perf_counter
from types import SimpleNamespace

from evaluate_rag_055_coverage import OUTPUT_ROOT, setup
from evaluate_retrieval_quality import inventory, private_directory, write_json
from baseline_rag_055 import load
from data_analysis_agent.agents.review_answers import semantic_answer
from data_analysis_agent.agents.review_retrieval import (
    assess_retrieval, permitted_references, retrieval_request, validate_retrieval, wants_semantic_search,
)
from data_analysis_agent.agents.review_task_contract import evidence_question, evidence_scope_matches
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import validate_vector
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import (
    PostgresSemanticRepository, SemanticRetrievalService, _scope, require_source_validated_result,
)

BASELINE = OUTPUT_ROOT / '055-stage2-live-20261121'
COVERAGE = OUTPUT_ROOT / '055-stage2-coverage-20261121'
ORDERS = ((5, 15), (15, 5), (5, 15), (15, 5))
DISPLAY_LIMIT = 5


def display_assessment(question, evidence, assessment):
    allowed = set(assessment['permitted_keys'])
    if len(allowed) <= DISPLAY_LIMIT:
        return assessment
    ranked = [row for row in evidence if row['key'] in allowed]
    if assessment['relation']:
        # Each displayed review must carry all required facts for a same-review claim.
        groups = defaultdict(dict)
        for binding in assessment['fact_bindings']:
            for fact in binding['fact_ids']:
                groups[binding['review_id']].setdefault(fact, binding['key'])
        selected = set()
        for group in groups.values():
            keys = set(group.values())
            if len(selected | keys) <= DISPLAY_LIMIT:
                selected.update(keys)
        ranked = [row for row in ranked if row['key'] in selected]
    result = assess_retrieval(question, ranked[:DISPLAY_LIMIT])
    require(result['status'] == assessment['status'], 'citation_budget_would_drop_all_support')
    return result


def replay_vector(space, question, vector):
    def embed_texts(texts):
        require(texts == [question], 'query_text_changed')
        return [vector]
    return SimpleNamespace(space=space, embed_texts=embed_texts)


def assess_candidates(payload, scope, question):
    options = scope.options()
    require(evidence_scope_matches(question, options), 'evidence_scope_mismatch')
    fact_question = evidence_question(question, options)
    assessment = assess_retrieval(fact_question, payload['evidence'])
    return display_candidates(payload, question, fact_question, assessment)


def display_candidates(payload, question, fact_question, assessment):
    evidence = payload['evidence']
    displayed = display_assessment(fact_question, evidence, assessment)
    refs = permitted_references(dict(retrieval=payload, assessment=displayed), 'r1')
    public_scope = dict(payload['scope'], scope_ref='scope1')
    answer = semantic_answer(SimpleNamespace(question=question, evidence=refs,
        scope_summary=[public_scope], source_result_ids=['r1'], statistics=[dict(
            analysis_type='semantic_evidence', source_result_id='r1', scope_ref='scope1',
            filters=dict(question=question, assessment_question=fact_question, assessment=displayed,
                context_policy=payload['context_policy'], context_version=payload['context_version'],
                candidate_count=len(evidence)))]))
    require(len(answer['highlights']) <= DISPLAY_LIMIT, 'citation_display_limit_exceeded')
    return dict(query=payload['query'], evidence=evidence, assessment=assessment,
                displayed_keys=displayed['permitted_keys'], public_answer=answer)


def measure(repository, scope, encoder, request):
    started = perf_counter()
    service = SemanticRetrievalService(encoder=encoder, repository=repository, scope=scope)
    result = service.search(request)
    require_source_validated_result(result)
    require(result.query == request and result.scope == scope, 'retrieval_binding_changed')
    payload = result.to_dict()
    output = assess_candidates(payload, scope, request.question)
    elapsed = (perf_counter() - started) * 1000
    if request.top_k == 5:
        options, question = scope.options(), request.question
        require(validate_retrieval(dict(scope=options, retrieval=payload, assessment=output['assessment'],
                    assessment_question=evidence_question(question, options)), question),
                'top5_adapter_validation_failed')
    return output, dict(service.timings, elapsed_ms=elapsed)


def signature(result):
    return [(hit['key'], hit['cosine_distance']) for hit in result['evidence']]


def verify_pair(reference, top5, top15):
    require(top5['query'] == reference['query'] and dict(top15['query'], top_k=5) == top5['query'],
            'retrieval_request_changed')
    require(signature(top5) == signature(reference) == signature(top15)[:5] and
            top5['assessment'] == reference['assessment'] and top5['displayed_keys'] == reference['citation_keys'],
            'top5_baseline_or_prefix_changed')
    require(set(top5['assessment']['permitted_keys']) <= set(top15['assessment']['permitted_keys']),
            'expansion_lost_previously_licensed_evidence')


def evaluate(output):
    settings, db, options = setup()
    scope = _scope(options)
    baseline = load(BASELINE / 'results.json')
    cases = [row for row in baseline['cases'] if row['retrieval'] is not None]
    require(len(cases) == 11 and all(wants_semantic_search(row['text']) for row in cases), 'frozen_routes_changed')
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        before, _, _ = inventory(snapshot.connection, options)
    require(before == baseline['freeze']['inventory'] and before['not_indexed_count'] == 0, 'frozen_inventory_changed')
    references = defaultdict(set)
    for row in cases:
        references[row['id']].update(item['key'] for item in row['historical_grade_2_progress']
                                     if item['indexed'] and item['raw_span_present'])
    for item in load(COVERAGE / 'results.json')['reference_observations']:
        for related in item['cases']:
            references[related['case_id']].add(item['key'])

    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    vectors, encoder_metrics = {}, {}
    try:
        for row in cases:
            vectors[row['id']] = validate_vector(encoder.embed_texts([row['text']])[0])
            encoder_metrics[row['id']] = encoder.last_metrics
        space = encoder.space
    finally:
        encoder.close()

    rows = []
    # The two arms read the same MVCC snapshot and share one real BGE-M3 query vector per question.
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_before_queries')
        repository = PostgresSemanticRepository(snapshot.connection)
        for case in cases:
            query = retrieval_request(case['text'])
            replay = replay_vector(space, query.question, vectors[case['id']])
            timings = []
            results = {}
            for order in ORDERS:
                pair = {}
                for k in order:
                    result, pair[k] = measure(repository, scope, replay, query.model_copy(update={'top_k': k}))
                    results.setdefault(k, result)
                    require(result == results[k], 'repeat_retrieval_changed')
                timings.append(pair)
            verify_pair(case['retrieval'], results[5], results[15])
            matches = {k: [dict(key=h['key'], rank=h['rank'], review_id=h['review_id']) for h in r['evidence']
                            if h['key'] in references[case['id']]] for k, r in results.items()}
            rows.append(dict(id=case['id'], question=case['text'], support_status=case['support_status'],
                             known_reference_count=len(references[case['id']]), matched_references=matches,
                             results=results, paired_timings_ms=timings))
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_after_queries')
    samples = [p for row in rows for p in row['paired_timings_ms']]
    summary = dict(cases=len(rows), repetitions=len(ORDERS), display_limit=DISPLAY_LIMIT,
        cases_with_citations={k: [row['id'] for row in rows if row['results'][k]['displayed_keys']] for k in (5, 15)},
        cases_with_known_reference={k: [row['id'] for row in rows if row['matched_references'][k]] for k in (5, 15)},
        median_ms={k: round(median(p[k]['elapsed_ms'] for p in samples), 3) for k in (5, 15)},
        median_paired_delta_ms=round(median(p[15]['elapsed_ms'] - p[5]['elapsed_ms'] for p in samples), 3))
    write_json(output / 'results.json', dict(baseline=BASELINE.name, inventory=before, inventory_unchanged=True,
        question_set_sha256=baseline['freeze']['question_set_sha256'], query_vectors_sha256=content_sha256(vectors),
        encoder_metrics=encoder_metrics, timing_excludes_query_encoding=True, order=ORDERS, summary=summary, cases=rows))
    write_json(output / 'comparison.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        output = private_directory(args.output)
        require(not any(output.iterdir()), 'use_empty_output_directory')
        print(json.dumps(evaluate(output)))
    except EvaluationError as exc:
        print(f'topk_evaluation_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'topk_evaluation_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
