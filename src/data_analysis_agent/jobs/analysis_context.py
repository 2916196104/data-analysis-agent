"""从有限任务链恢复分析条件；失败轮次只贡献用户请求，不贡献查询结果。"""

from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.jobs.result_storage import audit_ref_from_result
from data_analysis_agent.jobs.store import TaskNotFound, get_task
from data_analysis_agent.jobs.analysis_state import (
    empty_analysis_state, has_saved_integrity, restore_ranking, restore_pending_goal, analysis_state_for_scope, restore_focus,
)
from data_analysis_agent.reviews.parameters import BaselineScope


def _fetch_context_task(task_db, task_id, *, include_audit=False):
    """优先轻量读取历史任务；兼容测试中仅接收两个位置参数的 get_task Mock。"""
    try:
        return get_task(task_db, task_id, include_audit=include_audit)
    except TypeError:
        return get_task(task_db, task_id)


def load_analysis_context(task_db, parameters, *, max_turns=4):
    max_turns = max(1, min(int(max_turns), 8))
    task_id = parameters.get('context_task_id')
    conversation_id = parameters.get('conversation_id')
    selection = parameters.get('selection_scope')
    if not task_id or not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    context = {'user': None, 'users': [], 'validated': None, 'validated_history': [],
               'turns_read': 0, 'analysis_state': empty_analysis_state()}
    visited = set()
    keys = ('source', 'dataset_version', 'model', 'base_url', 'prompt_version', 'date_from',
            'date_to', 'baseline_from', 'baseline_to', 'as_of')

    def same_scope(scope):
        return all(parameters.get(key) == scope.get(key) for key in keys) and sorted(
            parameters.get('product_ids', [])) == sorted(scope.get('product_ids', []))

    while task_id and task_id not in visited and context['turns_read'] < max_turns:
        visited.add(task_id)
        try:
            task = _fetch_context_task(task_db, task_id, include_audit=False)
        except (TaskNotFound, ValueError, TypeError):
            break
        previous = task['parameters']
        if (not isinstance(previous, dict) or not isinstance(task.get('progress'), dict)
                or task['kind'] != 'analysis' or not isinstance(previous.get('question'), str)):
            break
        # 无会话归属的旧任务只能查看，不能作为任意新会话的上下文入口。
        if previous.get('conversation_id') != conversation_id:
            break
        if (task.get('progress', {}).get('analysis_integrity') is not None
                and audit_ref_from_result(task.get('result')) is not None):
            # 仅对通过会话与类型初筛的拆分大任务按需水合完整结果以校验 analysis_integrity。
            try:
                task = _fetch_context_task(task_db, task_id, include_audit=True)
            except (TaskNotFound, ValueError, TypeError):
                break
        if task.get('progress', {}).get('analysis_integrity') is not None and not has_saved_integrity(task):
            break
        result = task.get('result') or {}
        result_scope = result.get('scope') if isinstance(result, dict) else None
        if isinstance(result_scope, dict) and any(parameters.get(key) != result_scope.get(key) for key in keys[:5]):
            break
        valid = (isinstance(result, dict) and task['status'] == 'succeeded' and not task.get('error')
                 and result.get('status') == 'ok' and isinstance(result.get('validation'), dict)
                 and result['validation'].get('status') == 'passed')
        if context['validated'] is not None and not same_scope(result_scope or previous):break
        if selection is not None and context['validated'] is None:
            if any(parameters.get(key) != previous.get(key) for key in keys[:5]):
                break
            # 选择变化也约束失败轮的用户条件，不能等待成功事实才识别。
            if valid or 'selection_scope' not in context:
                try:
                    current = BaselineScope.model_validate(selection).model_dump()
                    original = BaselineScope.model_validate(previous.get('selection_scope') or {
                        key: previous[key] for key in BaselineScope.model_fields if key in previous}).model_dump()
                    BaselineScope.model_validate({key: previous[key] for key in BaselineScope.model_fields if key in previous})
                except (ValueError, KeyError, TypeError):
                    break
                if any(current[key] != parameters.get(key) or original[key] != previous.get(key) for key in keys[:5]):
                    break
                groups = {'product_ids': ('product_ids',), 'current_window': ('date_from', 'date_to'),
                          'baseline_window': ('baseline_from', 'baseline_to'), 'as_of': ('as_of',)}
                context.update(selection_scope=current, previous_selection_scope=original,
                    selection_changes=[name for name, fields in groups.items()
                        if any((sorted(current[key]) != sorted(original[key]) if key == 'product_ids'
                                else current[key] != original[key]) for key in fields)])
        elif not same_scope(previous) and not (valid and same_scope(result.get('scope', {}))):
            break
        context['turns_read'] += 1
        if context['user'] is None:
            context['user'] = {'task_id': task_id, 'question': previous['question'][:4000]}
        # Intent recovery uses only failures up to the latest successful turn;
        # older successful turns belong to structured history, not pending goals.
        if context['validated'] is None:
            context['users'].append({'task_id': task_id, 'question': previous['question'][:4000]})
        if context['validated'] is None and context['analysis_state']['pending_goal'] is None:
            context['analysis_state']['pending_goal'] = restore_pending_goal(task)
        if valid:
            try:
                scope = BaselineScope.model_validate(result['scope']).model_dump()
                expected = expected_result_scopes(result['results'],scope)
                checked = validate_report(result['results'], result['result_ids'], expected_scopes=expected)
                if checked['status'] == 'passed':
                    validated = {'task_id': task_id, 'scope': scope,
                        'sentiment': result.get('sentiment_intent'), 'analysis_type': result.get('analysis_intent'),
                        'queries': [{'kind': result['results'][key]['kind'], 'parameters': result['results'][key]['parameters']}
                                    for key in result['result_ids'] if key != 'overview'],
                        'result_refs': list(result['result_ids'])}
                    context['validated_history'].append({**validated,'question':previous['question'][:4000]})
                    if context['validated'] is None:
                        context['validated']=validated
                        focus=restore_focus(task,validated)
                        if focus:context['analysis_state']['focus']=focus
                        ranking=restore_ranking(task)
                        if ranking is not None:
                            context['analysis_state']['ranking_basis']=ranking
                            context.update(ranking_task=task, conversation_id=conversation_id)
            except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError, IndexError):
                pass
        task_id = previous.get('context_task_id')
    pending = context['analysis_state'].get('pending_goal') or {}
    effective = dict(pending.get('scope') or (context.get('validated') or {}).get('scope') or selection or parameters)
    current = context.get('selection_scope') or selection
    if current:
        for name, fields in {'product_ids': ('product_ids',), 'current_window': ('date_from', 'date_to'),
                             'baseline_window': ('baseline_from', 'baseline_to'), 'as_of': ('as_of',)}.items():
            if name in context.get('selection_changes', []):
                effective.update({key: current[key] for key in fields})
    context['analysis_state'] = analysis_state_for_scope(context, effective)
    return context if context['user'] else None
