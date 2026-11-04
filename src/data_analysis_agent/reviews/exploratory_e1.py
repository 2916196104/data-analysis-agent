"""Exploratory current-observation inputs and draft review projection. No predictions.

Local artifact hashes protect integrity; they do not establish historical provenance
or external approval. This module never calls prepare_trusted_batch or a judge.
"""
from datetime import datetime, timezone
from typing import Literal

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import SourceOpinion, load, require
from data_analysis_agent.reviews.e1_review_protocol import review_context_view, validate_human_reference
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch, INPUT_VERSION, _validate_sources
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId, QueryContract, ScopeRef, validate_query_contract
from data_analysis_agent.reviews.retrieval_evaluation import run_binding
from data_analysis_agent.reviews.scope import parse_day

EXPERIMENT_KIND = 'exploratory_current_observation_history_unproven'


class ObservationStamp(ContractModel):
    role: Literal['row_sources', 'complete_metadata']
    observed_at: Text
    transaction_snapshot: Text
    observation_file_sha256: Digest
    receipt_file_sha256: Digest


class ObservedSnapshot(ContractModel):
    review_id: int
    classification: Literal['history_unproven']
    computed_snapshot_sha256: Digest
    frozen_snapshot_sha256: Digest


class ExploratorySources(ContractModel):
    version: Literal['e1-observed-sources-v1']
    experiment_kind: Literal['exploratory_current_observation_history_unproven']
    data_kind: Literal['synthetic', 'frozen_query']
    scope_ref: ScopeRef
    inventory_snapshot_sha256: Digest
    observations: tuple[ObservationStamp, ...]
    snapshots: tuple[ObservedSnapshot, ...]
    records: tuple[SourceOpinion, ...]


class ExploratoryInput(ContractModel):
    version: Literal['e1-exploratory-input-v1'] = 'e1-exploratory-input-v1'
    experiment_kind: Literal['exploratory_current_observation_history_unproven'] = EXPERIMENT_KIND
    validation_policy: Literal['local_saved_sources_cross_time_v1'] = 'local_saved_sources_cross_time_v1'
    history_status: Literal['history_unproven'] = 'history_unproven'
    independent_seal: Literal[False] = False
    observed_sources_sha256: Digest
    candidate_batch: EvidenceBatch
    batch_sha256: Digest
    context_policy: Literal['quote_full_review_veto'] = 'quote_full_review_veto'
    context_version: Literal['1'] = '1'
    context_input_sha256: Digest


class ContractDraft(ContractModel):
    version: Literal['e1-query-contract-draft-v1'] = 'e1-query-contract-draft-v1'
    origin: Literal['developer_assisted_query_only'] = 'developer_assisted_query_only'
    query_id: Text
    query_sha256: Digest
    contract: QueryContract
    independent_target_fact_ids: tuple[FactId, ...] = ()
    human_confirmed: Literal[False] = False
    confirmation_status: Literal['pending_human_review'] = 'pending_human_review'


