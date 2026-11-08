"""业务任务验收：与冻结评估/检索许可分离，不查询数据库或调用模型。"""
from copy import deepcopy
import json
import re

from data_analysis_agent.agents.review_semantics import opinion_request


def build_task_goal(question, scope, plan, resolution):
    if plan.analysis_type in ('semantic_evidence', 'pros_cons'):
        return None
    original = question
    if question.startswith('执行本轮已确认的只读查询'):
        try:
            original = json.loads(question.split('\n', 1)[1]).get('question') or plan.answer_question or question
        except (ValueError, IndexError):
            return None
    semantic = opinion_request(original)
    levels = []
    if semantic['theme_summary'] or plan.analysis_type == 'aspect_drilldown':
        levels.append('themes')
    elif semantic['examples'] or semantic['content_detail'] or plan.analysis_type in ('evidence', 'opinion_breakdown'):
        levels.append('evidence')
    if not levels or (re.search(r'数量|占比|比例|多少', original) and any(
            request['tool'] in ('get_top_issues', 'get_positive_aspects') for request in plan.requirements)):
        levels.append('statistics')
    return {'levels': levels, 'aspect': semantic['aspect'], 'sentiment': plan.sentiment,
            'expected_scope': deepcopy(scope), 'scope_resolution': deepcopy(resolution or {})}


def validate_task_completion(goal, scope, plan, selected):
    checks = []
    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    check('本轮有效范围', scope == goal['expected_scope'], '当前范围须与执行前确认的任务范围一致。')
    resolution = goal['scope_resolution']
    previous = resolution.get('inherited_scope')
    fields = {'product_ids': ('product_ids',), 'current_window': ('date_from', 'date_to'),
              'baseline_window': ('baseline_from', 'baseline_to'), 'as_of': ('as_of',)}
    inherited_ok = not previous or all(all(scope.get(key) == previous.get(key) for key in fields[name])
        for name, origin in resolution.get('origins', {}).items() if origin == 'inherited')
    check('继承范围', inherited_ok, '未明确修改的继承字段须保持一致，显式选择优先。')
    check('所需查询', plan.satisfied(selected), '计划中的查询及完整筛选条件须已执行。')
    rows = [row for row in selected if row['kind'] != 'context']
    scoped = True
    for row in rows:
        actual = row['data'].get('scope', {})
        expected = {**scope}
        if row['parameters'].get('window') == 'baseline':
            expected.update(date_from=scope['baseline_from'], date_to=scope['baseline_to'])
        approved = next((request.get('execution_scope') for request in plan.requirements
            if request.get('execution_scope') and plan.parameters_match(request['tool'], row['parameters'], request['parameters'])
            and all(actual.get(key) == value for key, value in request['execution_scope'].items())), None)
        if approved:
            expected.update(approved)
        scoped &= all(actual.get(key) == expected.get(key) for key in
                      ('source', 'dataset_version', 'model', 'base_url', 'prompt_version', 'date_from', 'date_to', 'as_of'))
        scoped &= sorted(actual.get('product_ids', [])) == sorted(expected['product_ids'])
    check('实际查询范围', scoped, '查询快照须使用确认的商品、数据集、日期和标注身份。')
    missing = False
    if 'themes' in goal['levels']:
        topics = [row['data'] for row in rows if row['kind'] == 'aspect_drilldown']
        check('子主题查询', bool(topics), '具体表现归纳须读取候选子主题，维度计数不能替代。')
        complete = bool(topics) and all(data['analysis_status'] == 'complete' for data in topics)
        check('分类批次完整', complete, '超时、失败或部分复核不能作为完整主题分析。')
        supported = bool(topics) and all(data['topics'] and all(
            topic['evidence_opinion_ids'] and topic['review_ids']
            and topic['review_count'] == len(set(topic['review_ids']))
            and topic['opinion_count'] == len(set(topic['opinion_ids'])) for topic in data['topics']) for data in topics)
        # 空集合是证据不足；错误类型或失败批次仍是执行/验收失败。
        missing = bool(topics) and all(not data['topics'] and data['analysis_status'] in ('complete', 'insufficient_data') for data in topics)
        check('子主题及去重证据', supported, '候选主题须有来源观点与去重计数，不得编造全量频次。')
    if 'evidence' in goal['levels'] or 'themes' in goal['levels']:
        content = [row for row in rows if row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown')]
        check('观点查询', bool(content), '内容任务须执行观点、主题或原文查询。')
        check('观点筛选', all((not goal['aspect'] or row['data'].get('aspect') == goal['aspect'])
            and (not goal['sentiment'] or row['data'].get('sentiment') in (goal['sentiment'], None)) for row in content),
            '观点查询须保留本轮维度和情感。')
        evidence = [item for row in rows if row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown')
                    for item in row['data'].get('items', [])]
        supported = any(item.get('evidence_ref') and item.get('opinions') for item in evidence)
        missing |= not supported and any(row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown') for row in rows)
        check('原文引用', supported, '须存在带原始评论来源的观点，RAG 候选或计数不替代引用。')
    hard_failures = [row for row in checks if not row['passed'] and row['name'] not in ('子主题及去重证据', '原文引用', '分类批次完整')]
    if not missing:
        hard_failures.extend(row for row in checks if not row['passed'] and row['name'] in ('子主题及去重证据', '原文引用'))
    if any(not row['passed'] for row in checks if row['name'] == '分类批次完整') and not missing:
        hard_failures.append({'name': '分类批次完整'})
    status = 'failed' if hard_failures else 'insufficient_evidence' if missing else 'passed'
    return {'status': status, 'complete': status == 'passed', 'checks': checks,
            'classification_status': 'model_candidates_unreviewed' if 'themes' in goal['levels'] else 'not_requested',
            'message': '当前范围证据不足，尚未完成所请求的观点或子主题分析；没有证据不表示没有问题。'
                       if status == 'insufficient_evidence' else '本轮任务目标、范围与证据验收通过。' if status == 'passed'
                       else '本轮任务目标、范围或所需查询未通过验收，未发布分析结论。'}
