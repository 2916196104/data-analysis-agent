"""Query embedding -> existing vector search -> independently hydrated current evidence.

No Agent registration, scoring, sync, model construction or network client. Repository
and encoder are injected; the production repository only opens existing PostgreSQL.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import zip_longest
import math
import re
from time import perf_counter
from typing import Literal, Protocol

from pydantic import ConfigDict, Field

from data_analysis_agent.core.database import PostgresConnection, is_postgres
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Input, Text
from data_analysis_agent.reviews.embedding import EmbeddingSpace
from data_analysis_agent.reviews.embedding_store import (
    SPACE, SPACE_ID, OpinionMatch, _search_effective_opinions as search_effective_opinions, validate_vector,
)
from data_analysis_agent.reviews.opinions import Aspect, Sentiment
from data_analysis_agent.reviews.parameters import AnalysisScope
from data_analysis_agent.reviews.rag_corpus import (
    TEXT_BUILDER_VERSION, RagCorpus, RagOpinionDocument, RagReviewState,
    _document, _review_state, read_effective_corpus,
)
from data_analysis_agent.reviews.scope import analysis_scope
from data_analysis_agent.reviews.store import connect_review_database


class SemanticRetrievalError(RuntimeError):
    """Stable safe codes; never expose a question, review, DSN or backend exception."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise SemanticRetrievalError(code)


class RetrievalScope(AnalysisScope):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    source: Text
    dataset_version: Text
    product_ids: tuple[Text, ...] = Field(min_length=1, max_length=1)
    timezone: Literal['UTC'] = 'UTC'

    def options(self):
        return self.model_dump(mode='json', exclude={'timezone'})


class SemanticQuery(Input):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    question: str = Field(min_length=1, max_length=4000, pattern=r'\S')
    top_k: int = Field(default=5, ge=1, le=100)
    aspect: Aspect | None = None
    sentiment: Sentiment | None = None


def _parse(value, model, code):
    try:
        raw = value.model_dump(mode='json') if isinstance(value, Input) else value
        if isinstance(raw, str): raw = strict_json_loads(raw)
        _require(type(raw) is dict, code)
        return model.model_validate_json(canonical_json(raw))
    except (ValueError, TypeError, UnicodeError):
        raise SemanticRetrievalError(code) from None


def _scope(value):
    scope = _parse(value, RetrievalScope, 'invalid_retrieval_scope')
    try:
        _, _, _, normalized = analysis_scope(**scope.options())
        return _parse(normalized, RetrievalScope, 'invalid_retrieval_scope')
    except (ValueError, TypeError):
        raise SemanticRetrievalError('invalid_retrieval_scope') from None


@dataclass(frozen=True)
class SnapshotInfo:
    transaction_snapshot: str
    observed_at: str
    isolation_level: Literal['repeatable read', 'serializable'] = 'repeatable read'
    read_only: bool = True


@dataclass(frozen=True)
class ReviewSource:
    """Repository-owned source, including ALL effective opinions of this review."""
    state: RagReviewState
    opinions: tuple[RagOpinionDocument, ...]
    review_text: str = field(repr=False)


@dataclass(frozen=True)
class SemanticEvidence:
    rank: int
    cosine_distance: float | None
    document: RagOpinionDocument
    review_state: RagReviewState
    review_text: str = field(repr=False)
    review_text_sha256: str
    hybrid_metadata: dict | None = None

    @property
    def key(self): return self.document.stable_versioned_opinion_key

    @property
    def effective_snapshot_sha256(self): return self.review_state.effective_snapshot_sha256

    def to_dict(self):
        # Do not publish the labeling endpoint or repeat embedding_input=quote.
        document = asdict(self.document)
        for name in ('base_url', 'embedding_input', 'stable_versioned_opinion_key'): document.pop(name)
        payload = dict(rank=self.rank, key=self.key, cosine_distance=self.cosine_distance, **document,
            effective_snapshot_sha256=self.effective_snapshot_sha256, review_state=asdict(self.review_state),
            review_text=self.review_text, review_text_sha256=self.review_text_sha256)
        if self.hybrid_metadata is not None:
            payload.update(self.hybrid_metadata)
        return payload


