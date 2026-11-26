"""带版本号的领域规则配置加载与严格校验器。

将事实模式、同义问法扩展、蕴含/冲突关系和证据重排线索统一收口到版本化 JSON 配置中，
代码仅负责加载、正则编译校验、引用完整性校验和提供只读规则对象。
"""

from functools import lru_cache
from importlib import resources
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_analysis_agent.core.serialization import content_sha256, strict_json_loads

SCHEMA_VERSION = 'review-domain-rules-v1'
DEFAULT_RULES_FILENAME = 'domain_rules.v1.json'


def _require_regex(pattern: str, label: str) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f'领域规则正则不能为空：{label}')
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f'领域规则正则无效（{label}）：{exc}') from exc
    return pattern


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ReportedFactsConfig(_StrictModel):
    policy: str = Field(min_length=1)
    comparative_adverb: str
    patterns: dict[str, str] = Field(min_length=1)
    entailment_edges: dict[str, tuple[str, ...]]
    opposites: tuple[tuple[str, str], ...]
    domains: dict[str, str] = Field(min_length=1)
    connectives: str
    hazards: str
    retractions: str
    context_hazards_extra: str
    forward_frames: str

    @model_validator(mode='after')
    def _validate_rules(self):
        for field_name in ('comparative_adverb', 'connectives', 'hazards', 'retractions', 'forward_frames'):
            _require_regex(getattr(self, field_name), f'reported_facts.{field_name}')
        _require_regex(self.hazards + self.context_hazards_extra, 'reported_facts.context_hazards')
        for fact_id, pattern in self.patterns.items():
            _require_regex(pattern, f'reported_facts.patterns.{fact_id}')
            domain = fact_id.split('_', 1)[0]
            if domain not in self.domains:
                raise ValueError(f'事实 {fact_id} 缺少对应领域正则：{domain}')
        for domain, pattern in self.domains.items():
            _require_regex(pattern, f'reported_facts.domains.{domain}')
        for source, targets in self.entailment_edges.items():
            if source not in self.patterns:
                raise ValueError(f'蕴含关系起点未在 patterns 中定义：{source}')
            for target in targets:
                if target not in self.patterns:
                    raise ValueError(f'蕴含关系终点未在 patterns 中定义：{target}')
                if target == source:
                    raise ValueError(f'蕴含关系不能自指：{source}')
        for left, right in self.opposites:
            if left not in self.patterns or right not in self.patterns:
                raise ValueError(f'互斥关系引用了未定义事实：{left}, {right}')
            if left == right:
                raise ValueError(f'互斥关系不能自指：{left}')
        return self


class CoolingPoliciesConfig(_StrictModel):
    previous: str = Field(min_length=1)
    explicit: str = Field(min_length=1)
    progress: str = Field(min_length=1)
    current: str = Field(min_length=1)

    @property
    def keyword_policies(self) -> tuple[str, str]:
        return (self.progress, self.current)

    @property
    def all_policies(self) -> tuple[str, str, str, str]:
        return (self.previous, self.explicit, self.progress, self.current)

    @model_validator(mode='after')
    def _validate_unique(self):
        if len(set(self.all_policies)) != 4:
            raise ValueError('cooling_speed.policies 必须包含 4 个互不相同的策略版本。')
        return self


