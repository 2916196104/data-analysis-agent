"""Verify the native review RAG path against the existing local 055 and 053 data."""

import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch

from audit_all_products_rag_coverage import FIELDS, classify_vectors
from evaluate_rag_055_coverage import setup
from evaluate_retrieval_quality import private_directory, write_json
from data_analysis_agent.agents import reviews, runtime
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_retrieval import validate_retrieval
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import close_embedding_processes
from data_analysis_agent.reviews.embedding_store import SPACE_ID, _eligible, validate_vector
from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, PROFILE_KEY, require
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope


SKU_055 = '100013588055'
SKU_053 = '100013588053'
CASES = (
    ('055-delivery-report', SKU_055, '有没有送货慢的反馈？', ('delivery_slow',), False, (9958,)),
    ('055-same-review', SKU_055, '有没有同一条评论既说制冷快又说噪音小的反馈？', ('cooling_fast','noise_low'), True, (10200,)),
    ('055-no-cross-review', SKU_055, '有没有同一条评论既说物流很快又说包装破了的反馈？',
     ('delivery_fast','packaging_bad'), True, ()),
    ('055-explicit-slow-insufficient', SKU_055, '有没有制冷很慢的反馈？', ('cooling_slow',), False, ()),
    ('053-partial-coverage', SKU_053, '有没有制冷速度比较慢的反馈？', ('cooling_slow',), False, ()),
)


def coverage(db, options):
    scope = _scope(options)
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        snapshot.connection.execute("SET LOCAL statement_timeout='20s'")
        corpus = snapshot.corpus()
        require(corpus.label_profile_key == PROFILE_KEY, 'profile_changed')
        where, params, _ = review_scope(**{name: options[name] for name in
            ('source', 'dataset_version', 'product_ids', 'date_from', 'date_to')}, alias='r')
        query = 'SELECT ' + ','.join('r.' + name for name in FIELDS) + ''' ,
            r.stable_versioned_opinion_key,r.effective_snapshot_sha256,r.embedding_space_id,r.invalidated_at,
            r.embedding::text stored_vector FROM review_opinion_embeddings r WHERE ''' + where + ' ORDER BY r.id'
        rows = [dict(row) for row in snapshot.connection.execute(query, params)]
        docs = {doc.stable_versioned_opinion_key: doc for doc in corpus.documents}
        states = {state.review_id: state for state in corpus.reviews}
        snapshots = {item['key']: item['snapshot'] for item in _eligible(corpus)}
        _, valid, anomalies = classify_vectors(docs, states, snapshots, rows, SPACE_ID,
            TEXT_BUILDER_VERSION, corpus.label_profile_key, validate_vector)
        require(not anomalies and docs, 'coverage_not_verified')
        fingerprint = content_sha256(dict(reviews=[vars(r) for r in corpus.reviews],
            keys=sorted(docs), vectors=[(r['stable_versioned_opinion_key'], r['effective_snapshot_sha256'],
                str(r['invalidated_at']), r['stored_vector']) for r in rows]))
        return dict(reviews=len(states), effective_opinions=len(docs), usable_vectors=len(valid),
                    inventory_sha256=fingerprint)


