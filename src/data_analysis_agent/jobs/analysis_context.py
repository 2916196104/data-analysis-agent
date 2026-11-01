"""从有限任务链恢复分析条件；失败轮次只贡献用户请求，不贡献查询结果。"""

from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.jobs.store import TaskNotFound, get_task
from data_analysis_agent.reviews.parameters import BaselineScope


def load_analysis_context(task_db, parameters, *, max_turns=4):
    task_id = parameters.get('context_task_id')
    if not task_id:
        return None
    context = {'user': None, 'users': [], 'validated': None, 'turns_read': 0}
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
        except TaskNotFound:
            break
        previous = task['parameters']
        if task['kind'] != 'analysis' or not isinstance(previous.get('question'), str):
            break
        if previous.get('conversation_id') and parameters.get('conversation_id') != previous['conversation_id']:
            break
        result = task.get('result') or {}
        valid = (isinstance(result, dict) and task['status'] == 'succeeded' and not task.get('error')
                 and result.get('status') == 'ok' and result.get('validation', {}).get('status') == 'passed')
        if not same_scope(previous) and not (valid and same_scope(result.get('scope', {}))):
            break
        context['turns_read'] += 1
        if context['user'] is None:
            context['user'] = {'task_id': task_id, 'question': previous['question'][:4000]}
        context['users'].append({'task_id': task_id, 'question': previous['question'][:4000]})
        if valid:
            try:
                scope = BaselineScope.model_validate(result['scope']).model_dump()
                expected = expected_result_scopes(result['results'],scope)
                checked = validate_report(result['results'], result['result_ids'], expected_scopes=expected)
                if checked['status'] == 'passed':
                    context['validated'] = {'task_id': task_id, 'scope': scope,
                        'sentiment': result.get('sentiment_intent'), 'analysis_type': result.get('analysis_intent'),
                        'queries': [{'kind': result['results'][key]['kind'], 'parameters': result['results'][key]['parameters']}
                                    for key in result['result_ids'] if key != 'overview'],
                        'result_refs': list(result['result_ids'])}
                    break
            except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError, IndexError):
                pass
        task_id = previous.get('context_task_id')
    return context if context['user'] else None
