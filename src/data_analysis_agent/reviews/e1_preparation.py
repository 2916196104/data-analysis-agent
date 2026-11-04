"""4B-2b 纯离线准备：关联冻结列表与独立来源；不运行判断器或读取数据库。"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from pydantic import create_model

from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch, EvidenceCandidate, INPUT_VERSION, _validate_sources
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, ScopeRef
from data_analysis_agent.reviews.retrieval_evaluation import run_binding
from data_analysis_agent.reviews.scope import parse_day

# Reuse exactly the existing provenance field definitions, without query/ranking fields.
SourceOpinion = create_model('SourceOpinion', __base__=ContractModel, published_at=(Text, ...), **{
    name: (field.annotation, deepcopy(field)) for name, field in EvidenceCandidate.model_fields.items()
    if name not in ('query_id', 'mode', 'rank')
})


class PreparationError(ValueError):
    """固定错误码，不带原文、凭据或人工备注。"""


class SourceCollection(ContractModel):
    version: Literal['e1-source-collection-v1']
    data_kind: Literal['synthetic', 'frozen_query']
    basis: Literal['independent_source_snapshot']
    collector_id: Text
    collection_receipt_sha256: Digest
    scope_ref: ScopeRef
    inventory_snapshot_sha256: Digest
    records: tuple[SourceOpinion, ...]


class PreparedBatch(ContractModel):
    version: Literal['e1-prepared-batch-v1'] = 'e1-prepared-batch-v1'
    status: Literal['validated_against_pinned_sources'] = 'validated_against_pinned_sources'
    data_kind: Literal['synthetic', 'frozen_query']
    frozen_run_id: Digest
    source_collection_sha256: Digest
    collection_receipt_sha256: Digest
    batch_sha256: Digest
    trusted_batch: EvidenceBatch


def require(condition, code):
    if not condition:
        raise PreparationError(code)


def load(value, model):
    try:
        if isinstance(value, ContractModel):
            value = value.model_dump(mode='json', warnings=False)
        elif isinstance(value, str):
            value = strict_json_loads(value)
        return model.model_validate_json(canonical_json(value))
    except (ValueError, TypeError, RecursionError):
        raise PreparationError('invalid_preparation_structure') from None


def prepare_trusted_batch(frozen_run, source_collection, *, expected_scope: ScopeRef,
                          expected_source_collection_sha256: str, expected_receipt_sha256: str,
                          expected_data_kind: str, query_id: str, mode: str) -> PreparedBatch:
    """External pins must come from an independently approved collector/receipt.

    This function never infers missing source fields. Its hashes bind supplied artifacts;
    they cannot prove an operator's independence or replace a source-access authorization.
    """
    scope = load(expected_scope, ScopeRef)
    sources = load(source_collection, SourceCollection)
    require(sources.scope_ref == scope, 'source_collection_scope_mismatch')
    require(content_sha256(sources.model_dump(mode='json')) == expected_source_collection_sha256,
            'source_collection_pin_mismatch')
    require(sources.collection_receipt_sha256 == expected_receipt_sha256, 'source_receipt_pin_mismatch')
    require(expected_data_kind in ('synthetic', 'frozen_query') and sources.data_kind == expected_data_kind,
            'source_data_kind_mismatch')
    try:
        run = strict_json_loads(frozen_run) if isinstance(frozen_run, str) else deepcopy(frozen_run)
        require(type(run) is dict and run['run_id'] == scope.run_id == run_binding(run), 'frozen_run_mismatch')
        manifest = run['manifest']
        require(run['manifest_sha256'] == scope.manifest_sha256 == content_sha256(manifest), 'frozen_manifest_mismatch')
        require(run['scope_sha256'] == scope.scope_sha256, 'frozen_scope_mismatch')
        inventory = run['inventory']
        require(inventory['scope'] == manifest['scope'] and inventory['label_profile_key'] == manifest['label_profile_key']
                and inventory['text_builder_version'] == manifest['text_builder_version']
                and inventory['embedding_space_id'] == manifest['embedding_space_id'], 'frozen_inventory_identity_mismatch')
        require(sources.inventory_snapshot_sha256 == inventory['snapshot_sha256'] == manifest['inventory_snapshot_sha256'],
                'source_snapshot_mismatch')
        require(inventory['stored_count'] == inventory['valid_count'] and inventory['invalid_count'] == 0
                and run['authorized_snapshot_unchanged'] is True, 'frozen_inventory_invalid')
        query_ids = [q['id'] for q in manifest['queries']]
        require(len(query_ids) == len(set(query_ids)), 'duplicate_frozen_query')
        pairs = [(s['query_id'], s['mode']) for s in run['searches']]
        require(len(pairs) == len(set(pairs)) and set(pairs) == {
            (qid, m) for qid in query_ids for m in ('unfiltered', 'filtered')}, 'incomplete_frozen_lists')
        require((query_id, mode) in pairs, 'unknown_query_mode')
        search = next(s for s in run['searches'] if (s['query_id'], s['mode']) == (query_id, mode))
        query = next(q for q in manifest['queries'] if q['id'] == query_id)
        require(search['filters'] == (query['filters'] if mode == 'filtered' else {})
                and len(search['hits']) <= 5, 'frozen_filters_or_topk_mismatch')
        by_key = {record.key: record for record in sources.records}
        require(len(by_key) == len(sources.records), 'duplicate_source_key')
        frozen_keys = {h['key'] for s in run['searches'] for h in s['hits']}
        require(frozen_keys <= set(by_key), 'missing_independent_source_record')
        require(set(by_key) <= frozen_keys, 'unexpected_source_record')
        actual, trusted = [], []
        for hit in search['hits']:
            require(hit['key'] in by_key, 'missing_independent_source_record')
            record = by_key[hit['key']]
            require(record.source == manifest['scope']['source']
                    and record.dataset_version == manifest['scope']['dataset_version']
                    and record.product_id in manifest['scope']['product_ids']
                    and record.label_profile_key == manifest['label_profile_key']
                    and record.text_builder_version == manifest['text_builder_version'], 'source_range_identity_mismatch')
            published = datetime.fromisoformat(record.published_at)
            require(published.utcoffset() is not None, 'source_timestamp_timezone_required')
            require(parse_day(manifest['scope']['date_from'], 'date_from') <= published.astimezone(timezone.utc).date()
                    <= parse_day(manifest['scope']['date_to'], 'date_to'), 'source_date_out_of_range')
            provenance = record.model_dump(mode='json', exclude={'published_at'})
            for name in provenance.keys() & hit.keys():
                require(provenance[name] == hit[name], 'frozen_source_field_conflict')
            binding = dict(query_id=query_id, mode=mode, rank=hit['rank'])
            trusted.append({**provenance, **binding})
            # Runtime-side projection uses frozen fields wherever present. Missing fields
            # come exclusively from the pinned independent record, never from guesses.
            actual.append({**provenance, **{k:v for k,v in hit.items() if k in provenance}, **binding})
        batch_fields = dict(version=INPUT_VERSION, scope_ref=scope.model_dump(mode='json'), query_id=query_id, mode=mode)
        candidate_batch = load(dict(**batch_fields, candidates=actual), EvidenceBatch)
        trusted_batch = load(dict(**batch_fields, candidates=trusted), EvidenceBatch)
        _validate_sources(candidate_batch, trusted_batch, scope)
        return PreparedBatch(data_kind=sources.data_kind, frozen_run_id=scope.run_id,
            source_collection_sha256=expected_source_collection_sha256,
            collection_receipt_sha256=expected_receipt_sha256,
            batch_sha256=content_sha256(trusted_batch.model_dump(mode='json')), trusted_batch=trusted_batch)
    except PreparationError:
        raise
    except (KeyError, StopIteration, ValueError, TypeError, RecursionError):
        raise PreparationError('invalid_frozen_or_source_provenance') from None
