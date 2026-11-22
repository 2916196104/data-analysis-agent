"""Verify human-approved cooling evidence against the complete local product corpus."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
import json
from unittest.mock import patch

from accept_review_rag_mainline import coverage
from evaluate_rag_055_coverage import setup
from evaluate_retrieval_quality import private_directory, write_json
from data_analysis_agent.agents import review_cooling_speed_evidence as cooling, reviews, runtime
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_retrieval import validate_retrieval
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import close_embedding_processes
from data_analysis_agent.reviews.retrieval_evaluation import require
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.semantic_retrieval import (
    PostgresSemanticRepository, SemanticEvidence, _scope, _verified_sources,
)

QUESTION = '有没有制冷很慢的反馈？'
DIRECT = ('100019386660', 12590, '制冷慢的一塌糊涂')
TIMED = ('100013588053', 8826, '空调开16度，开了大半个小时，房间没明显制冷效果，三小时降温效果一般')


def complete_scope(db, options, sku):
    scope = _scope(dict(options, product_ids=[sku]))
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        where, params, _ = review_scope(source=scope.source, dataset_version=scope.dataset_version,
                                       product_ids=[sku], alias='r')
        first, last, count = snapshot.connection.execute(
            'SELECT MIN(r.published_at), MAX(r.published_at), COUNT(*) FROM reviews r WHERE ' + where, params).fetchone()
    require(count > 0, 'no_reviews_for_product')
    return dict(options, product_ids=[sku], date_from=first[:10], date_to=last[:10]), count


def official_request(db, options, settings):
    result = reviews.run_review_agent(db, QUESTION, scope=options, settings=settings)
    require(result['status'] == 'ok' and result['validation']['status'] == 'passed', 'formal_agent_failed')
    require([event['tool'] for event in result['trace'] if event['event'] == 'tool_call'] ==
            ['get_analysis_context', 'search_semantic_evidence'], 'wrong_tool_sequence')
    require(result['total_model_calls'] == 0 and result['answer_generation']['strategy'] == 'template',
            'unexpected_generation')
    data, = [r['data'] for r in result['results'].values() if r['kind'] == 'semantic_evidence']
    require(validate_retrieval(data, QUESTION), 'retrieval_validation_failed')
    task = dict(status='succeeded', result=result)
    frozen = json.loads(json.dumps(task, ensure_ascii=False, allow_nan=False))
    answer = read_public_answer(frozen)
    require(answer == result['public_answer'], 'public_answer_replay_mismatch')
    return frozen, data['retrieval'], answer


def check_timed_policy(db, options):
    sku, review_id, quote = TIMED
    full, _ = complete_scope(db, options, sku)
    scope = _scope(full)
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        doc, = [d for d in snapshot.corpus().documents if d.review_id == review_id and d.quote == quote]
        key = doc.stable_versioned_opinion_key
        checked, source = _verified_sources(snapshot.load_sources((key,)), (key,), scope)[key]
        text_hash = sha256(source.review_text.encode('utf8')).hexdigest()
        row = SemanticEvidence(1, None, checked, source.state, source.review_text, text_hash).to_dict()
    old = cooling.assess(QUESTION, [row], policy=cooling.EXPLICIT_POLICY)
    new = cooling.assess(QUESTION, [row])
    require(not old['permitted_keys'] and new['permitted_keys'] == [key] and
            cooling.reports_timed_progress(quote), 'timed_policy_failed')
    return dict(product_id=sku, review_id=review_id, quote=quote, key=key, quote_start=doc.quote_start,
                quote_end=doc.quote_end, policy=new['policy'], previous_policy_permitted=False)


def evaluate(out):
    values, db, options = setup()
    sku, review_id, approved_quote = DIRECT
    scope, count = complete_scope(db, options, sku)
    inventory = coverage(db, scope)
    baseline_day = (date.fromisoformat(scope['date_from']) - timedelta(days=1)).isoformat()
    request_scope = dict(scope, baseline_from=baseline_day, baseline_to=baseline_day)
    require(inventory['reviews'] == count, 'incomplete_product_corpus')
    settings = AgentSettings(model=values['LLM_MODEL_ID'], api_key='', base_url=options['base_url'],
        semantic_parser_enabled=False, rag_model_cache=values['RAG_MODEL_CACHE'], rag_python=values['RAG_PYTHON'],
        rag_timeout_seconds=90, rag_multi_query=False, rag_citation_rerank=False)
    try:
        with ExitStack() as guards:
            for module in (reviews, runtime):
                guards.enter_context(patch.object(module, 'create_llm', side_effect=AssertionError('LLM disabled')))
            guards.enter_context(patch('openai.OpenAI', side_effect=AssertionError('external model disabled')))
            _, raw_before, answer_before = official_request(db, request_scope, settings)
            task, raw, answer = official_request(db, request_scope, replace(settings, rag_cooling_keyword_fallback=True))
    finally:
        close_embedding_processes()
    require(raw_before['version'] == 'semantic-retrieval-v1' and raw['version'] == 'semantic-retrieval-v2'
            and raw['vector_baseline'] == raw_before['evidence'] and not answer_before['evidence_refs'],
            'vector_baseline_not_equal')
    ref, = [r for r in answer['evidence_refs'] if r['review_id'] == review_id and r['product_id'] == sku]
    quote, = ref['opinions']
    row, = [r for r in raw['evidence'] if r['key'] == quote['opinion_key']]
    start, end = quote['quote_start'], quote['quote_end']
    require(row['channel'] == 'keyword' and row['cosine_distance'] is None and
            row['quote_start'] == start and row['quote_end'] == end and
            row['review_text'][start:end] == row['quote'] == quote['quote'] == approved_quote and
            raw['snapshot']['read_only'] and raw['snapshot']['isolation_level'] == 'repeatable read',
            'approved_citation_not_bound_to_keyword_source')
    require(coverage(db, scope) == inventory, 'inventory_changed')
    timed = check_timed_policy(db, options)
    report = dict(passed=True, product_id=sku, days=[scope['date_from'], scope['date_to']], inventory=inventory,
        question=QUESTION, baseline_candidates=len(raw_before['evidence']), baseline_citations=0,
        candidate_count=len(raw['evidence']), public_citations=len(answer['evidence_refs']),
        approved_citation=dict(review_id=review_id, rank=row['rank'], channel_rank=row['channel_rank'],
            channel=row['channel'], quote=approved_quote, key=row['key'], quote_start=start, quote_end=end),
        timed_policy=timed, task_sha256=content_sha256(task), public_answer_sha256=content_sha256(answer),
        history_json_roundtrip=True, human_review_scope=[DIRECT[1], TIMED[1]])
    write_json(out/'task.json', task)
    require(read_public_answer(json.loads((out/'task.json').read_text(encoding='utf8'))) == answer,
            'saved_history_mismatch')
    write_json(out/'acceptance.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = private_directory(args.output)
    require(not any(out.iterdir()), 'use_empty_output_directory')
    print(json.dumps(evaluate(out), ensure_ascii=False))


if __name__ == '__main__':
    main()
