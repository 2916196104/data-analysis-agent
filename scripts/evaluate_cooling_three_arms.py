"""Review-disjoint, read-only ablation of cooling keyword retrieval and ranking."""
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys
from time import perf_counter

from evaluate_rag_055_coverage import OUTPUT_ROOT, setup
from evaluate_rag_055_hybrid import CUES, TOPICS, Candidate
from evaluate_retrieval_quality import ROOT, private_directory, write_json
from data_analysis_agent.agents import review_cooling_speed_evidence as cooling, review_reported_facts as facts
from data_analysis_agent.agents.review_retrieval import assess_retrieval, permitted_references, retrieval_request
from data_analysis_agent.agents.review_task_contract import evidence_policy, evidence_question
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import SPACE_ID, _search_effective_opinions
from data_analysis_agent.reviews.retrieval_evaluation import require
from data_analysis_agent.reviews.semantic_retrieval import (
    PostgresSemanticRepository, _scope, _verified_sources, load_review_contexts,
)

# Only the original rank feature is imported. The experimental permission policy is never used.
sys.path.insert(0, str(OUTPUT_ROOT / 'cooling-elapsed-evidence-20261121'))
from elapsed_policy import timed_clause

PRODUCTS = ('100013588055', '100013588053', '100010350689', '100010219881', '100019386660')
QUESTIONS = (
    ('S1', '有没有制冷很慢的反馈？'), ('S2', '有没有制冷速度比较慢的反馈？'),
    ('S3', '有没有空调降温速度缓慢的评论？'), ('C1', '有没有送货慢的反馈？'),
    ('C2', '有没有同一条评论既说制冷快又说噪音小的反馈？'),
)
ARMS = ('vector', 'keyword', 'ranked')
SEED = 'cooling-three-arm-fresh-v1'
SLOW_HINT = re.compile(r'(制冷|降温|变凉|冷得|凉得|室温|温度).{0,12}(慢|不快|迟缓)|'
                       r'(分钟|小时|钟头|半天|很久|好久|一会).{0,17}(才|还没|不凉|没凉)|'
                       r'(慢|等.{0,5}久|很久).{0,10}(制冷|凉快|降温|变凉)')
NEIGHBOR = re.compile(r'不制冷|不凉|不降温|没有冷风|风口|制冷差|制冷效果差|制冷慢|制冷快|降温快')
OPINION_KEY = re.compile(r'rag-opinion-v1:[0-9a-f]{64}')
TOPIC_QUOTE = TOPICS['cooling'][1] + r'|冷风|(?:室温|温度)(?:才)?(?:降下[来去]|下降)|才(?:能)?降[0-9一二两三四五六七八九十][0-9点.十百]*(?:度|℃)'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest_file(path):
    return sha256(path.read_bytes()).hexdigest()


def prior_exposures():
    files = sorted(OUTPUT_ROOT.glob('*-20261121/*.json'))
    files += sorted((OUTPUT_ROOT / 'baseline-v1').glob('*.json'))
    seen_ids, seen_keys, seen_text, hashes = set(), set(), set(), {}
    def visit(obj, field=''):
        if isinstance(obj, dict):
            if isinstance(obj.get('review_id'), int):
                seen_ids.add(obj['review_id'])
                for key in ('review_text', 'text'):
                    if isinstance(obj.get(key), str):
                        seen_text.add(content_sha256(obj[key].strip()))
            for name, value in obj.items():
                visit(value, name)
        elif isinstance(obj, list):
            for item in obj:
                visit(item, field)
        elif isinstance(obj, str):
            seen_keys.update(OPINION_KEY.findall(obj))
        elif isinstance(obj, int) and (field.endswith(('review_ids', '_reviews')) or field == 'review_id'):
            seen_ids.add(obj)
    for path in files:
        if path.name in ('plan.json','freeze.json','apply-receipt.json','inventory.json'):
            continue
        if path.parent.name.startswith('053-cross-product') and path.name == 'source-packet.json':
            continue  # Full underlying corpus export. Prior labels and hits are read separately.
        data = read(path)
        if path.name == 'review-packet.json' and 'effective_opinions' in data:
            data = data.get('human_review', [])
        visit(data)
        hashes[str(path.relative_to(OUTPUT_ROOT))] = digest_file(path)
    require(seen_ids and seen_keys, 'missing_prior_candidates')
    return seen_ids, seen_keys, seen_text, hashes


