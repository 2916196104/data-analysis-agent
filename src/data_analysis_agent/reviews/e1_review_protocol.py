"""4B-2b 人工充分性参考格式；只校验人工记录，不推断或生成参考答案。"""

from typing import Literal

from pydantic import Field

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import PreparationError, load, require
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch, FactEvidence
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId, ScopeRef, validate_query_contract

State = Literal['sufficient', 'limited', 'insufficient']
ContextPolicy = Literal['quote_only', 'quote_full_review_veto']


class FactFinding(ContractModel):
    fact_id: FactId
    support: Literal['direct', 'partial', 'not_supported', 'unknown']
    evidence_refs: tuple[FactEvidence, ...] = ()
    rationale: Text


class RelationFinding(ContractModel):
    relation_id: Text
    support: Literal['supported', 'not_supported', 'unknown']
    evidence_keys: tuple[Text, ...] = ()
    rationale: Text


class ContextObservation(ContractModel):
    key: Text
    quote_start: int
    quote_end: int
    rationale: Text


class ReviewerDecision(ContractModel):
    reviewer_id: Text
    reference_state: State
    supported_target_fact_ids: tuple[FactId, ...] = ()
    rationale: Text


class Adjudication(ContractModel):
    adjudicator_id: Text
    reference_state: State
    supported_target_fact_ids: tuple[FactId, ...] = ()
    rationale: Text


class HumanReference(ContractModel):
    version: Literal['e1-human-reference-v1']
    origin: Literal['independent_human_reference']
    judge_output_seen: Literal[False]
    review_id: Text
    revision: int = Field(ge=1)
    parent_reference_sha256: Digest | None = None
    scope_ref: ScopeRef
    query_id: Text
    mode: Literal['unfiltered', 'filtered']
    contract_version: Literal['rag-query-contract-v1']
    contract_sha256: Digest
    contract_confirmed: bool
    required_fact_ids: tuple[FactId, ...]
    independent_target_fact_ids: tuple[FactId, ...] = ()
    batch_sha256: Digest
    context_policy: ContextPolicy
    context_version: Literal['1']
    context_input_sha256: Digest
    review_status: Literal['draft', 'confirmed', 'adjudicated']
    human_confirmed: bool
    reference_state: State | None = None
    supported_target_fact_ids: tuple[FactId, ...] = ()
    missing_required_fact_ids: tuple[FactId, ...] = ()
    fact_findings: tuple[FactFinding, ...] = ()
    relation_findings: tuple[RelationFinding, ...] = ()
    context_observations: tuple[ContextObservation, ...] = ()
    reviewer_decisions: tuple[ReviewerDecision, ...] = ()
    disagreements: tuple[Text, ...] = ()
    adjudication: Adjudication | None = None


def _ids(values, allowed):
    require(len(values) == len(set(values)) and set(values) <= allowed, 'invalid_reference_fact_ids')


def review_context_view(trusted_batch, context_policy: ContextPolicy):
    """Human review projection only; this does not implement a quote-only judge variant."""
    require(context_policy in ('quote_only', 'quote_full_review_veto'), 'unknown_context_policy')
    batch = load(trusted_batch, EvidenceBatch)
    value = batch.model_dump(mode='json')
    if context_policy == 'quote_only':
        for candidate in value['candidates']:
            del candidate['review_text']
    return dict(version='e1-review-context-v1', context_policy=context_policy, context_version='1', batch=value)