class CoolingSpeedConfig(_StrictModel):
    policies: CoolingPoliciesConfig
    fact_id: str = Field(min_length=1)
    speed_pattern: str
    slow_clause_template: str
    waiting_pattern: str
    ambiguous_unit_pattern: str
    context_qualifiers_extra: str
    domain_cooling_extra: str
    extra_conflicts: tuple[tuple[str, str], ...]
    room_drop: str
    room_slow_suffix: str
    explicit_slow: str
    time_pattern: str
    cooling_run_prefix: str
    still_warm: str
    low_setting: str
    active_cooling_extra: str
    companion_extra: tuple[str, ...]
    other_experience: str
    v2_domain_cooling_extra: str
    condition_change: str
    minute_bounds: dict[str, tuple[int, int]] = Field(min_length=1)
    progress_middle_1: str
    progress_middle_2: str
    progress_tail: str
    trailing_report: str
    progress_hazards: str
    progress_before_speakers: str
    joined_effect: str
    untested_heating: str
    cooling_sensation: str

    @property
    def duration_pattern(self) -> str:
        return r'(?:[1-9][0-9]{0,2}(?:分钟|小时)|' + '|'.join(self.minute_bounds) + ')'

    @property
    def slow_clause_pattern(self) -> str:
        return self.slow_clause_template.format(speed_pattern=self.speed_pattern)

    @property
    def progress_pattern(self) -> str:
        duration = self.duration_pattern
        return (
            self.low_setting
            + self.progress_middle_1.format(duration=duration)
            + self.progress_middle_2
            + self.progress_tail.format(duration=duration)
        )

    @model_validator(mode='after')
    def _validate_cooling(self):
        for label, bounds in self.minute_bounds.items():
            low, high = bounds
            if low <= 0 or low > high:
                raise ValueError(f'cooling_speed.minute_bounds 区间无效：{label}={bounds}')
        for name in (
            'speed_pattern', 'waiting_pattern', 'ambiguous_unit_pattern', 'room_drop',
            'explicit_slow', 'time_pattern', 'cooling_run_prefix', 'still_warm', 'low_setting',
            'active_cooling_extra', 'other_experience', 'condition_change', 'trailing_report',
            'progress_hazards', 'progress_before_speakers', 'joined_effect', 'untested_heating',
            'cooling_sensation',
        ):
            _require_regex(getattr(self, name), f'cooling_speed.{name}')
        _require_regex(self.slow_clause_pattern, 'cooling_speed.slow_clause_pattern')
        _require_regex(self.room_drop + self.room_slow_suffix, 'cooling_speed.room_slow')
        _require_regex(self.progress_pattern, 'cooling_speed.progress_pattern')
        for index, item in enumerate(self.companion_extra):
            _require_regex(item, f'cooling_speed.companion_extra[{index}]')
        return self


class DeliveryTimingConfig(_StrictModel):
    policy: str = Field(min_length=1)
    not_received_fact_id: str = Field(min_length=1)
    install_pending_fact_id: str = Field(min_length=1)
    now_pattern: str
    goods_pattern: str
    buyer_pattern: str
    signed_without_receipt: str
    install_slow: str
    elapsed_pattern: str
    wait_install_body: str
    other_order: str
    domains: dict[str, str] = Field(min_length=1)
    opposites: tuple[tuple[str, str], ...]

    @model_validator(mode='after')
    def _validate_timing(self):
        for name in (
            'now_pattern', 'goods_pattern', 'buyer_pattern', 'signed_without_receipt',
            'install_slow', 'elapsed_pattern', 'wait_install_body', 'other_order',
        ):
            _require_regex(getattr(self, name), f'delivery_timing.{name}')
        for domain, pattern in self.domains.items():
            _require_regex(pattern, f'delivery_timing.domains.{domain}')
        return self


class QueryExpansionRule(_StrictModel):
    id: str = Field(min_length=1)
    fact_policy: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    single_fact_only: bool = True
    variants: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _validate_variants(self):
        cleaned = [item.strip() for item in self.variants]
        if any(not item for item in cleaned):
            raise ValueError(f'同义问法不能为空：{self.id}')
        if len(set(cleaned)) != len(cleaned):
            raise ValueError(f'同义问法存在重复项：{self.id}')
        return self


class QueryExpansionConfig(_StrictModel):
    version: str = Field(min_length=1)
    rules: tuple[QueryExpansionRule, ...]

    @model_validator(mode='after')
    def _validate_unique_ids(self):
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError('retrieval_query_expansion.rules 存在重复规则 ID。')
        return self


class EvidenceRerankConfig(_StrictModel):
    policy: str = Field(min_length=1)
    topics: dict[str, tuple[str, str]] = Field(min_length=1)
    cues: dict[str, tuple[str, str]] = Field(min_length=1)