def prepare_exploratory_input(frozen_run, observed_sources, *, expected_scope: ScopeRef,
                              expected_data_kind: str, query_id: str, mode: str) -> ExploratoryInput:
    """Only a local, independently collected record projection, never historical trust.

    The caller must first verify the saved observations/receipts against their existing
    audit anchors, and construct records from SQL observations, not frozen candidates.
    """
    scope = load(expected_scope, ScopeRef)
    sources = load(observed_sources, ExploratorySources)
    require(sources.scope_ref == scope and sources.data_kind == expected_data_kind, 'exploratory_scope_or_kind_mismatch')
    require(expected_data_kind in ('synthetic', 'frozen_query'), 'invalid_exploratory_data_kind')
    require(len(sources.observations) == 2 and {o.role for o in sources.observations} == {'row_sources', 'complete_metadata'},
            'two_observation_versions_required')
    for stamp in sources.observations:
        require(datetime.fromisoformat(stamp.observed_at).utcoffset() is not None, 'observation_timezone_required')
    run = frozen_run
    require(run['run_id'] == scope.run_id == run_binding(run), 'exploratory_frozen_run_mismatch')
    manifest = run['manifest']
    require(run['manifest_sha256'] == scope.manifest_sha256 == content_sha256(manifest)
            and run['scope_sha256'] == scope.scope_sha256 == content_sha256([
                manifest['scope'], manifest['label_profile_key'], manifest['embedding_space_id']]),
            'exploratory_manifest_scope_mismatch')
    inventory = run['inventory']
    require(all(inventory[k] == manifest[k] for k in ('scope', 'label_profile_key', 'text_builder_version', 'embedding_space_id'))
            and inventory['stored_count'] == inventory['valid_count'] and inventory['invalid_count'] == 0
            and run['authorized_snapshot_unchanged'] is True, 'exploratory_inventory_identity_error')
    require(sources.inventory_snapshot_sha256 == manifest['inventory_snapshot_sha256'] == run['inventory']['snapshot_sha256'],
            'exploratory_inventory_mismatch')
    qids = [q['id'] for q in manifest['queries']]
    pairs = [(s['query_id'], s['mode']) for s in run['searches']]
    require(len(qids) == len(set(qids)) and len(pairs) == len(set(pairs)) and set(pairs) == {
        (q,m) for q in qids for m in ('unfiltered','filtered')}, 'exploratory_incomplete_lists')
    require((query_id,mode) in pairs, 'exploratory_unknown_list')
    by_key = {r.key:r for r in sources.records}
    keys = {h['key'] for s in run['searches'] for h in s['hits']}
    require(len(by_key) == len(sources.records) and set(by_key) == keys, 'exploratory_source_coverage_error')
    snapshots = {s.review_id:s for s in sources.snapshots}
    require(len(snapshots) == len(sources.snapshots) and set(snapshots) == {r.review_id for r in sources.records},
            'exploratory_snapshot_coverage_error')
    for r in sources.records:
        snapshot = snapshots[r.review_id]
        require(snapshot.computed_snapshot_sha256 == snapshot.frozen_snapshot_sha256 == r.effective_snapshot_sha256,
                'observed_snapshot_conflict')
        require(r.source == manifest['scope']['source'] and r.dataset_version == manifest['scope']['dataset_version']
                and r.product_id in manifest['scope']['product_ids'] and r.label_profile_key == manifest['label_profile_key']
                and r.text_builder_version == manifest['text_builder_version'], 'exploratory_source_range_error')
        published = datetime.fromisoformat(r.published_at)
        require(published.utcoffset() is not None, 'source_timestamp_timezone_required')
        require(parse_day(manifest['scope']['date_from'],'date_from') <= published.astimezone(timezone.utc).date()
                <= parse_day(manifest['scope']['date_to'],'date_to'), 'exploratory_source_date_error')
    search = next(s for s in run['searches'] if (s['query_id'],s['mode']) == (query_id,mode))
    query = next(q for q in manifest['queries'] if q['id'] == query_id)
    require(search['filters'] == (query['filters'] if mode == 'filtered' else {}) and len(search['hits']) <= 5,
            'exploratory_filters_or_topk_error')
    actual, observed = [], []
    for hit in search['hits']:
        source = by_key[hit['key']].model_dump(mode='json', exclude={'published_at'})
        require(all(source[name] == hit[name] for name in source.keys() & hit.keys()), 'exploratory_candidate_source_conflict')
        binding = dict(query_id=query_id,mode=mode,rank=hit['rank'])
        observed.append({**source,**binding})
        actual.append({**source,**{k:v for k,v in hit.items() if k in source},**binding})
    fields = dict(version=INPUT_VERSION,scope_ref=scope.model_dump(mode='json'),query_id=query_id,mode=mode)
    batch = load(dict(**fields,candidates=actual),EvidenceBatch)
    comparison = load(dict(**fields,candidates=observed),EvidenceBatch)
    _validate_sources(batch,comparison,scope)
    return ExploratoryInput(observed_sources_sha256=content_sha256(sources.model_dump(mode='json')),
        candidate_batch=batch,batch_sha256=content_sha256(batch.model_dump(mode='json')),
        context_input_sha256=content_sha256(review_context_view(batch,'quote_full_review_veto')))


