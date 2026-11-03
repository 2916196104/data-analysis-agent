"""4B-2a：只读来源校验、受限显式陈述规则和事实关系判断。

不是通用自然语言蕴含器。规则外的陈述/否定/关系保留未知。
trusted_batch 必须由调用方从独立核验的来源获得，不能从待验证 batch 自证。
"""

from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import re
from typing import Annotated, Literal

from pydantic import Field

from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.opinions import Aspect, Sentiment
from data_analysis_agent.reviews.query_contract import (
    CONTRACT_VERSION, ContractError, ContractModel, Digest, FactId, QueryContract, ScopeRef,
    validate_query_contract,
)

INPUT_VERSION = 'rag-evidence-judge-input-v1'
RULE_VERSION = 'explicit-clauses-zh-v1'
PositiveId = Annotated[int, Field(ge=1)]
Mode = Literal['unfiltered', 'filtered']


class EvidenceCandidate(ContractModel):
    query_id: Text
    mode: Mode
    rank: PositiveId
    key: Annotated[str, Field(pattern=r'^rag-opinion-v1:[0-9a-f]{64}$')]
    review_id: PositiveId
    annotation_id: PositiveId
    source: Text
    dataset_version: Text
    product_id: Text
    label_profile_key: Digest
    opinion_source: Literal['model', 'human']
    model_opinion_id: PositiveId | None
    human_opinion_id: PositiveId | None
    human_revision_id: PositiveId | None
    aspect: Aspect
    sentiment: Sentiment
    quote: Annotated[str, Field(min_length=1, max_length=2000)]
    quote_start: Annotated[int, Field(ge=0)]
    quote_end: PositiveId
    review_text: Annotated[str, Field(min_length=1)]
    review_content_sha256: Digest
    embedding_input_sha256: Digest
    effective_snapshot_sha256: Digest
    file_sha256: Digest
    line_number: PositiveId
    evidence_ref: Text
    text_builder_version: Literal['quote-utf8-v1']
    evidence_valid: bool


class EvidenceBatch(ContractModel):
    version: Literal['rag-evidence-judge-input-v1']
    scope_ref: ScopeRef
    query_id: Text
    mode: Mode
    candidates: tuple[EvidenceCandidate, ...] = Field(max_length=20)


class FactEvidence(ContractModel):
    fact_id: FactId
    query_id: Text
    mode: Mode
    rank: PositiveId
    key: str
    review_id: PositiveId
    annotation_id: PositiveId
    quote_start: int
    quote_end: int
    effective_snapshot_sha256: Digest
    embedding_input_sha256: Digest
    evidence_ref: Text


class JudgeResult(ContractModel):
    version: Literal['rag-evidence-judge-result-v1'] = 'rag-evidence-judge-result-v1'
    input_version: Literal['rag-evidence-judge-input-v1'] = INPUT_VERSION
    rule_version: Literal['explicit-clauses-zh-v1'] = RULE_VERSION
    contract_version: Literal['rag-query-contract-v1'] = CONTRACT_VERSION
    execution_status: Literal['completed', 'validation_failed', 'execution_failed']
    status: Literal['sufficient', 'limited', 'insufficient'] | None
    query_id: Text | None = None
    mode: Mode | None = None
    input_sha256: Digest | None = None
    supported_target_fact_ids: tuple[FactId, ...] = ()
    missing_required_fact_ids: tuple[FactId, ...] = ()
    matched_required_fact_ids: tuple[FactId, ...] = ()
    missing_relation_ids: tuple[str, ...] = ()
    evidence_refs: tuple[FactEvidence, ...] = ()
    condition_evidence_refs: tuple[FactEvidence, ...] = ()
    reason_codes: tuple[Text, ...]
    limitations: tuple[Text, ...] = (
        'candidate_evidence_only_not_population_statistics',
        'unknown_is_not_proof_of_absence',
        'controlled_explicit_clauses_only_not_general_entailment',
    )


class SourceValidationError(ValueError):
    """只使用固定错误码。"""


def _check(condition, code):
    if not condition:
        raise SourceValidationError(code)


def _data(value):
    if isinstance(value, ContractModel):
        return value.model_dump(mode='json', warnings=False)
    return strict_json_loads(value) if isinstance(value, str) else value


def _load(value, model):
    return model.model_validate_json(canonical_json(_data(value)))


