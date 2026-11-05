"""E1-A contracts. Labels live in reference artifacts, never in PredictionInput."""
from typing import Literal

from pydantic import Field

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1_review_protocol import FactFinding, RelationFinding, ContextObservation, State
from data_analysis_agent.reviews.evidence_judge import FactEvidence, JudgeResult, RULE_VERSION
from data_analysis_agent.reviews.exploratory_e1 import ExploratoryInput
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, FactId, QueryContract, ScopeRef, validate_query_contract

PROTOCOL = 'e1a-v1'
POLICY = 'local_saved_sources_cross_time_v1'


def digest(value):
    return content_sha256(value.model_dump(mode='json') if isinstance(value, ContractModel) else value)


class OperationAuthorization(ContractModel):
    action: Literal['publish_reference', 'predict_r0']
    data_kind: Literal['synthetic', 'frozen_query']
    scope_ref: ScopeRef
    artifact_sha256: Digest
    approval_ref: Text


def authorize(value, *, expected_sha256, action, data_kind, scope, artifact_sha256):
    """Caller pins a separate authorization record; no boolean self-approval."""
    a=load(value,OperationAuthorization)
    require(digest(a)==expected_sha256,'e1a_authorization_pin_mismatch')
    require((a.action,a.data_kind,a.scope_ref,a.artifact_sha256)==(action,data_kind,scope,artifact_sha256),
            'e1a_authorization_scope_or_action_mismatch')
    return a


class PredictionInput(ContractModel):
    version: Literal['e1a-prediction-input-v1'] = 'e1a-prediction-input-v1'
    protocol_version: Literal['e1a-v1'] = PROTOCOL
    experiment_id: Text
    data_kind: Literal['synthetic', 'frozen_query']
    engine_id: Literal['R0'] = 'R0'
    engine_version: Literal['explicit-clauses-zh-v1'] = RULE_VERSION
    question: Text
    question_sha256: Digest
    contract: QueryContract
    contract_sha256: Digest
    contract_revision: int = Field(ge=1)
    independent_target_fact_ids: tuple[FactId,...] = ()
    prepared: ExploratoryInput


def validate_prediction_input(value, *, expected_scope, expected_input_sha256):
    p=load(value,PredictionInput)
    require(digest(p)==expected_input_sha256,'e1a_input_pin_mismatch')
    c=validate_query_contract(p.contract.model_dump(mode='json'),expected_scope=expected_scope)
    require(p.prepared.candidate_batch.scope_ref==expected_scope,'e1a_scope_mismatch')
    require(p.question_sha256==content_sha256(p.question) and p.contract_sha256==digest(c),'e1a_query_contract_digest_mismatch')
    ids=p.independent_target_fact_ids
    require(len(ids)==len(set(ids)) and set(ids)<={f.id for f in c.required_facts.items},'e1a_independent_ids_invalid')
    require(p.prepared.batch_sha256==digest(p.prepared.candidate_batch),'e1a_batch_digest_mismatch')
    require(len(p.prepared.candidate_batch.candidates)<=5,'e1a_frozen_topk_exceeded')
    return p


class PredictionOutput(ContractModel):
    version: Literal['e1a-prediction-output-v1'] = 'e1a-prediction-output-v1'
    protocol_version: Literal['e1a-v1'] = PROTOCOL
    engine_id: Literal['R0'] = 'R0'
    engine_version: Literal['explicit-clauses-zh-v1'] = RULE_VERSION
    input_sha256: Digest
    contract_sha256: Digest
    query_id: Text
    mode: Literal['unfiltered','filtered']
    context_policy: Literal['quote_full_review_veto'] = 'quote_full_review_veto'
    context_version: Literal['1'] = '1'
    execution_status: Literal['completed','validation_failed','execution_failed']
    status: State | None = None
    fact_findings: tuple[FactFinding,...] = ()
    relation_findings: tuple[RelationFinding,...] = ()
    supported_target_fact_ids: tuple[FactId,...] = ()
    missing_required_fact_ids: tuple[FactId,...] = ()
    evidence_refs: tuple[FactEvidence,...] = ()
    context_observations: tuple[ContextObservation,...] = ()
    reason_codes: tuple[Text,...]
    raw_r0: JudgeResult | None = None


def relation_definitions(contract):
    definitions={f'relation_{i}':(r.kind,r.fact_ids) for i,r in enumerate(contract.required_facts.relations)}
    if contract.polarity_structure.contrast_fact_ids:
        definitions['contrast']=('contrast',contract.polarity_structure.contrast_fact_ids)
    return definitions


def decision_conditions(p, facts, relations):
    """Structural conditions plus the explicitly versioned R0 conservative policy."""
    c=p.contract;ids={f.id for f in c.required_facts.items}
    direct={f.fact_id for f in facts if f.support=='direct'}
    definitions=relation_definitions(c);rs={r.relation_id:r for r in relations}
    complete=bool(ids) and (ids<=direct if c.required_facts.operator=='all_of' else bool(direct))
    complete=complete and all(rid in rs and rs[rid].support=='supported' for rid in definitions)
    veto=bool(c.uncertainty or c.clarification) or c.task_type in ('statistics','clarify')
    # R0 treats every linked condition as part of the compound core, never a subanswer.
    linked={fid for _,fids in definitions.values() for fid in fids}
    independent=(set(p.independent_target_fact_ids)&direct)-linked
    if c.task_type=='mixed':complete=False
    missing=set() if complete and not veto and c.required_facts.operator=='any_of' else ids-direct
    return complete,direct,independent,missing,veto
