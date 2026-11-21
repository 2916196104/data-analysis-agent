"""Freeze a vector-only versus vector + quote-keyword comparison for product 055."""

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import zip_longest
import json
import re
from statistics import median
import sys
from time import perf_counter

from baseline_rag_055 import load
from evaluate_rag_055_coverage import OUTPUT_ROOT, setup
from evaluate_rag_055_topk import BASELINE, COVERAGE, assess_candidates, signature
from evaluate_retrieval_quality import inventory, private_directory, write_json
from data_analysis_agent.agents.review_reported_facts import DOMAINS
from data_analysis_agent.agents.review_retrieval import retrieval_request, wants_semantic_search
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import validate_vector
from data_analysis_agent.reviews.rag_corpus import RagOpinionDocument
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, _verified_sources

TOPK = OUTPUT_ROOT / '055-stage3-topk-20261121'
SPARSE_INDEX = OUTPUT_ROOT / '055-stage1-query-routing-20261121'
ORDERS = (('vector', 'hybrid'), ('hybrid', 'vector'), ('vector', 'hybrid'), ('hybrid', 'vector'))
TOPICS = {
    'cooling': (DOMAINS['cooling'], DOMAINS['cooling'] + '|凉快|凉爽|变凉|有点凉'),
    'heating': (DOMAINS['heating'], DOMAINS['heating'] + '|暖和'),
    'delivery': (DOMAINS['delivery'] + '|发货|收.{0,3}到货', DOMAINS['delivery'] + '|发货|收.{0,3}到货|送达|签收'),
    'return': ('退货|退回|寄回', '退货|退回|寄回|退件|取件|退运'),
    'installation': ('安装|上门', '安装|上门'),
    'noise': (DOMAINS['noise'], DOMAINS['noise'] + '|静音|很吵'),
    'packaging': ('包装|纸箱|外箱', '包装|纸箱|外箱|包裹'),
}
CUES = {
    'delay': ('慢|不快|迟迟|不到货', '慢|不快|迟迟|迟缓|不及时|拖|才|等了|没收到|未收到|没送到'),
    'not_received': ('收不到货|不到货', '收不到|没收到|未收到|没送到|未送到|还没到'),
    'quick': ('(?<!不)快', '很快|挺快|迅速|及时|当天|次日'),
    'working_well': ('(?:制冷|降温)(?:效果)?好', '很好|效果好|不错|很棒|满意'),
    'loud': ('(?:噪音|噪声|声音)(?:很|比较)?大', '声音大|噪音大|噪声大|(?:声音|噪音|噪声).{0,3}大|很吵'),
    'damaged': ('破了|破损|损坏', '破了|破损|损坏|破裂|裂开'),
    'temperature': ('零下|负[一二三四五六七八九十0-9]', '零下|负[一二三四五六七八九十0-9]'),
    'duration': ('慢|不快|迟迟|小时|分钟|连续',
                 r'(?:[0-9]+|[几一二三四五六七八九十两半]+)\s*(?:个\s*)?(?:小时|钟(?:头)?|天|分钟|周)'),
}


@dataclass(frozen=True)
class Candidate:
    document: RagOpinionDocument
    channel: str
    channel_rank: int
    cosine_distance: float | None = None
    keyword_hits: tuple[str, ...] = ()

    @property
    def key(self):
        return self.document.stable_versioned_opinion_key


def features(question):
    topics = {name: re.compile(quote) for name, (query, quote) in TOPICS.items() if re.search(query, question)}
    cues = {name: re.compile(quote) for name, (query, quote) in CUES.items() if re.search(query, question)}
    return topics, cues


def keyword_candidates(documents, request, eligible, exclude):
    topics, cues = features(request.question)
    matched = []
    for doc in documents:
        key = doc.stable_versioned_opinion_key
        if key not in eligible or key in exclude:
            continue
        if (request.aspect and request.aspect != doc.aspect) or (request.sentiment and request.sentiment != doc.sentiment):
            continue
        topic_hits = [name for name, pattern in topics.items() if pattern.search(doc.quote)]
        if not topic_hits:
            continue
        cue_hits = [name for name, pattern in cues.items() if pattern.search(doc.quote)]
        matched.append(((-len(cue_hits), -len(topic_hits), key), doc, tuple(topic_hits + cue_hits)))
    return [Candidate(doc, 'keyword', rank, keyword_hits=hits)
            for rank, (_, doc, hits) in enumerate(sorted(matched)[:5], 1)]


