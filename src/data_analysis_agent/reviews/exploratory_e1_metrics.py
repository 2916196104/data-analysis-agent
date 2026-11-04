"""Future E1 aggregation of independently audited rows, not a judge or output auditor.

An upstream audit must bind confirmed human references, predictions and exact source
maps before constructing these rows. This module does not infer semantic support.
"""
from typing import Literal

from pydantic import Field

from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId

STATES = ('sufficient','limited','insufficient')


class LicensedFactAudit(ContractModel):
    fact_id: FactId
    supported_by_reference_and_citations: bool | None


class AuditedCase(ContractModel):
    version: Literal['e1-preaudited-case-v1']
    origin: Literal['independent_reference_and_prediction_audit']
    query_id: Text
    mode: Literal['unfiltered','filtered']
    contract_sha256: Digest
    observed_sources_sha256: Digest
    context_policy: Literal['quote_full_review_veto']
    context_version: Literal['1']
    audit_record_sha256: Digest
    source_valid: bool
    reference_confirmed: bool
    reference_state: Literal['sufficient','limited','insufficient'] | None
    reference_licensed_fact_ids: tuple[FactId,...] = ()
    output_execution: Literal['missing','completed','validation_failed','execution_failed']
    output_state: Literal['sufficient','limited','insufficient'] | None
    output_licensed_fact_ids: tuple[FactId,...] = ()
    licensed_fact_audits: tuple[LicensedFactAudit,...] = ()
    citation_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    unassessed_citation_count: int = Field(default=0,ge=0)
    semantic_coverage_unknown: bool


def ratio(numerator, denominator):
    return dict(numerator=numerator,denominator=denominator,value=numerator/denominator if denominator else None)