def code_identity():
    from data_analysis_agent.agents import review_retrieval, review_task_contract
    from data_analysis_agent.reviews import semantic_retrieval, embedding_store
    import elapsed_policy, evaluate_rag_055_hybrid
    modules = (cooling, facts, review_retrieval, review_task_contract, semantic_retrieval,
               embedding_store, elapsed_policy, evaluate_rag_055_hybrid)
    return {str(p.relative_to(ROOT)): digest_file(p) for p in
            (Path(__file__).resolve(), *(Path(m.__file__).resolve() for m in modules))}


def pick_reviews(snapshot, scope, excluded_ids, excluded_keys, used_text):
    docs = defaultdict(list)
    for doc in snapshot.corpus().documents:
        docs[doc.review_id].append(doc)
    eligible = sorted(rid for rid, group in docs.items() if rid not in excluded_ids and
                      all(d.stable_versioned_opinion_key not in excluded_keys for d in group))
    strata = defaultdict(list)
    for i in range(0, len(eligible), 250):
        for row in load_review_contexts(snapshot.connection, scope, eligible[i:i+250]):
            rid, text = row['review_id'], row['text']
            fingerprint = content_sha256(text.strip())
            if fingerprint in used_text:
                continue
            kind = 'speed_or_time' if SLOW_HINT.search(text) else 'nearby' if NEIGHBOR.search(text) else 'other'
            strata[kind].append((content_sha256([SEED, scope.product_ids[0], rid]), rid, text, fingerprint))
    picked = []
    for kind, quota in [('speed_or_time', 10), ('nearby', 7), ('other', 7)]:
        count = 0
        for _, rid, text, fingerprint in sorted(strata[kind]):
            if fingerprint in used_text:
                continue
            used_text.add(fingerprint)
            opinions = []
            for d in sorted(docs[rid], key=lambda d: (d.quote_start, d.stable_versioned_opinion_key)):
                require(text[d.quote_start:d.quote_end] == d.quote, 'source_span_changed')
                opinions.append(dict(key=d.stable_versioned_opinion_key, quote=d.quote,
                                     start=d.quote_start, end=d.quote_end))
            picked.append(dict(product=scope.product_ids[0], review_id=rid, text=text, stratum=kind,
                               opinions=opinions))
            count += 1
            if count == quota:
                break
    return picked


def inventory(snapshot, ids):
    corpus = snapshot.corpus()
    docs = tuple(doc for doc in corpus.documents if doc.review_id in ids)
    states = tuple(r for r in corpus.reviews if r.review_id in ids)
    require({r.review_id for r in states} == ids == {d.review_id for d in docs}, 'frozen_membership_changed')
    subset = replace(corpus, documents=docs, reviews=states)
    keys = sorted(d.stable_versioned_opinion_key for d in docs)
    rows = snapshot.connection.execute(
        'SELECT id,stable_versioned_opinion_key,effective_snapshot_sha256,embedding::text '
        'FROM review_opinion_embeddings WHERE stable_versioned_opinion_key=ANY(?::text[]) '
        'AND invalidated_at IS NULL AND embedding_space_id=? AND text_builder_version=? '
        'AND label_profile_key=? ORDER BY id', (keys, SPACE_ID, snapshot.text_builder_version, corpus.label_profile_key))
    states_by_key = {s.review_id:s.effective_snapshot_sha256 for s in states}
    expected = {d.stable_versioned_opinion_key:states_by_key[d.review_id] for d in docs}
    vectors = [tuple(row[i] for i in range(4)) for row in rows]
    require(all(expected[key] == digest for _,key,digest,_ in vectors) and
            len({v[1] for v in vectors}) == len(vectors), 'stale_or_duplicate_vector')
    return subset, dict(reviews=len(states), opinions=len(docs), vectors=len(vectors),
        digest=content_sha256([keys, [vars(s) for s in states], vectors]))


