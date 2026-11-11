"""P6-A 纯契约：冻结目标、核验选择与逐维产物；不查询数据库或执行工具。"""
from dataclasses import asdict, dataclass, replace
import json
import re

from data_analysis_agent.agents.review_acceptance import (
    RequestGoal, build_request_goal, build_task_goal, validate_task_completion,
)
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.jobs.analysis_state import restore_ranking
from data_analysis_agent.reviews.opinions import ASPECTS
from data_analysis_agent.reviews.parameters import BaselineScope

MODE = 'multi_aspect_negative_drilldown'
GOAL_VERSION = 'review-request-goal-p6-v1'
SELECTION_RULE = 'negative-count-desc-aspect-asc-top3-v1'


def select_required_aspects(basis):
    """结构校验及确定性选择；此纯函数本身不授予来源信任。"""
    rows = basis['ranking']
    denominator = basis['denominator']
    if (denominator['field'] != 'successful_reviews' or type(denominator['value']) is not int
            or denominator['value'] < 0 or len(rows) != len(ASPECTS)
            or {row['aspect'] for row in rows} != set(ASPECTS)):
        raise ValueError('需要完整六维排名和成功标注分母。')
    for row in rows:
        count = row['negative_review_count']
        if (type(count) is not int or not 0 <= count <= denominator['value']
                or type(row['denominator']) is not int or row['denominator'] != denominator['value']):
            raise ValueError('排名计数或分母无效。')
    ordered = sorted(rows, key=lambda row: (-row['negative_review_count'], row['aspect']))
    if rows != ordered or any(type(row['rank']) is not int or row['rank'] != i for i, row in enumerate(rows, 1)):
        raise ValueError('可信历史排名顺序不正确，不能静默重排后使用。')
    return tuple(row['aspect'] for row in ordered if row['negative_review_count'] > 0)[:3]


@dataclass(frozen=True, kw_only=True)
class P6RequestGoal(RequestGoal):
    required_aspects: tuple[str, ...]
    selection_basis_json: str
    selection_basis_sha256: str

    def __post_init__(self):
        if (type(self.required_aspects) is not tuple or not 1 <= len(self.required_aspects) <= 3
                or len(set(self.required_aspects)) != len(self.required_aspects)
                or not set(self.required_aspects) <= set(ASPECTS)
                or self.levels != ('themes',) or self.sentiment != 'negative'
                or self.aspect is not None or self.topic is not None):
            raise ValueError('P6 目标必须保留一至三个独立负向主题维度。')

    @property
    def analysis_mode(self):
        return MODE

    @property
    def per_aspect_level(self):
        return 'themes'

    @property
    def selection_basis(self):
        # JSON 字符串封存嵌套集合，读取时返回副本，不能经可变字典修改冻结目标。
        return json.loads(self.selection_basis_json)

    @property
    def ranking_source(self):
        basis = self.selection_basis['ranking_basis']
        return {**basis['source'], 'scope_sha256': basis['scope_sha256']}

    @property
    def expected_scope(self):
        return self.selection_basis['ranking_basis']['scope']

    def to_dict(self):
        result = asdict(self)
        result.pop('selection_basis_json')
        return {**result, 'version': GOAL_VERSION, 'levels': list(self.levels),
                'statistic_families': list(self.statistic_families),
                'required_aspects': list(self.required_aspects), 'analysis_mode': MODE,
                'per_aspect_level': 'themes', 'ranking_source': self.ranking_source,
                'expected_scope': self.expected_scope, 'selection_basis': self.selection_basis}

    @classmethod
    def from_dict(cls, value):
        """只解析 P6 版本；来源资格仍须由原始任务独立复核。"""
        try:
            request = {key: value[key] for key in RequestGoal.__dataclass_fields__}
            for key in ('levels', 'statistic_families'):
                request[key] = tuple(request[key])
            goal = cls(**request, required_aspects=tuple(value['required_aspects']),
                selection_basis_json=canonical_json(value['selection_basis']),
                selection_basis_sha256=value['selection_basis_sha256'])
            if goal.to_dict() != value or goal.selection_basis_sha256 != content_sha256(goal.selection_basis):
                raise ValueError('P6 目标版本、序列化或选择摘要失配。')
            return goal
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('P6 目标字段不完整。') from exc


@dataclass(frozen=True)
class P6Decision:
    route: str
    reason: str
    goal: P6RequestGoal | None = None