def make_review_draft(prepared: ExploratoryInput, draft, *, question: str, expected_scope: ScopeRef):
    p = load(prepared,ExploratoryInput)
    d = load(draft,ContractDraft)
    c = validate_query_contract(d.contract.model_dump(mode='json'),expected_scope=expected_scope)
    b = p.candidate_batch
    require(d.query_id == b.query_id and d.query_sha256 == content_sha256(question), 'draft_query_identity_mismatch')
    require(p.batch_sha256 == content_sha256(b.model_dump(mode='json'))
            and p.context_input_sha256 == content_sha256(review_context_view(b,p.context_policy)), 'exploratory_input_digest_mismatch')
    ids = tuple(f.id for f in c.required_facts.items)
    require(len(d.independent_target_fact_ids) == len(set(d.independent_target_fact_ids))
            and set(d.independent_target_fact_ids) <= set(ids), 'draft_independent_targets_invalid')
    relations = [f'relation_{i}' for i,_ in enumerate(c.required_facts.relations)]
    if c.polarity_structure.contrast_fact_ids:
        relations.append('contrast')
    ref = dict(version='e1-human-reference-v1',origin='independent_human_reference',judge_output_seen=False,
        review_id=f'draft-{b.query_id}-{b.mode}',revision=1,parent_reference_sha256=None,
        scope_ref=expected_scope.model_dump(mode='json'),query_id=b.query_id,mode=b.mode,
        contract_version=c.version,contract_sha256=content_sha256(c.model_dump(mode='json')),contract_confirmed=False,
        required_fact_ids=list(ids),independent_target_fact_ids=list(d.independent_target_fact_ids),
        batch_sha256=p.batch_sha256,context_policy=p.context_policy,context_version=p.context_version,
        context_input_sha256=p.context_input_sha256,review_status='draft',human_confirmed=False,reference_state=None,
        supported_target_fact_ids=[],missing_required_fact_ids=[],fact_findings=[dict(fact_id=fid,support='unknown',
            evidence_refs=[],rationale='待人工评审') for fid in ids],relation_findings=[dict(relation_id=rid,
            support='unknown',evidence_keys=[],rationale='待人工评审') for rid in relations],context_observations=[],
        reviewer_decisions=[],disagreements=[],adjudication=None)
    return validate_human_reference(ref,contract=c,trusted_batch=b,expected_scope=expected_scope,
        expected_batch_sha256=p.batch_sha256,expected_context_policy=p.context_policy).model_dump(mode='json')


def blind_review_case(prepared, draft, *, question, case_id, expected_scope):
    """Allowlist projection. Mode/filters/predictions/scores never enter review payload."""
    ref = make_review_draft(prepared,draft,question=question,expected_scope=expected_scope)
    d = load(draft,ContractDraft)
    return dict(case_id=case_id,question=question,query_id=d.query_id,
        contract=d.contract.model_dump(mode='json'),contract_confirmed=False,
        independent_target_fact_ids=list(d.independent_target_fact_ids),context_policy=prepared.context_policy,
        context_version=prepared.context_version,context_input_sha256=prepared.context_input_sha256,
        candidates=[{name:getattr(c,name) for name in ('rank','key','review_id','annotation_id','quote','quote_start',
            'quote_end','review_text','evidence_ref')} for c in prepared.candidate_batch.candidates],
        reference_state=None,human_confirmed=False,reviewer_id=None,reviewer_count=1,
        single_reviewer_limitation='单人初评，无双人盲审或独立裁决声明',prior_relevance_exposure=None,
        fact_findings=ref['fact_findings'],relation_findings=ref['relation_findings'],
        supported_target_fact_ids=[],missing_required_fact_ids=[],review_notes='',confirmation_record=None)