@dataclass(frozen=True)
class SemanticResult:
    query: SemanticQuery
    scope: RetrievalScope = field(repr=False)
    snapshot: SnapshotInfo
    evidence: tuple[SemanticEvidence, ...]
    embedding_space_id: str = SPACE_ID
    text_builder_version: str = TEXT_BUILDER_VERSION
    source_policy: str = 'current_effective_readonly_v1'
    context_policy: str = 'quote_full_review_veto'
    context_version: str = '1'
    _source_validation: object = field(default=None, repr=False, compare=False)
    vector_baseline: tuple[SemanticEvidence, ...] | None = None
    query_channels: tuple[tuple[str, tuple[str, ...]], ...] = ()
    hybrid_retrieval: dict | None = None

    def to_dict(self):
        data = dict(version='semantic-retrieval-v1', query=self.query.model_dump(mode='json'),
            scope=self.scope.model_dump(mode='json', exclude={'base_url'}),
            label_profile_key=content_sha256([self.scope.model,self.scope.base_url,self.scope.prompt_version]),
            snapshot=asdict(self.snapshot), embedding_space_id=self.embedding_space_id,
            text_builder_version=self.text_builder_version, source_policy=self.source_policy,
            context_policy=self.context_policy, context_version=self.context_version,
            evidence=[row.to_dict() for row in self.evidence])
        if self.vector_baseline is not None:
            data['version'] = 'semantic-retrieval-v2'
            data['vector_baseline'] = [row.to_dict() for row in self.vector_baseline]
            vector_ranks = {row.key: row.rank for row in self.vector_baseline}
            keyword_rank = 0
            for row in data['evidence']:
                if row['cosine_distance'] is None:
                    keyword_rank += 1
                    row.update(channel='keyword', channel_rank=keyword_rank)
                else:
                    row.update(channel='vector', channel_rank=vector_ranks[row['key']])
        if self.query_channels:
            data['version'] = 'semantic-retrieval-v3'
            data['query_channels'] = [dict(question=text, keys=list(keys)) for text,keys in self.query_channels]
        if self.hybrid_retrieval is not None:
            data['version'] = 'semantic-retrieval-v4'
            data['hybrid_retrieval'] = dict(self.hybrid_retrieval)
        return data


_SOURCE_VALIDATOR = object()


@dataclass(frozen=True)
class _SourceValidation:
    issuer: object
    result_sha256: str


def _result_integrity(result):
    return content_sha256([result.to_dict(), result.scope.model_dump(mode='json'),
                           [asdict(row.document) for row in result.evidence]])


def require_source_validated_result(result):
    """An in-memory P0 binding, not a historic seal or an assertion from JSON."""
    proof = getattr(result, '_source_validation', None)
    _require(isinstance(result, SemanticResult) and isinstance(proof, _SourceValidation)
             and proof.issuer is _SOURCE_VALIDATOR
             and proof.result_sha256 == _result_integrity(result), 'p0_validation_binding_missing_or_changed')
    return result


class QueryEncoder(Protocol):
    space: EmbeddingSpace
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class RetrievalSnapshot(Protocol):
    scope: RetrievalScope
    info: SnapshotInfo
    embedding_space_id: str
    text_builder_version: str
    def search(self, vector, *, top_k, aspect, sentiment) -> tuple[OpinionMatch, ...]: ...
    def load_sources(self, keys: tuple[str, ...]) -> tuple[ReviewSource, ...]: ...


class RetrievalRepository(Protocol):
    def snapshot(self, scope: RetrievalScope): ...


def _snapshot_identity(snapshot, scope, expected_info=None):
    _require(snapshot.scope == scope, 'repository_scope_mismatch')
    _require(snapshot.embedding_space_id == SPACE_ID, 'repository_embedding_space_mismatch')
    _require(snapshot.text_builder_version == TEXT_BUILDER_VERSION, 'repository_text_version_mismatch')
    info = snapshot.info
    _require(expected_info is None or info == expected_info, 'snapshot_identity_changed')
    _require(isinstance(info, SnapshotInfo) and info.read_only is True
             and info.isolation_level in ('repeatable read','serializable')
             and isinstance(info.transaction_snapshot,str) and bool(info.transaction_snapshot.strip()),
             'inconsistent_read_snapshot')
    try: valid_time = datetime.fromisoformat(info.observed_at).utcoffset() is not None
    except (ValueError, TypeError): valid_time = False
    _require(valid_time, 'invalid_snapshot_time')


