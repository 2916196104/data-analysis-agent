"""从有限任务链恢复分析条件；失败轮次只贡献用户请求，不贡献查询结果。"""

from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.jobs.store import TaskNotFound, get_task
from data_analysis_agent.jobs.analysis_state import (
    empty_analysis_state, has_saved_integrity, restore_ranking, restore_pending_goal, analysis_state_for_scope,
)
from data_analysis_agent.reviews.parameters import BaselineScope


def load_analysis_context(task_db, parameters, *, max_turns=4):
    task_id = parameters.get('context_task_id')
    conversation_id = parameters.get('conversation_id')
    selection = parameters.get('selection_scope')
    if not task_id or not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    context = {'user': None, 'users': [], 'validated': None, 'turns_read': 0, 'analysis_state': empty_analysis_state()}
    visited = set()
    keys = ('source', 'dataset_version', 'model', 'base_url', 'prompt_version', 'date_from',
            'date_to', 'baseline_from', 'baseline_to', 'as_of')

    def same_scope(scope):
        return all(parameters.get(key) == scope.get(key) for key in keys) and sorted(
            parameters.get('product_ids', [])) == sorted(scope.get('product_ids', []))

    while task_id and task_id not in visited and context['turns_read'] < max_turns:
        visited.add(task_id)
        try:
            task = get_task(task_db, task_id)
        except (TaskNotFound, ValueError, TypeError):
            break
        previous = task['parameters']
        if (not isinstance(previous, dict) or not isinstance(task.get('progress'), dict)
                or task['kind'] != 'analysis' or not isinstance(previous.get('question'), str)):
            break
        # 无会话归属的旧任务只能查看，不能作为任意新会话的上下文入口。
        if previous.get('conversation_id') != conversation_id:
            break
        if task.get('progress', {}).get('analysis_integrity') is not None and not has_saved_integrity(task):
            break
        result = task.get('result') or {}
        valid = (isinstance(result, dict) and task['status'] == 'succeeded' and not task.get('error')
                 and result.get('status') == 'ok' and isinstance(result.get('validation'), dict)
                 and result['validation'].get('status') == 'passed')
        if selection is not None:
            if any(parameters.get(key) != previous.get(key) for key in keys[:5]):
                break
        elif not same_scope(previous) and not (valid and same_scope(result.get('scope', {}))):
            break
        context['turns_read'] += 1
        if context['user'] is None:
            context['user'] = {'task_id': task_id, 'question': previous['question'][:4000]}
        context['users'].append({'task_id': task_id, 'question': previous['question'][:4000]})
        if context['analysis_state']['pending_goal'] is None:
            context['analysis_state']['pending_goal'] = restore_pending_goal(task)
        if valid:
            try:
                scope = BaselineScope.model_validate(result['scope']).model_dump()
                expected = expected_result_scopes(result['results'],scope)
                checked = validate_report(result['results'], result['result_ids'], expected_scopes=expected)
                if checked['status'] == 'passed':
                    if selection is not None:
                        current = BaselineScope.model_validate(selection).model_dump()
                        if any(current[key] != parameters.get(key) for key in keys[:5]):
                            break
                        original = BaselineScope.model_validate(previous.get('selection_scope') or {
                            key: previous[key] for key in BaselineScope.model_fields if key in previous}).model_dump()
                        groups = {'product_ids': ('product_ids',), 'current_window': ('date_from', 'date_to'),
                                  'baseline_window': ('baseline_from', 'baseline_to'), 'as_of': ('as_of',)}
                        context.update(selection_scope=current, previous_selection_scope=original,
                            selection_changes=[name for name, fields in groups.items()
                                if any((sorted(current[key]) != sorted(original[key]) if key == 'product_ids'
                                        else current[key] != original[key]) for key in fields)])
                    context['validated'] = {'task_id': task_id, 'scope': scope,
                        'sentiment': result.get('sentiment_intent'), 'analysis_type': result.get('analysis_intent'),
                        'queries': [{'kind': result['results'][key]['kind'], 'parameters': result['results'][key]['parameters']}
                                    for key in result['result_ids'] if key != 'overview'],
                        'result_refs': list(result['result_ids'])}
                    context['analysis_state']['ranking_basis'] = restore_ranking(task)
                    break
            except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError, IndexError):
                pass
        task_id = previous.get('context_task_id')
    effective = ((context.get('selection_scope') or selection) if context.get('selection_changes')
                 else (context.get('validated') or {}).get('scope')) or selection or parameters
    context['analysis_state'] = analysis_state_for_scope(context, effective)
    return context if context['user'] else None