def freeze(out):
    _, db, options = setup()
    old_ids, old_keys, old_text, archives = prior_exposures()
    batch, products = [], {}
    for product in PRODUCTS:
        scope = _scope(dict(options, product_ids=[product]))
        with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
            picked = pick_reviews(snapshot, scope, old_ids, old_keys, old_text)
            _, products[product] = inventory(snapshot, {r['review_id'] for r in picked})
            batch.extend(picked)
    for i, r in enumerate(sorted(batch, key=lambda row: content_sha256([SEED,row['review_id']]))):
        r['blind_id'] = f'R{i+1:03d}'
    batch.sort(key=lambda r: r['blind_id'])
    require(len({r['review_id'] for r in batch}) == len(batch) and
            len({r['text'].strip() for r in batch}) == len(batch), 'duplicate_holdout_review')
    manifest = dict(seed=SEED, questions=QUESTIONS, products=products, dates=[options['date_from'],options['date_to']],
        prior_archives=archives, code=code_identity(), batch_sha256=content_sha256(batch),
        gates='single cooling_slow only and native permitted_keys empty; final budget 5; B/C share complete keyword pool and slots')
    write_json(out/'batch.json', batch)
    write_json(out/'freeze.json', manifest)
    print(json.dumps({'products':products,'reviews':len(batch),'batch_sha256':manifest['batch_sha256']}))


def freeze_regression(out, previous):
    """Bind current code to a previously scored batch in a separate output directory."""
    previous = Path(previous).resolve()
    require(previous.is_relative_to(OUTPUT_ROOT) and previous != out, 'invalid_previous_run')
    frozen, batch, labels, scored = (read(previous/name) for name in
        ('freeze.json','batch.json','blind-labels.json','results.json'))
    require(content_sha256(batch)==frozen['batch_sha256']==labels['batch_sha256']==scored['batch_sha256']
            and content_sha256(labels)==scored['label_sha256'] and frozen['questions']==[list(q) for q in QUESTIONS]
            and set(frozen['products'])==set(PRODUCTS), 'previous_run_drift')
    updated = dict(frozen, code=code_identity(), regression_of=dict(
        directory=previous.name, freeze_sha256=content_sha256(frozen), results_sha256=content_sha256(scored)))
    write_json(out/'batch.json', batch)
    write_json(out/'blind-labels.json', labels)
    write_json(out/'freeze.json', updated)


def priority(quote):
    clauses = [s.strip() for _,_,s in facts.clauses(quote)]
    explicit = cooling.SLOW in cooling._quote_facts(quote)
    if explicit and any(cooling._WAITING.fullmatch(s) for s in clauses):
        return 3
    if any(timed_clause(s) for s in clauses):
        return 2
    return int(explicit)


def keyword_pool(request, documents, excluded):
    topics = [re.compile(TOPIC_QUOTE if name=='cooling' else q) for name,(p,q) in TOPICS.items()
              if re.search(p, request.question)]
    cues = [re.compile(q) for name,(p,q) in CUES.items()
            if re.search(p + ('|迟缓' if name in ('delay','duration') else ''), request.question)]
    pool = []
    for d in documents:
        if d.stable_versioned_opinion_key in excluded or (request.aspect and d.aspect!=request.aspect) or (
                request.sentiment and d.sentiment!=request.sentiment):
            continue
        topics_hit = sum(bool(p.search(d.quote)) for p in topics)
        if topics_hit:
            pool.append((d, -sum(bool(p.search(d.quote)) for p in cues), -topics_hit, d.stable_versioned_opinion_key))
    return pool