def merge(vector, keyword):
    merged = [item for pair in zip_longest(vector, keyword) for item in pair if item is not None]
    require(len(merged) <= 10 and len({item.key for item in merged}) == len(merged), 'combined_candidate_budget')
    return merged


def answer_candidates(snapshot, scope, request, candidates, indexed):
    keys = tuple(item.key for item in candidates)
    checked = _verified_sources(snapshot.load_sources(keys) if keys else (), keys, scope)
    evidence = []
    for rank, item in enumerate(candidates, 1):
        doc, source = checked[item.key]
        require(doc == item.document, 'candidate_opinion_version_changed')
        fields = asdict(doc)
        for name in ('base_url', 'embedding_input', 'stable_versioned_opinion_key'):
            fields.pop(name)
        evidence.append(dict(**fields, key=item.key, rank=rank, cosine_distance=item.cosine_distance,
            effective_snapshot_sha256=source.state.effective_snapshot_sha256, review_text=source.review_text,
            channel=item.channel, channel_rank=item.channel_rank, keyword_hits=list(item.keyword_hits),
            indexed_in_arm=item.key in indexed))
    payload = dict(query=request.model_dump(mode='json'), evidence=evidence, context_policy='quote_full_review_veto',
                   context_version='1', scope=scope.model_dump(mode='json', exclude={'base_url'}))
    return assess_candidates(payload, scope, request.question)


def run_arm(repository, scope, request, vector, indexed, arm):
    started = perf_counter()
    with repository.snapshot(scope) as snapshot:
        distance_rows = snapshot.search(vector, top_k=10 if arm == 'vector' else 5,
                                        aspect=request.aspect, sentiment=request.sentiment)
        candidates = [Candidate(hit.document, 'vector', rank, hit.cosine_distance)
                      for rank, hit in enumerate(distance_rows, 1)]
        if arm == 'hybrid':
            keyword = keyword_candidates(snapshot.corpus().documents, request, indexed, {item.key for item in candidates})
            candidates = merge(candidates, keyword)
        answer = answer_candidates(snapshot, scope, request, candidates, indexed)
    return answer, (perf_counter() - started) * 1000


def replay_sparse(repository, scope, request, old_result, indexed, full):
    with repository.snapshot(scope) as snapshot:
        docs = {doc.stable_versioned_opinion_key: doc for doc in snapshot.corpus().documents}
        require(indexed <= docs.keys(), 'sparse_opinion_versions_changed')
        candidates = []
        for hit in old_result['evidence']:
            doc = docs[hit['key']]
            require(hit['key'] in indexed and doc.quote == hit['quote'], 'sparse_vector_source_changed')
            candidates.append(Candidate(doc, 'vector', hit['rank'], hit['cosine_distance']))
        known = {item.key for item in candidates}
        keyword = keyword_candidates(docs.values(), request, full, known)
        result = answer_candidates(snapshot, scope, request, merge(candidates, keyword), indexed)
        require(all(hit['effective_snapshot_sha256'] == original['effective_snapshot_sha256']
                    and hit['review_text'] == original['review_text']
                    for hit, original in zip((h for h in result['evidence'] if h['channel'] == 'vector'),
                                             old_result['evidence'], strict=True)), 'sparse_context_changed')
    return result


def known_keys(cases):
    references = defaultdict(set)
    for row in cases:
        references[row['id']].update(item['key'] for item in row['historical_grade_2_progress']
                                     if item['indexed'] and item['raw_span_present'])
    for item in load(COVERAGE / 'results.json')['reference_observations']:
        for related in item['cases']:
            references[related['case_id']].add(item['key'])
    return references