class DomainRulesBundle(_StrictModel):
    schema_version: Literal['review-domain-rules-v1']
    rule_version: str = Field(min_length=1)
    reported_facts: ReportedFactsConfig
    cooling_speed: CoolingSpeedConfig
    delivery_timing: DeliveryTimingConfig
    retrieval_query_expansion: QueryExpansionConfig
    evidence_rerank: EvidenceRerankConfig
    rules_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode='after')
    def _validate_cross_references(self):
        known_facts = set(self.reported_facts.patterns) | {
            self.delivery_timing.not_received_fact_id,
            self.delivery_timing.install_pending_fact_id,
        }
        known_policies = {
            self.reported_facts.policy,
            self.delivery_timing.policy,
            *self.cooling_speed.policies.all_policies,
        }
        if self.cooling_speed.fact_id not in self.reported_facts.patterns:
            raise ValueError(f'cooling_speed.fact_id 未在 reported_facts.patterns 中定义：{self.cooling_speed.fact_id}')
        for left, right in self.cooling_speed.extra_conflicts:
            if left not in self.reported_facts.patterns or right not in self.reported_facts.patterns:
                raise ValueError(f'cooling_speed.extra_conflicts 引用了未定义事实：{left}, {right}')
        for rule in self.retrieval_query_expansion.rules:
            if rule.fact_policy not in known_policies:
                raise ValueError(f'同义问法规则 {rule.id} 引用了未知策略：{rule.fact_policy}')
            if rule.fact_id not in known_facts:
                raise ValueError(f'同义问法规则 {rule.id} 引用了未知事实：{rule.fact_id}')
        for name, pair in self.resolved_rerank_topics().items():
            _require_regex(pair[0], f'evidence_rerank.topics.{name}[0]')
            _require_regex(pair[1], f'evidence_rerank.topics.{name}[1]')
        for name, pair in self.evidence_rerank.cues.items():
            _require_regex(pair[0], f'evidence_rerank.cues.{name}[0]')
            _require_regex(pair[1], f'evidence_rerank.cues.{name}[1]')
        return self

    def resolved_rerank_topics(self) -> dict[str, tuple[str, str]]:
        replacements = {
            **{f'{{domain:{key}}}': value for key, value in self.reported_facts.domains.items()},
            '{cooling:room_drop}': self.cooling_speed.room_drop,
        }
        def expand(template: str) -> str:
            result = template
            for placeholder, value in replacements.items():
                result = result.replace(placeholder, value)
            return result
        return {name: (expand(query), expand(quote)) for name, (query, quote) in self.evidence_rerank.topics.items()}

    def matching_query_variants(self, *, policy: str, fact_ids: tuple[str, ...], has_relation: bool) -> tuple[str, ...]:
        """按配置匹配当前事实策略与目标事实 ID，返回有序去重后的同义问法。"""
        variants: list[str] = []
        seen: set[str] = set()
        for rule in self.retrieval_query_expansion.rules:
            if rule.fact_policy != policy:
                continue
            if rule.single_fact_only and (has_relation or fact_ids != (rule.fact_id,)):
                continue
            if not rule.single_fact_only and rule.fact_id not in fact_ids:
                continue
            for variant in rule.variants:
                if variant not in seen:
                    seen.add(variant)
                    variants.append(variant)
        return tuple(variants)


def load_domain_rules(path: str | Path | None = None) -> DomainRulesBundle:
    """加载并严格校验版本化领域规则配置；重复键、非法正则或悬空引用均立即失败。"""
    if path is None:
        raw_text = resources.files('data_analysis_agent.agents').joinpath(
            'rules', DEFAULT_RULES_FILENAME
        ).read_text(encoding='utf-8')
    else:
        raw_text = Path(path).read_text(encoding='utf-8')
    raw = strict_json_loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError('领域规则配置根节点必须为 JSON 对象。')
    digest = content_sha256(raw)
    return DomainRulesBundle.model_validate({**raw, 'rules_sha256': digest})


@lru_cache(maxsize=1)
def domain_rules() -> DomainRulesBundle:
    """返回已校验的默认领域规则只读单例。"""
    return load_domain_rules()