def adjudicate(snapshot, scope, question, chosen):
    keys = tuple(c.key for c in chosen)
    require(len(keys)==len(set(keys))<=5, 'invalid_candidate_budget')
    checked = _verified_sources(snapshot.load_sources(keys) if keys else (), keys, scope)
    rows = []
    for c in chosen:
        d, source = checked[c.key]
        require(d==c.document and source.review_text[d.quote_start:d.quote_end]==d.quote, 'quote_binding_changed')
        rows.append(dict(key=c.key, review_id=d.review_id, product_id=d.product_id, published_at=d.published_at,
            file_sha256=d.file_sha256, line_number=d.line_number, opinion_source=d.opinion_source,
            annotation_id=d.annotation_id, effective_snapshot_sha256=source.state.effective_snapshot_sha256,
            quote=d.quote, quote_start=d.quote_start, quote_end=d.quote_end, aspect=d.aspect, sentiment=d.sentiment,
            review_text=source.review_text))
    policy = evidence_policy(question, scope.options())
    target = evidence_question(question, scope.options(), fact_policy=policy)
    assessment = assess_retrieval(target, rows, policy=policy)
    refs = permitted_references(dict(assessment=assessment, retrieval={'evidence':rows}), 'offline')
    verifier = cooling if policy == cooling.POLICY else facts
    require(verifier.validate_answer_bindings(target, assessment, refs), 'invalid_fact_binding')
    return dict(candidates=[dict(key=c.key, review=c.document.review_id, channel=c.channel) for c in chosen],
                cited=assessment['permitted_keys'], policy=policy, facts=assessment['required_fact_ids'])


def compare(snapshot, scope, corpus, query_id, question, vector, order):
    req = retrieval_request(question)
    start = perf_counter()
    matches = _search_effective_opinions(snapshot.connection, vector, options=scope.options(),
        embedding_space_id=SPACE_ID, top_k=5, aspect=req.aspect, sentiment=req.sentiment, corpus=corpus)
    native = [Candidate(m.document, 'vector', i, m.cosine_distance) for i,m in enumerate(matches,1)]
    baseline = adjudicate(snapshot, scope, question, native)
    native_ms = (perf_counter()-start)*1000
    slow = baseline['policy']==cooling.POLICY and baseline['facts']==[cooling.SLOW]
    require(slow==query_id.startswith('S'), 'unexpected_query_intent')
    trigger = slow and not baseline['cited']
    arms = {'vector':baseline}
    additional_ms = {}
    pool_hashes = []
    for mode in order:
        if not trigger:
            arms[mode], additional_ms[mode] = baseline, 0
            continue
        start = perf_counter()
        pool = keyword_pool(req, corpus.documents, {c.key for c in native})
        items = sorted(pool, key=lambda t: ((-priority(t[0].quote),) if mode=='ranked' else ()) + t[1:])
        lex = [Candidate(t[0], 'keyword', i) for i,t in enumerate(items[:5],1)]
        chosen = (native[:1] + lex[:4] + native[1:])[:5] if native else lex
        arms[mode] = adjudicate(snapshot, scope, question, chosen)
        additional_ms[mode] = (perf_counter()-start)*1000
        pool_hashes.append(content_sha256(sorted(t[3] for t in pool)))
    require(len(set(pool_hashes))<=1, 'keyword_pools_diverged')
    return dict(product=scope.product_ids[0], query=query_id, trigger=trigger, arms=arms,
                pool_sha256=next(iter(pool_hashes),None), native_ms=native_ms, extra_ms=additional_ms)


