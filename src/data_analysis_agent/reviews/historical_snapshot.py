"""纯离线历史快照核验；不采集来源、不确认审批、不准备 E1 批次。"""

from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.opinions import Aspect, Sentiment
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, ScopeRef
from data_analysis_agent.reviews.rag_corpus import CORPUS_CONTRACT_VERSION, _review_state

PositiveId = Annotated[int, Field(ge=1)]


class SnapshotState(ContractModel):
    source: Text
    dataset_version: Text
    product_id: Text
    review_id: PositiveId
    review_content_sha256: Digest
    published_at: Text
    annotation_id: PositiveId | None
    human_revision_id: PositiveId | None
    processing_status: Literal['success', 'failed', 'pending', 'empty']
    human_status: Literal['unreviewed', 'confirmed', 'corrected']
    opinion_source: Literal['model', 'human'] | None
    error_code: Text | None
    opinion_count: Annotated[int, Field(ge=0)]


class SnapshotOpinion(ContractModel):
    model_opinion_id: PositiveId | None
    human_opinion_id: PositiveId | None
    aspect: Aspect
    sentiment: Sentiment
    quote_start: Annotated[int, Field(ge=0)]
    quote_end: PositiveId
    embedding_input_sha256: Digest


class SnapshotInput(ContractModel):
    version: Literal['rag-historical-snapshot-input-v1']
    data_kind: Literal['synthetic', 'frozen_query']
    scope_ref: ScopeRef
    basis: Literal['current_observation', 'historical_export']
    history_receipt_sha256: Digest | None
    label_profile_key: Digest
    corpus_contract_version: Literal['rag-effective-opinions-v1']
    collection_complete: bool
    state: SnapshotState
    review_text: str
    opinions: tuple[SnapshotOpinion, ...]


class SnapshotResult(ContractModel):
    version: Literal['rag-historical-snapshot-result-v1'] = 'rag-historical-snapshot-result-v1'
    execution_status: Literal['completed', 'validation_failed']
    classification: Literal['rebuilt_match', 'rebuilt_mismatch', 'history_unproven', 'missing_source', 'unverified']
    review_id: PositiveId
    computed_snapshot_sha256: Digest | None = None
    input_sha256: Digest | None = None
    opinion_count: int | None = None
    reason_codes: tuple[Text, ...]


def _parse(value, model):
    if isinstance(value, ContractModel):
        value = value.model_dump(mode='json')
    if isinstance(value, str):
        value = strict_json_loads(value)
    return model.model_validate_json(canonical_json(value))


def verify_historical_snapshot(value, *, expected_scope: ScopeRef, expected_review_id: int,
                               expected_snapshot_sha256: str, expected_data_kind: str,
                               expected_historical_input_sha256: str | None = None,
                               expected_history_receipt_sha256: str | None = None) -> SnapshotResult:
    """Pins must be fixed outside this operation, with historical provenance audited.

    A matching current observation is diagnostic only. Caller-supplied pins cannot
    prove personnel independence, source history, or completeness without that audit.
    """
    try:
        scope = _parse(expected_scope, ScopeRef)
        TypeAdapter(PositiveId).validate_python(expected_review_id, strict=True)
        TypeAdapter(Digest).validate_python(expected_snapshot_sha256)
        for pin in (expected_historical_input_sha256, expected_history_receipt_sha256):
            if pin is not None:
                TypeAdapter(Digest).validate_python(pin)
        if expected_data_kind not in ('synthetic', 'frozen_query'):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise ValueError('invalid_snapshot_trust_parameters') from None

    def result(classification, reason, *, failed=False, **fields):
        return SnapshotResult(review_id=expected_review_id, classification=classification,
            execution_status='validation_failed' if failed else 'completed', reason_codes=(reason,), **fields)

    if value is None:
        return result('missing_source', 'snapshot_source_absent')
    try:
        item = _parse(value, SnapshotInput)
    except (ValueError, TypeError, RecursionError):
        return result('unverified', 'missing_or_invalid_snapshot_input', failed=True)
    state = item.state
    if (item.scope_ref != scope or state.review_id != expected_review_id or item.data_kind != expected_data_kind
            or item.corpus_contract_version != CORPUS_CONTRACT_VERSION):
        return result('unverified', 'snapshot_identity_mismatch', failed=True)
    input_hash = content_sha256(item.model_dump(mode='json'))
    if (expected_historical_input_sha256 is not None and input_hash != expected_historical_input_sha256
            or expected_history_receipt_sha256 is not None and item.history_receipt_sha256 != expected_history_receipt_sha256):
        return result('unverified', 'historical_pin_conflict', failed=True, input_sha256=input_hash)
    if not item.collection_complete:
        return result('unverified', 'complete_collection_not_established', input_sha256=input_hash)
    if state.opinion_count != len(item.opinions):
        return result('missing_source', 'opinion_count_mismatch', failed=True, input_sha256=input_hash)
    try:
        if datetime.fromisoformat(state.published_at).utcoffset() is None:
            raise ValueError
        source = ('human' if state.human_revision_id is not None
                  else 'model' if state.processing_status == 'success' else None)
        if (state.opinion_source != source
                or (state.human_revision_id is None) != (state.human_status == 'unreviewed')
                or state.processing_status == 'success' and state.annotation_id is None
                or state.human_revision_id is not None and state.annotation_id is None
                or state.opinion_count and state.processing_status != 'success'):
            raise ValueError
        keys, identities = [], set()
        for opinion in item.opinions:
            if not (source == 'model' and opinion.model_opinion_id is not None and opinion.human_opinion_id is None
                    or source == 'human' and opinion.model_opinion_id is None and opinion.human_opinion_id is not None):
                raise ValueError
            identity = (source, opinion.model_opinion_id, opinion.human_opinion_id)
            if identity in identities:
                raise ValueError
            identities.add(identity)
            if not 0 <= opinion.quote_start < opinion.quote_end <= len(item.review_text):
                raise ValueError
            quote = item.review_text[opinion.quote_start:opinion.quote_end]
            # The independent metadata digest must describe the source quote, not a
            # digest filled by the caller from this same slice during verification.
            if not quote.strip() or sha256(quote.encode('utf-8')).hexdigest() != opinion.embedding_input_sha256:
                raise ValueError
            # Field order is the rag_corpus._document version-key contract.
            key = 'rag-opinion-v1:' + content_sha256([
                state.source, state.dataset_version, state.product_id, state.review_id,
                state.review_content_sha256, item.label_profile_key, state.annotation_id, state.human_revision_id,
                source, opinion.model_opinion_id, opinion.human_opinion_id, opinion.aspect, opinion.sentiment,
                opinion.quote_start, opinion.quote_end, opinion.embedding_input_sha256,
            ])
            keys.append(SimpleNamespace(stable_versioned_opinion_key=key))
        rebuilt = _review_state(state.model_dump(mode='json'), keys, item.label_profile_key)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return result('unverified', 'snapshot_state_or_opinion_integrity_error', failed=True, input_sha256=input_hash)
    fields = dict(computed_snapshot_sha256=rebuilt.effective_snapshot_sha256,
                  input_sha256=input_hash, opinion_count=rebuilt.opinion_count)
    if (item.basis != 'historical_export' or expected_historical_input_sha256 is None
            or expected_history_receipt_sha256 is None):
        return result('history_unproven', 'independently_fixed_historical_proof_required', **fields)
    matched = rebuilt.effective_snapshot_sha256 == expected_snapshot_sha256
    return result('rebuilt_match' if matched else 'rebuilt_mismatch',
                  'historical_snapshot_matches' if matched else 'historical_snapshot_conflict', **fields)
