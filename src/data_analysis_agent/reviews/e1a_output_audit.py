"""Independent structural and exact-human-map audit. Never runs the rule engine."""
from typing import Literal

from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1_review_protocol import State, validate_human_reference
from data_analysis_agent.reviews.e1a_protocol import (
    PredictionOutput, digest, validate_prediction_input, relation_definitions, decision_conditions, POLICY,
)
from data_analysis_agent.reviews.e1a_prediction import validate_observed_sources
from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId


class FactSupportAudit(ContractModel):
    fact_id: FactId
    supported: bool | None


class OutputAudit(ContractModel):
    version: Literal['e1a-output-audit-v1'] = 'e1a-output-audit-v1'
    source_policy: Literal['local_saved_sources_cross_time_v1'] = POLICY
    history_status: Literal['history_unproven'] = 'history_unproven'
    independent_seal: Literal[False] = False
    query_id: Text
    mode: Literal['unfiltered','filtered']
    contract_sha256: Digest
    observed_sources_sha256: Digest
    input_sha256: Digest
    experiment_id: Text
    contract_revision: int
    engine_id: Literal['R0'] = 'R0'
    engine_version: Literal['explicit-clauses-zh-v1'] = 'explicit-clauses-zh-v1'
    context_policy: Literal['quote_full_review_veto'] = 'quote_full_review_veto'
    context_version: Literal['1'] = '1'
    release_sha256: Digest
    output_sha256: Digest | None
    source_valid: bool
    execution: Literal['missing','completed','validation_failed','execution_failed']
    errors: tuple[Text,...] = ()
    reference_state: State
    reference_licensed_ids: tuple[FactId,...]
    output_state: State | None = None
    output_licensed_ids: tuple[FactId,...] = ()
    fact_correct: int = 0
    fact_count: int = 0
    relation_correct: int = 0
    relation_count: int = 0
    licensed_set_agrees: bool = False
    missing_set_agrees: bool = False
    citation_count: int = 0
    valid_citation_count: int = 0
    unknown_fact_count: int = 0
    unknown_relation_count: int = 0
    fact_support: tuple[FactSupportAudit,...] = ()
    unparseable_output: bool = False


