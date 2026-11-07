"""Offline review preparation only. No database reader, model payload or P0 issuer.

Historical content is read from fixed local archives and written only under ignored
outputs. New four-state answers and executable Runtime contracts remain unset.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / 'src'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from data_analysis_agent.core.serialization import content_sha256

ARCHIVE = Path('outputs/rag-retrieval-quality')
BASELINE = ARCHIVE / 'baseline-v1/review.json'
OBSERVED = ARCHIVE / 'step4b-phase2c1-exploratory-v1-20261104/observed-sources.json'
BUNDLE = ARCHIVE / 'step4b-phase2c1c-adjudication-20261104/bundle.json'
SESSION = ARCHIVE / 'e1a-b2-publication-20261105/source-session.original.json'
RELEASE = ARCHIVE / 'e1a-b2-publication-20261105/release-v1/reference-release.json'
DESTINATION = ROOT / 'outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1'
PINS = {
    BASELINE: 'c6ca491983c23b736c43d2ac93118ec9733af36a813ac68aaec9ec2a375eb2b2',
    OBSERVED: 'c6331fffd3c1ead27e763f84e5c3359ae62d67fd06215ce21acbb3df5e47c1fa',
    BUNDLE: 'f6b5046793647343428754cd763dcb9603d89851901c1884056bad8833ece932',
    SESSION: 'b5e722b632e6639b6ebcedbe883edd0ca4f2699e5460ae8cf2b75c6c719b171b',
    RELEASE: '3119110db89e0c9afc88f90865e73b907f6529b4045be359001ce682f2586d26',
}
# Quotas are review units, never instructions to a model or four-state labels.
QUOTAS = {'Q02': 1, 'Q04': 3, 'Q05': 4, 'Q06': 2,
          'Q09': 4, 'Q11': 3, 'Q12': 4, 'Q18': 3}
STRATA = {'Q02': 'customer_support_role', 'Q04': 'after_sales_followup',
          'Q05': 'delivery_speed', 'Q06': 'delivery_delay', 'Q09': 'early_quality_failure',
          'Q11': 'hot_weather_cooling_speed', 'Q12': 'prolonged_cooling_failure',
          'Q18': 'installation_after_delivery'}
CHECKS = {
    'Q02': '明确客服对象；泛称服务不能自动归给客服，区分对象不同与对象未知。',
    'Q04': '保留购买后、出现状况、持续无人跟进三个条件；安装等待不自动等于全部售后。',
    'Q05': 'quote须支持从下单到收货的快速经历；安装快不替代收货快。',
    'Q06': '区分未收到货与已收货后无人安装；全文不能补充quote缺失的支持。',
    'Q09': '保留使用不久与损坏/故障；质量差、售后差不自动证明早期故障；核对配件对象。',
    'Q11': '保留炎热环境、开机、室内很快变凉；制热、一般制冷快不能自动补齐条件。',
    'Q12': '保留运行较久与仍不凉快；不制冷的简短评价不能自动补齐时长。',
    'Q18': '两事实须quote支持，same_review及before均保留；不能从全文补到货事实。',
}
SEMANTIC_FIELDS = ('task_type', 'target_entity', 'subtopic', 'sentiment_intent',
                   'required_facts', 'polarity_structure', 'proposed_filters',
                   'uncertainty', 'clarification', 'scope_ref')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def text_digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


def pool_records(records, exposed_ids, exposed_text_hashes):
    """Exclude at whole-review level, including an exposed text under another ID."""
    require(len({r['key'] for r in records}) == len(records), 'duplicate_archive_key')
    pool, excluded = {}, []
    for record in records:
        reason = ('known_development_review' if record['review_id'] in exposed_ids else
                  'same_text_as_development_review' if text_digest(record['review_text']) in exposed_text_hashes else None)
        if reason:
            excluded.append(dict(key=record['key'], review_id=record['review_id'], reason=reason))
        else:
            start, end = record['quote_start'], record['quote_end']
            require(type(start) is int and type(end) is int and
                    0 <= start < end <= len(record['review_text']) and
                    record['review_text'][start:end] == record['quote'], 'archive_quote_mismatch')
            pool[record['key']] = deepcopy(record)
    return pool, excluded


def memberships(searches, pool):
    """U/F entries are retained as provenance, collapsed as evidence units."""
    result = defaultdict(list)
    for search in searches:
        ranks = [h['rank'] for h in search['hits']]
        require(ranks == list(range(1, len(ranks) + 1)), 'historical_rank_sequence_changed')
        for hit in search['hits']:
            if hit['key'] not in pool:
                continue
            record = pool[hit['key']]
            for field in ('review_id', 'annotation_id', 'quote', 'quote_start', 'quote_end', 'review_text'):
                require(hit[field] == record[field], 'historical_membership_source_mismatch')
            result[(search['query_id'], hit['key'])].append(dict(mode=search['mode'], rank=hit['rank']))
    return dict(result)


def support_seeds(references, pool):
    seeds = {}
    for release_index, released in enumerate(references):
        reference = released['reference']
        require(reference['human_confirmed'] and reference['contract_confirmed'], 'reference_not_confirmed')
        for finding_index, finding in enumerate(reference['fact_findings']):
            if finding['support'] != 'direct':
                continue
            for ref_index, ref in enumerate(finding['evidence_refs']):
                if ref['key'] not in pool:
                    continue
                record = pool[ref['key']]
                require(record['quote_start'] <= ref['quote_start'] < ref['quote_end'] <= record['quote_end'],
                        'old_support_outside_quote')
                identity = (reference['query_id'], finding['fact_id'], ref['key'])
                seed = seeds.setdefault(identity, dict(query_id=identity[0], fact_id=identity[1], key=identity[2],
                    provenance_status='previously_confirmed', reuse_scope='original_direct_fact_evidence_only',
                    new_four_state_confirmed=False, references=[]))
                seed['references'].append(dict(case_id=released['case_id'], mode=reference['mode'],
                    original_contract_version=reference['contract_version'],
                    contract_revision=released['contract_revision'], evidence_ref=deepcopy(ref),
                    rationale=finding['rationale'],
                    evidence_text=record['review_text'][ref['quote_start']:ref['quote_end']],
                    pointer=f'{RELEASE.as_posix()}#/references/{release_index}/reference/fact_findings/{finding_index}/evidence_refs/{ref_index}'))
    return list(seeds.values())


def select_units(pool, pairs, seeds, quotas):
    require(0 < sum(quotas.values()) <= 30 and all(0 < n <= 5 for n in quotas.values()), 'invalid_review_quota')
    seed_pairs = {(s['query_id'], s['key']) for s in seeds}
    reserved = {pool[s['key']]['review_id'] for s in seeds if s['query_id'] in quotas}
    used, selected = set(), []
    for qid, quota in quotas.items():
        candidates = [key for query, key in pairs if query == qid]
        for _ in range(min(quota, len(candidates))):
            def priority(key):
                record = pool[key]
                return ((qid, key) not in seed_pairs,
                        record['review_id'] in reserved and (qid, key) not in seed_pairs,
                        record['review_id'] in used,
                        qid == 'Q11' and record['sentiment'] != 'positive',
                        min(p['rank'] for p in pairs[qid, key]), key)
            key = min(candidates, key=priority)
            candidates.remove(key)
            selected.append((qid, key))
            used.add(pool[key]['review_id'])
    return selected


def contract_mapping(qid, question, query, runtime):
    original = query['payload']['contract']
    draft = {name: deepcopy(original[name]) for name in SEMANTIC_FIELDS}
    independent = deepcopy(query['payload']['independent_target_fact_ids'])
    rows = []
    for name, value in draft.items():
        rows.append(dict(original_path=f'/{name}', draft_path=f'/{name}', copied_exactly=True,
            current_runtime_has_field=name in runtime,
            current_runtime_value_equal=name in runtime and runtime[name] == value))
    return dict(query_id=qid, original_question=question,
        original_contract_version=original['version'], original_contract_revision=query['revision'],
        original_contract=deepcopy(original), original_confirmation_event=query['event_id'],
        original_confirmation_rationale=query['rationale'], original_confirmation_status='previously_confirmed',
        field_mapping=rows, experimental_semantic_draft=draft,
        independent_target_fact_ids=independent, original_scope_ref=deepcopy(original['scope_ref']),
        current_runtime_unmodified=runtime, experimental_runtime_contract=None,
        trusted_runtime_sha256=None, new_confirmation_record=None,
        semantic_copy_exact=True, runtime_equivalence_status='pending_contract_equivalence',
        runtime_binding_status='not_created', new_experimental_subtargets=[],
        human_review_enabled=False,
        blockers=['current_runtime_not_equivalent_to_confirmed_query_contract',
                  'no_admitted_experimental_runtime_binding', 'source_validation_pending'],
        review_focus=CHECKS.get(qid, '核对原事实及全部条件。'))


def review_rows(units, mappings, seeds):
    result = []
    for unit in units:
        mapping = mappings[unit['query_id']]
        for fact in mapping['original_contract']['required_facts']['items']:
            prior = [s for s in seeds if (s['query_id'], s['fact_id'], s['key']) ==
                     (unit['query_id'], fact['id'], unit['key'])]
            result.append(dict(unit_id=unit['unit_id'], query_id=unit['query_id'], key=unit['key'],
                fact_id=fact['id'], target_fact=deepcopy(fact),
                original_contract_version=mapping['original_contract_version'],
                original_contract_revision=mapping['original_contract_revision'],
                original_reference=prior, prior_provenance_status='previously_confirmed' if prior else 'not_available',
                list_level_reference_pointers=unit.get('list_level_reference_pointers', []),
                reusable_scope='original_support_evidence_only' if prior else 'no_candidate_level_confirmed_four_state',
                quote=unit['quote'], quote_start=unit['quote_start'], quote_end=unit['quote_end'],
                full_context_pointer=f'candidate-pool.json#/records/{unit["key"]}/review_text',
                suggestion_status='proposed', review_focus=mapping['review_focus'],
                adjudication_status='pending_human_review', expected_status=None,
                expected_reference_type=None, necessary_evidence_texts=[],
                evidence_requirement='人工作出判断并选充分原文；support/refutation限quote，context_veto限同全文；全文不可补支持。',
                human_confirmed=False, human_confirmation_record=None, ambiguity_review='pending',
                source_drift='not_checked_against_current_database',
                review_enabled=mapping['human_review_enabled']))
    return result


def build_materials(read):
    """Only caller-supplied local JSON reader; no environment or external services."""
    from data_analysis_agent.agents.review_plan import build_query_plan
    from data_analysis_agent.reviews.human_adjudication import validate_session
    from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
    baseline, observed, bundle, session, release = (read(p) for p in PINS)
    ReferenceRelease.model_validate_json(json.dumps(release))
    require(validate_session(bundle, session)['ready_for_formal_reference_freeze'], 'old_session_invalid')
    require(release['bundle_sha256'] == content_sha256(bundle) and
            release['session_sha256'] == content_sha256(session) and
            release['observed_sources_sha256'] == content_sha256(observed), 'published_reference_link_mismatch')
    exposed_ids, exposed_hashes, exposed_texts = set(), set(), {}
    parent = Path('outputs/rag-semantic-retrieval')
    for experiment, names in [('first-siliconflow-shadow-v1', ('S1', 'S2', 'S3')),
                              ('semantic-boundary-shadow-v2', ('N1', 'N2', 'N3'))]:
        for name in names:
            for hit in read(parent / experiment / name / 'retrieval.json')['evidence']:
                exposed_ids.add(hit['review_id'])
                exposed_hashes.add(text_digest(hit['review_text']))
                exposed_texts[hit['review_id']] = hit['review_text']
    pool, excluded = pool_records(observed['records'], exposed_ids, exposed_hashes)
    pairs = memberships(baseline['searches'], pool)
    seeds = support_seeds(release['references'], pool)
    require((len(pool), len({r['review_id'] for r in pool.values()}), len(seeds)) == (51, 37, 8),
            'historical_pool_or_seed_count_drift')
    chosen = select_units(pool, pairs, seeds, QUOTAS)
    scope = read(parent / 'first-siliconflow-shadow-v1/S1/contract.json')['scope']
    queries = {q['query_id']: q for q in bundle['queries']}
    mappings = {qid: contract_mapping(qid, queries[qid]['question'], session['queries'][qid],
                build_query_plan(queries[qid]['question'], scope).task_contract.model_dump(mode='json'))
                for qid in QUOTAS}
    units = []
    review_texts = {r['review_id']: r['review_text'] for r in pool.values()}
    all_review_texts = {r['review_id']: r['review_text'] for r in observed['records']} | exposed_texts
    for key, record in pool.items():
        # Near-copy flags are a screening heuristic, not a semantic or source decision.
        near = [dict(review_id=rid, known_development_review=rid in exposed_ids,
                    similarity=round(SequenceMatcher(None, record['review_text'], text,
                    autojunk=False).ratio(), 4)) for rid, text in all_review_texts.items()
                if rid != record['review_id'] and SequenceMatcher(None, record['review_text'], text,
                    autojunk=False).ratio() >= .80]
        record.update(review_text_utf8_sha256=text_digest(record['review_text']),
            historical_source_identity={k: record[k] for k in (
                'source', 'dataset_version', 'product_id', 'review_id', 'annotation_id', 'label_profile_key',
                'opinion_source', 'model_opinion_id', 'human_opinion_id', 'human_revision_id',
                'file_sha256', 'line_number', 'review_content_sha256', 'effective_snapshot_sha256')},
            source_identity_sha256=None, source_binding_status='history_unproven_no_current_p0',
            missing_current_source_fields=['import_run_id', 'processing_status', 'human_status',
                                          'complete_current_effective_opinion_collection', 'P0_in_memory_binding'],
            development_exposure='no_known_S_or_N_review_exposure',
            broader_development_exposure='not_proven_unseen', near_duplicate_flags=near,
            near_duplicate_review='pending' if near else 'no_flag_at_raw_text_ratio_0.80',
            original_occurrences=[dict(query_id=q, **p) for (q, k), occurrences in pairs.items()
                                  if k == key for p in occurrences],
            selection_status='pending_contract_equivalence' if any(k == key for _, k in chosen) else 'excluded',
            exclusion_reason=None if any(k == key for _, k in chosen) else 'outside_this_limited_cohort')
    for index, (qid, key) in enumerate(chosen, 1):
        record = pool[key]
        old_lists = [dict(case_id=r['case_id'], mode=r['reference']['mode'],
            pointer=f'{RELEASE.as_posix()}#/references/{i}/reference',
            reuse_scope='list_level_fact_findings_only_not_candidate_four_state')
            for i, r in enumerate(release['references']) if r['reference']['query_id'] == qid
            and r['reference']['mode'] in {p['mode'] for p in pairs[qid, key]}]
        units.append(dict(unit_id=f'R{index:03d}', query_id=qid, key=key,
            review_id=record['review_id'], annotation_id=record['annotation_id'],
            original_fact_ids=[f['id'] for f in mappings[qid]['original_contract']['required_facts']['items']],
            original_contract_version=mappings[qid]['original_contract_version'],
            original_contract_revision=mappings[qid]['original_contract_revision'],
            quote=record['quote'], quote_start=record['quote_start'], quote_end=record['quote_end'],
            review_text_utf8_sha256=record['review_text_utf8_sha256'],
            source_identity=record['historical_source_identity'], source_identity_sha256=None,
            historical_top_k=True, historical_occurrences=pairs[qid, key],
            development_exposure=record['development_exposure'],
            near_duplicate_flags=record['near_duplicate_flags'],
            business_stratum=STRATA[qid], review_cluster_id=record['review_id'],
            original_human_reference=[s for s in seeds if (s['query_id'], s['key']) == (qid, key)],
            list_level_reference_pointers=old_lists,
            admission_status='pending_contract_equivalence',
            pending_gates=['runtime_equivalence', 'four_state_human_gold', 'current_p0_source_binding'],
            p0_admitted=False, executable=False))
    sidecar = review_rows(units, mappings, seeds)
    selected_keys = {k for _, k in chosen}
    for seed in seeds:
        selected = (seed['query_id'], seed['key']) in chosen
        seed.update(cohort_status='selected_original_support_seed' if selected else 'excluded_from_this_cohort',
            exclusion_reason=None if selected else 'target_outside_eight_target_limit',
            same_key_selected_under_other_target=not selected and seed['key'] in selected_keys)
    groups = [dict(query_id=q, unit_ids=[u['unit_id'] for u in units if u['query_id'] == q],
        purpose='human_review_arrangement_only', executable=False, request_body=None) for q in QUOTAS]
    review_ids = sorted({u['review_id'] for u in units})
    source_request = dict(status='design_only_not_authorized', review_ids=review_ids,
        scope=scope, selected_keys=sorted(selected_keys), required_collection='all_current_effective_opinions_of_selected_reviews',
        required_fields=['review full text and stored content digest', 'source/dataset/product/date/profile',
            'processing_status/human_status/current annotation and revision', 'model/human opinion IDs, aspect/sentiment, quote/ranges',
            'import_run_id/file_sha256/line_number', 'current complete effective opinion count and snapshot'],
        database_calls=0, p0_bindings_issued=0, database_authorized=False,
        constraints=['read_only_repeatable_read', 'ID_pushdown_in_current_review_and_annotation_CTE',
                     'no_vector_reads_or_writes', 'no_encoder', 'no_whole_corpus_reader',
                     'drift_reject_no_silent_rebase', 'owner_boundary_source_checks_before_any_future_seal'])
    selected_seed_count = sum(s['cohort_status'] == 'selected_original_support_seed' for s in seeds)
    summary = dict(version='p5-real-review-preflight-v1', original_reviews=len({r['review_id'] for r in observed['records']}),
        original_keys=len(observed['records']), original_rank_entries=sum(len(s['hits']) for s in baseline['searches']),
        known_development_reviews=len(exposed_ids), excluded_archive_reviews=len({e['review_id'] for e in excluded}),
        excluded_archive_keys=len(excluded), pool_reviews=len(review_texts), pool_keys=len(pool),
        pool_rank_entries=sum(len(v) for v in pairs.values()), pool_query_candidate_pairs=len(pairs),
        selected_units=len(units), selected_fact_candidate_rows=len(sidecar), selected_reviews=len(review_ids),
        selected_keys=len(selected_keys), unselected_pool_keys=len(pool)-len(selected_keys),
        selected_target_count=len(mappings), target_unit_counts=dict(Counter(u['query_id'] for u in units)),
        selected_target_available_pairs=sum(q in QUOTAS for q, _ in pairs),
        primary_status_counts={name: sum(u['admission_status'] == name for u in units) for name in
            ('suitable_for_next_review', 'pending_contract_equivalence', 'pending_human_review', 'pending_source_validation', 'excluded')},
        unique_support_seeds=len(seeds), support_seed_reference_occurrences=sum(len(s['references']) for s in seeds),
        selected_support_seed_facts=selected_seed_count, reserved_support_seed_facts=len(seeds)-selected_seed_count,
        prior_support_selected_units=sum(bool(u['original_human_reference']) for u in units),
        new_four_state_confirmed=0, new_four_state_decisions_required=len(sidecar), active_human_review_rows=0,
        selected_near_duplicate_flags=sum(bool(pool[k]['near_duplicate_flags']) for k in selected_keys),
        human_arrangement_groups=len(groups), executable_requests=0, model_calls=0, database_calls=0, embedding_calls=0,
        publication_allowed=False, semantic_verified=False, permitted_target_fact_ids=[])
    log = [dict(query_id=q, key=k, selected=(q, k) in chosen,
        reason='selected' if (q, k) in chosen else 'target_outside_cohort' if q not in QUOTAS else 'quota_and_review_diversity')
        for q, k in pairs]
    return {'candidate-pool.json': dict(records=pool, exposure_exclusions=excluded),
            'selected-units.json': units, 'contract-mappings.json': mappings,
            'review-sidecar.json': sidecar, 'seed-reuse.json': seeds,
            'source-validation-request.json': source_request, 'review-groups.json': groups,
            'selection-log.json': log, 'summary.json': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'prepare'))
    args = parser.parse_args()
    # These transport/encoder guards remain active even if an imported path changes.
    from contextlib import ExitStack
    from unittest.mock import patch
    from data_analysis_agent.agents import review_shadow_diagnostics as privacy
    require(not DESTINATION.exists(), 'new_private_directory_already_exists')
    require(subprocess.run(['git', 'check-ignore', '-q', '--', str(DESTINATION / 'review.md')],
            cwd=ROOT).returncode == 0, 'destination_not_git_ignored')
    digests = {}
    def read(relative):
        raw = (ROOT / relative).read_bytes()
        digest = sha256(raw).hexdigest()
        require(relative not in PINS or digest == PINS[relative], 'pinned_source_changed')
        digests[relative.as_posix()] = digest
        return json.loads(raw)
    with ExitStack() as stack:
        for target in ('socket.socket.connect', 'socket.create_connection', 'psycopg.connect',
                       'httpx.HTTPTransport.handle_request', 'httpx.AsyncHTTPTransport.handle_async_request',
                       'openai.OpenAI', 'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder',
                       'data_analysis_agent.reviews.semantic_retrieval.SemanticRetrievalService.search'):
            stack.enter_context(patch(target, side_effect=AssertionError('offline_preparation_only')))
        materials = build_materials(read)
    require(all(row['expected_status'] is None and not row['human_confirmed'] and not row['review_enabled']
                for row in materials['review-sidecar.json']), 'new_gold_or_review_gate_fabricated')
    for name, digest in digests.items():
        require(sha256((ROOT / name).read_bytes()).hexdigest() == digest, 'input_changed_during_preparation')
    if args.action == 'prepare':
        destination = privacy.destination(str(DESTINATION))
        privacy._private_mkdir(destination)
        materials['input-digests.json'] = digests
        for name, value in materials.items():
            with (destination / name).open('x', encoding='utf-8') as out:
                json.dump(value, out, ensure_ascii=False, indent=2)
                out.write('\n')
        lines = ['# 私有评审材料（离线，未准入）', '',
                 '先核对 contract-mappings.json。全部新四状态留空；原人工支持不自动升级。',
                 '契约等价未确认，当前不启用逐候选评审。全文只解释/否决quote，不能补支持。', '']
        pool = materials['candidate-pool.json']['records']
        for unit in materials['selected-units.json']:
            lines.extend([f'## {unit["unit_id"]} / {unit["query_id"]}',
                f'原题：{materials["contract-mappings.json"][unit["query_id"]]["original_question"]}',
                f'候选：{unit["key"]}；review_id={unit["review_id"]}；annotation_id={unit["annotation_id"]}',
                f'原历史位置：{json.dumps(unit["historical_occurrences"], ensure_ascii=False)}',
                f'quote [{unit["quote_start"]}, {unit["quote_end"]})：', unit['quote'],
                '完整评论（未截断）：', pool[unit['key']]['review_text'],
                '待核对：' + CHECKS[unit['query_id']],
                '新四状态/引用类型/充分依据/人工确认：均待填写，详见 review-sidecar.json。', ''])
        with (destination / 'review.md').open('x', encoding='utf-8') as out:
            out.write('\n\n'.join(lines))
    print(json.dumps(materials['summary.json'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
