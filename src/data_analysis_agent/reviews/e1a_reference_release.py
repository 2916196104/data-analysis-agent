"""Event-derived, versioned reference publisher. Importing never publishes anything."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile
from typing import Literal
from pydantic import Field

from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1_review_protocol import HumanReference, validate_human_reference
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
from data_analysis_agent.reviews.human_adjudication import validate_session
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, QueryContract, ScopeRef
from data_analysis_agent.reviews.e1a_protocol import OperationAuthorization, authorize, digest


class ReleasedCase(ContractModel):
    case_id: Text
    question_sha256: Digest
    contract: QueryContract
    contract_revision: int = Field(ge=1)
    reference: HumanReference
    query_event_sha256: Digest
    case_event_sha256: Digest


class ReferenceRelease(ContractModel):
    version: Literal['e1a-reference-release-v1'] = 'e1a-reference-release-v1'
    release_id: Text
    published_at: Text
    protocol_version: Literal['e1a-v1'] = 'e1a-v1'
    source_policy: Literal['local_saved_sources_cross_time_v1'] = 'local_saved_sources_cross_time_v1'
    history_status: Literal['history_unproven'] = 'history_unproven'
    independent_seal: Literal[False] = False
    parent_release_sha256: Digest | None = None
    bundle_sha256: Digest
    session_sha256: Digest
    scope_ref: ScopeRef
    authorization: OperationAuthorization
    authorization_sha256: Digest
    completion_event_sha256: Digest
    observed_sources_sha256: Digest
    prior_relevance_exposure: Text
    reviewer_count: Literal[1] = 1
    references: tuple[ReleasedCase,...]


def build_reference_release(bundle, session, *, expected_bundle_sha256, expected_session_sha256,
                            authorization, expected_authorization_sha256, release_id, published_at):
    """New artifact only; all human content comes from already-confirmed events."""
    require(digest(bundle)==expected_bundle_sha256 and digest(session)==expected_session_sha256,'e1a_release_input_pin_mismatch')
    scope=load(bundle['scope_ref'],ScopeRef)
    auth=authorize(authorization,expected_sha256=expected_authorization_sha256,action='publish_reference',
        data_kind=bundle['data_kind'],scope=scope,artifact_sha256=expected_session_sha256)
    require(datetime.fromisoformat(published_at.replace('Z','+00:00')).utcoffset() is not None,'e1a_release_time_timezone')
    audit=validate_session(bundle,session)
    require(audit['ready_for_formal_reference_freeze'],'e1a_adjudication_not_complete')
    events={e['event_id']:e for e in session['events']};records=[]
    for item in audit['pending_references']:
        cid=item['case_id'];case=next(c for c in bundle['cases'] if c['case_id']==cid)
        query=session['queries'][case['query_id']];review=session['cases'][cid]['review']
        event=item['review_confirmation_record'];fields=dict(item['prepared_fields'])
        # Confirmation is derived, never copied from a user-editable true flag.
        fields.update(human_confirmed=True,contract_confirmed=True,review_status='confirmed',
            reviewer_decisions=[dict(reviewer_id=event['reviewer_id'],reference_state=review['reference_state'],
                supported_target_fact_ids=review['supported_target_fact_ids'],rationale=review['decision_rationale'])])
        batch=load(case['batch'],EvidenceBatch)
        ref=validate_human_reference(dict(version='e1-human-reference-v1',origin='independent_human_reference',**fields),
            contract=query['payload']['contract'],trusted_batch=batch,expected_scope=scope,
            expected_batch_sha256=case['batch_sha256'],expected_context_policy='quote_full_review_veto',for_evaluation=True)
        records.append(dict(case_id=cid,question_sha256=digest(case['question']),contract=query['payload']['contract'],contract_revision=query['revision'],
            reference=ref.model_dump(mode='json'),query_event_sha256=digest(item['contract_confirmation_record']),
            case_event_sha256=digest(event)))
    data=dict(release_id=release_id,published_at=published_at,bundle_sha256=expected_bundle_sha256,
        session_sha256=expected_session_sha256,scope_ref=bundle['scope_ref'],authorization=auth.model_dump(mode='json'),
        authorization_sha256=expected_authorization_sha256,completion_event_sha256=digest(events[session['completion_event_id']]),
        observed_sources_sha256=bundle['observed_sources_sha256'],
        prior_relevance_exposure=session['prior_relevance_exposure'],references=records)
    return load(data,ReferenceRelease)


def publish_reference_release(bundle, session, *, destination: Path, **options):
    """Stage beside an absent destination. Failed writes never create a ready marker."""
    release=build_reference_release(bundle,session,**options)
    destination=Path(destination)
    if release.authorization.data_kind=='frozen_query':
        private_root=Path(__file__).resolve().parents[3]/'outputs/rag-retrieval-quality'
        require(destination.resolve().is_relative_to(private_root.resolve()) and destination.resolve()!=private_root.resolve(),
                'e1a_real_release_requires_private_output_directory')
    require(not destination.exists(),'e1a_release_destination_exists')
    require(destination.parent.is_dir(),'e1a_release_parent_missing')
    staging=Path(tempfile.mkdtemp(prefix='.e1a-release-',dir=destination.parent))
    try:
        (staging/'reference-release.json').write_text(canonical_json(release.model_dump(mode='json'))+'\n',encoding='utf-8')
        (staging/'manifest.json').write_text(json.dumps(dict(release_sha256=digest(release),
            history_status='history_unproven',independent_seal=False),indent=2)+'\n',encoding='utf-8')
        require(not destination.exists(),'e1a_release_destination_exists')
        staging.rename(destination)
    finally:
        if staging.exists():
            require(staging.resolve().parent==destination.parent.resolve() and staging.name.startswith('.e1a-release-'),'e1a_invalid_staging_path')
            shutil.rmtree(staging)
    return release