def audit_output(value, output, release, sources, *, expected_scope, expected_input_sha256,
                 expected_sources_sha256, expected_release_sha256):
    p=validate_prediction_input(value,expected_scope=expected_scope,expected_input_sha256=expected_input_sha256)
    release=load(release,ReferenceRelease)
    require(digest(release)==expected_release_sha256,'e1a_reference_release_pin_mismatch')
    require(release.scope_ref==expected_scope and release.observed_sources_sha256==expected_sources_sha256,
            'e1a_reference_release_scope_mismatch')
    batch=p.prepared.candidate_batch
    matches=[c for c in release.references if (c.reference.query_id,c.reference.mode)==(batch.query_id,batch.mode)]
    require(len(matches)==1,'e1a_reference_case_coverage_invalid')
    item=matches[0]
    require(item.contract==p.contract and item.contract_revision==p.contract_revision,'e1a_reference_contract_mismatch')
    require(item.question_sha256==p.question_sha256 and item.reference.independent_target_fact_ids==p.independent_target_fact_ids,
            'e1a_reference_query_or_independent_targets_mismatch')
    ref=validate_human_reference(item.reference,contract=p.contract,trusted_batch=batch,expected_scope=expected_scope,
        expected_batch_sha256=p.prepared.batch_sha256,expected_context_policy='quote_full_review_veto',for_evaluation=True)
    errors=[]
    try:validate_observed_sources(p,sources,expected_sources_sha256=expected_sources_sha256)
    except (ValueError,TypeError):errors.append('source_validation_failed')
    fields=dict(query_id=batch.query_id,mode=batch.mode,contract_sha256=p.contract_sha256,
        observed_sources_sha256=expected_sources_sha256,input_sha256=expected_input_sha256,release_sha256=expected_release_sha256,
        experiment_id=p.experiment_id,contract_revision=p.contract_revision,
        reference_state=ref.reference_state,reference_licensed_ids=ref.supported_target_fact_ids,source_valid=not errors,
        fact_count=len(p.contract.required_facts.items),relation_count=len(relation_definitions(p.contract)))
    if output is None:
        return OutputAudit(**fields,output_sha256=None,execution='validation_failed' if errors else 'missing',errors=tuple(errors))
    try:out=load(output,PredictionOutput)
    except (ValueError,TypeError):
        return OutputAudit(**fields,output_sha256=None,execution='validation_failed',errors=tuple(errors+['unparseable_output']),unparseable_output=True)
    if (out.input_sha256,out.contract_sha256,out.query_id,out.mode)!=(expected_input_sha256,p.contract_sha256,batch.query_id,batch.mode):
        errors.append('output_identity_mismatch')
    hits={h.key:h for h in batch.candidates}
    def valid(w):
        h=hits.get(w.key)
        return bool(fields['source_valid'] and h and w.fact_id in {f.id for f in p.contract.required_facts.items} and
            all(getattr(w,k)==getattr(h,k) for k in ('query_id','mode','rank','review_id','annotation_id',
                'effective_snapshot_sha256','embedding_input_sha256','evidence_ref'))
            and h.quote_start<=w.quote_start<w.quote_end<=h.quote_end)
    witnesses={digest(w):w for w in [*out.evidence_refs,*[w for f in out.fact_findings for w in f.evidence_refs]]}
    valid_refs={key:valid(w) for key,w in witnesses.items()}
    if out.execution_status!='completed':
        if out.status is not None or out.supported_target_fact_ids or out.evidence_refs or out.fact_findings or out.relation_findings:
            errors.append('failed_output_has_normal_decision')
        return OutputAudit(**fields,output_sha256=digest(out),execution='validation_failed' if errors else out.execution_status,
            errors=tuple(errors),output_state=out.status,output_licensed_ids=out.supported_target_fact_ids,
            citation_count=len(witnesses),valid_citation_count=sum(valid_refs.values()))
    facts={f.fact_id:f for f in out.fact_findings}
    relations={r.relation_id:r for r in out.relation_findings};definitions=relation_definitions(p.contract)
    ids={f.id for f in p.contract.required_facts.items}
    if set(facts)!=ids or len(facts)!=len(out.fact_findings):errors.append('fact_coverage_invalid')
    if set(relations)!=set(definitions) or len(relations)!=len(out.relation_findings):errors.append('relation_coverage_invalid')
    for f in out.fact_findings:
        if f.support=='direct' and not f.evidence_refs:errors.append('direct_fact_without_citation')
        keys=[digest(w) for w in f.evidence_refs]
        if len(set(keys))!=len(keys):errors.append('duplicate_fact_citation')
        for w in f.evidence_refs:
            witnesses[digest(w)]=w
            if w.fact_id!=f.fact_id:errors.append('citation_fact_mismatch')
    for w in out.evidence_refs:witnesses[digest(w)]=w
    if not all(valid_refs.values()):errors.append('invalid_citation_identity_or_span')
    permitted=out.supported_target_fact_ids
    if len(permitted)!=len(set(permitted)) or not set(permitted)<=ids:errors.append('invalid_licensed_fact_ids')
    if len(out.missing_required_fact_ids)!=len(set(out.missing_required_fact_ids)) or not set(out.missing_required_fact_ids)<=ids:
        errors.append('invalid_missing_fact_ids')
    licensed_refs={digest(w) for f in out.fact_findings if f.fact_id in permitted for w in f.evidence_refs}
    if {digest(w) for w in out.evidence_refs}!=licensed_refs or len(out.evidence_refs)!=len({digest(w) for w in out.evidence_refs}):
        errors.append('licensed_citations_inconsistent')
    for rid,r in relations.items():
        if len(r.evidence_keys)!=len(set(r.evidence_keys)) or not set(r.evidence_keys)<=set(hits):errors.append('relation_source_invalid')
        if r.support!='supported' or rid not in definitions:continue
        kind,fids=definitions[rid]
        if not r.evidence_keys or not all(fid in facts and facts[fid].support=='direct' and
                any(w.key in r.evidence_keys for w in facts[fid].evidence_refs) for fid in fids):errors.append('relation_missing_direct_witnesses')
        if kind=='same_review' and len({(hits[k].source,hits[k].dataset_version,hits[k].product_id,hits[k].review_id)
                                        for k in r.evidence_keys if k in hits})!=1:errors.append('cross_review_relation')
    complete,direct,independent,missing,veto=decision_conditions(p,out.fact_findings,out.relation_findings)
    # R0's missing list is a condition diagnostic; veto does not invent absent facts.
    if set(out.missing_required_fact_ids)!=missing:errors.append('missing_fact_set_inconsistent')
    if not set(permitted)<=direct:errors.append('licensed_fact_not_direct')
    if out.status=='sufficient':
        if veto or not complete or not permitted or set(permitted)!=direct:errors.append('sufficient_inconsistent')
    elif out.status=='limited':
        if veto or complete or not permitted or set(permitted)!=independent:errors.append('limited_inconsistent')
    elif out.status=='insufficient':
        if permitted or (not veto and (complete or independent)):errors.append('insufficient_inconsistent')
    else:errors.append('completed_output_without_state')
    for obs in out.context_observations:
        h=hits.get(obs.key)
        if not h or not 0<=obs.quote_start<obs.quote_end<=len(h.review_text):errors.append('invalid_context_observation')
    # Exact human mappings are the independent semantic evidence, not R0 recomputation.
    reference_facts={f.fact_id:f for f in ref.fact_findings};reference_relations={r.relation_id:r for r in ref.relation_findings}
    semantic=[]
    for fid in permitted:
        f=facts.get(fid);human=reference_facts.get(fid);verdict=None
        if fid not in ref.supported_target_fact_ids:verdict=False
        elif f and human and f.evidence_refs:
            spans={digest(w) for w in human.evidence_refs}
            exact=all(digest(w) in spans and valid(w) for w in f.evidence_refs)
            relation_exact=all(rid in relations and relations[rid].support==r.support and set(relations[rid].evidence_keys)==set(r.evidence_keys)
                               for rid,r in reference_relations.items() if fid in definitions[rid][1])
            if exact and relation_exact and out.context_observations==ref.context_observations:verdict=True
        semantic.append(FactSupportAudit(fact_id=fid,supported=verdict))
    return OutputAudit(**fields,output_sha256=digest(out),execution='validation_failed' if errors else 'completed',
        errors=tuple(sorted(set(errors))),output_state=out.status,output_licensed_ids=permitted,
        citation_count=len(witnesses),valid_citation_count=sum(valid_refs.values()),
        fact_correct=sum(f.fact_id in reference_facts and f.support==reference_facts[f.fact_id].support for f in out.fact_findings),
        relation_correct=sum(r.relation_id in reference_relations and r.support==reference_relations[r.relation_id].support for r in out.relation_findings),
        licensed_set_agrees=set(permitted)==set(ref.supported_target_fact_ids),
        missing_set_agrees=set(out.missing_required_fact_ids)==set(ref.missing_required_fact_ids),
        unknown_fact_count=sum(f.support=='unknown' for f in out.fact_findings),
        unknown_relation_count=sum(r.support=='unknown' for r in out.relation_findings),fact_support=tuple(semantic))
