"""Aggregate pinned independent audits, including every expected query-mode row."""
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1a_output_audit import OutputAudit
from data_analysis_agent.reviews.e1a_protocol import digest
from data_analysis_agent.reviews.exploratory_e1_metrics import ratio, STATES


def evaluate_e1a(rows, *, expected_pairs, expected_audit_sha256, groups=None):
    expected=list(expected_pairs)
    require(len(expected)==len(set(expected)) and all(m in ('filtered','unfiltered') for _,m in expected),'e1a_expected_pairs_invalid')
    data=[load(r,OutputAudit) for r in rows];by_pair={(r.query_id,r.mode):r for r in data}
    require(len(by_pair)==len(data) and set(by_pair)<=set(expected),'e1a_audit_coverage_invalid')
    require(set(expected_audit_sha256)==set(by_pair),'e1a_audit_pin_coverage_invalid')
    for pair,r in by_pair.items():
        require(digest(r)==expected_audit_sha256[pair],'e1a_audit_pin_mismatch')
        require(0<=r.valid_citation_count<=r.citation_count and 0<=r.fact_correct<=r.fact_count
                and 0<=r.relation_correct<=r.relation_count,'e1a_audit_counts_invalid')
        require(not r.reference_licensed_ids if r.reference_state=='insufficient' else bool(r.reference_licensed_ids),'e1a_reference_permissions_invalid')
        if r.execution=='completed':
            require(r.source_valid and not r.errors and r.output_state in STATES,'e1a_completed_audit_invalid')
            require((r.output_state!='insufficient')==bool(r.output_licensed_ids),'e1a_output_permissions_invalid')
        require(len({f.fact_id for f in r.fact_support})==len(r.fact_support)
                and {f.fact_id for f in r.fact_support}<=set(r.output_licensed_ids),'e1a_fact_audit_ids_invalid')
    def aggregate(pairs):
        available=[by_pair[p] for p in pairs if p in by_pair]
        human=[r for r in available if r.source_valid]
        valid=[r for r in human if r.execution=='completed']
        hp=[r for r in human if r.reference_licensed_ids];hn=[r for r in human if not r.reference_licensed_ids]
        vp=[r for r in valid if r.reference_licensed_ids];vn=[r for r in valid if not r.reference_licensed_ids]
        correct=sum(r.output_state==r.reference_state for r in valid)
        licensed=sum(bool(r.output_licensed_ids) for r in vp)
        matrix={s:{t:0 for t in STATES} for s in STATES}
        for r in valid:matrix[r.reference_state][r.output_state]+=1
        total=sum(len(r.output_licensed_ids) for r in valid)
        assessed=sum(f.supported is not None for r in valid for f in r.fact_support)
        supported=sum(f.supported is True for r in valid for f in r.fact_support)
        support=ratio(supported,total)
        if assessed!=total:support['value']=None
        citations=ratio(sum(r.valid_citation_count for r in available),sum(r.citation_count for r in available))
        if any(r.unparseable_output for r in available):citations['value']=None
        return dict(expected=len(pairs),available=len(available),human_source_policy_valid=len(human),valid_comparisons=len(valid),
            missing_audits=len(pairs)-len(available),source_failures=sum(not r.source_valid for r in available),
            missing_outputs=sum(r.execution=='missing' for r in available),
            execution_failures=sum(r.execution=='execution_failed' for r in available),
            validation_failures=sum(r.execution=='validation_failed' for r in available),
            unparseable_outputs=sum(r.unparseable_output for r in available),
            state_agreement=ratio(correct,len(valid)),full_reference_success=ratio(correct,len(human)),confusion_matrix=matrix,
            reference_state_denominators={s:sum(r.reference_state==s for r in valid) for s in STATES},
            state_recall={s:ratio(matrix[s][s],sum(matrix[s].values())) for s in STATES},
            evidence_permission_rate=ratio(licensed,len(vp)),end_to_end_permission_coverage=ratio(licensed,len(hp)),
            over_refusal_rate=ratio(sum(r.output_state=='insufficient' for r in vp),len(vp)),
            answerable_unserved_rate=ratio(len(hp)-licensed,len(hp)),
            unsupported_permission_rate=ratio(sum(bool(r.output_licensed_ids) for r in vn),len(vn)),
            raw_unsupported_permission_attempts=sum(bool(r.output_licensed_ids) for r in hn),
            unresolved_no_support_outputs=sum(r.execution!='completed' for r in hn),
            citation_validity_rate=citations,
            licensed_fact_support_rate=support,assessed_fact_support_rate=ratio(supported,assessed),
            licensed_fact_audit_coverage=ratio(assessed,total),unassessed_licensed_facts=total-assessed,
            fact_agreement=ratio(sum(r.fact_correct for r in valid),sum(r.fact_count for r in valid)),
            relation_agreement=ratio(sum(r.relation_correct for r in valid),sum(r.relation_count for r in valid)),
            fact_evaluation_coverage=ratio(sum(r.fact_count for r in valid),sum(r.fact_count for r in human)),
            relation_evaluation_coverage=ratio(sum(r.relation_count for r in valid),sum(r.relation_count for r in human)),
            licensed_set_agreement=ratio(sum(r.licensed_set_agrees for r in valid),len(valid)),
            missing_set_agreement=ratio(sum(r.missing_set_agrees for r in valid),len(valid)),
            coverage_unknown_lists=sum(bool(r.unknown_fact_count or r.unknown_relation_count) for r in valid),
            coverage_unknown_over_refusals=sum(bool(r.unknown_fact_count or r.unknown_relation_count) and r.output_state=='insufficient' for r in vp),
            state_agree_but_coverage_unknown=sum(bool(r.unknown_fact_count or r.unknown_relation_count) and r.output_state==r.reference_state for r in valid),
            unknown_fact_count=sum(r.unknown_fact_count for r in valid),unknown_relation_count=sum(r.unknown_relation_count for r in valid))
    names=('state_agreement','evidence_permission_rate','over_refusal_rate','unsupported_permission_rate',
           'fact_agreement','relation_agreement','citation_validity_rate','licensed_fact_support_rate')
    details=[]
    for qid in sorted({q for q,_ in expected}):
        u,f=by_pair.get((qid,'unfiltered')),by_pair.get((qid,'filtered'));reason=None
        if u is None or f is None:reason='missing_audit_pair'
        elif any(getattr(u,k)!=getattr(f,k) for k in ('contract_sha256','contract_revision','observed_sources_sha256','release_sha256',
                    'experiment_id','engine_id','engine_version','context_policy','context_version')):
            reason='pair_identity_mismatch'
        elif not u.source_valid or not f.source_valid or u.execution!='completed' or f.execution!='completed':reason='pair_not_completed'
        metrics={}
        for name in names:
            a=aggregate([(qid,'unfiltered')])[name]['value'] if (qid,'unfiltered') in expected else None
            b=aggregate([(qid,'filtered')])[name]['value'] if (qid,'filtered') in expected else None
            metrics[name]=dict(filtered_minus_unfiltered=b-a if reason is None and a is not None and b is not None else None,
                exclusion_reason=reason or ('metric_denominator_not_available' if a is None or b is None else None))
        details.append(dict(query_id=qid,metrics=metrics))
    paired={}
    for name in names:
        values=[r['metrics'][name]['filtered_minus_unfiltered'] for r in details if r['metrics'][name]['filtered_minus_unfiltered'] is not None]
        paired[name]=dict(paired_query_denominator=len(values),filtered_minus_unfiltered=sum(values)/len(values) if values else None)
    return dict(version='e1a-evaluation-v1',source_policy='local_saved_sources_cross_time_v1',history_status='history_unproven',
        independent_seal=False,quality_thresholds_approved=False,overall=aggregate(expected),
        by_mode={m:aggregate([p for p in expected if p[1]==m]) for m in ('unfiltered','filtered')},
        by_group={g:aggregate([p for p in expected if p[0] in ids]) for g,ids in (groups or {}).items()},
        case_ledger=[dict(query_id=q,mode=m,terminal=('missing_audit' if (q,m) not in by_pair else
            'source_validation_failed' if not by_pair[q,m].source_valid else by_pair[q,m].execution)) for q,m in expected],
        paired=paired,paired_query_details=details)
