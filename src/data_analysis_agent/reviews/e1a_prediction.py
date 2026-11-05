"""Pure R0 exploratory adapter; no reference access, filesystem, DB or model client."""
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.e1_preparation import load, require, PreparationError
from data_analysis_agent.reviews.e1_review_protocol import review_context_view
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch, SourceValidationError, _validate_sources, _judge, _input_digest
from data_analysis_agent.reviews.exploratory_e1 import ExploratorySources
from data_analysis_agent.reviews.e1a_protocol import (
    PredictionInput, PredictionOutput, authorize, digest, validate_prediction_input, relation_definitions,
)


def build_prediction_input(*, question, contract, contract_revision, independent_target_fact_ids,
                           prepared, experiment_id, data_kind, expected_scope,
                           expected_question_sha256, expected_contract_sha256, expected_batch_sha256):
    """Allowlisted arguments only; callers cannot pass a session/bundle or human labels."""
    p=load(dict(experiment_id=experiment_id,data_kind=data_kind,question=question,
        question_sha256=expected_question_sha256,contract=contract.model_dump(mode='json') if hasattr(contract,'model_dump') else contract,contract_sha256=expected_contract_sha256,
        contract_revision=contract_revision,independent_target_fact_ids=independent_target_fact_ids,
        prepared=prepared.model_dump(mode='json') if hasattr(prepared,'model_dump') else prepared),PredictionInput)
    require(p.prepared.batch_sha256==expected_batch_sha256,'e1a_expected_batch_mismatch')
    return validate_prediction_input(p,expected_scope=expected_scope,expected_input_sha256=digest(p))


def validate_observed_sources(p, sources, *, expected_sources_sha256):
    """Rebuild the comparison from saved SQL-source records, not the candidate batch."""
    sources=load(sources,ExploratorySources)
    require(digest(sources)==expected_sources_sha256==p.prepared.observed_sources_sha256,'e1a_observed_source_pin_mismatch')
    require(sources.scope_ref==p.contract.scope_ref and sources.data_kind==p.data_kind,'e1a_source_scope_or_kind_mismatch')
    stamps=sources.observations
    require(len(stamps)==2 and {s.role for s in stamps}=={'row_sources','complete_metadata'},'e1a_observation_stamps_invalid')
    from datetime import datetime
    for stamp in stamps:require(datetime.fromisoformat(stamp.observed_at).utcoffset() is not None,'e1a_observation_timezone')
    rows={r.key:r for r in sources.records};snapshots={s.review_id:s for s in sources.snapshots}
    require(len(rows)==len(sources.records) and len(snapshots)==len(sources.snapshots),'e1a_duplicate_sources')
    require(set(snapshots)=={r.review_id for r in rows.values()},'e1a_source_snapshot_coverage')
    for r in rows.values():
        require(datetime.fromisoformat(r.published_at).utcoffset() is not None,'e1a_source_timezone')
        s=snapshots[r.review_id]
        require(s.computed_snapshot_sha256==s.frozen_snapshot_sha256==r.effective_snapshot_sha256,'e1a_observed_snapshot_conflict')
    batch=p.prepared.candidate_batch
    records=[]
    for h in batch.candidates:
        require(h.key in rows,'e1a_missing_source')
        records.append(dict(**rows[h.key].model_dump(mode='json',exclude={'published_at'}),
                            query_id=batch.query_id,mode=batch.mode,rank=h.rank))
    comparison=load(dict(version=batch.version,scope_ref=batch.scope_ref.model_dump(mode='json'),
        query_id=batch.query_id,mode=batch.mode,candidates=records),EvidenceBatch)
    _validate_sources(batch,comparison,p.contract.scope_ref)
    require(p.prepared.context_input_sha256==content_sha256(review_context_view(batch,'quote_full_review_veto')),
            'e1a_context_digest_mismatch')
    return sources


def run_r0(value, sources, *, expected_scope, expected_input_sha256, expected_sources_sha256,
           authorization, expected_authorization_sha256):
    p=validate_prediction_input(value,expected_scope=expected_scope,expected_input_sha256=expected_input_sha256)
    authorize(authorization,expected_sha256=expected_authorization_sha256,action='predict_r0',
        data_kind=p.data_kind,scope=expected_scope,artifact_sha256=expected_input_sha256)
    batch=p.prepared.candidate_batch
    fields=dict(input_sha256=expected_input_sha256,contract_sha256=p.contract_sha256,query_id=batch.query_id,mode=batch.mode)
    try:validate_observed_sources(p,sources,expected_sources_sha256=expected_sources_sha256)
    except (ValueError,TypeError,PreparationError,SourceValidationError):
        return PredictionOutput(**fields,execution_status='validation_failed',reason_codes=('source_validation_failed',))
    return _predict_r0_validated(p)


def _predict_r0_validated(p):
    """Same R0 projection, called only after the entry point's source checks."""
    batch=p.prepared.candidate_batch
    fields=dict(input_sha256=digest(p),contract_sha256=p.contract_sha256,query_id=batch.query_id,mode=batch.mode)
    try:
        raw=_judge(p.contract,batch,p.independent_target_fact_ids,
                   _input_digest(p.contract,batch,p.independent_target_fact_ids))
        evidence={f.id:[w for w in raw.condition_evidence_refs if w.fact_id==f.id] for f in p.contract.required_facts.items}
        facts=[dict(fact_id=f.id,support='direct' if evidence[f.id] else 'unknown',
                    evidence_refs=[w.model_dump(mode='json') for w in evidence[f.id]],
                    rationale='R0 explicit clause match' if evidence[f.id] else 'R0 did not establish this fact; not proof of absence')
               for f in p.contract.required_facts.items]
        relations=[]
        for rid,(_,ids) in relation_definitions(p.contract).items():
            supported=rid not in raw.missing_relation_ids
            relations.append(dict(relation_id=rid,support='supported' if supported else 'unknown',
                evidence_keys=list(dict.fromkeys(w.key for fid in ids for w in evidence[fid])) if supported else [],
                rationale='R0 joint witness assignment' if supported else 'R0 relation not established'))
        return load(dict(**fields,execution_status='completed',status=raw.status,fact_findings=facts,
            relation_findings=relations,supported_target_fact_ids=raw.supported_target_fact_ids,
            missing_required_fact_ids=raw.missing_required_fact_ids,evidence_refs=[w.model_dump(mode='json') for w in raw.evidence_refs],
            reason_codes=raw.reason_codes,raw_r0=raw.model_dump(mode='json')),PredictionOutput)
    except Exception:
        return PredictionOutput(**fields,execution_status='execution_failed',reason_codes=('r0_execution_failed',))
