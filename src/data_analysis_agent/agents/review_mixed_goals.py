"""Conservative clause binding for mixed trend, evidence and six-aspect requests."""
import re
from copy import deepcopy

VERSION = 'review-mixed-goal-v1'
ASPECTS = {'包装': 'packaging', '物流': 'logistics', '客服': 'service', '服务': 'service',
           '质量': 'quality', '效果': 'effectiveness', '价格': 'price'}


def mixed_subgoals(question, scope):
    """Return None outside the narrow mixed grammar, or [] when it is ambiguous."""
    if not (re.search(r'趋势|变化', question) and re.search(r'原文|原话|证据', question)
            and re.search(r'六\s*(?:个)?维', question)):
        return None
    clauses = [s.strip(' 。？?！!') for s in re.split(r'[、，,；;]|以及|并且', question) if s.strip(' 。？?！!')]
    goals = []
    for text in clauses:
        # Do not silently discard unsupported qualifiers or scope restrictions.
        if re.search(r'最近|只看|仅看|排除|不含|除外|之前|之后|以来|以内|以上|以下|每周|每年|原因|建议|基线|对比|比较', text):
            return []
        recognized = text
        for product in scope.get('product_ids', []):
            recognized = recognized.replace(str(product), '')
        recognized = re.sub(r'查看|分析|请问|帮我|分别|商品|产品|差评|负面|评论|评价|按月|按日|按天|'
            r'整体|总体|趋势|变化|原文|原话|证据|六\s*(?:个)?维(?:度)?|统计|主要|问题|'
            r'包装|物流|客服|服务|质量|效果|价格|有哪些|什么|请|看|的|\s', '', recognized)
        if recognized:
            return []
        types = [name for name, pattern in (
            ('trend', r'趋势|变化'), ('evidence', r'原文|原话|证据'), ('summary', r'六\s*(?:个)?维'))
            if re.search(pattern, text)]
        aspects = {value for word, value in ASPECTS.items() if word in text}
        if len(types) != 1 or len(aspects) > 1:
            return []
        family = types[0]
        aspect = next(iter(aspects), None)
        if family == 'summary' and aspect is not None:
            return []
        if family == 'evidence' and aspect is None:
            return []
        if family == 'trend' and aspect is None and not re.search(r'整体|总体', text):
            return []
        if re.search(r'好评|正向|正面|\d+\s*条|第\s*\d+\s*页', text):
            return []
        period = 'day' if re.search(r'按日|按天', text) else 'month'
        goals.append({'level': 'evidence' if family == 'evidence' else 'statistics',
                      'family': family, 'aspect': aspect, 'sentiment': 'negative',
                      'topic': None, 'overall': aspect is None, 'scope': deepcopy(scope),
                      'period': period if family == 'trend' else None})
    return goals if len(goals) == 3 and {g['family'] for g in goals} == {'trend', 'evidence', 'summary'} else []


def tool_request(goal):
    if goal['family'] == 'trend':
        tool = 'get_aspect_trend' if goal['aspect'] else 'get_trend'
        return {'tool': tool, 'parameters': {**({'aspect': goal['aspect']} if goal['aspect'] else {}),
                                             'period': goal['period']}}
    if goal['family'] == 'summary':
        return {'tool': 'get_top_issues', 'parameters': {}}
    return {'tool': 'get_evidence', 'parameters': {'sentiment': goal['sentiment'], 'aspect': goal['aspect'],
                                                    'page': 1, 'page_size': 3, 'window': 'current'}}
