"""Test conditional keyword recall and duration-based cooling evidence on frozen 055 data."""

import argparse
import json
import re
import sys
from time import perf_counter

from baseline_rag_055 import evaluate_case, load
from evaluate_rag_055_coverage import OUTPUT_ROOT, setup
from evaluate_rag_055_hybrid import answer_candidates, keyword_candidates, known_keys
from evaluate_rag_055_topk import display_assessment, replay_vector
from evaluate_retrieval_quality import inventory, private_directory, write_json
from data_analysis_agent.agents import review_reported_facts as facts
from data_analysis_agent.agents.review_retrieval import permitted_references, retrieval_request
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope


BASELINE = OUTPUT_ROOT / '055-delivery-timing-20261121'
POLICY = 'experiment_cooling_duration_v1'
_DURATION = r'(?:[1-9][0-9]{0,2}|[一二两三四五六七八九十半几]+)\s*(?:个\s*)?(?:小时|钟(?:头)?|分钟)'
_DIRECT = re.compile(r'(?:(?:我家|我买的)?(?:这台|这个|这款)空调|我家空调)'
                     rf'(?:开了|运行了|放了?|用了|吹了){_DURATION}'
                     r'才(?:开始)?(?:有点凉|凉快(?:了)?|变凉(?:了)?|感觉到(?:一点)?凉意)(?:了)?')
_REASON_REPORT = re.compile(r'售后(?:工作人员|人员|师傅)(?:上门(?:看)?)?说是'
                            r'(?:房间太大|房间和房子墙热|房间温度太高)所以(?:制冷|降温)慢')
_OTHER_UNIT = re.compile(r'(?:差不多大的房间)?(?:隔壁(?:房间)?)?(?:其他空调|另一台空调|别的空调)'
                         r'(?:[0-9]{1,2}度)?(?:都)?(?:制冷很快|很快就凉了|很凉快了)')
_OUTSIDE_VETO = re.compile(f'{facts.RETRACTIONS}|广告|宣传|传闻|未必|可能|假如|如果|只在|仅在|只限|其他模式|'
                           r'[“”「」"？?]')
HOLDOUTS = (
    ('N01', '有没有空调降温速度缓慢的评论？', True),
    ('N02', '有买家反映制冷速度不快吗？', True),
    ('N03', '有没有完全不制冷的反馈？', False),
    ('N04', '有买家反映退货寄回很慢吗？', False),
)


def eligible(question):
    request = facts.target(question)
    return bool(request and request['facts'] == ['cooling_slow'] and request['relation'] is None)


def duration_fact(quote):
    return bool(_DIRECT.fullmatch(quote.strip().rstrip('。！!')))


def quote_facts(quote):
    return {'cooling_slow'} if duration_fact(quote) else facts.quote_facts(quote)


def context_ok(row, supported):
    if not duration_fact(row['quote']):
        return facts.context_ok(row, supported)
    text, start, end = row['review_text'], row['quote_start'], row['quote_end']
    if text[start:end] != row['quote'] or _OUTSIDE_VETO.search(text):
        return False
    clauses = list(facts.clauses(text))
    matches = [i for i, (left, right, value) in enumerate(clauses)
               if left <= start < right <= end and value.strip() == row['quote'].strip().rstrip('。！!')]
    if len(matches) != 1:
        return False
    i = matches[0]
    # A separately bounded explanation plus an explicit comparison can precede
    # the buyer's own observation. Generic or adjacent attributions stay unresolved.
    scoped_report = None
    if (i >= 3 and _REASON_REPORT.fullmatch(clauses[i-2][2].strip())
            and _OTHER_UNIT.fullmatch(clauses[i-1][2].strip())
            and facts.fact(clauses[i-3][2]) == 'cooling_poor'):
        scoped_report = i - 2

    for j, (_, _, value) in enumerate(clauses):
        if j == i or j == scoped_report:
            continue
        clause = value.strip()
        if _OTHER_UNIT.fullmatch(clause):
            continue
        if re.search(facts.CONTEXT_HAZARDS, clause):
            return False
        if re.search(r'凉|制冷|冷气|降温|空调', clause) and facts.fact(clause) not in ('cooling_poor', 'cooling_slow'):
            return False
    return True


def experimental_refs(question, evidence):
    assessment = facts.assess(question, evidence, policy=POLICY, quote_parser=quote_facts, context_check=context_ok)
    displayed = display_assessment(question, evidence, assessment)
    refs = permitted_references(dict(assessment=displayed, retrieval={'evidence': evidence}), 'r1')
    require(facts.validate_answer_bindings(question, displayed, refs, quote_parser=quote_facts),
            'experimental_quote_binding_failed')
    return dict(assessment=assessment, citation_keys=displayed['permitted_keys'])


def keyword_fallback(repository, scope, question, vector_evidence, indexed):
    started = perf_counter()
    request = retrieval_request(question)
    with repository.snapshot(scope) as snapshot:
        candidates = keyword_candidates(snapshot.corpus().documents, request, indexed,
                                        {row['key'] for row in vector_evidence})
        result = answer_candidates(snapshot, scope, request, candidates, indexed)
    return result['evidence'], (perf_counter() - started) * 1000


