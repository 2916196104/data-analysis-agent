"""P6-C 内部串行执行；不持有数据库连接，不启用 P6-D 公开产物。"""
from dataclasses import dataclass, field
import json

from data_analysis_agent.agents.review_budget import P6SharedBudget, P6BudgetBlocked, P6InputIntegrityError
from data_analysis_agent.agents.review_multi_aspect import validate_p6_plan, validate_p6_completion
from data_analysis_agent.agents.review_plan import build_p6_query_plan
from data_analysis_agent.agents.review_preparation import P6PreparedAnalysis
from data_analysis_agent.agents.review_prompts import PROMPT_VERSION
from data_analysis_agent.agents.review_topics import analyze_aspect_topics
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256


@dataclass(frozen=True)
class P6ExecutedAnalysis:
    payload_json: str = field(repr=False)
    payload_sha256: str

    def _payload(self):
        value = json.loads(self.payload_json)
        if content_sha256(value) != self.payload_sha256:
            raise P6InputIntegrityError('P6 内部执行结果摘要失配。')
        return value

    @property
    def acceptance(self):
        return self._payload()['acceptance']

    def validated_results(self):
        value = self._payload()
        allowed = value['acceptance']['publishable_aspects']
        return [row for outcome in value['outcomes'] if outcome['aspect'] in allowed for row in outcome['results']]

    def revalidate_for_publication(self, prepared, **authorization):
        """在服务端冻结来源上重新验收；摘要本身不授予发布权限。无 I/O。"""
        from data_analysis_agent.agents.review_topics import validate_topic_result
        if type(prepared) is not P6PreparedAnalysis:
            raise P6InputIntegrityError('发布只接受本次服务端冻结输入。')
        try:
            source, value, goal = prepared.validate(), self._payload(), prepared.goal
            if (value['version'] != 'review-p6-execution-v1' or value['goal'] != goal.to_dict()
                    or value['prepared_sha256'] != prepared.payload_sha256
                    or value['required_aspects'] != list(goal.required_aspects)):
                raise ValueError('执行结果与冻结目标不一致。')
            for index, outcome in enumerate(value['outcomes']):
                frozen = prepared.get_input(outcome['aspect'])
                for result in outcome['results']:
                    data = result['data']
                    if (result['kind'] != 'aspect_drilldown' or result['result_id'] != f'r{index+1}'
                            or result['parameters'] != {'product_ids': goal.expected_scope['product_ids'],
                                'aspect': outcome['aspect'], 'sentiment': 'negative'}
                            or data['scope'] != frozen['query_scope']
                            or data['grouping_model'] != authorization['agent_identity']['model']
                            or any(data[key] != frozen[key] for key in
                                ('summary', 'aspect', 'sentiment', 'total', 'total_opinions', 'opinions', 'items'))
                            or not validate_topic_result(data)):
                        raise ValueError('主题来源、引用或重算计数不一致。')
            checked = validate_p6_completion(goal, goal.expected_scope, build_p6_query_plan(goal),
                value['outcomes'], current_snapshot=source['current_snapshot'], **authorization)
            if (checked != value['acceptance'] or not checked['plan_check']['passed']
                    or not checked['current_ranking_matches'] or not checked['outcome_coverage']):
                raise ValueError('父级/逐维独立验收不一致。')
            return value
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, ReviewDataError) as exc:
            raise P6InputIntegrityError('P6 发布完整性校验失败，禁止发布任何主题。') from exc

    def diagnostics(self):
        value = self._payload()
        return {'version':'review-p6-execution-v1', 'status':value['acceptance']['status'],
            'complete':value['acceptance']['complete'], 'required_aspects':value['required_aspects'],
            'mapped_agent_status':value['acceptance']['agent_status'], 'mapped_task_status':value['acceptance']['task_status'],
            'publishable_aspects':value['acceptance']['publishable_aspects'], 'budget':value['budget'],
            'per_aspect':[{k:row[k] for k in ('aspect','status','reason','model_attempts','elapsed_seconds')}
                          for row in value['outcomes']], 'public_release_enabled':False}


