"""Compare a single query and bounded paraphrases on known 055 retrieval misses."""

import argparse
from itertools import zip_longest
import json
import re
from statistics import median
import sys
from time import perf_counter

from baseline_rag_055 import load
from evaluate_rag_055_coverage import OUTPUT_ROOT, setup
from evaluate_rag_055_hybrid import BASELINE, Candidate, answer_candidates, known_keys, signature
from evaluate_retrieval_quality import inventory, private_directory, write_json
from data_analysis_agent.agents.review_reported_facts import target
from data_analysis_agent.agents.review_retrieval import retrieval_request, wants_semantic_search
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import validate_vector
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope


HYBRID = OUTPUT_ROOT / '055-stage4-hybrid-20261121'
CASE_IDS = ('D01', 'D02', 'H01')
PARAPHRASES = ('有没有制冷速度迟缓的反馈？', '有没有空调开了好一会儿才凉快的评论？')
ARM_BUDGETS = {'single': (10,), 'multi': (4, 3, 3)}
ORDERS = (('single', 'multi'), ('multi', 'single'), ('single', 'multi'), ('multi', 'single'))
COLLOQUIAL = {
    'cooling_slow': r'(?:空调)?(?:开了|等了)(?:好一会儿|很久)才(?:凉快|变凉|有凉意)',
    'first_delivery_slow': r'(?:首次|第一次)(?:配送|送货)(?:比较|很|太|较)?(?:慢|迟缓)',
    'return_slow': r'(?:退货(?:时|后)?(?:寄回|退回)|退货返程(?:物流)?)(?:比较|很|太|较)?慢',
}
CONTROLS = (
    ('有没有制冷不快的反馈？', PARAPHRASES[0], True),
    ('有没有制冷不快的反馈？', PARAPHRASES[1], True),
    ('有没有制冷不快的反馈？', '有没有完全不制冷的反馈？', False),
    ('有没有完全不制冷的反馈？', PARAPHRASES[1], False),
    ('有没有制冷不快的反馈？', '有没有制冷很快的反馈？', False),
    ('有没有送货慢的反馈？', '有没有配送迟缓的评论？', True),
    ('有没有送货慢的反馈？', '有没有退货寄回很慢的反馈？', False),
    ('有没有退货寄回很慢的反馈？', '有没有退货返程物流比较慢的评论？', True),
    ('有没有退货寄回很慢的反馈？', '有没有配送迟缓的评论？', False),
    ('有没有首次配送很慢的反馈？', '有没有第一次送货比较慢的评论？', True),
    ('有没有首次配送很慢的反馈？', '有没有配送迟缓的评论？', False),
    ('有没有首次配送很慢的反馈？', '有没有退货寄回很慢的反馈？', False),
    ('有没有退货寄回很慢的反馈？', '有没有首次配送很慢的反馈？', False),
    ('有没有退货寄回很慢的反馈？', '有没有退货退款很慢的评论？', False),
    ('有没有制冷很慢的反馈？', '有没有上周制冷很慢的反馈？', False),
    ('有没有制冷很慢的反馈？', '商品100013588053有没有制冷很慢的反馈？', False),
)


def intent(question):
    parsed = target(question)
    if parsed and len(parsed['facts']) == 1 and parsed['relation'] is None:
        return parsed['facts'][0]
    # Narrow experiment vocabulary for equivalent wording outside the production grammar.
    for fact, clause in COLLOQUIAL.items():
        if re.fullmatch(rf'有没有(?:{clause})的(?:反馈|评论)[？?]?', question):
            return fact
    return None


def same_intent(original, paraphrase):
    goal = intent(original)
    return goal is not None and goal == intent(paraphrase)


def run_controls():
    outcomes = []
    for original, paraphrase, expected in CONTROLS:
        observed = same_intent(original, paraphrase)
        require(observed == expected, 'paraphrase_boundary_regression')
        outcomes.append(dict(original=original, paraphrase=paraphrase, accepted=observed))
    return outcomes


def unique_round_robin(channels):
    unique = {}
    for group in zip_longest(*channels):
        for item in group:
            if item is not None:
                unique.setdefault(item.key, item)
    return list(unique.values())


def run_arm(repository, scope, request, vectors, arm, indexed):
    started = perf_counter()
    budget = ARM_BUDGETS[arm]
    with repository.snapshot(scope) as snapshot:
        ranked = []
        for i, (vector, limit) in enumerate(zip(vectors, budget, strict=True)):
            matches = snapshot.search(vector, top_k=limit, aspect=request.aspect, sentiment=request.sentiment)
            ranked.append([Candidate(hit.document, 'original' if i == 0 else f'paraphrase_{i}', rank, hit.cosine_distance)
                           for rank, hit in enumerate(matches, 1)])
        require(len(ranked) == len(budget) and sum(map(len, ranked)) <= sum(budget), 'arm_candidate_budget')
        combined = unique_round_robin(ranked)
        result = answer_candidates(snapshot, scope, request, combined, indexed)
    return dict(result, channel_keys=[[hit.key for hit in group] for group in ranked],
                raw_candidates=sum(map(len, ranked))), (perf_counter()-started)*1000