def summarize_e1(rows, *, expected_pairs, groups=None):
    """Conditional metrics retain all missing/error counts. Never executes a prediction.

    A supplied audit digest is an integrity reference, not proof of human approval.
    Exact-span semantic audits must be independently completed before this API is used.
    """
    expected=list(expected_pairs)
    require(len(expected)==len(set(expected)), 'duplicate_expected_pair')
    require(all(m in ('unfiltered','filtered') for _,m in expected), 'invalid_expected_mode')
    expected=set(expected)
    data=[load(v,AuditedCase) for v in rows]
    by_pair={(r.query_id,r.mode):r for r in data}
    require(len(by_pair)==len(data) and set(by_pair)<=expected, 'unexpected_or_duplicate_audited_pair')
    for r in data:
        require(r.valid_citation_count+r.unassessed_citation_count<=r.citation_count,'invalid_citation_counts')
        require(r.source_valid or r.valid_citation_count == 0,'source_failure_cannot_have_valid_citations')
        for values in (r.reference_licensed_fact_ids,r.output_licensed_fact_ids):
            require(len(values)==len(set(values)),'duplicate_licensed_fact')
        if r.reference_confirmed:
            require(r.reference_state in STATES and (bool(r.reference_licensed_fact_ids)==(r.reference_state!='insufficient')),
                    'inconsistent_reference_permissions')
        if r.output_execution=='completed':
            require(r.output_state in STATES and (bool(r.output_licensed_fact_ids)==(r.output_state!='insufficient')),
                    'inconsistent_output_permissions')
        else:
            require(r.output_state is None and not r.output_licensed_fact_ids and not r.licensed_fact_audits,
                    'failed_output_has_normal_permissions')
        audits={a.fact_id:a for a in r.licensed_fact_audits}
        require(len(audits)==len(r.licensed_fact_audits) and set(audits)<=set(r.output_licensed_fact_ids),
                'invalid_licensed_fact_audit')
        require(all(a.supported_by_reference_and_citations is not True or
                    r.reference_confirmed and a.fact_id in r.reference_licensed_fact_ids and r.valid_citation_count > 0
                    for a in r.licensed_fact_audits), 'positive_audit_requires_human_permission_and_valid_citation')

    def aggregate(selected):
        available=[by_pair[p] for p in selected if p in by_pair]
        human=[r for r in available if r.source_valid and r.reference_confirmed]
        valid=[r for r in human if r.output_execution=='completed']
        unsupported=[r for r in valid if not r.reference_licensed_fact_ids]
        answerable=[r for r in valid if r.reference_licensed_fact_ids]
        licensed=[r for r in valid if r.output_licensed_fact_ids]
        matrix={s:{t:0 for t in STATES} for s in STATES}
        supported=assessed=total_licensed=0
        for r in valid:
            matrix[r.reference_state][r.output_state]+=1
            total_licensed+=len(r.output_licensed_fact_ids)
            for a in r.licensed_fact_audits:
                if a.supported_by_reference_and_citations is not None:
                    assessed+=1
                    supported+=int(a.supported_by_reference_and_citations)
        support_rate=ratio(supported,total_licensed)
        if assessed != total_licensed:
            support_rate['value']=None
        citations=sum(r.citation_count for r in available)
        unassessed_citations=sum(r.unassessed_citation_count for r in available)
        citation_rate=ratio(sum(r.valid_citation_count for r in available),citations)
        if unassessed_citations:
            citation_rate['value']=None
        return dict(expected=len(selected),available=len(available),confirmed_source_valid_references=len(human),
            valid_comparisons=len(valid),missing_rows=len(selected)-len(available),
            source_failures=sum(not r.source_valid for r in available),
            missing_references=sum(not r.reference_confirmed for r in available),
            missing_outputs=sum(r.output_execution=='missing' for r in available),
            validation_failures=sum(r.output_execution=='validation_failed' for r in available),
            execution_failures=sum(r.output_execution=='execution_failed' for r in available),
            unsupported_answer_permission_rate=ratio(sum(bool(r.output_licensed_fact_ids) for r in unsupported),len(unsupported)),
            over_refusal_rate=ratio(sum(r.output_state=='insufficient' for r in answerable),len(answerable)),
            state_agreement=ratio(sum(r.reference_state==r.output_state for r in valid),len(valid)),
            confusion_matrix=matrix,
            reference_state_denominators={s:sum(r.reference_state==s for r in valid) for s in STATES},
            licensed_fact_support_rate=support_rate,assessed_fact_support_rate=ratio(supported,assessed),
            licensed_fact_audit_coverage=ratio(assessed,total_licensed),licensed_fact_count=total_licensed,
            unassessed_licensed_fact_count=total_licensed-assessed,
            citation_validity_rate=citation_rate,unassessed_citation_count=unassessed_citations,
            citation_audit_coverage=ratio(citations-unassessed_citations,citations),
            coverage_unknown_comparisons=sum(r.semantic_coverage_unknown for r in valid),
            coverage_unknown_over_refusals=sum(r.semantic_coverage_unknown and r.output_state=='insufficient' for r in answerable),
            state_agree_but_coverage_unknown=sum(r.semantic_coverage_unknown and r.reference_state==r.output_state for r in valid),
            output_permission_lists=len(licensed))

    per_pair={}
    for pair in expected:
        row=aggregate({pair})
        per_pair[pair]=row
    pairings=[]
    for qid in sorted({q for q,_ in expected}):
        u,f=by_pair.get((qid,'unfiltered')),by_pair.get((qid,'filtered'))
        reason=None
        if u is None or f is None:
            reason='missing_pair'
        elif u.contract_sha256!=f.contract_sha256 or u.observed_sources_sha256!=f.observed_sources_sha256:
            reason='pair_input_identity_mismatch'
        metrics={}
        for name in ('unsupported_answer_permission_rate','over_refusal_rate','state_agreement',
                     'licensed_fact_support_rate','citation_validity_rate'):
            a=per_pair.get((qid,'unfiltered'),{}).get(name,{}).get('value')
            b=per_pair.get((qid,'filtered'),{}).get(name,{}).get('value')
            metrics[name]=dict(filtered_minus_unfiltered=b-a if reason is None and a is not None and b is not None else None,
                exclusion_reason=reason or ('metric_denominator_not_available' if a is None or b is None else None))
        pairings.append(dict(query_id=qid,metrics=metrics))
    paired={}
    for name in pairings[0]['metrics'] if pairings else ():
        values=[p['metrics'][name]['filtered_minus_unfiltered'] for p in pairings
                if p['metrics'][name]['filtered_minus_unfiltered'] is not None]
        paired[name]=dict(paired_query_denominator=len(values),filtered_minus_unfiltered=sum(values)/len(values) if values else None)
    return dict(version='e1-exploratory-metrics-v1',overall=aggregate(expected),
        by_mode={m:aggregate({p for p in expected if p[1]==m}) for m in ('unfiltered','filtered')},
        by_group={g:aggregate({p for p in expected if p[0] in ids}) for g,ids in (groups or {}).items()},
        query_mode_details=[dict(query_id=q,mode=m,**per_pair[q,m]) for q,m in sorted(expected)],
        paired=paired,paired_query_details=pairings)