def execute_p6_prepared(prepared, *, settings, budget, conversation_id, agent_identity,
                        ranking_task, context=None, plan=None, scope=None):
    if type(prepared) is not P6PreparedAnalysis or type(budget) is not P6SharedBudget:
        raise P6InputIntegrityError('只接受本次服务端运行时准备对象和共享预算，不接受诊断 JSON。')
    goal = prepared.goal
    expected_sha = prepared.payload_sha256
    scope = goal.expected_scope if scope is None else scope
    plan = build_p6_query_plan(goal) if plan is None else plan
    authorization = dict(conversation_id=conversation_id, agent_identity=agent_identity,
                         ranking_task=ranking_task, context=context)

    def verify():
        try:
            prepared.validate()
            identity = {'model':settings.model, 'base_url':settings.base_url, 'prompt_version':PROMPT_VERSION}
            if (prepared.payload_sha256 != expected_sha or prepared.goal != goal or agent_identity != identity
                    or not validate_p6_plan(goal, scope, plan, **authorization)['passed']):
                raise ValueError('执行目标、身份、来源或计划不一致。')
        except (ValueError, TypeError, KeyError, ReviewDataError) as exc:
            raise P6InputIntegrityError('P6 全局输入/来源校验失败，禁止发布部分结果。') from exc

    verify()
    payload = prepared.validate()
    budget.start(goal.required_aspects, verify,
        input_hashes={r['input']['aspect']:r['input_sha256'] for r in payload['inputs']})
    outcomes = [{'aspect':a, 'status':'skipped_budget', 'reason':'not_started', 'results':[],
                 'model_attempts':0, 'elapsed_seconds':0} for a in goal.required_aspects]
    for index, aspect in enumerate(goal.required_aspects):
        verify()
        budget.begin_aspect(aspect)
        frozen = prepared.get_input(aspect)
        budget.verify_input(aspect, frozen)
        started, before = budget.clock(), budget.snapshot()['attempts']
        outcome = outcomes[index]
        try:
            budget.ensure_authorized(settings)
            data = analyze_aspect_topics(settings=settings, frozen_input=frozen, shared_budget=budget)
            source_fields = ('summary','aspect','sentiment','total','total_opinions','opinions','items')
            if (data['scope'] != frozen['query_scope'] or any(data[key] != frozen[key] for key in source_fields)
                    or data['grouping_model'] != settings.model):
                raise P6InputIntegrityError('分类过程修改了冻结来源、范围或观点，不能部分发布。')
            verify()
            budget.check_time()
            attempts = budget.snapshot()['attempts'] - before
            failed = data['analysis_status'] in ('partial','failed')
            state = ('skipped_budget' if failed and not attempts else 'failed' if failed
                     else 'complete' if data['topics'] else 'insufficient_evidence')
            outcome.update(status=state, reason=data['failures'][0]['code'] if data['failures'] else state,
                results=[{'kind':'aspect_drilldown', 'result_id':f'r{index+1}', 'parameters':{
                    'product_ids':scope['product_ids'], 'aspect':aspect, 'sentiment':'negative'}, 'data':data}])
            checked = validate_p6_completion(goal, scope, plan, outcomes,
                current_snapshot=payload['current_snapshot'], **authorization)
            if state in ('complete','insufficient_evidence') and checked['per_aspect'][index]['status'] != state:
                outcome.update(status='failed', reason='independent_aspect_acceptance_failed')
            budget.check_time()
        except P6InputIntegrityError:
            raise
        except P6BudgetBlocked as exc:
            outcome.update(status='failed' if budget.snapshot()['attempts'] > before else 'skipped_budget',
                           reason=exc.code, results=[])
        except ReviewDataError:
            raise
        except Exception as exc:
            outcome.update(status='failed', reason=type(exc).__name__, results=[])
        outcome.update(model_attempts=budget.snapshot()['attempts'] - before,
                       elapsed_seconds=round(budget.clock() - started, 6))
        verify()
    acceptance = validate_p6_completion(goal, scope, plan, outcomes,
        current_snapshot=payload['current_snapshot'], **authorization)
    verify()
    value = {'version':'review-p6-execution-v1', 'required_aspects':list(goal.required_aspects),
        'goal':goal.to_dict(), 'prepared_sha256':expected_sha, 'outcomes':outcomes,
        'acceptance':acceptance, 'budget':budget.snapshot(), 'public_release_enabled':False}
    return P6ExecutedAnalysis(canonical_json(value), content_sha256(value))