def evaluate(output):
    settings, db, options = setup()
    scope = _scope(options)
    baseline = load(BASELINE / 'results.json')
    topk = load(TOPK / 'results.json')
    sparse = load(SPARSE_INDEX / 'results.json')
    cases = [row for row in baseline['cases'] if row['retrieval'] is not None]
    require(len(cases) == 11 and all(wants_semantic_search(row['text']) for row in cases), 'frozen_routes_changed')
    require(baseline['freeze']['question_set_sha256'] == topk['question_set_sha256'] ==
            sparse['freeze']['question_set_sha256'], 'question_set_changed')
    sparse_indexed = {d['key'] for d in load(SPARSE_INDEX / 'review-packet.json')['effective_opinions'] if d['indexed']}
    require(len(sparse_indexed) == 200, 'sparse_index_count_changed')
    prior = {c['id']: c['retrieval'] for c in sparse['cases']}
    top15 = {c['id']: c['results']['15'] for c in topk['cases']}
    references = known_keys(cases)
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        before, _, valid = inventory(snapshot.connection, options)
    require(before == baseline['freeze']['inventory'] == topk['inventory'] and before['not_indexed_count'] == 0,
            'frozen_inventory_changed')
    indexed = {doc.stable_versioned_opinion_key for doc in valid}
    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    try:
        vectors = {case['id']: validate_vector(encoder.embed_texts([case['text']])[0]) for case in cases}
    finally:
        encoder.close()
    require(content_sha256(vectors) == topk['query_vectors_sha256'], 'query_vectors_changed')
    rows = []
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_before_queries')
        repository = PostgresSemanticRepository(snapshot.connection)
        for case in cases:
            case_id = case['id']
            request = retrieval_request(case['text'])
            require(request.model_dump(mode='json') == case['retrieval']['query'] == prior[case_id]['query'],
                    'original_request_changed')
            request = request.model_copy(update={'top_k': 10})
            results, timings = {}, []
            for order in ORDERS:
                pair = {}
                for arm in order:
                    observed, pair[arm] = run_arm(repository, scope, request, vectors[case_id], indexed, arm)
                    results.setdefault(arm, observed)
                    require(observed == results[arm], 'arm_changed_on_repeat')
                timings.append(pair)
            require(signature(results['vector']) == signature(top15[case_id])[:10], 'vector_baseline_changed')
            require(signature(dict(evidence=[h for h in results['hybrid']['evidence'] if h['channel'] == 'vector'])) ==
                    signature(results['vector'])[:5], 'hybrid_vector_prefix_changed')
            results['sparse_same'] = replay_sparse(repository, scope, request, prior[case_id], sparse_indexed, sparse_indexed)
            results['sparse_full'] = replay_sparse(repository, scope, request, prior[case_id], sparse_indexed, indexed)
            hit_keys = {arm: [h['key'] for h in r['evidence'] if h['key'] in references[case_id]]
                        for arm, r in results.items()}
            known_new = [h['key'] for h in results['sparse_full']['evidence']
                         if not h['indexed_in_arm'] and h['key'] in references[case_id]]
            rows.append(dict(id=case_id, split=case['split'], question=case['text'],
                             keyword_features=[list(group) for group in features(case['text'])],
                             known_reference_count=len(references[case_id]), known_hits=hit_keys,
                             known_from_unindexed=known_new, results=results, paired_timings_ms=timings))
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_after_queries')

    arms = ('vector', 'hybrid', 'sparse_same', 'sparse_full')
    samples = [p for row in rows for p in row['paired_timings_ms']]
    summary = dict(cases=len(rows), pairs=len(samples),
        cases_with_known_reference={arm: [r['id'] for r in rows if r['known_hits'][arm]] for arm in arms},
        cases_with_citations={arm: [r['id'] for r in rows if r['results'][arm]['displayed_keys']] for arm in arms},
        cases_with_known_unindexed_hit=[r['id'] for r in rows if r['known_from_unindexed']],
        total_known_hits={arm: sum(len(r['known_hits'][arm]) for r in rows) for arm in arms},
        total_candidates={arm: sum(len(r['results'][arm]['evidence']) for r in rows) for arm in arms},
        median_ms={arm: round(median(s[arm] for s in samples), 3) for arm in ('vector', 'hybrid')},
        median_paired_delta_ms=round(median(s['hybrid'] - s['vector'] for s in samples), 3))
    write_json(output / 'results.json', dict(inventory=before, inventory_unchanged=True,
        question_set_sha256=baseline['freeze']['question_set_sha256'], query_vectors_sha256=content_sha256(vectors),
        keyword_rules_sha256=content_sha256([TOPICS, CUES]), historical_index_count=len(sparse_indexed),
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
        print(f'hybrid_evaluation_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'hybrid_evaluation_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