def check_case(db, options, settings, case):
    name, product, question, facts, same_review, known_reviews = case
    scope = dict(options, product_ids=[product], baseline_from='2021-09-10', baseline_to='2021-09-10')
    result = reviews.run_review_agent(db, question, scope=scope, settings=settings)
    record = dict(id=name, product_id=product, question=question, agent_status=result['status'],
                  error_code=(result.get('error') or {}).get('code'))
    if result['status'] != 'ok':
        return dict(record, passed=False)

    data, = [r['data'] for r in result['results'].values() if r['kind'] == 'semantic_evidence']
    assessment = data['assessment']
    candidates = data['retrieval']['evidence']
    snapshot = data['retrieval']['snapshot']
    answer = result['public_answer']
    evidence = answer['evidence_refs']
    quotes = {q['opinion_key']: (ref, q) for ref in evidence for q in ref['opinions']}
    keys = assessment['permitted_keys']
    candidate_by_key = {candidate['key']: candidate for candidate in candidates}
    actual_reviews = {ref['review_id'] for ref in evidence}
    relation = assessment.get('relation')
    bound_facts = {rid: {fact for item in assessment['fact_bindings'] if item['review_id'] == rid
                         for fact in item['fact_ids']} for rid in actual_reviews}
    checks = dict(
        native_tool_order=[r['tool'] for r in result['trace'] if r['event']=='tool_call'] ==
                          ['get_analysis_context', 'search_semantic_evidence'],
        native_validator=result['validation']['status']=='passed' and validate_retrieval(data, question),
        original_question_and_top5=data['retrieval']['query']['question']==question and len(candidates)==5 and
                                   data['retrieval']['query']['top_k']==5,
        readonly_snapshot=snapshot['read_only'] is True and snapshot['isolation_level']=='repeatable read',
        exact_reference_binding=[q['opinion_key'] for ref in evidence for q in ref['opinions']]==keys and
            all(ref['product_id']==product and ref['review_id']==candidate_by_key[key]['review_id'] and
                all(q[k]==candidate_by_key[key][k] for k in ('quote','quote_start','quote_end'))
                for key,(ref,q) in quotes.items()),
        question_scope=data['scope']['product_ids']==[product] and
            all(data['scope'][key]==options[key] for key in ('source','dataset_version','date_from','date_to','model','prompt_version')),
        result_is_expected=bool(known_reviews)==bool(keys) and set(known_reviews)<=actual_reviews and
            assessment['status']==('sufficient' if known_reviews else 'insufficient') and
            set(assessment['required_fact_ids'])==set(facts),
        same_review_binding=(bool(relation and relation['type']=='same_review')==same_review and
            (not same_review or (set(relation['review_ids'])==actual_reviews and
             all(group==set(facts) for group in bound_facts.values())))),
        history_replay=read_public_answer({'status':'succeeded','result':result})==answer,
        user_scope_explanation=bool(keys or (('本次' in answer['summary']) and
            ('全部评论' in answer['summary'] or same_review))),
        no_external_generation=result['total_model_calls']==0 and result['answer_generation']['strategy']=='template',
    )
    return dict(record, passed=all(checks.values()), checks=checks, policy=assessment['policy'],
        assessment_status=assessment['status'], reason=assessment['reason'], candidate_count=len(candidates),
        review_ids=sorted(actual_reviews), reference_count=len(keys), relation=relation, summary=answer['summary'],
        citations=[dict(review_id=ref['review_id'], key=key, quote_start=q['quote_start'], quote_end=q['quote_end'],
                        quote_sha256=sha256(q['quote'].encode('utf-8')).hexdigest()) for key,(ref,q) in quotes.items()])


def run():
    values, db, options = setup()
    settings = AgentSettings(model=values['LLM_MODEL_ID'], api_key='', base_url=options['base_url'],
        semantic_parser_enabled=False, rag_model_cache=values['RAG_MODEL_CACHE'],
        rag_python=values['RAG_PYTHON'], rag_timeout_seconds=90,
        rag_multi_query=False, rag_citation_rerank=False)
    products = {sku: dict(options, product_ids=[sku]) for sku in (SKU_055, SKU_053)}
    before = {sku: coverage(db, scoped) for sku, scoped in products.items()}
    require(before[SKU_055]['usable_vectors']==before[SKU_055]['effective_opinions'] and
            0<before[SKU_053]['usable_vectors']<before[SKU_053]['effective_opinions'], 'unexpected_coverage_for_acceptance')
    try:
        with ExitStack() as guards:
            for module in (reviews, runtime):
                guards.enter_context(patch.object(module, 'create_llm', side_effect=AssertionError('LLM disabled in acceptance')))
            guards.enter_context(patch('openai.OpenAI', side_effect=AssertionError('external client disabled in acceptance')))
            cases = [check_case(db, options, settings, case) for case in CASES]
    finally:
        close_embedding_processes()
    after = {sku: coverage(db, scoped) for sku, scoped in products.items()}
    require(before==after, 'inventory_changed_during_acceptance')
    return dict(scope={k:options[k] for k in ('source','dataset_version','date_from','date_to','model','prompt_version')},
        profile_key=PROFILE_KEY, embedding_space=SPACE_ID, text_builder=TEXT_BUILDER_VERSION,
        coverage=before, inventory_unchanged=True, cases=cases, passed=all(c['passed'] for c in cases))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.execute_authorized:
        parser.error('pass --execute-authorized for the local read-only run')
    try:
        out = private_directory(args.output)
        require(not (out/'acceptance.json').exists(), 'use_new_output_directory')
        result = run()
        write_json(out/'acceptance.json', result)
        print(json.dumps({'passed':result['passed'], 'cases':[{k:r.get(k) for k in
            ('id','agent_status','error_code','passed','checks','review_ids','summary')} for r in result['cases']]}, ensure_ascii=False))
        return 0 if result['passed'] else 1
    except EvaluationError as exc:
        print(f'mainline_acceptance_failed: {exc}', file=sys.stderr)
    except Exception as exc:
        print(f'mainline_acceptance_failed: {type(exc).__name__}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
