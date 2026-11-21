"""Measure keyword reranking and quote display on the same authenticated Top 15."""

import argparse
import json
from statistics import mean, median
import sys
from time import perf_counter

from baseline_rag_055 import load
from evaluate_rag_055_coverage import OUTPUT_ROOT
from evaluate_rag_055_hybrid import CUES, TOPICS, features
from evaluate_rag_055_topk import DISPLAY_LIMIT, display_candidates
from evaluate_retrieval_quality import ROOT, business_options, private_directory, write_json
from data_analysis_agent.agents.review_retrieval import assess_retrieval
from data_analysis_agent.agents.review_task_contract import evidence_question, evidence_scope_matches
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import _scope


INPUT = OUTPUT_ROOT / '055-stage3-topk-20261121'
ORDERS = (('cosine', 'rerank'), ('rerank', 'cosine'), ('cosine', 'rerank'), ('rerank', 'cosine'))


def rerank(question, candidates):
    topics, cues = features(question)
    scores = {}
    for row in candidates:
        topic_count = sum(bool(pattern.search(row['quote'])) for pattern in topics.values())
        cue_count = sum(bool(pattern.search(row['quote'])) for pattern in cues.values()) if topic_count else 0
        scores[row['key']] = (cue_count, topic_count)
    ranked = sorted(candidates, key=lambda row: (*(-s for s in scores[row['key']]), row['rank']))
    return ranked, [dict(key=row['key'], cosine_rank=row['rank'], rerank_rank=rank,
                         matched_cues=scores[row['key']][0], matched_topics=scores[row['key']][1])
                    for rank, row in enumerate(ranked, 1)]


def rebind_order(assessment, ranked):
    order = {row['key']: rank for rank, row in enumerate(ranked)}
    return dict(assessment, permitted_keys=sorted(assessment['permitted_keys'], key=order.__getitem__),
                fact_bindings=sorted(assessment['fact_bindings'], key=lambda row: order[row['key']]),
                candidate_judgments=sorted(assessment['candidate_judgments'], key=lambda row: order[row['key']]))


def measure(payload, question, fact_question, mode):
    started = perf_counter()
    evidence = payload['evidence']
    assessment = assess_retrieval(fact_question, evidence)
    rank_trace = []
    if mode == 'rerank':
        evidence, rank_trace = rerank(question, evidence)
        assessment = rebind_order(assessment, evidence)
    result = display_candidates(dict(payload, evidence=evidence), question, fact_question, assessment)
    return dict(result, rank_trace=rank_trace), (perf_counter() - started) * 1000


def ranks(result, keys):
    return {row['key']: rank for rank, row in enumerate(result['evidence'], 1) if row['key'] in keys}


def evaluate(output):
    source = load(INPUT / 'results.json')
    scope = _scope(business_options(read_settings(ROOT / '.env')))
    public_scope = scope.model_dump(mode='json', exclude={'base_url'})
    require(source['inventory_unchanged'] and source['inventory']['not_indexed_count'] == 0 and
            len(source['cases']) == 11, 'invalid_frozen_inventory')
    require(source['inventory']['label_profile_key'] ==
            content_sha256([scope.model, scope.base_url, scope.prompt_version]), 'label_profile_changed')
    input_cases = {case['id']: case['results']['15'] for case in source['cases']}
    freeze = dict(inventory_sha256=source['inventory']['snapshot_sha256'], frozen_candidates_sha256=content_sha256(input_cases),
                  question_set_sha256=source['question_set_sha256'], candidate_count=15, display_limit=DISPLAY_LIMIT,
                  keyword_rules_sha256=content_sha256([TOPICS, CUES]), timing_orders=ORDERS)
    write_json(output / 'freeze.json', freeze)
    rows = []
    for case in source['cases']:
        reference = input_cases[case['id']]
        question = case['question']
        require(reference['query']['question'] == question and reference['query']['top_k'] == 15 and
                evidence_scope_matches(question, scope.options()), 'query_binding_changed')
        fact_question = evidence_question(question, scope.options())
        candidates = reference['evidence']
        by_key = {row['key']: row for row in candidates}
        require(len(candidates) == len(by_key) == 15 and [r['rank'] for r in candidates] == list(range(1, 16)) and
                all(r['product_id'] == scope.product_ids[0] and r['source'] == scope.source and
                    r['dataset_version'] == scope.dataset_version and
                    scope.date_from <= r['published_at'][:10] <= scope.date_to and
                    r['review_text'][r['quote_start']:r['quote_end']] == r['quote'] for r in candidates),
                'frozen_source_binding_changed')
        payload = dict(query=reference['query'], evidence=candidates, scope=public_scope,
                       context_policy='quote_full_review_veto', context_version='1')
        results, timings = {}, []
        for order in ORDERS:
            pair = {}
            for mode in order:
                observed, pair[mode] = measure(payload, question, fact_question, mode)
                results.setdefault(mode, observed)
                require(observed == results[mode], 'order_not_repeatable')
            timings.append(pair)
        before, after = results['cosine'], results['rerank']
        require(all(before[name] == reference[name] for name in ('evidence', 'assessment', 'displayed_keys', 'public_answer')),
                'frozen_cosine_result_changed')
        require({h['key']: h for h in after['evidence']} == by_key and
                after['assessment'] == assess_retrieval(fact_question, after['evidence']), 'rerank_changed_evidence_or_assessment')
        require(set(before['assessment']['permitted_keys']) == set(after['assessment']['permitted_keys']) and
                before['assessment']['status'] == after['assessment']['status'], 'rerank_changed_permissions')
        known = {hit['key'] for hit in case['matched_references']['15']}
        require(known <= by_key.keys(), 'known_reference_missing_from_freeze')
        rows.append(dict(id=case['id'], question=question, known_reference_ranks={m:ranks(r, known) for m,r in results.items()},
                         licensed_reference_ranks={m:ranks(r, set(r['assessment']['permitted_keys'])) for m,r in results.items()},
                         results=results, paired_timings_ms=timings))

    samples = [p for row in rows for p in row['paired_timings_ms']]
    known_rows = [r for r in rows if r['known_reference_ranks']['cosine']]
    arms = ('cosine', 'rerank')
    summary = dict(cases=len(rows), pairs=len(samples), candidates_unchanged=True, licensed_sets_unchanged=True,
        cases_with_known_reference=len(known_rows),
        first_known_rank={row['id']:{m:min(row['known_reference_ranks'][m].values()) for m in arms} for row in known_rows},
        known_hits_in_first_5={m:sum(sum(rank<=5 for rank in r['known_reference_ranks'][m].values()) for r in known_rows)
                              for m in arms},
        first_licensed_candidate_rank={row['id']:{m:min(row['licensed_reference_ranks'][m].values()) for m in arms}
                                       for row in rows if row['licensed_reference_ranks']['cosine']},
        reciprocal_first_known_rank={m:round(mean(1/min(r['known_reference_ranks'][m].values()) for r in known_rows), 4)
                                     for m in arms},
        cited_cases={m:[r['id'] for r in rows if r['results'][m]['displayed_keys']] for m in arms},
        display_changes=[r['id'] for r in rows if r['results']['cosine']['displayed_keys'] !=
                         r['results']['rerank']['displayed_keys']],
        median_ms={m:round(median(p[m] for p in samples), 4) for m in arms},
        median_paired_delta_ms=round(median(p['rerank']-p['cosine'] for p in samples), 4))
    write_json(output / 'results.json', dict(freeze=freeze, summary=summary, cases=rows))
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
        print(f'rerank_evaluation_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'rerank_evaluation_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