def _validate_sources(batch, trusted, scope):
    _check(batch.scope_ref == trusted.scope_ref == scope, 'source_scope_mismatch')
    # Exact ordered-list identity prevents injecting, dropping or borrowing other-mode candidates.
    _check(batch == trusted, 'candidate_list_identity_mismatch')
    _check([c.rank for c in batch.candidates] == list(range(1, len(batch.candidates) + 1)), 'invalid_ranks')
    _check(len({c.key for c in batch.candidates}) == len(batch.candidates), 'duplicate_candidate_key')
    reviews = {}
    for c in batch.candidates:
        _check(c.query_id == batch.query_id and c.mode == batch.mode, 'candidate_query_mode_mismatch')
        _check(c.evidence_valid, 'invalid_or_stale_source')
        _check(c.quote.strip() and 0 <= c.quote_start < c.quote_end <= len(c.review_text)
               and c.review_text[c.quote_start:c.quote_end] == c.quote, 'source_span_mismatch')
        _check(sha256(c.quote.encode('utf-8')).hexdigest() == c.embedding_input_sha256, 'source_quote_hash_mismatch')
        _check(c.evidence_ref == f'sha256:{c.file_sha256}#line={c.line_number}', 'source_ref_mismatch')
        _check((c.opinion_source == 'model' and c.model_opinion_id is not None
                and c.human_opinion_id is None and c.human_revision_id is None)
               or (c.opinion_source == 'human' and c.model_opinion_id is None
                   and c.human_opinion_id is not None and c.human_revision_id is not None), 'invalid_opinion_identity')
        key = 'rag-opinion-v1:' + content_sha256([
            c.source, c.dataset_version, c.product_id, c.review_id, c.review_content_sha256,
            c.label_profile_key, c.annotation_id, c.human_revision_id, c.opinion_source,
            c.model_opinion_id, c.human_opinion_id, c.aspect, c.sentiment,
            c.quote_start, c.quote_end, c.embedding_input_sha256,
        ])
        _check(c.key == key, 'stable_source_key_mismatch')
        rid = (c.source, c.dataset_version, c.product_id, c.review_id)
        identity = (c.review_content_sha256, c.review_text, c.annotation_id, c.effective_snapshot_sha256,
                    c.label_profile_key, c.file_sha256, c.line_number, c.human_revision_id, c.opinion_source)
        _check(rid not in reviews or reviews[rid] == identity, 'conflicting_review_identity')
        reviews[rid] = identity


@dataclass(frozen=True)
class ExplicitClause:
    entity: str
    topic: str
    sentiment: str
    cue: str = ''
    negation_scope: str = ''


# An intentionally small, query-ID-independent grammar. Descriptions must name exactly
# one registered proposition; no keyword, substring, metadata or distance-based entailment.
CLAUSES = {
    '客服回复及时': ExplicitClause('customer_support', 'response_time', 'positive'),
    '客服回复不及时': ExplicitClause('customer_support', 'response_time', 'negative', '不', '及时'),
    '客服没有拖延回复': ExplicitClause('customer_support', 'response_time', 'positive', '没有', '拖延回复'),
    '物流人员配送及时': ExplicitClause('delivery_staff', 'delivery_time', 'positive'),
    '安装人员上门及时': ExplicitClause('installer', 'installation_time', 'positive'),
    '安装人员仍未上门': ExplicitClause('installer', 'installation_time', 'negative', '未', '上门'),
    '设备已到货': ExplicitClause('device', 'delivery', 'neutral'),
    '设备制冷良好': ExplicitClause('device', 'cooling', 'positive'),
    '设备制冷不足': ExplicitClause('device', 'cooling', 'negative'),
    '设备制冷并不良好': ExplicitClause('device', 'cooling', 'negative', '并不', '良好'),
    '设备制热良好': ExplicitClause('device', 'heating', 'positive'),
    '设备制热不足': ExplicitClause('device', 'heating', 'negative'),
    '设备噪声很大': ExplicitClause('device', 'noise', 'negative'),
    '噪声影响睡眠': ExplicitClause('device', 'sleep_impact', 'negative'),
    '设备短期内发生故障': ExplicitClause('device', 'early_failure', 'negative'),
}
SEPARATOR = re.compile(r'，然后|，但是|，但|。|；')


@dataclass(frozen=True)
class Match:
    candidate: EvidenceCandidate
    start: int
    end: int


def _clauses(candidate):
    """Require complete controlled context, so a quote cannot omit its negator/qualifier."""
    whole = _split_clauses(candidate.review_text, candidate, 0)
    local = _split_clauses(candidate.quote, candidate, candidate.quote_start)
    spans = {(text, match.start, match.end) for text, match in whole}
    return local if local and all((text, m.start, m.end) in spans for text, m in local) else ()


