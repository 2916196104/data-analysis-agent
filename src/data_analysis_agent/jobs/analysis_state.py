"""任务库原始快照的可信投影；不读取业务库，不接受客户端排名，不执行分析。"""
from copy import deepcopy
from dataclasses import fields

from data_analysis_agent.agents.review_acceptance import RequestGoal, validate_task_completion
from data_analysis_agent.agents.review_plan import ReviewQueryPlan
from data_analysis_agent.agents.review_task_contract import RuntimeQueryContract
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.jobs.store import analysis_result_integrity
from data_analysis_agent.reviews.opinions import ASPECTS
from data_analysis_agent.reviews.parameters import BaselineScope

VERSION = 'review-analysis-state-v1'


def empty_analysis_state():
    return {'version': VERSION, 'ranking_basis': None, 'pending_goal': None}


def has_saved_integrity(task):
    try:
        return task['progress'].get('analysis_integrity') == analysis_result_integrity(
            task['task_id'], task['status'], task['parameters'], task['result'])
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _source(task, result_id=None):
    source = {'task_id': task['task_id'], 'task_sha256': task['progress']['analysis_integrity']['sha256'],
              'integrity_version': 'analysis-task-integrity-v1', 'finished_at': task['finished_at']}
    if result_id is not None:
        source.update(result_id=result_id, result_sha256=content_sha256(task['result']['results'][result_id]))
    return source


def restore_ranking(task):
    """仅新任务且业务验收成功的完整当前窗口负面排名可成为 P6 输入。"""
    if not has_saved_integrity(task):
        return None
    try:
        result = task['result']
        if (task['kind'] != 'analysis' or task['status'] != 'succeeded' or task.get('error')
                or result['status'] != 'ok' or result['validation']['status'] != 'passed'
                or result['task_acceptance']['status'] != 'passed' or result['task_acceptance']['complete'] is not True
                or result['question'] != task['parameters']['question']):
            return None
        scope = BaselineScope.model_validate(result['scope']).model_dump()
        saved = result['query_plan']
        plan = ReviewQueryPlan(**{field.name: saved[field.name] for field in fields(ReviewQueryPlan)
                                  if field.name in saved and field.name != 'task_contract'})
        plan.task_contract = RuntimeQueryContract.model_validate_json(canonical_json(saved['task_contract']))
        if (len(scope['product_ids']) != 1 or plan.scope != scope or plan.analysis_type != 'issues'
                or result['analysis_intent'] != 'issues' or result['sentiment_intent'] != 'negative'
                or plan.sentiment != 'negative' or len(plan.requirements) != 1
                or plan.required_tools != ['get_top_issues'] or saved['required_tools'] != plan.required_tools
                or plan.requirements[0]['parameters'].get('aspect') is not None
                or plan.requirements[0]['parameters'].get('include_baseline')):
            return None
        ids = result['result_ids']
        selected = [result['results'][key] for key in ids]
        rows = [row for row in selected if row['kind'] != 'context']
        if len(rows) != 1 or rows[0]['kind'] != 'issues' or result['task_goal']['levels'] != ['statistics']:
            return None
        row = rows[0]
        if row['result_id'] not in ids or result['results'][row['result_id']] != row:
            return None
        checked = validate_report(result['results'], ids, sentiment_intent='negative', query_plan=plan,
                                  expected_scopes=expected_result_scopes(result['results'], scope))
        business = validate_task_completion(result['task_goal'], scope, plan, selected)
        if checked['status'] != 'passed' or business['status'] != 'passed' or not business['complete']:
            return None
        data = row['data']
        counts = data['current']['aspects']
        if set(counts) != set(ASPECTS) or data.get('baseline') is not None:
            return None
        denominator = data['current']['successful_reviews']
        if (type(denominator) is not int or denominator < 0
                or any(type(counts[aspect]['negative_review_count']) is not int
                       or not 0 <= counts[aspect]['negative_review_count'] <= denominator for aspect in ASPECTS)
                or data['definitions']['negative_rate_denominator'] != 'successful_reviews'):
            return None
        ordered = sorted(ASPECTS, key=lambda aspect: (-counts[aspect]['negative_review_count'], aspect))
        issues = data['issues']
        if len(issues) != len(ASPECTS) or any(issue['aspect'] != aspect or type(issue['rank']) is not int or issue['rank'] != rank
                or issue['negative_review_count'] != counts[aspect]['negative_review_count']
                for rank, (issue, aspect) in enumerate(zip(issues, ordered), 1)):
            return None
        return {'source': {**_source(task, row['result_id']), 'snapshot_at': result['snapshot_at'],
                           'validation_sha256': content_sha256({'numeric': checked, 'business': business})},
                'scope': scope, 'scope_sha256': content_sha256(scope), 'sentiment': 'negative',
                'denominator': {'field': 'successful_reviews', 'value': denominator},
                'ranking': [{'rank': issue['rank'], 'aspect': issue['aspect'],
                             'negative_review_count': issue['negative_review_count'],
                             'negative_rate': issue['negative_rate'], 'denominator': denominator} for issue in issues]}
    except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError, IndexError):
        return None


def restore_pending_goal(task):
    """未完成的是用户请求，不是已通过验收的分析事实；仅恢复合法澄清任务。"""
    if not has_saved_integrity(task):
        return None
    try:
        result = task['result']
        if (task['status'] != 'succeeded' or task.get('error') or result['status'] != 'unsupported'
                or result['user_intent'].get('needs_clarification') is not True):
            return None
        goal = RequestGoal(**result['request_goal'])
        if list(goal.levels) != ['themes'] or goal.sentiment != 'negative' or goal.aspect is not None:
            return None
        scope = BaselineScope.model_validate(result['scope']).model_dump()
        if (len(scope['product_ids']) != 1 or result['question'] != task['parameters']['question']
                or goal.original_question != result['question']):
            return None
        return {'source': _source(task), 'scope': scope, 'scope_sha256': content_sha256(scope),
                'goal': deepcopy(result['request_goal']), 'status': 'awaiting_aspect', 'completed': False}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def analysis_state_for_scope(context, scope):
    """使用方必须在范围最终绑定后投影，不能把旧排名用于新的显式范围。"""
    state = empty_analysis_state()
    try:
        bound = BaselineScope.model_validate(scope).model_dump()
        for key in ('ranking_basis', 'pending_goal'):
            value = (context or {}).get('analysis_state', {}).get(key)
            if value and value['scope'] == bound and value['scope_sha256'] == content_sha256(bound):
                state[key] = deepcopy(value)
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return state


def pending_context(context):
    """已核验的结构化目标优先于重复解析旧文字；更新的用户请求仍按原规则覆盖。"""
    users = (context or {}).get('users') or [((context or {}).get('user') or {})]
    pending = (context or {}).get('analysis_state', {}).get('pending_goal')
    if pending:
        ids = [user.get('task_id') for user in users]
        if pending['source']['task_id'] in ids:
            return pending['goal'], users[:ids.index(pending['source']['task_id'])]
    # 范围变化已使结构化目标失效时，不能再从旧文字重建同一个目标。
    if (context or {}).get('selection_changes'):
        return None, []
    return None, users