def freeze_p6_goal(question, *, scope, conversation_id, agent_identity, ranking_task, context=None):
    """ranking_task 必须由服务端任务库读取；不接收客户端 ranking_basis 或 Planner 目标。"""
    request = build_request_goal(question, context)
    explicit = build_request_goal(question)
    excluded = re.search(r'多少|几条|数量|占比|比例|排名|原文|原话|证据|举例|例子|'
        r'好评|正面|正向|优点|趋势|变化|按[日月天]|按月份|比较|对比|基线|窗口|'
        r'覆盖率|数据质量|失败标注|缺失正文|待标注|星级|评分', question)
    deeper = explicit and (explicit.levels == ('themes',) or re.search(
        r'具体.*(?:问题|抱怨|投诉)|(?:主题|问题).*(?:深入|下钻)|(?:深入|下钻).*(?:主题|问题)', question))
    if (not request or not deeper or excluded or request.aspect or request.topic
            or request.sentiment not in (None, 'negative') or request.source != 'user_request'):
        return P6Decision('existing', 'explicit_goal_priority')
    # 先确定独立用户产物，再读取排名绑定维度，Planner 不参与目标构造。
    request = replace(request, levels=('themes',), sentiment='negative', statistic_families=())
    if not ranking_task:
        return P6Decision('clarify', 'missing_trusted_ranking')
    try:
        if (not isinstance(conversation_id, str) or not conversation_id.strip()
                or ranking_task['parameters']['conversation_id'] != conversation_id
                or not agent_identity or ranking_task['parameters']['model_identity'] != agent_identity
                or ranking_task['result']['agent_model_identity'] != agent_identity):
            return P6Decision('reject', 'session_or_agent_identity_mismatch')
        basis = restore_ranking(ranking_task)
        if basis is None:
            return P6Decision('reject', 'unverified_ranking_source')
        bound = BaselineScope.model_validate(scope).model_dump()
        if (basis['scope'] != bound or basis['scope_sha256'] != content_sha256(bound)
                or len(bound['product_ids']) != 1 or basis['sentiment'] != 'negative'):
            return P6Decision('clarify', 'ranking_scope_mismatch')
        aspects = select_required_aspects(basis)
        if not aspects:
            return P6Decision('reject', 'no_negative_evidence')
        selection = {'selection_rule_version': SELECTION_RULE, 'ranking_basis': basis,
                     'required_aspects': list(aspects)}
        goal = P6RequestGoal(**asdict(request), required_aspects=aspects,
            selection_basis_json=canonical_json(selection), selection_basis_sha256=content_sha256(selection))
        return P6Decision('p6_contract', 'historical_goal_frozen', goal)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return P6Decision('reject', 'invalid_ranking_source')


def check_current_ranking(goal, snapshot):
    """消费服务端同快照统计；不读取数据，不用当前排名替换历史必做集合。"""
    try:
        basis = goal.selection_basis['ranking_basis']
        counts = snapshot['negative_review_counts']
        return (snapshot['scope'] == goal.expected_scope and type(snapshot['denominator']['value']) is int
                and snapshot['denominator'] == basis['denominator']
                and set(counts) == set(ASPECTS) and all(type(counts[row['aspect']]) is int
                    and counts[row['aspect']] == row['negative_review_count'] for row in basis['ranking']))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def validate_p6_plan(goal, scope, plan, *, conversation_id, agent_identity, ranking_task, context=None):
    """从原始来源重算目标，不使用计划自报的必做集合证明选择正确。"""
    checks = []
    try:
        expected = freeze_p6_goal(goal.original_question, scope=scope, conversation_id=conversation_id,
            agent_identity=agent_identity, ranking_task=ranking_task, context=context)
        checks.append({'name': 'independent_goal', 'passed': expected.goal is not None
                       and goal.to_dict() == expected.goal.to_dict()})
        checks.append({'name': 'scope_and_mode', 'passed': scope == goal.expected_scope == plan.scope
            and plan.analysis_type == 'aspect_drilldown' and plan.sentiment == 'negative' and not plan.unsupported_reason
            and not plan.optional_tools})
        requirements = plan.requirements
        if plan.analysis_mode is not None:
            checks.append({'name': 'p6_metadata', 'passed': plan.analysis_mode == MODE
                           and tuple(plan.required_aspects) == goal.required_aspects})
        checks.append({'name': 'exact_required_aspects', 'passed':
            [row['parameters'].get('aspect') for row in requirements] == list(goal.required_aspects)})
        checks.append({'name': 'per_aspect_capability', 'passed': bool(requirements) and all(
            row['tool'] == 'get_aspect_drilldown'
            and plan.parameters_match(row['tool'], row['parameters'], {'product_ids': scope['product_ids'],
                'aspect': aspect, 'sentiment': 'negative'})
            and (row.get('execution_scope') is None or row['execution_scope'] == scope)
            for aspect, row in zip(goal.required_aspects, requirements))})
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        checks.append({'name': 'contract_shape', 'passed': False})
    return {'passed': bool(checks) and all(row['passed'] for row in checks), 'checks': checks}