def _split_clauses(text, candidate, offset):
    result, start = [], 0
    for sep in [*SEPARATOR.finditer(text), None]:
        end = sep.start() if sep else len(text)
        statement = text[start:end]
        if statement:
            if statement not in CLAUSES:
                return ()
            result.append((statement, Match(candidate, offset + start, offset + end)))
        elif sep is not None:
            return ()
        if sep is not None and sep.end() == len(text) and sep.group() not in ('。', '；'):
            return ()
        start = sep.end() if sep else end
    return tuple(result)


def _relation_ok(kind, matches):
    first = matches[0]
    if any(m.candidate.review_id != first.candidate.review_id
           or m.candidate.source != first.candidate.source
           or m.candidate.dataset_version != first.candidate.dataset_version
           or m.candidate.product_id != first.candidate.product_id for m in matches):
        return False
    if kind == 'same_review':
        return True
    # Narrative order alone is not event order. Require an explicit supported connector
    # in the same quote and a consistent ordered assignment to all participating facts.
    if any(m.candidate.key != first.candidate.key for m in matches):
        return False
    for left, right in zip(matches, matches[1:]):
        if left.end > right.start:
            return False
        connector = first.candidate.review_text[left.end:right.start]
        if connector not in (('，然后',) if kind == 'before' else ('，但是', '，但')):
            return False
    return True


def _solve_relations(relations, choices):
    """Use a shared assignment for overlapping relations; never join incompatible witnesses."""
    if not relations:
        return {}, ()
    ids = tuple(dict.fromkeys(fid for _, _, fids in relations for fid in fids))
    if any(not choices[i] for i in ids):
        return {}, tuple(name for name, _, _ in relations)
    for count, values in enumerate(product(*(choices[i] for i in ids)), 1):
        if count > 10000:
            raise RuntimeError('relation_search_budget_exceeded')
        assignment = dict(zip(ids, values))
        if all(_relation_ok(kind, [assignment[i] for i in fids]) for _, kind, fids in relations):
            return assignment, ()
    return {}, tuple(name for name, _, _ in relations)


def _reference(fid, match):
    c = match.candidate
    return FactEvidence(fact_id=fid, query_id=c.query_id, mode=c.mode, rank=c.rank, key=c.key,
        review_id=c.review_id, annotation_id=c.annotation_id, quote_start=match.start, quote_end=match.end,
        effective_snapshot_sha256=c.effective_snapshot_sha256, embedding_input_sha256=c.embedding_input_sha256,
        evidence_ref=c.evidence_ref)


def _judge(contract, batch, independent, digest):
    facts = contract.required_facts.items
    choices = {f.id: [] for f in facts}
    reasons = set()
    candidate_clauses = [_clauses(c) for c in batch.candidates]
    parsed = [item for rows in candidate_clauses for item in rows]
    if any(not rows for rows in candidate_clauses):
        reasons.add('unrecognized_clause_or_context')
    for fact in facts:
        text = fact.description.removesuffix('。')
        rule = CLAUSES.get(text)
        if rule is None:
            reasons.add('unrecognized_target_fact')
            continue
        if contract.target_entity not in (rule.entity, 'multiple'):
            reasons.add('target_entity_not_confirmed')
            continue
        if contract.subtopic not in (rule.topic, 'multiple'):
            reasons.add('subtopic_not_confirmed')
            continue
        sentiment = fact.sentiment or contract.sentiment_intent
        if (contract.sentiment_intent in ('positive', 'negative')
                and sentiment in ('positive', 'negative') and sentiment != contract.sentiment_intent):
            reasons.add('query_fact_polarity_conflict')
            continue
        if sentiment != rule.sentiment:
            reasons.add('sentiment_not_confirmed')
            continue
        negations = [n for n in contract.polarity_structure.negations if n.fact_id == fact.id]
        if any(n.cue != rule.cue or n.scope != rule.negation_scope for n in negations):
            reasons.add('negation_scope_unknown')
            continue
        seen = set()
        for statement, match in parsed:
            if statement == text:
                identity = (match.candidate.key, match.start, match.end)
                if identity not in seen:
                    choices[fact.id].append(match)
                    seen.add(identity)
    matched = tuple(f.id for f in facts if choices[f.id])
    relations = [(f'relation_{i}', r.kind, r.fact_ids) for i, r in enumerate(contract.required_facts.relations)]
    if contract.polarity_structure.contrast_fact_ids:
        relations.append(('contrast', 'contrast', contract.polarity_structure.contrast_fact_ids))
    assignment, missing_relations = _solve_relations(relations, choices)
    if missing_relations:
        reasons.add('necessary_relations_not_confirmed')
    complete = (len(matched) == len(facts) if contract.required_facts.operator == 'all_of' else bool(matched))
    complete = complete and not missing_relations and bool(facts)
    blocked = bool(contract.uncertainty or contract.clarification) or contract.task_type in ('statistics', 'clarify')
    if blocked:
        reasons.add('query_requires_clarification_or_statistics')
    if contract.task_type == 'mixed':
        reasons.add('statistics_require_separate_evaluation')
        complete = False
    if complete and not blocked:
        status, permitted = 'sufficient', matched
        reasons.add('explicit_required_facts_and_relations_supported')
    else:
        # A condition of a compound core is not an independently answerable target.
        linked = {fid for _, _, fids in relations for fid in fids}
        permitted = tuple(fid for fid in matched if fid in independent and fid not in linked) if not blocked else ()
        status = 'limited' if permitted else 'insufficient'
        reasons.add('independent_subanswer_only' if permitted else 'no_licensed_core_conclusion')
    if not batch.candidates:
        reasons.add('no_candidates_not_proof_of_absence')
    missing = tuple(f.id for f in facts if not choices[f.id])
    if status == 'sufficient' and contract.required_facts.operator == 'any_of':
        missing = ()  # Unselected alternatives are not missing mandatory facts.
    refs = tuple(_reference(fid, assignment.get(fid) or choices[fid][0]) for fid in permitted)
    return JudgeResult(execution_status='completed', status=status, query_id=batch.query_id, mode=batch.mode,
        input_sha256=digest,
        supported_target_fact_ids=permitted, missing_required_fact_ids=missing,
        matched_required_fact_ids=matched, missing_relation_ids=missing_relations,
        evidence_refs=refs,
        condition_evidence_refs=tuple(_reference(fid, assignment.get(fid) or choices[fid][0]) for fid in matched),
        reason_codes=tuple(sorted(reasons)))