def evaluate(output):
    settings, db, options = setup()
    scope = _scope(options)
    baseline = load(BASELINE / 'results.json')
    hybrid = load(HYBRID / 'results.json')
    require(baseline['freeze']['question_set_sha256'] == hybrid['question_set_sha256'], 'question_set_changed')
    cases = {c['id']: c for c in baseline['cases'] if c['retrieval'] is not None}
    stage4 = {c['id']: c for c in hybrid['cases']}
    selection = {i for i, r in stage4.items() if not r['known_hits']['vector'] and r['known_hits']['hybrid']}
    require(selection == set(CASE_IDS), 'documented_miss_set_changed')
    references = known_keys([cases[i] for i in CASE_IDS])
    controls = run_controls()
    for case_id in CASE_IDS:
        question = cases[case_id]['text']
        require(bool(references[case_id]) and wants_semantic_search(question) and intent(question) == 'cooling_slow'
                and all(same_intent(question, p) for p in PARAPHRASES), 'unbound_paraphrases')
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        before, _, valid = inventory(snapshot.connection, options)
    require(before == baseline['freeze']['inventory'] == hybrid['inventory'] and before['not_indexed_count'] == 0,
            'frozen_inventory_changed')
    indexed = {doc.stable_versioned_opinion_key for doc in valid}
    queries = [cases[i]['text'] for i in CASE_IDS] + list(PARAPHRASES)
    frozen = dict(inventory_sha256=before['snapshot_sha256'], case_ids=CASE_IDS, queries=queries,
                  candidate_budgets=ARM_BUDGETS, controls=controls)
    write_json(output / 'freeze.json', frozen)
    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    vectors, encoding = {}, {}
    try:
        for query in queries:
            started = perf_counter()
            vectors[query] = validate_vector(encoder.embed_texts([query])[0])
            encoding[query] = dict(encoder.last_metrics, elapsed_ms=(perf_counter()-started)*1000)
    finally:
        encoder.close()
    rows = []
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_before_queries')
        repository = PostgresSemanticRepository(snapshot.connection)
        for case_id in CASE_IDS:
            question = cases[case_id]['text']
            request = retrieval_request(question)
            require(request.model_dump(mode='json') == cases[case_id]['retrieval']['query'],
                    'original_request_changed')
            request = request.model_copy(update={'top_k': 10})
            query_vectors = [vectors[q] for q in (question, *PARAPHRASES)]
            results, timings = {}, []
            for order in ORDERS:
                pair = {}
                for arm in order:
                    inputs = query_vectors[:len(ARM_BUDGETS[arm])]
                    candidate_result, pair[arm] = run_arm(repository, scope, request, inputs, arm, indexed)
                    results.setdefault(arm, candidate_result)
                    require(candidate_result == results[arm], 'arm_changed_on_repeat')
                timings.append(pair)
            require(signature(results['single']) == signature(stage4[case_id]['results']['vector']) and
                    results['single']['displayed_keys'] == stage4[case_id]['results']['vector']['displayed_keys'],
                    'single_query_baseline_changed')
            require(results['multi']['channel_keys'][0] == results['single']['channel_keys'][0][:4],
                    'original_query_prefix_changed')
            hits = {arm: [h['key'] for h in r['evidence'] if h['key'] in references[case_id]]
                    for arm, r in results.items()}
            unique_budget = len(results['multi']['evidence'])
            matched_budget_hits = [h['key'] for h in results['single']['evidence'][:unique_budget]
                                   if h['key'] in references[case_id]]
            require(all(r['query']['question'] == question for r in results.values()), 'answer_question_changed')
            rows.append(dict(id=case_id, split=cases[case_id]['split'], original_question=question, paraphrases=PARAPHRASES,
                known_reference_count=len(references[case_id]), known_hits=hits,
                matched_unique_budget_single_hits=matched_budget_hits, results=results, paired_timings_ms=timings))
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_after_queries')

    samples = [p for row in rows for p in row['paired_timings_ms']]
    recovered = {key for row in rows for key in set(row['known_hits']['multi'])-set(row['known_hits']['single'])}
    summary = dict(cases=len(rows), pairs=len(samples), boundaries_checked=len(controls),
        known_hit_cases={arm: [r['id'] for r in rows if r['known_hits'][arm]] for arm in ARM_BUDGETS},
        matched_unique_budget_single_hit_cases=[r['id'] for r in rows if r['matched_unique_budget_single_hits']],
        unique_known_references_recovered=len(recovered),
        cited_cases={arm: [r['id'] for r in rows if r['results'][arm]['displayed_keys']] for arm in ARM_BUDGETS},
        total_raw_candidates={arm: sum(r['results'][arm]['raw_candidates'] for r in rows) for arm in ARM_BUDGETS},
        total_unique_candidates={arm: sum(len(r['results'][arm]['evidence']) for r in rows) for arm in ARM_BUDGETS},
        median_query_to_reference_ms={arm: round(median(p[arm] for p in samples), 3) for arm in ARM_BUDGETS},
        median_paired_delta_ms=round(median(p['multi'] - p['single'] for p in samples), 3),
        extra_warm_encoding_ms=round(sum(encoding[q]['elapsed_ms'] for q in PARAPHRASES), 3))
    write_json(output / 'results.json', dict(freeze=frozen, freeze_sha256=content_sha256(frozen), inventory=before,
        inventory_unchanged=True, query_vectors_sha256=content_sha256(vectors), encoding=encoding,
        timing_excludes_query_encoding=True, order=ORDERS, summary=summary, cases=rows))
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
        print(f'multi_query_evaluation_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'multi_query_evaluation_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