def validate_human_reference(value, *, contract, trusted_batch, expected_scope: ScopeRef,
                             expected_batch_sha256: str, expected_context_policy: ContextPolicy,
                             for_evaluation=False) -> HumanReference:
    """The batch pin comes from preparation, never a judge prediction or this reference.

    Confirmation and blindness are human declarations, not cryptographic proof. This
    validator checks identity/consistency; it does not assert actual semantic support.
    """
    ref = load(value, HumanReference)
    batch = load(trusted_batch, EvidenceBatch)
    c = validate_query_contract(contract.model_dump(mode='json') if hasattr(contract, 'model_dump') else contract,
                                expected_scope=expected_scope)
    require(content_sha256(batch.model_dump(mode='json')) == expected_batch_sha256 == ref.batch_sha256,
            'reference_batch_pin_mismatch')
    require(ref.scope_ref == batch.scope_ref == c.scope_ref and ref.query_id == batch.query_id
            and ref.mode == batch.mode, 'reference_identity_mismatch')
    require(ref.context_policy == expected_context_policy, 'reference_context_mismatch')
    require(ref.context_input_sha256 == content_sha256(review_context_view(batch, expected_context_policy)),
            'reference_context_input_mismatch')
    require(ref.contract_sha256 == content_sha256(c.model_dump(mode='json')), 'reference_contract_mismatch')
    required = tuple(f.id for f in c.required_facts.items)
    require(ref.required_fact_ids == required, 'reference_required_facts_mismatch')
    for ids in (ref.independent_target_fact_ids, ref.supported_target_fact_ids, ref.missing_required_fact_ids):
        _ids(ids, set(required))
    hits = {h.key:h for h in batch.candidates}
    require(len(hits) == len(batch.candidates), 'reference_duplicate_candidate_key')
    findings = {f.fact_id:f for f in ref.fact_findings}
    require(len(findings) == len(ref.fact_findings) and set(findings) <= set(required), 'invalid_fact_findings')
    for finding in ref.fact_findings:
        require(len({(w.key,w.quote_start,w.quote_end) for w in finding.evidence_refs}) == len(finding.evidence_refs),
                'duplicate_fact_source_reference')
        require(finding.support != 'direct' or bool(finding.evidence_refs), 'direct_fact_requires_source')
        for witness in finding.evidence_refs:
            require(witness.fact_id == finding.fact_id and witness.key in hits, 'reference_source_outside_list')
            hit = hits[witness.key]
            require(all(getattr(witness,k) == getattr(hit,k) for k in (
                'query_id','mode','rank','review_id','annotation_id','effective_snapshot_sha256',
                'embedding_input_sha256','evidence_ref')), 'reference_source_identity_mismatch')
            require(hit.quote_start <= witness.quote_start < witness.quote_end <= hit.quote_end,
                    'reference_fact_outside_quote')
    for observation in ref.context_observations:
        require(observation.key in hits, 'context_source_outside_list')
        hit = hits[observation.key]
        require(0 <= observation.quote_start < observation.quote_end <= len(hit.review_text), 'invalid_context_span')
        if ref.context_policy == 'quote_only':
            require(hit.quote_start <= observation.quote_start < observation.quote_end <= hit.quote_end,
                    'quote_only_context_outside_quote')
    relations = {f'relation_{i}':r.fact_ids for i,r in enumerate(c.required_facts.relations)}
    if c.polarity_structure.contrast_fact_ids:
        relations['contrast'] = c.polarity_structure.contrast_fact_ids
    relation_findings = {r.relation_id:r for r in ref.relation_findings}
    require(len(relation_findings) == len(ref.relation_findings) and set(relation_findings) <= set(relations),
            'invalid_relation_findings')
    for relation in ref.relation_findings:
        require(len(set(relation.evidence_keys)) == len(relation.evidence_keys)
                and set(relation.evidence_keys) <= set(hits), 'invalid_relation_sources')
        if relation.support == 'supported':
            require(all(fid in findings and findings[fid].support == 'direct'
                        and any(w.key in relation.evidence_keys for w in findings[fid].evidence_refs)
                        for fid in relations[relation.relation_id]), 'relation_requires_direct_fact_sources')
            if relation.relation_id != 'contrast':
                index = int(relation.relation_id.removeprefix('relation_'))
                if c.required_facts.relations[index].kind == 'same_review':
                    require(len({(hits[k].source,hits[k].dataset_version,hits[k].product_id,hits[k].review_id)
                                 for k in relation.evidence_keys}) == 1, 'reference_same_review_splicing')
    if not ref.human_confirmed:
        require(ref.review_status == 'draft', 'unconfirmed_reference_not_draft')
        require(not for_evaluation, 'reference_not_confirmed')
        return ref
    require(ref.contract_confirmed and ref.reference_state is not None
            and ref.review_status in ('confirmed','adjudicated'), 'reference_confirmation_incomplete')
    require(set(findings) == set(required) and all(f.support != 'unknown' for f in findings.values()),
            'reference_facts_unreviewed')
    require(set(relation_findings) == set(relations) and all(r.support != 'unknown' for r in relation_findings.values()),
            'reference_relations_unreviewed')
    direct = {fid for fid,f in findings.items() if f.support == 'direct'}
    require(set(ref.supported_target_fact_ids) <= direct, 'licensed_fact_not_direct')
    complete = ((set(required) <= direct) if c.required_facts.operator == 'all_of' else bool(direct))
    complete = complete and bool(required) and all(r.support == 'supported' for r in relation_findings.values())
    missing = set(required) - direct
    if c.required_facts.operator == 'any_of' and complete:
        missing = set()
    require(set(ref.missing_required_fact_ids) == missing, 'reference_missing_facts_inconsistent')
    blocked = {fid for name,ids in relations.items() if relation_findings[name].support != 'supported' for fid in ids}
    independent = (set(ref.independent_target_fact_ids) & direct) - blocked
    if ref.reference_state == 'sufficient':
        require(complete and bool(ref.supported_target_fact_ids)
                and set(ref.supported_target_fact_ids) == direct, 'reference_sufficient_inconsistent')
    elif ref.reference_state == 'limited':
        require(not complete and bool(ref.supported_target_fact_ids)
                and set(ref.supported_target_fact_ids) == independent, 'reference_limited_inconsistent')
    else:
        require(not complete and not independent and not ref.supported_target_fact_ids, 'reference_insufficient_inconsistent')
    decisions = ref.reviewer_decisions
    require(bool(decisions) and len({d.reviewer_id for d in decisions}) == len(decisions), 'reviewer_confirmation_missing')
    for decision in decisions:
        _ids(decision.supported_target_fact_ids, set(required))
    final = (ref.reference_state, frozenset(ref.supported_target_fact_ids))
    conflict = bool(ref.disagreements) or any((d.reference_state,frozenset(d.supported_target_fact_ids)) != final for d in decisions)
    if conflict or ref.review_status == 'adjudicated':
        require(bool(ref.disagreements) and ref.review_status == 'adjudicated' and ref.adjudication is not None,
                'unresolved_human_disagreement')
        require((ref.adjudication.reference_state,frozenset(ref.adjudication.supported_target_fact_ids)) == final,
                'adjudication_final_mismatch')
    else:
        require(ref.adjudication is None, 'unexpected_adjudication')
    return ref