def validate_p6_completion(goal, scope, plan, outcomes, *, current_snapshot,
                           conversation_id, agent_identity, ranking_task, context=None):
    """纯离线父/子验收；outcomes 是完整必做集合，不能用成功子集替换。"""
    plan_check = validate_p6_plan(goal, scope, plan, conversation_id=conversation_id,
        agent_identity=agent_identity, ranking_task=ranking_task, context=context)
    freshness = check_current_ranking(goal, current_snapshot)
    outcomes = outcomes if isinstance(outcomes, (list, tuple)) else []
    try:
        coverage = [row['aspect'] for row in outcomes] == list(goal.required_aspects)
    except (KeyError, TypeError):
        coverage = False
    valid = plan_check['passed'] and freshness and coverage
    children = []
    allowed = {'complete', 'insufficient_evidence', 'failed', 'skipped_budget'}
    for aspect in goal.required_aspects:
        found = [row for row in outcomes if isinstance(row, dict) and row.get('aspect') == aspect]
        outcome = found[0] if len(found) == 1 else {}
        status, acceptance = outcome.get('status', 'failed'), None
        if status not in allowed:
            valid, status = False, 'failed'
        if not valid and status in ('complete', 'insufficient_evidence'):
            status = 'failed'
        if valid and status in ('complete', 'insufficient_evidence'):
            parameters = {'product_ids': scope['product_ids'], 'aspect': aspect, 'sentiment': 'negative'}
            child_plan = build_query_plan('执行本轮已确认的只读查询\n' + canonical_json(
                {'tool': 'get_aspect_drilldown', 'parameters': parameters}), scope)
            child_goal = build_task_goal(goal.original_question, scope, child_plan, {},
                request_goal=RequestGoal(goal.original_question, ('themes',), aspect, 'negative'))
            try:
                results = outcome['results']
                # 当前窗口、标注身份和维度来源错误是全局边界失败，不允许部分发布。
                scope_fields = [key for key in scope if key not in ('baseline_from', 'baseline_to')]
                boundary = len(results) == 1 and all(row['kind'] == 'aspect_drilldown'
                    and row['data']['aspect'] == aspect and row['data']['sentiment'] == 'negative'
                    and type(row['data']['total']) is int
                    and row['data']['total'] == current_snapshot['negative_review_counts'][aspect]
                    and all(row['data']['scope'].get(key) == scope[key] for key in scope_fields)
                    and child_plan.parameters_match('get_aspect_drilldown', row['parameters'], parameters)
                    for row in results)
                if not boundary:
                    valid = False
                acceptance = validate_task_completion(child_goal, scope, child_plan, results)
                expected_status = 'passed' if status == 'complete' else 'insufficient_evidence'
                if not boundary or acceptance['status'] != expected_status:
                    status = 'failed'
            except (KeyError, TypeError, ValueError, AttributeError, IndexError):
                valid, status = False, 'failed'
        children.append({'aspect': aspect, 'status': status, 'acceptance': acceptance})
    publishable = [row['aspect'] for row in children if row['status'] == 'complete' and row['acceptance']
                   and row['acceptance']['complete']] if valid else []
    states = [row['status'] for row in children]
    status = ('failed' if not valid else 'complete' if len(publishable) == len(goal.required_aspects)
        else 'partial' if publishable else 'insufficient_evidence' if all(s == 'insufficient_evidence' for s in states)
        else 'skipped_budget' if all(s == 'skipped_budget' for s in states) else 'failed')
    agent_status, task_status = {
        'complete': ('ok', 'succeeded'), 'partial': ('partial', 'partial'),
        'insufficient_evidence': ('unsupported', 'succeeded'),
        'failed': ('error', 'failed'), 'skipped_budget': ('error', 'failed'),
    }[status]
    return {'version': 'review-p6-acceptance-v1', 'status': status, 'complete': status == 'complete',
            'required_aspects': list(goal.required_aspects), 'per_aspect': children,
            'publishable_aspects': publishable, 'plan_check': plan_check,
            'current_ranking_matches': freshness, 'outcome_coverage': coverage,
            'agent_status': agent_status, 'task_status': task_status}