def _in_scope(state, scope):
    if (state.source,state.dataset_version,state.product_id) != (scope.source,scope.dataset_version,scope.product_ids[0]): return False
    try:
        instant = datetime.fromisoformat(state.published_at)
        if instant.utcoffset() is None: return False
        instant = instant.astimezone(timezone.utc)
        start = datetime.fromisoformat(scope.date_from).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(scope.date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
        if not start <= instant < end: return False
        if scope.as_of:
            if len(scope.as_of) == 10:
                return instant < datetime.fromisoformat(scope.as_of).replace(tzinfo=timezone.utc)+timedelta(days=1)
            return instant <= datetime.fromisoformat(scope.as_of).astimezone(timezone.utc)
        return True
    except (ValueError, TypeError, OverflowError): return False


def _verified_sources(sources, keys, scope):
    identity = (scope.model,scope.base_url,scope.prompt_version); profile = content_sha256(list(identity))
    documents = {}; reviews = {}; wanted = set(keys)
    for source in sources:
        _require(isinstance(source,ReviewSource), 'invalid_review_source')
        state = source.state
        _require(isinstance(state,RagReviewState) and state.review_id not in reviews, 'duplicate_or_invalid_review_source')
        _require(type(state.review_id) is int and state.review_id>0 and type(state.opinion_count) is int
                 and state.human_status in ('unreviewed','confirmed','corrected'), 'invalid_current_review')
        _require((state.human_revision_id is None and state.human_status=='unreviewed') or
                 (state.human_revision_id is not None and state.human_status in ('confirmed','corrected')),
                 'source_human_revision_state_mismatch')
        _require(_in_scope(state,scope), 'source_scope_mismatch')
        _require(state.processing_status == 'success' and isinstance(source.review_text,str) and bool(source.review_text.strip()), 'invalid_current_review')
        _require(bool(source.opinions) and len({d.stable_versioned_opinion_key for d in source.opinions}) == len(source.opinions), 'incomplete_or_duplicate_source_opinions')
        _require(any(d.stable_versioned_opinion_key in wanted for d in source.opinions), 'unrequested_review_source')
        _require(len({(d.import_run_id,d.file_sha256,d.line_number) for d in source.opinions})==1,
                 'conflicting_review_import_identity')
        for doc in source.opinions:
            _require(isinstance(doc,RagOpinionDocument), 'invalid_source_document')
            _require((doc.source,doc.dataset_version,doc.product_id,doc.review_id,doc.annotation_id,doc.human_revision_id,
                      doc.opinion_source,doc.review_content_sha256,doc.published_at) ==
                     (state.source,state.dataset_version,state.product_id,state.review_id,state.annotation_id,state.human_revision_id,
                      state.opinion_source,state.review_content_sha256,state.published_at), 'source_review_version_mismatch')
            _require((doc.requested_model,doc.base_url,doc.prompt_version) == identity and doc.label_profile_key == profile,
                     'source_label_profile_mismatch')
            _require(doc.text_builder_version == TEXT_BUILDER_VERSION, 'source_text_version_mismatch')
            for name in ('review_id','annotation_id','import_run_id','line_number'):
                _require(type(getattr(doc,name)) is int and getattr(doc,name)>0, 'invalid_source_identity')
            for value in (doc.model_opinion_id,doc.human_opinion_id,doc.human_revision_id):
                _require(value is None or type(value) is int and value>0, 'invalid_source_identity')
            for value in (doc.review_content_sha256,doc.file_sha256):
                _require(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None, 'invalid_source_digest')
            try:
                rebuilt = _document(dict(**asdict(doc),processing_status=state.processing_status,review_text=source.review_text),identity,profile)
                _require(rebuilt == doc, 'source_document_hash_or_span_mismatch')
            except (ValueError, TypeError, UnicodeError):
                raise SemanticRetrievalError('source_document_hash_or_span_mismatch') from None
            _require(doc.stable_versioned_opinion_key not in documents, 'duplicate_source_key')
            documents[doc.stable_versioned_opinion_key] = (doc,source)
        _require(_review_state(asdict(state),source.opinions,profile) == state, 'effective_snapshot_mismatch')
        reviews[state.review_id] = state
    _require(wanted <= set(documents), 'missing_source')
    return documents


def query_channel_limits(top_k, channel_count):
    base, remainder = divmod(top_k, channel_count)
    return tuple(base + (index < remainder) for index in range(channel_count))


def merge_query_channels(channels, *, key):
    unique = {}
    for batch in zip_longest(*channels):
        for item in batch:
            if item is not None:
                unique.setdefault(key(item), item)
    return tuple(unique.values())


class SemanticRetrievalService:
    def __init__(self, *, encoder: QueryEncoder, repository: RetrievalRepository, scope):
        self._scope = _scope(scope)
        self.encoder, self.repository = encoder, repository
        self._prepared = None
        self.timings = {}

    @property
    def scope(self): return self._scope

    def _check_embedding_space(self):
        try:
            space = self.encoder.space
            _require(isinstance(space, EmbeddingSpace) and space.space_id == SPACE_ID,
                     'encoder_embedding_space_mismatch')
        except (AttributeError, ValueError, TypeError, UnicodeError):
            raise SemanticRetrievalError('encoder_embedding_space_mismatch') from None

    def _encode_query(self, text):
        return self._encode_queries((text,))[0]

    def _encode_queries(self, texts):
        for text in texts:
            try: text.encode('utf-8')
            except UnicodeError: raise SemanticRetrievalError('invalid_retrieval_request') from None
        self._check_embedding_space()
        try: vectors = self.encoder.embed_texts(list(texts))
        except Exception: raise SemanticRetrievalError('embedding_failed') from None
        try:
            _require(isinstance(vectors,(list,tuple)) and len(vectors)==len(texts), 'invalid_query_vector')
            validated = tuple(validate_vector(vector) for vector in vectors)
            self._check_embedding_space()
        except SemanticRetrievalError: raise
        except Exception: raise SemanticRetrievalError('invalid_query_vector') from None
        return validated

    def prepare(self, request, *, query_variants=()):
        self._prepared = None
        started = perf_counter()
        query = _parse(request,SemanticQuery,'invalid_retrieval_request')
        query_variants = tuple(query_variants)
        texts = (query.question, *query_variants)
        _require(len(texts) <= min(3, query.top_k) and all(isinstance(text, str) and text.strip() for text in texts)
                 and len(texts) == len(set(texts)), 'invalid_query_variants')
        if query_variants and getattr(self.encoder, 'supports_batch_encode', False):
            try:
                all_vectors = self._encode_queries(texts)
                vector, variants = all_vectors[0], all_vectors[1:]
            except SemanticRetrievalError:
                # An optional expansion failure leaves the original query usable.
                self._check_embedding_space()
                vector = self._encode_query(query.question)
                variants = ()
        else:
            vector = self._encode_query(query.question)
            variants = ()
            if query_variants:
                try:
                    variants = tuple(self._encode_query(text) for text in query_variants)
                except SemanticRetrievalError:
                    # An optional expansion failure leaves the original query usable.
                    self._check_embedding_space()
        self._prepared = (query, query_variants, (vector, *variants))
        self.timings = {'encoding_total_ms':(perf_counter()-started)*1000}
        return query

    def _hydrate(self, snapshot, scope, info, query, documents, distances, *, track_timing=False):
        keys = tuple(doc.stable_versioned_opinion_key for doc in documents)
        _require(len(keys) == len(set(keys)), 'duplicate_candidate_key')
        started = perf_counter()
        sources = snapshot.load_sources(keys) if keys else ()
        _snapshot_identity(snapshot, scope, info)
        if track_timing:
            self.timings['source_hydration_ms'] = (perf_counter() - started) * 1000
        started = perf_counter()
        verified = _verified_sources(sources, keys, scope)
        if track_timing:
            self.timings['source_verification_ms'] = (perf_counter() - started) * 1000
        rows = []
        for rank, (candidate, distance) in enumerate(zip(documents, distances, strict=True), 1):
            doc, source = verified[candidate.stable_versioned_opinion_key]
            _require(candidate == doc, 'candidate_source_version_mismatch')
            _require(query.aspect is None or doc.aspect == query.aspect, 'candidate_filter_mismatch')
            _require(query.sentiment is None or doc.sentiment == query.sentiment, 'candidate_filter_mismatch')
            rows.append(SemanticEvidence(rank, distance, doc, source.state, source.review_text,
                sha256(source.review_text.encode('utf-8')).hexdigest()))
        return tuple(rows)

    def search(
        self,
        request,
        *,
        keyword_fallback=None,
        query_variants=(),
        hybrid_bm25: bool = False,
        reranker=None,
        rrf_k: int = 60,
    ) -> SemanticResult:
        query = _parse(request,SemanticQuery,'invalid_retrieval_request')
        scope = self.scope
        query_variants = tuple(query_variants)
        hybrid_mode = bool(hybrid_bm25) or reranker is not None
        _require(not (query_variants and keyword_fallback), 'conflicting_retrieval_modes')
        _require(not (hybrid_mode and (query_variants or keyword_fallback)), 'conflicting_retrieval_modes')
        if hybrid_mode:
            _require(type(rrf_k) is int and 1 <= rrf_k <= 200, 'invalid_retrieval_request')
        if self._prepared is None:self.prepare(query, query_variants=query_variants)
        prepared,self._prepared = self._prepared,None
        _require(prepared[:2]==(query,query_variants),'prepared_query_mismatch')
        vectors = prepared[2]
        # No model work under a database transaction. No normal result on a failure.
        try:
            with self.repository.snapshot(scope) as snapshot:
                _snapshot_identity(snapshot,scope)
                info = snapshot.info
                started=perf_counter()
                if hybrid_mode:
                    from data_analysis_agent.reviews.hybrid_retrieval import (
                        BM25Index,
                        RerankItem,
                        reciprocal_rank_fusion,
                    )

                    recall_k = min(100, max(query.top_k * 3, 15))
                    vector_hits = tuple(
                        snapshot.search(vectors[0], top_k=recall_k, aspect=query.aspect, sentiment=query.sentiment)
                    )
                    _require(
                        len(vector_hits) <= recall_k and all(isinstance(hit, OpinionMatch) for hit in vector_hits),
                        'invalid_candidate_list',
                    )
                    _require(
                        all(type(hit.cosine_distance) in (int, float) and math.isfinite(hit.cosine_distance)
                            for hit in vector_hits),
                        'invalid_candidate_distance',
                    )
                    bm25_hits = ()
                    if hybrid_bm25:
                        bm25_started = perf_counter()
                        scoped_docs = tuple(
                            doc for doc in snapshot.corpus().documents
                            if (query.aspect is None or doc.aspect == query.aspect)
                            and (query.sentiment is None or doc.sentiment == query.sentiment)
                        )
                        bm25_hits = BM25Index(scoped_docs).search(query.question, top_k=recall_k)
                        self.timings['bm25_search_ms'] = (perf_counter() - bm25_started) * 1000
                    fusion_started = perf_counter()
                    fusion_limit = recall_k if reranker is not None else query.top_k
                    fused = reciprocal_rank_fusion(vector_hits, bm25_hits, k=rrf_k, top_k=fusion_limit)
                    self.timings['rrf_fusion_ms'] = (perf_counter() - fusion_started) * 1000
                    self.timings['search_including_corpus_ms'] = (perf_counter() - started) * 1000
                    _snapshot_identity(snapshot, scope, info)

                    hydrated = self._hydrate(
                        snapshot,
                        scope,
                        info,
                        query,
                        tuple(item.document for item in fused),
                        tuple(item.cosine_distance for item in fused),
                        track_timing=True,
                    )
                    meta_by_key = {
                        item.document.stable_versioned_opinion_key: {
                            'channel': item.channel,
                            'vector_rank': item.vector_rank,
                            'bm25_rank': item.bm25_rank,
                            'bm25_score': item.bm25_score,
                            'rrf_score': item.rrf_score,
                        }
                        for item in fused
                    }
                    evidence = tuple(
                        replace(row, hybrid_metadata=dict(meta_by_key[row.key]))
                        for row in hydrated
                    )
                    rerank_policy = None
                    if reranker is not None:
                        rerank_started = perf_counter()
                        rerank_policy = str(getattr(reranker, 'policy', '') or '').strip()
                        _require(bool(rerank_policy), 'invalid_reranker')
                        rerank_items = [
                            RerankItem(
                                key=row.key,
                                quote=row.document.quote,
                                review_text=row.review_text,
                                cosine_distance=row.cosine_distance,
                                bm25_score=meta_by_key[row.key]['bm25_score'],
                                rrf_score=meta_by_key[row.key]['rrf_score'],
                                channel=meta_by_key[row.key]['channel'],
                            )
                            for row in evidence
                        ]
                        try:
                            score_map = reranker.score(query.question, rerank_items)
                        except Exception:
                            raise SemanticRetrievalError('rerank_failed') from None
                        _require(
                            isinstance(score_map, dict)
                            and set(score_map) == {row.key for row in evidence}
                            and all(type(v) in (int, float) and math.isfinite(v) for v in score_map.values()),
                            'invalid_reranker_scores',
                        )
                        ordered = sorted(
                            evidence,
                            key=lambda row: (-float(score_map[row.key]), row.rank),
                        )[:query.top_k]
                        evidence = tuple(
                            replace(
                                row,
                                rank=rank,
                                hybrid_metadata={**row.hybrid_metadata, 'rerank_score': round(float(score_map[row.key]), 6)},
                            )
                            for rank, row in enumerate(ordered, 1)
                        )
                        self.timings['rerank_ms'] = (perf_counter() - rerank_started) * 1000
                    else:
                        evidence = tuple(
                            replace(row, rank=rank)
                            for rank, row in enumerate(evidence[:query.top_k], 1)
                        )
                    self.timings.update(getattr(snapshot, 'timings', {}))
                    hybrid_summary = {
                        'rrf_k': rrf_k,
                        'bm25_enabled': bool(hybrid_bm25),
                        'vector_keys': [hit.document.stable_versioned_opinion_key for hit in vector_hits],
                        'bm25_keys': [hit.document.stable_versioned_opinion_key for hit in bm25_hits],
                        'rerank_policy': rerank_policy,
                    }
                    result = SemanticResult(query, scope, info, evidence, hybrid_retrieval=hybrid_summary)
                    result = replace(result, _source_validation=_SourceValidation(
                        _SOURCE_VALIDATOR, _result_integrity(result)))
                    return result

                channels = []
                for vector, limit in zip(vectors, query_channel_limits(query.top_k,len(vectors)), strict=True):
                    hits = tuple(snapshot.search(vector,top_k=limit,aspect=query.aspect,sentiment=query.sentiment))
                    _require(len(hits)<=limit and all(isinstance(hit,OpinionMatch) for hit in hits), 'invalid_candidate_list')
                    _require(all(type(hit.cosine_distance) in (int,float) and math.isfinite(hit.cosine_distance)
                                 for hit in hits), 'invalid_candidate_distance')
                    channels.append(hits)
                query_channels = ()
                matches = channels[0]
                if len(channels)>1:
                    key = lambda hit: hit.document.stable_versioned_opinion_key
                    matches = merge_query_channels(channels,key=key)
                    query_channels = tuple((text,tuple(map(key,hits))) for text,hits in
                                           zip((query.question,*query_variants),channels,strict=True))
                self.timings['search_including_corpus_ms']=(perf_counter()-started)*1000
                _snapshot_identity(snapshot,scope,info)
                distances = tuple(match.cosine_distance for match in matches)
                evidence = self._hydrate(snapshot, scope, info, query, tuple(m.document for m in matches),
                                         distances, track_timing=True)
                vector_baseline = None
                if keyword_fallback is not None:
                    fallback_started = perf_counter()
                    documents = keyword_fallback(query, snapshot, evidence)
                    if documents:
                        _require(len(documents) <= query.top_k - bool(evidence), 'keyword_budget_exceeded')
                        keywords = self._hydrate(snapshot, scope, info, query, documents, (None,) * len(documents))
                        _require(not ({row.key for row in evidence} & {row.key for row in keywords}), 'duplicate_candidate_key')
                        vector_baseline = evidence
                        merged = (evidence[:1] + keywords + evidence[1:])[:query.top_k]
                        evidence = tuple(replace(row, rank=rank) for rank, row in enumerate(merged, 1))
                    self.timings['keyword_fallback_ms'] = (perf_counter() - fallback_started) * 1000
                self.timings.update(getattr(snapshot,'timings',{}))
                result = SemanticResult(query,scope,info,evidence,vector_baseline=vector_baseline,query_channels=query_channels)
                result = replace(result, _source_validation=_SourceValidation(
                    _SOURCE_VALIDATOR, _result_integrity(result)))
            return result
        except SemanticRetrievalError: raise
        except Exception: raise SemanticRetrievalError('repository_failed') from None


def load_review_contexts(connection, scope, review_ids):
    """One bounded raw-source query, independent of vector rows. Caller owns RR."""
    if not review_ids:return []
    placeholders=','.join('?' for _ in review_ids)
    return [dict(row) for row in connection.execute('''SELECT r.id review_id,r.source,r.dataset_version,
        r.product_id,r.content_sha256,r.published_at,r.import_run_id,r.line_number,r.text,i.file_sha256
        FROM reviews r JOIN import_runs i ON i.id=r.import_run_id
        WHERE r.source=? AND r.dataset_version=? AND r.product_id=?
        AND r.published_at::timestamptz>=?::date AND r.published_at::timestamptz<(?::date+interval '1 day')
        AND r.id IN ('''+placeholders+')',
        (scope.source,scope.dataset_version,scope.product_ids[0],scope.date_from,scope.date_to,*review_ids)).fetchall()]


class _PostgresSnapshot:
    embedding_space_id = SPACE_ID
    text_builder_version = TEXT_BUILDER_VERSION

    def __init__(self, connection, scope, info):
        self.connection,self.scope,self.info = connection,scope,info
        self._corpus = None
        self._partial_documents = {}
        self._partial_reviews = {}
        self._candidate_review_ids = {}
        self.active = True
        self.timings = {}

    def _verify_snapshot(self):
        _require(self.active and self.connection.in_transaction,'expired_retrieval_snapshot')
        row=self.connection.execute('SELECT txid_current_snapshot(), transaction_timestamp()').fetchone()
        observed=row[1].isoformat() if isinstance(row[1],datetime) else str(row[1])
        _require((str(row[0]),observed)==(self.info.transaction_snapshot,self.info.observed_at),
                 'snapshot_identity_changed')

    def _validate_corpus(self, corpus):
        _require(corpus.scope == analysis_scope(**self.scope.options())[3], 'repository_scope_mismatch')
        _require(corpus.label_profile_key == content_sha256([self.scope.model,self.scope.base_url,self.scope.prompt_version]),
                 'source_label_profile_mismatch')
        return corpus

    def corpus(self):
        self._verify_snapshot()
        if self._corpus is None:
            started=perf_counter()
            self._corpus = self._validate_corpus(read_effective_corpus(self.connection,**self.scope.options()))
            self.timings['effective_corpus_ms']=(perf_counter()-started)*1000
        return self._corpus

    def load_corpus_for_reviews(self, review_ids):
        """按候选 review_id 定向加载有效观点快照，避免每次检索扫描全商品语料。"""
        self._verify_snapshot()
        if self._corpus is not None:
            return self._corpus
        target_ids = sorted(set(review_ids))
        missing = tuple(rid for rid in target_ids if rid not in self._partial_reviews)
        if missing:
            started = perf_counter()
            subset = self._validate_corpus(
                read_effective_corpus(self.connection, **self.scope.options(), review_ids=missing)
            )
            self.timings['effective_corpus_ms'] = self.timings.get('effective_corpus_ms', 0.0) + (
                (perf_counter() - started) * 1000
            )
            for state in subset.reviews:
                self._partial_reviews[state.review_id] = state
            for doc in subset.documents:
                self._partial_documents[doc.stable_versioned_opinion_key] = doc
            # 若目标评论已删除或不在范围，也记入空标记避免重复查询。
            loaded_reviews = tuple(self._partial_reviews[rid] for rid in target_ids if rid in self._partial_reviews)
            loaded_docs = tuple(
                doc for doc in self._partial_documents.values() if doc.review_id in set(target_ids)
            )
            return RagCorpus(subset.scope, subset.label_profile_key, loaded_docs, loaded_reviews)
        scope_dict = analysis_scope(**self.scope.options())[3]
        profile_key = content_sha256([self.scope.model, self.scope.base_url, self.scope.prompt_version])
        target_set = set(target_ids)
        loaded_reviews = tuple(self._partial_reviews[rid] for rid in target_ids if rid in self._partial_reviews)
        loaded_docs = tuple(doc for doc in self._partial_documents.values() if doc.review_id in target_set)
        return RagCorpus(scope_dict, profile_key, loaded_docs, loaded_reviews)

    def search(self, vector, *, top_k, aspect, sentiment):
        self._verify_snapshot()
        started=perf_counter()
        try:
            matches = search_effective_opinions(
                self.connection,
                vector,
                options=self.scope.options(),
                embedding_space_id=SPACE_ID,
                top_k=top_k,
                aspect=aspect,
                sentiment=sentiment,
                corpus=self._corpus,
                corpus_loader=None if self._corpus is not None else self.load_corpus_for_reviews,
            )
            for match in matches:
                self._candidate_review_ids[match.document.stable_versioned_opinion_key] = match.document.review_id
            return matches
        finally:self.timings['pgvector_fetch_validate_ms']=(perf_counter()-started)*1000

    def load_sources(self, keys):
        # Sources are rebuilt from the DB, never from the search result's document.
        self._verify_snapshot()
        if self._corpus is not None:
            corpus = self._corpus
        elif keys and set(keys) <= set(self._partial_documents):
            ids = tuple(sorted({self._partial_documents[k].review_id for k in keys}))
            corpus = self.load_corpus_for_reviews(ids)
        elif keys and set(keys) <= set(self._candidate_review_ids):
            ids = tuple(sorted({self._candidate_review_ids[k] for k in keys}))
            corpus = self.load_corpus_for_reviews(ids)
        else:
            corpus = self.corpus()
        by_key = {d.stable_versioned_opinion_key:d for d in corpus.documents}
        _require(len(by_key)==len(corpus.documents) and set(keys)<=set(by_key), 'missing_source')
        ids = {by_key[k].review_id for k in keys}; sources=[]
        rows = load_review_contexts(self.connection,self.scope,sorted(ids))
        _require(len(rows)==len(ids) and {r['review_id'] for r in rows}==ids,'missing_review_context')
        contexts = {row['review_id']:row for row in rows}
        for state in corpus.reviews:
            if state.review_id not in ids: continue
            opinions = tuple(d for d in corpus.documents if d.review_id==state.review_id)
            row = contexts[state.review_id]
            for doc in opinions:
                _require(all(row[k]==getattr(doc,k) for k in ('source','dataset_version','product_id','review_id',
                    'published_at','file_sha256','line_number','import_run_id'))
                    and row['content_sha256']==doc.review_content_sha256,
                    'review_context_version_mismatch')
            sources.append(ReviewSource(state,opinions,row['text']))
        _require(len(sources)==len(ids), 'missing_review_context')
        return tuple(sources)


class PostgresSemanticRepository:
    """Own a readonly RR transaction, or reuse an already-readonly stable transaction."""
    def __init__(self, db):
        self.db = db

    @contextmanager
    def snapshot(self, scope):
        owned = not isinstance(self.db,PostgresConnection)
        _require(not owned or is_postgres(self.db), 'postgres_required')
        connection = connect_review_database(self.db,readonly=True) if owned else self.db
        try:
            _require(isinstance(connection,PostgresConnection), 'postgres_required')
            if owned: connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            _require(connection.in_transaction, 'stable_transaction_required')
            readonly = connection.execute('SHOW transaction_read_only').fetchone()[0]
            isolation = connection.execute('SHOW transaction_isolation').fetchone()[0]
            _require(readonly=='on' and isolation in ('repeatable read','serializable'), 'stable_readonly_transaction_required')
            row = connection.execute('SELECT txid_current_snapshot(), transaction_timestamp()').fetchone()
            observed = row[1].isoformat() if isinstance(row[1],datetime) else str(row[1])
            snapshot = _PostgresSnapshot(connection,scope,SnapshotInfo(str(row[0]),observed,isolation))
            try:yield snapshot
            finally:
                snapshot.active=False
                snapshot._corpus=None
                snapshot._partial_documents.clear()
                snapshot._partial_reviews.clear()
                snapshot._candidate_review_ids.clear()
        finally:
            if owned:
                try: connection.rollback()
                finally: connection.close()
