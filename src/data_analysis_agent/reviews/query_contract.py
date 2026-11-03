"""独立查询契约与开发者人工回放校验；不解析自然语言、不执行检索。

调用方必须从可信冻结配置提供 expected_scope，不能从待校验输入反推它。
公开校验函数是接收外部数据的边界；模型本身只描述可序列化的结构。
"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Input, Text
from data_analysis_agent.reviews.opinions import Aspect, Sentiment

CONTRACT_VERSION = 'rag-query-contract-v1'
REPLAY_VERSION = 'rag-query-replay-v1'
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
FactId = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
TaskType = Literal['statistics', 'evidence', 'mixed', 'clarify']
SentimentIntent = Sentiment | Literal['mixed']
DataKind = Literal['synthetic', 'frozen_query']


class ContractError(ValueError):
    """固定错误码，不回显问题、人工理由或未验证的输入。"""


class ContractModel(Input):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)


class ScopeRef(ContractModel):
    """只引用已冻结的范围、运行及问题集；不携带可被解析器修改的范围参数。"""
    run_id: Digest
    manifest_sha256: Digest
    scope_sha256: Digest


class AspectCandidate(ContractModel):
    aspect: Aspect
    rationale: Text


class RequiredFact(ContractModel):
    id: FactId
    description: Text
    sentiment: Sentiment | None = None


class FactRelation(ContractModel):
    kind: Literal['before', 'same_review']
    fact_ids: tuple[FactId, ...] = Field(min_length=2)


class RequiredFacts(ContractModel):
    operator: Literal['all_of', 'any_of'] = 'all_of'
    items: tuple[RequiredFact, ...] = ()
    relations: tuple[FactRelation, ...] = ()


class Negation(ContractModel):
    fact_id: FactId
    cue: Text
    scope: Text


class PolarityStructure(ContractModel):
    negations: tuple[Negation, ...] = ()
    contrast_fact_ids: tuple[FactId, ...] = ()


class ProposedFilters(ContractModel):
    aspect: Aspect | None = None
    sentiment: Sentiment | None = None


class QueryContract(ContractModel):
    version: Literal['rag-query-contract-v1']
    task_type: TaskType
    aspect_candidates: tuple[AspectCandidate, ...] = ()
    sentiment_intent: SentimentIntent
    target_entity: Text = 'unknown'
    subtopic: Text = 'unknown'
    required_facts: RequiredFacts = Field(default_factory=RequiredFacts)
    polarity_structure: PolarityStructure = Field(default_factory=PolarityStructure)
    proposed_filters: ProposedFilters = Field(default_factory=ProposedFilters)
    uncertainty: tuple[Text, ...] = ()
    clarification: tuple[Text, ...] = ()
    scope_ref: ScopeRef


class ManualReplay(ContractModel):
    version: Literal['rag-query-replay-v1']
    input_kind: Literal['developer_contract']
    data_kind: DataKind
    replay_id: Text
    query_id: Text
    query_sha256: Digest
    contract_sha256: Digest
    contract: QueryContract


def _require(condition, code):
    if not condition:
        raise ContractError(code)


def _parse(value, model):
    try:
        data = strict_json_loads(value) if isinstance(value, str) else value
        _require(type(data) is dict, 'contract_object_required')
        # JSON arrays become immutable tuples; strict field types still reject coercion.
        return model.model_validate_json(canonical_json(data))
    except ContractError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise ContractError('invalid_contract_structure_or_json') from None


def _references(ids, known, *, minimum=1):
    _require(len(ids) >= minimum and len(ids) == len(set(ids)) and set(ids) <= known,
             'invalid_fact_references')


def validate_query_contract(value, *, expected_scope: ScopeRef) -> QueryContract:
    """校验 dict/严格 JSON 并返回不可变契约；不会自动生成或应用过滤。"""
    # Revalidate even a model_copy/model_construct supplied as trusted configuration.
    _require(type(expected_scope) is ScopeRef, 'trusted_scope_required')
    trusted = _parse(dict(expected_scope), ScopeRef)
    contract = _parse(value, QueryContract)
    _require(contract.scope_ref == trusted, 'scope_ref_mismatch')
    aspects = [candidate.aspect for candidate in contract.aspect_candidates]
    _require(len(aspects) == len(set(aspects)), 'duplicate_aspect_candidate')
    facts = contract.required_facts
    ids = {fact.id for fact in facts.items}
    _require(len(ids) == len(facts.items), 'duplicate_fact_id')
    _require(contract.task_type == 'clarify' or bool(ids), 'required_facts_missing')
    for relation in facts.relations:
        _references(relation.fact_ids, ids, minimum=2)
        _require(relation.kind != 'before' or len(relation.fact_ids) == 2,
                 'before_requires_two_facts')
    polarity = contract.polarity_structure
    for negation in polarity.negations:
        _references((negation.fact_id,), ids)
    if polarity.contrast_fact_ids:
        _references(polarity.contrast_fact_ids, ids, minimum=2)
    if contract.sentiment_intent == 'mixed':
        by_id = {fact.id: fact for fact in facts.items}
        _require(facts.operator == 'all_of' and any(
            relation.kind == 'same_review' and
            {'positive', 'negative'} <= {by_id[i].sentiment for i in relation.fact_ids}
            for relation in facts.relations), 'mixed_requires_same_review_polarities')
    _require(contract.task_type != 'clarify' or bool(contract.clarification), 'clarification_required')
    filters = contract.proposed_filters
    if filters.aspect is not None or filters.sentiment is not None:
        safe = (contract.task_type in ('evidence', 'mixed') and not contract.uncertainty
                and not contract.clarification and len(aspects) == 1
                and contract.target_entity != 'unknown' and contract.subtopic != 'unknown'
                and contract.sentiment_intent != 'unknown')
        _require(safe, 'uncertain_or_non_evidence_filters_forbidden')
        _require(filters.aspect is None or filters.aspect == aspects[0], 'filter_aspect_mismatch')
        _require(filters.sentiment is None or filters.sentiment == contract.sentiment_intent,
                 'filter_sentiment_mismatch')
    return contract


def validate_manual_replay(value, *, expected_scope: ScopeRef, expected_query_id: str,
                           expected_question: str, expected_data_kind: DataKind) -> ManualReplay:
    """只校验人工契约输入，拒绝预测/参考答案格式；哈希不证明人工语义正确。"""
    replay = _parse(value, ManualReplay)
    _require(type(expected_query_id) is str and bool(expected_query_id.strip())
             and type(expected_question) is str and bool(expected_question.strip()),
             'trusted_query_required')
    _require(expected_data_kind in ('synthetic', 'frozen_query'), 'trusted_data_kind_required')
    _require(replay.query_id == expected_query_id
             and replay.query_sha256 == content_sha256(expected_question), 'replay_query_mismatch')
    _require(replay.data_kind == expected_data_kind, 'replay_data_kind_mismatch')
    contract = validate_query_contract(replay.contract.model_dump(mode='json'), expected_scope=expected_scope)
    _require(replay.contract_sha256 == content_sha256(contract.model_dump(mode='json')),
             'replay_contract_hash_mismatch')
    return replay