def _input_digest(contract, batch, independent):
    return content_sha256(dict(contract=contract.model_dump(mode='json'), batch=batch.model_dump(mode='json'),
                               independent_target_fact_ids=independent, rule_version=RULE_VERSION))


def _validate_judge_result(value, *, contract, batch, independent_target_fact_ids=()):
    result = _load(value, JudgeResult)
    expected = _judge(contract, batch, independent_target_fact_ids,
                      _input_digest(contract, batch, independent_target_fact_ids))
    _check(result == expected, 'judge_output_mismatch')
    return result


def judge_evidence(contract, batch, *, expected_scope: ScopeRef, trusted_batch,
                   independent_target_fact_ids=()) -> JudgeResult:
    """Pure offline judgment; failures have status=None and never masquerade as insufficient.

    independent_target_fact_ids is a query-side declaration, not support labels. Default
    empty conservatively treats partial all_of coverage as an unanswerable compound core.
    """
    digest = None
    try:
        contract = validate_query_contract(_data(contract), expected_scope=expected_scope)
        batch, trusted = _load(batch, EvidenceBatch), _load(trusted_batch, EvidenceBatch)
        _validate_sources(batch, trusted, contract.scope_ref)
        ids = {f.id for f in contract.required_facts.items}
        _check(isinstance(independent_target_fact_ids, (list, tuple))
               and all(type(i) is str for i in independent_target_fact_ids), 'invalid_independent_targets')
        independent = tuple(independent_target_fact_ids)
        _check(len(set(independent)) == len(independent) and set(independent) <= ids, 'invalid_independent_targets')
        digest = _input_digest(contract, batch, independent)
    except (ContractError, SourceValidationError) as exc:
        return JudgeResult(execution_status='validation_failed', status=None, reason_codes=(str(exc),))
    except (ValueError, TypeError, RecursionError):
        return JudgeResult(execution_status='validation_failed', status=None, reason_codes=('invalid_judge_input',))
    except Exception:
        return JudgeResult(execution_status='execution_failed', status=None, reason_codes=('judge_input_execution_failed',))
    try:
        result = _judge(contract, batch, independent, digest)
        return _validate_judge_result(result, contract=contract, batch=batch, independent_target_fact_ids=independent)
    except Exception:
        return JudgeResult(execution_status='execution_failed', status=None, input_sha256=digest,
                           reason_codes=('judge_execution_failed',))


def validate_judge_result(value, contract, batch, *, expected_scope, trusted_batch,
                          independent_target_fact_ids=()):
    """Revalidate inputs and recompute permissions, hashes and exact source references."""
    result = _load(value, JudgeResult)
    expected = judge_evidence(contract, batch, expected_scope=expected_scope, trusted_batch=trusted_batch,
                              independent_target_fact_ids=independent_target_fact_ids)
    _check(result == expected, 'judge_output_mismatch')
    return result
