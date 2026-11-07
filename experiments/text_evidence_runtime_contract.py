"""Finite, offline-only P5 goal compiler. No source issuer or model entry point.

Historical confirmations establish the origin of a proposed interpretation, not
new human gold. The checked projection deliberately lacks a bound Runtime scope.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import Field, model_validator

from data_analysis_agent.agents.review_task_contract import RuntimeScope, evidence_question, task_limit
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.query_contract import (
    ContractModel, Digest, FactId, QueryContract, RequiredFact, RequiredFacts,
    SentimentIntent, validate_query_contract,
)


class EquivalenceError(ValueError):
    """Stable codes only: never echo private confirmations or source configuration."""


def _require(condition, code):
    if not condition:
        raise EquivalenceError(code)


def _parse(value, model):
    try:
        if isinstance(value, ContractModel):
            value = value.model_dump(mode='json')
        raw = strict_json_loads(value) if isinstance(value, str) else value
        return model.model_validate_json(canonical_json(raw))
    except (ValueError, TypeError, RecursionError, AttributeError):
        raise EquivalenceError('invalid_' + model.__name__) from None


# Closed vocabulary describes goal requirements, never decides what a review says.
LABELS = MappingProxyType({
    'customer_support': '评价对象明确为客服，不能归给安装或配送人员',
    'impatient': '客服说话不耐烦', 'poor_attitude': '客服沟通态度差、敷衍或不礼貌',
    'attitude_not_delay': '普通回复慢、机器人回复或问题未解决不能单独证明态度差',
    'post_purchase_issue': '购买后出现具体问题',
    'continuous_no_followup': '售后持续无人跟进处理，不设固定天数阈值',
    'unresolved_not_unfollowed': '已沟通或处理但尚未解决不能自动视为无人跟进',
    'order_to_receipt_fast': '从下单到实际收到货物的速度快，不设统一数值阈值',
    'direct_receipt': 'quote直接表达实际收货时效',
    'no_installation_inference': '不能由安装时间推定直接收货时间或收货速度',
    'prolonged_nonreceipt': '长时间仍未实际收货，不设固定延期天数',
    'clear_delivery_delay': '到货配送明显延迟、超过约定到货时间或明确抱怨送货过慢',
    'ordinary_nonreceipt_insufficient': '普通未收货或错误签收标记不自动证明迟迟未收货',
    'receipt_not_dispatch': '区分到货配送与单纯发货、安装等待、客服处理拖延',
    'main_device': '对象仅为设备主机', 'use_started': '设备已经实际开始使用',
    'early_after_use': '开始使用后的较短时间内发生，不设固定天数',
    'damage': '设备损坏', 'malfunction': '设备功能故障',
    'no_accessories': '不自动包含遥控器等随附配件',
    'no_preuse_damage': '尚未使用、收到即坏不属于使用后早期故障',
    'no_generic_quality': '泛质量差或缺少发生时间的故障不能补齐目标',
    'hot_environment': '明确炎热环境，不能仅由夏季推断',
    'startup': '已经开机运行', 'cooling_mode': '针对制冷而非制热体验',
    'room_cooling': '室内得到降温而非仅冷风、风量或一般效果好',
    'rapid_cooling': '室内很快凉下来，不设固定分钟数',
    'same_episode': '天气、开机后室内降温及速度属于同一次使用体验',
    'long_running': '设备持续运行较长时间，不设固定分钟或小时数',
    'room_not_cool': '较长运行后室内仍不凉快或未有效降温',
    'no_short_run_or_wind': '不以短时尚未冷、仅风不凉或仅开机时间长替代完整事实',
    'receipt_completed': '货物已经送达买家处',
    'after_receipt': '安装等待发生在实际到货之后',
    'prolonged_no_installation': '到货后长时间无人上门安装，不设固定天数',
    'no_booking_substitute': '配送延误、安装态度或未明确到货的预约变更不能替代',
    'classification_not_new_fact': '分类只解释安装服务延迟主题，不新增分类事实或独立许可',
})
Predicate = Literal[*LABELS.keys()]


class SemanticExpr(ContractModel):
    kind: Literal['atom', 'and', 'or']
    predicate: Predicate | None = None
    children: tuple['SemanticExpr', ...] = Field(default=(), max_length=12)

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'atom':
            if self.predicate is None or self.children:
                raise ValueError('invalid_atom')
        elif self.predicate is not None or len(self.children) < 2:
            raise ValueError('invalid_connective')
        return self


def _atom(predicate):
    return SemanticExpr(kind='atom', predicate=predicate)


def _expr(kind, *children):
    return SemanticExpr(kind=kind, children=tuple(_atom(c) if isinstance(c, str) else c for c in children))


def predicates(expr):
    return (expr.predicate,) if expr.kind == 'atom' else tuple(p for c in expr.children for p in predicates(c))


@dataclass(frozen=True)
class _Definition:
    question: str
    entity: str
    subtopic: str
    facts: tuple[tuple[str, str, str, SemanticExpr], ...]
    # (fact ID, cue, original scope, scoped predicate)
    negations: tuple[tuple[str, str, str, str], ...] = ()
    notes: tuple[tuple[str, int, str], ...] = ()


DEFINITIONS = MappingProxyType({
    'Q02': _Definition('有人说客服说话不耐烦、服务态度差吗？', 'customer_support', 'attitude', (
        ('f0', '客服说话不耐烦或服务态度差', 'negative',
         _expr('and', 'customer_support', _expr('or', 'impatient', 'poor_attitude'), 'attitude_not_delay')),)),
    'Q04': _Definition('买完之后出了状况，一直没人跟进处理吗？', 'after_sales', 'post_purchase_followup', (
        ('f0', '购买后出现问题且持续无人跟进处理', 'negative',
         _expr('and', 'post_purchase_issue', 'continuous_no_followup', 'unresolved_not_unfollowed')),),
        (('f0', '没人', '跟进处理', 'continuous_no_followup'),)),
    'Q05': _Definition('下单后很快就收到货的评价有哪些？', 'delivery_service', 'order_to_receipt_speed', (
        ('f0', '下单后很快收到货物', 'positive',
         _expr('and', 'order_to_receipt_fast', 'direct_receipt', 'no_installation_inference')),),
        notes=(('clarification', 0, 'resolved_by_existing_confirmation'),)),
    'Q06': _Definition('买家有没有抱怨迟迟收不到货？', 'delivery_service', 'delivery_delay', (
        ('f0', '买家迟迟未收到货或配送明显延误', 'negative',
         _expr('and', _expr('or', 'prolonged_nonreceipt', 'clear_delivery_delay'),
               'ordinary_nonreceipt_insufficient', 'receipt_not_dispatch')),),
        (('f0', '收不到', '货物到达', 'prolonged_nonreceipt'),)),
    'Q09': _Definition('用了不久就坏了或者发生故障的反馈有哪些？', 'device', 'early_failure', (
        ('f0', '设备使用不久即损坏或发生故障', 'negative',
         _expr('and', 'main_device', 'use_started', 'early_after_use', _expr('or', 'damage', 'malfunction'),
               'no_accessories', 'no_preuse_damage', 'no_generic_quality')),),
        notes=(('clarification', 0, 'resolved_by_existing_confirmation'),)),
    'Q11': _Definition('炎热时开机，屋里很快就凉下来了吗？', 'device', 'hot_weather_cooling_speed', (
        ('f0', '炎热环境开机后室内很快凉下来', 'positive',
         _expr('and', 'hot_environment', 'startup', 'cooling_mode', 'room_cooling', 'rapid_cooling', 'same_episode')),),
        notes=(('uncertainty', 0, 'constraint_note'),)),
    'Q12': _Definition('开了很久还是不凉快，有这样的评价吗？', 'device', 'prolonged_operation_without_cooling', (
        ('f0', '设备开机运行较久后室内仍不凉快', 'negative',
         _expr('and', 'long_running', 'room_not_cool', 'cooling_mode', 'no_short_run_or_wind')),),
        (('f0', '不', '凉快', 'room_not_cool'),), (('uncertainty', 0, 'resolved_by_existing_confirmation'),)),
    'Q18': _Definition('货到了却迟迟没人来安装，这是哪类反馈？', 'installation_service', 'installation_after_delivery', (
        ('f0', '货物已经送达买家处', 'neutral', _atom('receipt_completed')),
        ('f1', '到货后仍长时间无人上门安装', 'negative',
         _expr('and', 'after_receipt', 'prolonged_no_installation', 'no_booking_substitute', 'classification_not_new_fact'))),
        (('f1', '没人', '到货后安装', 'prolonged_no_installation'),),
        (('uncertainty', 0, 'resolved_by_existing_confirmation'), ('clarification', 0, 'resolved_by_existing_confirmation'))),
})


class OriginRef(ContractModel):
    query_id: Text
    version: Literal['rag-query-contract-v1']
    revision: int = Field(gt=0)
    event_id: Text
    question_sha256: Digest
    contract_sha256: Digest
    confirmation_sha256: Digest


class FactSpec(ContractModel):
    original: RequiredFact
    expression: SemanticExpr


class CoverageBasis(ContractModel):
    fact_id: FactId
    predicate: Predicate
    confirmation_pointer: Text
    confirmation_sha256: Digest


class ScopedNegation(ContractModel):
    fact_id: FactId
    cue: Text
    scope: Text
    predicate: Predicate


class NoteResolution(ContractModel):
    field_name: Literal['uncertainty', 'clarification']
    index: int = Field(ge=0)
    original_text: Text
    state: Literal['constraint_note', 'resolved_by_existing_confirmation', 'unresolved']
    confirmation_pointer: Text
    confirmation_sha256: Digest


class ExperimentGoalSpec(ContractModel):
    version: Literal['p5-experiment-goal-spec-v1'] = 'p5-experiment-goal-spec-v1'
    origin: OriginRef
    original_question: Text
    original_contract: QueryContract = Field(repr=False)
    historical_scope_hint: RuntimeScope = Field(repr=False)
    label_profile_key: Digest
    facts: tuple[FactSpec, ...] = Field(min_length=1, max_length=8)
    coverage: tuple[CoverageBasis, ...]
    scoped_negations: tuple[ScopedNegation, ...] = ()
    resolutions: tuple[NoteResolution, ...] = ()
    independent_target_fact_ids: tuple[FactId, ...] = ()
    support_range: Literal['quote'] = 'quote'
    refutation_range: Literal['quote'] = 'quote'
    context_veto_range: Literal['same_review_full_text'] = 'same_review_full_text'
    context_can_add_support: Literal[False] = False
    publication_allowed: Literal[False] = False
    semantic_verified: Literal[False] = False


class RuntimeProjection(ContractModel):
    """Runtime-compatible goal fields only. Missing scope makes it non-executable."""
    version: Literal['review-runtime-contract-v1'] = 'review-runtime-contract-v1'
    original_question: Text
    evidence_question: Text
    task_type: Literal['evidence'] = 'evidence'
    operation: Literal['fact_search'] = 'fact_search'
    target_entity: Text
    subtopic: Text
    sentiment_intent: SentimentIntent
    required_facts: RequiredFacts
    statistics: tuple = ()
    support_status: Literal['unsupported'] = 'unsupported'
    unsupported_reason: Literal['offline_only_no_p0_binding'] = 'offline_only_no_p0_binding'
    uncertainty: tuple[Text, ...] = ('真实P0来源与范围未绑定；仅离线结构投影。',)


@dataclass(frozen=True, init=False)
class _Origins:
    _json: str = field(repr=False)
    _kind: str
    _digest: str = field(repr=False)
    _issuer: object = field(repr=False)


_ORIGIN_LOADER = object()
_COMPILER = object()
_PIN_LOADER = object()


def _origin_data(origins):
    _require(type(origins) is _Origins and getattr(origins, '_issuer', None) is _ORIGIN_LOADER,
             'unverified_origin_bundle')
    _require(origins._digest == content_sha256([origins._kind, origins._json]), 'origin_bundle_changed')
    return strict_json_loads(origins._json)


def _make_origins(records, scope, *, kind, _pin_proof=None):
    """Internal loader result; tests use explicitly synthetic origins, never P0."""
    _require(kind in ('historical_pinned', 'synthetic_fixture'), 'invalid_origin_kind')
    _require(kind != 'historical_pinned' or _pin_proof is _PIN_LOADER, 'pinned_loader_required')
    _require(set(records) == set(DEFINITIONS), 'origin_target_set_mismatch')
    scope = _parse(scope, RuntimeScope)
    _require(len(scope.product_ids) == 1 and scope.baseline_from is None and scope.baseline_to is None,
             'invalid_historical_scope')
    for qid, record in records.items():
        definition = DEFINITIONS[qid]
        contract = _parse(record['contract'], QueryContract)
        validate_query_contract(contract.model_dump(mode='json'), expected_scope=contract.scope_ref)
        _require(record['question'] == definition.question and type(record['revision']) is int and record['revision'] == 1
                 and record['event_id'] and record['rationale'], 'origin_identity_mismatch')
        _require(contract.task_type == 'evidence' and contract.target_entity == definition.entity
                 and contract.subtopic == definition.subtopic and not task_limit(record['question']), 'origin_goal_mismatch')
        required = dict(operator='all_of', items=[dict(id=i, description=d, sentiment=s) for i,d,s,_ in definition.facts],
            relations=[dict(kind=k, fact_ids=['f0', 'f1']) for k in ('same_review', 'before')] if qid == 'Q18' else [])
        _require(contract.required_facts.model_dump(mode='json') == required, 'origin_facts_mismatch')
        directions = {s for _,_,s,_ in definition.facts} - {'neutral'}
        _require(directions == {contract.sentiment_intent}, 'origin_direction_mismatch')
        _require(contract.polarity_structure.model_dump(mode='json') == dict(negations=[
            dict(fact_id=i, cue=c, scope=s) for i,c,s,_ in definition.negations], contrast_fact_ids=[]), 'origin_polarity_mismatch')
        _require(not record['independent_target_fact_ids'] and
                 contract.proposed_filters.model_dump() == dict(aspect=None, sentiment=None), 'origin_permission_or_filter_mismatch')
        expected_notes = {(f,i) for f,i,_ in definition.notes}
        actual_notes = {(f,i) for f in ('uncertainty','clarification') for i,_ in enumerate(getattr(contract,f))}
        _require(expected_notes == actual_notes, 'origin_notes_mismatch')
    value = object.__new__(_Origins)
    raw = canonical_json(dict(records=records, scope=scope.model_dump(mode='json')))
    for name, item in dict(_json=raw, _kind=kind, _digest=content_sha256([kind, raw]), _issuer=_ORIGIN_LOADER).items():
        object.__setattr__(value, name, item)
    return value


ROOT = Path(__file__).resolve().parents[1]
_PATHS = {
    'outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1/contract-mappings.json':
        '2c283ab88f913c8cdaf41d6010e978ede137081d767fe1eb6eaddc762ef65689',
    'outputs/rag-retrieval-quality/step4b-phase2c1c-adjudication-20261104/bundle.json':
        'f6b5046793647343428754cd763dcb9603d89851901c1884056bad8833ece932',
    'outputs/rag-retrieval-quality/e1a-b2-publication-20261105/source-session.original.json':
        'b5e722b632e6639b6ebcedbe883edd0ca4f2699e5460ae8cf2b75c6c719b171b',
    'outputs/rag-retrieval-quality/e1a-b2-publication-20261105/release-v1/reference-release.json':
        '3119110db89e0c9afc88f90865e73b907f6529b4045be359001ce682f2586d26',
}


def load_confirmed_origins():
    """Read pinned local confirmations, not review judgments or live data sources."""
    from data_analysis_agent.reviews.human_adjudication import validate_session
    from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
    loaded = []
    for name, expected in _PATHS.items():
        raw = (ROOT / name).read_bytes()
        _require(sha256(raw).hexdigest() == expected, 'original_file_pin_mismatch')
        loaded.append(strict_json_loads(raw.decode('utf-8')))
    mappings, bundle, session, release = loaded
    _require(set(mappings) == set(DEFINITIONS), 'origin_target_set_mismatch')
    _require(validate_session(bundle, session)['ready_for_formal_reference_freeze'], 'original_confirmation_invalid')
    _parse(release, ReferenceRelease)
    _require(release['bundle_sha256'] == content_sha256(bundle) and
             release['session_sha256'] == content_sha256(session), 'original_release_link_mismatch')
    records, scope = {}, None
    for qid, mapping in mappings.items():
        query = session['queries'][qid]
        contract = query['payload']['contract']
        released = [r for r in release['references'] if r['reference']['query_id'] == qid]
        _require(released and all(r['contract'] == contract and r['contract_revision'] == query['revision'] for r in released),
                 'original_release_target_mismatch')
        _require(mapping['original_contract'] == contract and mapping['original_confirmation_event'] == query['event_id']
                 and mapping['original_confirmation_rationale'] == query['rationale']
                 and mapping['original_contract_revision'] == query['revision']
                 and mapping['independent_target_fact_ids'] == query['payload']['independent_target_fact_ids'],
                 'original_mapping_mismatch')
        _require(contract['scope_ref'] == bundle['scope_ref'] == release['scope_ref'], 'original_scope_ref_mismatch')
        question = next(q['question'] for q in bundle['queries'] if q['query_id'] == qid)
        _require(question == mapping['original_question'], 'original_question_mismatch')
        hint = mapping['current_runtime_unmodified']['scope']
        _require(scope is None or scope == hint, 'historical_scope_conflict')
        scope = hint
        records[qid] = dict(question=question, contract=contract, revision=query['revision'], event_id=query['event_id'],
            rationale=query['rationale'], independent_target_fact_ids=query['payload']['independent_target_fact_ids'])
    return _make_origins(records, scope, kind='historical_pinned', _pin_proof=_PIN_LOADER)


def propose_spec(origins, query_id):
    data = _origin_data(origins)
    _require(query_id in DEFINITIONS, 'unknown_experiment_target')
    definition = DEFINITIONS[query_id]
    record = data['records'][query_id]
    contract = _parse(record['contract'], QueryContract)
    digest = content_sha256(record['rationale'])
    pointer = f'/queries/{query_id}/rationale'
    return ExperimentGoalSpec(origin=OriginRef(query_id=query_id, version=contract.version,
        revision=record['revision'], event_id=record['event_id'], question_sha256=content_sha256(record['question']),
        contract_sha256=content_sha256(record['contract']), confirmation_sha256=digest),
        original_question=record['question'], original_contract=contract,
        historical_scope_hint=_parse(data['scope'], RuntimeScope),
        label_profile_key=content_sha256([data['scope'][k] for k in ('model','base_url','prompt_version')]),
        facts=tuple(FactSpec(original=f, expression=definition.facts[i][3]) for i,f in enumerate(contract.required_facts.items)),
        coverage=tuple(CoverageBasis(fact_id=i, predicate=p, confirmation_pointer=pointer, confirmation_sha256=digest)
                       for i,_,_,expr in definition.facts for p in predicates(expr)),
        scoped_negations=tuple(ScopedNegation(fact_id=i, cue=c, scope=s, predicate=p) for i,c,s,p in definition.negations),
        resolutions=tuple(NoteResolution(field_name=f, index=i, original_text=getattr(contract,f)[i], state=state,
                          confirmation_pointer=pointer, confirmation_sha256=digest) for f,i,state in definition.notes))


def _wire_expr(expr):
    if expr.kind == 'atom':
        return {'条件': LABELS[expr.predicate]}
    return {'且' if expr.kind == 'and' else '或': [_wire_expr(c) for c in expr.children]}


def _unwire_expr(value):
    _require(type(value) is dict and len(value) == 1, 'invalid_description_expression')
    operator, children = next(iter(value.items()))
    if operator == '条件':
        inverse = {v:k for k,v in LABELS.items()}
        _require(isinstance(children, str) and children in inverse, 'unknown_description_condition')
        return _atom(inverse[children])
    _require(operator in ('且','或') and type(children) is list, 'invalid_description_expression')
    return _expr('and' if operator == '且' else 'or', *(_unwire_expr(c) for c in children))


def render_fact(fact, negations=()):
    fact = _parse(fact, FactSpec)
    negations = tuple(_parse(n, ScopedNegation) for n in negations)
    _require(all(n.fact_id == fact.original.id and n.predicate in predicates(fact.expression) for n in negations),
             'invalid_scoped_negation')
    value = dict(原事实=fact.original.description, 必要语义=_wire_expr(fact.expression),
        否定作用域=[dict(线索=n.cue, 范围=n.scope, 条件=LABELS[n.predicate]) for n in negations])
    text = 'P5实验语义v1 ' + canonical_json(value)
    _require(len(text) <= 1000, 'description_too_long')
    return text


def decode_description(text):
    try:
        _require(isinstance(text,str) and len(text) <= 1000 and text.startswith('P5实验语义v1 '), 'invalid_description')
        value = strict_json_loads(text[len('P5实验语义v1 '):])
        _require(type(value) is dict and set(value) == {'原事实','必要语义','否定作用域'}, 'description_fields_mismatch')
        expression = _unwire_expr(value['必要语义'])
        _require(type(value['原事实']) is str and type(value['否定作用域']) is list, 'invalid_description')
        for negation in value['否定作用域']:
            _require(type(negation) is dict and set(negation) == {'线索','范围','条件'}
                     and all(isinstance(v,str) and v.strip() for v in negation.values())
                     and negation['条件'] in LABELS.values(), 'invalid_description_negation')
        _require(text == 'P5实验语义v1 ' + canonical_json(value), 'noncanonical_description')
        return value['原事实'], expression, value['否定作用域']
    except EquivalenceError:
        raise
    except (ValueError,TypeError,KeyError,RecursionError):
        raise EquivalenceError('invalid_description') from None


def _validate_spec(origins, value):
    _origin_data(origins)
    spec = _parse(value, ExperimentGoalSpec)
    expected = propose_spec(origins, spec.origin.query_id)
    _require(spec.origin == expected.origin and spec.original_question == expected.original_question
             and spec.original_contract == expected.original_contract, 'original_identity_or_contract_changed')
    _require(spec.historical_scope_hint == expected.historical_scope_hint
             and spec.label_profile_key == expected.label_profile_key, 'scope_or_profile_mismatch')
    _require(not spec.independent_target_fact_ids, 'independent_permission_forbidden')
    _require(all(n.state != 'unresolved' for n in spec.resolutions), 'unresolved_clarification')
    _require(spec.resolutions == expected.resolutions, 'resolution_without_original_basis')
    _require(spec.facts == expected.facts and spec.scoped_negations == expected.scoped_negations,
             'semantic_graph_mismatch')
    _require(spec.coverage == expected.coverage, 'constraint_coverage_mismatch')
    _require(spec == expected, 'unsupported_experiment_constraint')
    return spec


def _project(spec):
    facts = tuple(RequiredFact(id=f.original.id, sentiment=f.original.sentiment,
        description=render_fact(f, tuple(n for n in spec.scoped_negations if n.fact_id == f.original.id))) for f in spec.facts)
    return RuntimeProjection(original_question=spec.original_question, evidence_question=evidence_question(spec.original_question),
        target_entity=spec.original_contract.target_entity, subtopic=spec.original_contract.subtopic,
        sentiment_intent=spec.original_contract.sentiment_intent,
        required_facts=RequiredFacts(operator=spec.original_contract.required_facts.operator, items=facts,
                                    relations=spec.original_contract.required_facts.relations))


def _validate_projection(spec, value):
    projection = _parse(value, RuntimeProjection)
    _require(projection == _project(spec), 'runtime_projection_mismatch')
    for fact, projected in zip(spec.facts, projection.required_facts.items, strict=True):
        original, expression, negations = decode_description(projected.description)
        _require(original == fact.original.description and expression == fact.expression, 'lossy_runtime_description')
        _require(negations == [dict(线索=n.cue, 范围=n.scope, 条件=LABELS[n.predicate]) for n in spec.scoped_negations
                               if n.fact_id == fact.original.id], 'lossy_negation_projection')
    return projection


@dataclass(frozen=True, init=False)
class CheckedExperimentTarget:
    _spec_json: str = field(repr=False)
    _projection_json: str = field(repr=False)
    _origins: _Origins = field(repr=False)
    _digest: str = field(repr=False)
    _issuer: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('checked_target_requires_compiler')

    @property
    def spec(self):
        return _parse(self._spec_json, ExperimentGoalSpec)

    @property
    def runtime_projection(self):
        return _parse(self._projection_json, RuntimeProjection)

    def report(self):
        require_checked(self)
        return dict(query_id=self.spec.origin.query_id, origin_kind=self._origins._kind,
            original_confirmation_verified=self._origins._kind == 'historical_pinned',
            semantic_coverage_complete=True, projection_lossless=True,
            fact_ids=[f.original.id for f in self.spec.facts], condition_count=len(self.spec.coverage),
            description_lengths=[len(f.description) for f in self.runtime_projection.required_facts.items],
            p0_binding_present=False, scope_trust='historical_hint_only', executable=False,
            mapping_human_confirmed=False, human_review_admission='pending_typed_mapping_review',
            publication_allowed=False, semantic_verified=False, permitted_target_fact_ids=[])


def compile_target(origins, value):
    spec = _validate_spec(origins, value)
    projection = _validate_projection(spec, _project(spec))
    target = object.__new__(CheckedExperimentTarget)
    a, b = canonical_json(spec.model_dump(mode='json')), canonical_json(projection.model_dump(mode='json'))
    for name, item in dict(_spec_json=a, _projection_json=b, _origins=origins,
            _digest=content_sha256([a,b,origins._digest]), _issuer=_COMPILER).items():
        object.__setattr__(target, name, item)
    return target


def require_checked(target):
    _require(type(target) is CheckedExperimentTarget and getattr(target,'_issuer',None) is _COMPILER,
             'untrusted_checked_target')
    _origin_data(target._origins)
    _require(target._digest == content_sha256([target._spec_json,target._projection_json,target._origins._digest]),
             'checked_target_changed')
    spec = _validate_spec(target._origins, target._spec_json)
    _validate_projection(spec, target._projection_json)
    return target


def validate_projection(target, value):
    require_checked(target)
    return _validate_projection(target.spec, value)


def require_executable(target):
    require_checked(target)
    raise EquivalenceError('p0_binding_and_execution_not_implemented')