def run(out, repeat_file):
    frozen, batch, labels = (read(out/name) for name in ('freeze.json','batch.json','blind-labels.json'))
    require(frozen['code']==code_identity() and frozen['questions']==[list(x) for x in QUESTIONS]
            and frozen['batch_sha256']==labels['batch_sha256']==content_sha256(batch), 'input_drift')
    ids = {r['blind_id']:r for r in batch}
    require(set(labels['rows'])==set(ids), 'incomplete_blind_review')
    gold, review_of = set(), {}
    for blind, r in ids.items():
        ops = r['opinions']
        entry = labels['rows'][blind]
        indices = entry['direct']
        uncertain = entry['unclear']
        require(all(type(i) is int and 1<=i<=len(ops) for i in indices+uncertain)
                and len(set(indices+uncertain))==len(indices+uncertain), 'invalid_blind_label')
        gold.update(ops[i-1]['key'] for i in indices)
        review_of.update({o['key']:r['review_id'] for o in ops})
    settings, db, options = setup()
    encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'],settings['RAG_MODEL_CACHE'],timeout=120)
    try:
        vectors = {qid:encoder.embed_texts([q])[0] for qid,q in QUESTIONS}
    finally:
        encoder.close()
    runs = []
    for product in PRODUCTS:
        scope = _scope(dict(options, product_ids=[product]))
        selected = {r['review_id'] for r in batch if r['product']==product}
        with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
            corpus, state = inventory(snapshot, selected)
            require(state==frozen['products'][product], 'source_or_vector_drift')
            for qid,q in QUESTIONS:
                case = None
                raw_times = []
                for order in [('keyword','ranked'), ('ranked','keyword'), ('ranked','keyword'), ('keyword','ranked')]:
                    result = compare(snapshot,scope,corpus,qid,q,vectors[qid],order)
                    if case:
                        require(case['arms']==result['arms'] and case['pool_sha256']==result['pool_sha256'], 'replay_changed')
                    case = result
                    raw_times.append(dict(native=result['native_ms'],**result['extra_ms']))
                case['times_ms'] = raw_times
                runs.append(case)
    slow = [r for r in runs if r['query'].startswith('S')]
    triggers = [r for r in slow if r['trigger']]
    summary = {}
    for arm in ARMS:
        cited = {k for r in slow for k in r['arms'][arm]['cited']}
        hits = {c['key'] for r in slow for c in r['arms'][arm]['candidates']}
        by_review = lambda keys: sorted({review_of[k] for k in keys})
        ms = [median([t[arm] for t in r['times_ms']]) for r in triggers] if arm!='vector' else []
        summary[arm] = dict(cited_cases=sum(bool(r['arms'][arm]['cited']) for r in slow),
            direct_candidate_reviews=by_review(gold & hits), direct_cited_reviews=by_review(gold & cited),
            non_direct_cited_keys=sorted(cited-gold), cited_keys=sorted(cited),
            lost_native_keys=sorted({k for r in runs for k in set(r['arms']['vector']['cited'])-set(r['arms'][arm]['cited'])}),
            extra_median_ms=median(ms) if ms else 0)
    data = dict(batch_sha256=frozen['batch_sha256'], label_sha256=content_sha256(labels), query_vectors_sha256=content_sha256(vectors),
        human_confirmed=labels['human_confirmed'], total_cases=len(runs), slow_cases=len(slow),
        probes=len(triggers), unchanged_controls=sum(all(r['arms'][arm]==r['arms']['vector'] for arm in ARMS)
        for r in runs if r not in slow), gold_keys=sorted(gold), gold_review_ids=sorted({review_of[k] for k in gold}),
        summary=summary, runs=runs)
    write_json(repeat_file, data)
    print(json.dumps({k:v for k,v in data.items() if k in ('total_cases','slow_cases','probes','unchanged_controls','gold_review_ids','summary')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('freeze','regression','run'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--result-name', default='results.json')
    parser.add_argument('--previous', help='Scored batch to reuse only for the regression stage')
    args = parser.parse_args()
    if (args.stage == 'regression') != bool(args.previous):
        parser.error('--previous is required only with regression')
    out = private_directory(args.output)
    if args.stage in ('freeze','regression'):
        require(not any(out.iterdir()), 'freeze_requires_empty_directory')
        if args.stage == 'regression':
            freeze_regression(out, args.previous)
        else:
            freeze(out)
    else:
        require(re.fullmatch(r'(results|replay)(-[a-zA-Z0-9_-]+)?\.json',args.result_name)
                and not (out/args.result_name).exists(), 'use_new_result_filename')
        run(out, out/args.result_name)


if __name__=='__main__':
    main()