def evaluate(output):
    settings, db, options = setup()
    scope = _scope(options)
    baseline = load(BASELINE / 'results.json')
    cases = baseline['cases']
    references = known_keys([row for row in cases if row['retrieval'] is not None])
    additions = [dict(id=i, text=q, split='new_holdout', expected_route='semantic_evidence')
                 for i,q,_ in HOLDOUTS]
    require(all(eligible(q)==expected for _,q,expected in HOLDOUTS), 'holdout_gate_mismatch')
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        before, _, valid = inventory(snapshot.connection, options)
        catalog = [dict(row) for row in snapshot.connection.execute(
            'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
            (scope.source, scope.dataset_version))]
    require(before == baseline['freeze']['inventory'] and before['not_indexed_count'] == 0, 'frozen_inventory_changed')
    indexed = {doc.stable_versioned_opinion_key for doc in valid}
    freeze = dict(inventory_sha256=before['snapshot_sha256'], question_set_sha256=baseline['freeze']['question_set_sha256'],
                  holdouts=HOLDOUTS, keyword_limit=5, policy=POLICY)
    write_json(output / 'freeze.json', freeze)

    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
    replay = {}
    try:
        for row in [c for c in cases if c['retrieval'] is not None] + additions:
            q = row['text']
            replay[q] = replay_vector(encoder.space, q, encoder.embed_texts([q])[0])
            replay[q].last_metrics = encoder.last_metrics
    finally:
        encoder.close()
    rows = []
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        require(inventory(snapshot.connection, options)[0] == before, 'inventory_changed_before_queries')
        repository = PostgresSemanticRepository(snapshot.connection)
        for case in cases + additions:
            q = case['text']
            observed = evaluate_case(case, options, catalog, repository, replay.get(q))
            result = observed['retrieval']
            if 'retrieval' in case:
                require((observed['route'],observed.get('tools')) == (case['route'],case.get('tools')),
                        'frozen_route_changed')
                require((result is None) == (case['retrieval'] is None), 'frozen_retrieval_route_changed')
                if result is not None:
                    saved = case['retrieval']
                    frozen_evidence = [{k:v for k,v in h.items() if k != 'historical_grade'} for h in saved['evidence']]
                    require(result['evidence'] == frozen_evidence and
                            all(result[k] == saved[k] for k in ('query','assessment','citation_keys')),
                            'frozen_top5_changed')
            elif result is None:
                raise EvaluationError('holdout_not_routed_to_retrieval')
            if result is None:
                rows.append(dict(id=case['id'], route=observed['route'], triggered=False))
                continue

            base_keys = result['citation_keys']
            available = eligible(q)
            vector = result['evidence']
            enhanced = experimental_refs(q, vector) if available else dict(citation_keys=base_keys)
            legacy_probe = available and not base_keys
            enhanced_probe = available and not enhanced['citation_keys']
            keyword, latency = (keyword_fallback(repository, scope, q, vector, indexed)
                                if legacy_probe or enhanced_probe else ([], 0))
            require(len(keyword)<=5 and not ({r['key'] for r in keyword} & {r['key'] for r in vector}),
                    'keyword_budget_or_duplicate')
            legacy = (facts.assess(q, vector + keyword)['permitted_keys'][:5] if legacy_probe else base_keys)
            joined = experimental_refs(q, vector + keyword) if enhanced_probe else enhanced
            known = references[case['id']]
            if case['id'] in ('N01','N02'):
                known = references['D01']
            rows.append(dict(id=case['id'], split=case['split'], question=q, route=observed['route'],
                eligible=available, triggered=enhanced_probe, keyword_ms=latency, keyword=keyword,
                known_vector_hits=[h['key'] for h in vector if h['key'] in known],
                known_keyword_hits=[h['key'] for h in keyword if h['key'] in known],
                citation_keys=dict(top5=base_keys, proof_only=enhanced['citation_keys'],
                    keyword_only=legacy, joint=joined['citation_keys']),
                joint_assessment=joined.get('assessment')))
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        require(inventory(snapshot.connection,options)[0] == before, 'inventory_changed_after_queries')
    arms = ('top5','proof_only','keyword_only','joint')
    actual = [row for row in rows if 'citation_keys' in row]
    frozen = [row for row in actual if row['split'] != 'new_holdout']
    holdouts = [row for row in actual if row['split'] == 'new_holdout']
    require(all(row['triggered'] == (row['eligible'] and not row['citation_keys']['proof_only']) for row in actual),
            'unexpected_fallback_trigger')
    summary = dict(frozen_rules_cases_matching=len(cases), frozen_retrievals=len(frozen),
        frozen_cases_with_citations={arm:[r['id'] for r in frozen if r['citation_keys'][arm]] for arm in arms},
        new_holdout_cases_with_citations={arm:[r['id'] for r in holdouts if r['citation_keys'][arm]] for arm in arms},
        triggered_cases=[r['id'] for r in actual if r['triggered']],
        distinct_added_licensed_keys=len({key for r in actual for key in r['citation_keys']['joint']
                                         if key not in r['citation_keys']['top5']}),
        database_inventory_unchanged=True)
    write_json(output / 'results.json',dict(freeze=freeze, freeze_sha256=content_sha256(freeze), summary=summary,cases=rows))
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
        print(f'keyword_evidence_evaluation_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'keyword_evidence_evaluation_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
