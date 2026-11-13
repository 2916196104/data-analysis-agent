"""业务任务验收：与冻结评估/检索许可分离，不查询数据库或调用模型。"""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
import re

from data_analysis_agent.agents.review_prompts import detect_sentiment_directions, detect_sentiment_intent


@dataclass(frozen=True)
class RequestGoal:
    original_question: str
    levels: tuple[str, ...]
    aspect: str | None = None
    sentiment: str | None = None
    topic: str | None = None
    statistic_families: tuple[str, ...] = ()
    source: str = 'user_request'


# 验收能力不是工具路由：只声明用户要求的产物，不生成计划或读取本轮 Intent。
CAPABILITIES = {
    'themes': {'get_aspect_drilldown'},
    'opinion_content': {'get_opinion_breakdown', 'get_aspect_drilldown'},
    'evidence': {'get_evidence'},
    'statistics': {'get_top_issues', 'get_positive_aspects', 'get_trend', 'get_aspect_trend',
                   'get_data_quality', 'get_rating_distribution', 'compare_products', 'compare_time_windows'},
}
STATISTIC_FAMILIES = {
    'summary': {'get_top_issues', 'get_positive_aspects'},
    'trend': {'get_trend', 'get_aspect_trend'},
    'quality': {'get_data_quality'}, 'ratings': {'get_rating_distribution'},
    'comparison': {'compare_products'}, 'window_comparison': {'compare_time_windows'},
}
REFERENCE = re.compile(
    r'(?:那|那么|再|也|只看|仅看)?(?:使用效果|质量|效果|包装|物流|客服|服务|价格)(?:方面|问题)?(?:呢|怎么样)?[？?。]*|'
    r'(?:那|那么)?(?:主要)?集中在(?:哪些|什么)(?:地方|方面)(?:呢)?[？?。]*|'
    r'(?:那|那么)?(?:好评|差评|正面|负面|优点|缺点)(?:呢|吧)?[？?。]*')


def build_request_goal(question, context=None):
    """在编译前冻结请求产物；不调用 opinion_request、Router、Planner 或模型。"""
    from data_analysis_agent.agents.review_semantics import active_request, ASPECT_SWITCH
    text = active_request(question)
    if question.startswith('执行本轮已确认的只读查询'):
        try:
            declared = json.loads(question.split('\n', 1)[1])
            if declared.get('question'):
                return build_request_goal(declared['question'], context)
            tool = declared['tool']
            level = next((level for level, tools in CAPABILITIES.items() if tool in tools), None)
            if level is None:
                return None
            params = declared['parameters']
            direction = 'positive' if tool == 'get_positive_aspects' else 'negative' if tool in {
                'get_top_issues', 'get_trend', 'get_aspect_trend', 'compare_products', 'compare_time_windows'} else params.get('sentiment')
            families = tuple(family for family, tools in STATISTIC_FAMILIES.items() if tool in tools)
            return RequestGoal(question, (level,), params.get('aspect'), direction,
                               params.get('topic'), families, source='confirmed_request')
        except (ValueError, KeyError, IndexError, TypeError):
            return None
    from data_analysis_agent.agents.review_retrieval import wants_semantic_search
    if wants_semantic_search(text) or detect_sentiment_directions(text) == {'positive', 'negative'} or re.search(r'好的.*坏的', text):
        # 既有 RAG / 双向比较使用各自的严格契约，本轮不替换它们。
        return None
    topic = next((word for word in ('制冷', '静音', '安装') if word in text), None)
    aspect = next((value for word, value in (
        ('使用效果', 'effectiveness'), ('用起来', 'effectiveness'), ('制冷', 'effectiveness'),
        ('静音', 'quality'), ('安装', 'service'), ('质量', 'quality'), ('效果', 'effectiveness'),
        ('物流', 'logistics'), ('客服', 'service'), ('服务', 'service'),
        ('包装', 'packaging'), ('价格', 'price')) if word in text), None)
    sentiment = detect_sentiment_intent(text)
    examples = bool(re.search(r'原文|原话|证据|举例|例子|\d+\s*条', text))
    metrics = bool(re.search(r'多少|几条|数量|占比|比例|排名|分布', text))
    metric_manifestation = bool(re.search(r'(?:数量|占比|比例|评论数|观点数).*(?:体现|表现)', text))
    themes = not examples and not metric_manifestation and bool(re.search(
        r'体现|表现|归纳|总结|子主题|主题归类|具体(?:在)?抱怨什么|(?:抱怨|吐槽|投诉).*(?:哪些事情|哪些问题|些什么)|有哪些不满意的(?:地方|方面)', text)
        or (aspect and re.search(r'(?:具体(?:涉及|有)?|主要有)哪些问题|主要问题集中.*环节', text))
        or (sentiment == 'positive' and re.search(r'具体.*好评|好评.*具体', text) and not metrics
            and not re.match(r'这些|那些|上述', text)))
    content = bool(topic or re.search(r'观点内容|具体内容|原始观点|观点文本|逐条观点|用起来.*(?:怎么样|如何)|效果.*(?:怎么样|如何)|'
        r'(?:说|讲|提到|反映|表达|说明).*(?:什么|哪些)|称赞|夸|好在哪里|差在哪里|好在哪|差在哪|往里挖', text)
        or (aspect and re.search(r'里面|细分|关注|提及最多|哪些问题|哪些方面|哪些地方|主要集中|有什么问题|具体有什么差评|'
                                r'(?:抱怨|吐槽|投诉|不满意).*什么|负面评价.*有哪些', text)))
    levels = []
    if examples:
        levels.append('evidence')
    elif themes and not topic:
        levels.append('themes')
    elif content and not (metrics and not themes):
        levels.append('opinion_content')
    families = []
    for family, pattern in (
        ('quality', r'覆盖率|数据质量|失败标注|缺失正文|待标注|多少.*评论|评论.*多少'),
        ('ratings', r'星级|评分.*分布'), ('trend', r'趋势|变化|按[日月天]|按月份'),
        ('window_comparison', r'基线|窗口.*(?:比较|对比)|上一时间段|上个.*相比'),
        ('comparison', r'商品对比|(?:比较|对比).*(?:商品|空调)|另一款|这两款|它们.*(?:对比|比较)')):
        if re.search(pattern, text):
            families.append(family)
    if 'window_comparison' in families and not re.search(r'趋势|按[日月天]|按月份', text):
        families = [family for family in families if family != 'trend']
    if not levels or (examples and (families or re.search(r'主要.*(?:方面|维度)|维度.*排名', text))):
        levels.append('statistics')
    if 'statistics' in levels and not families:
        families = ['summary'] if sentiment or re.search(r'方面|维度|集中|买账', text) else []
    goal = RequestGoal(question, tuple(levels), aspect, sentiment, topic,
                       tuple(families) if 'statistics' in levels else ())
    if context:
        previous = context.get('validated') or {}
        queries = previous.get('queries', [])
        matching = [row for row in queries if row['parameters'].get('aspect') == aspect] if aspect and len(queries) > 1 else queries
        last = matching[0] if len(matching) == 1 else {}
        params = last.get('parameters', {})
        prior_level = {'aspect_drilldown': 'themes', 'opinion_breakdown': 'opinion_content', 'evidence': 'evidence'}.get(last.get('kind'), 'statistics')
        inherited_sentiment = previous.get('sentiment') or params.get('sentiment')
        inherited_aspect, inherited_topic = params.get('aspect'), params.get('topic')
        from data_analysis_agent.jobs.analysis_state import pending_context
        pending, users = pending_context(context)
        if aspect and REFERENCE.fullmatch(text.strip()) and last.get('kind') == 'semantic_evidence':
            # 维度明确不代表统计、主题或检索命题已明确。
            return None
        if pending:
            prior_level = pending['levels'][0]
            inherited_sentiment = pending['sentiment'] or inherited_sentiment
        # 失败轮次只提供明确的用户目标，绝不读取失败结果来证明业务完成。
        for user in reversed(users[:4]):
            if user.get('question'):
                prior = build_request_goal(user['question'])
                if prior:
                    inherited_sentiment = prior.sentiment or inherited_sentiment
                    if prior.aspect:
                        if prior.aspect != inherited_aspect:
                            inherited_topic = None
                        inherited_aspect = prior.aspect
                    inherited_topic = prior.topic or inherited_topic
                    if not REFERENCE.fullmatch(user['question'].strip()):
                        prior_level = prior.levels[0]
        if (prior_level == 'themes' and re.fullmatch(r'(?:那|那么|然后)?(?:好评|差评|正面|负面|优点|缺点|投诉)(?:呢|吧)?[？?。]*', text.strip())
                and last.get('kind') in ('issues', 'positive_aspects') and not params.get('aspect')):
            # 结构化待办或合法用户请求都可能与已完成统计竞争；这里只澄清，不恢复事实。
            return None
        switch = bool(ASPECT_SWITCH.fullmatch(text.strip()) and len(queries) == 1)
        count_reference = bool(re.fullmatch(r'(?:那|那么)?(?:有多少条|多少条|几条|数量呢)[？?。]*', text.strip()))
        reference = bool(REFERENCE.fullmatch(text.strip()) or re.search(r'^那再往里挖', text) or switch)
        if reference:
            goal = replace(goal, levels=(prior_level,), statistic_families=())
        if count_reference:
            goal = replace(goal, levels=('statistics',), statistic_families=('summary',))
        whole = bool(re.search(r'整体|总体|全部|所有|商品的?好评|评论中具体的好评', text))
        count_target = count_reference and bool(last and params.get('aspect') and
            (params.get('sentiment') or previous.get('sentiment')) in ('positive', 'negative'))
        inherit_aspect = not whole and (reference or count_target or bool(re.search(r'具体|体现|表现|抱怨|吐槽|往里挖', text)))
        inherit_topic = reference or bool(re.search(r'往里挖|^(?:这些|那些|上述)', text))
        goal = replace(goal, aspect=aspect or (inherited_aspect if inherit_aspect else None),
                       sentiment=sentiment or inherited_sentiment,
                       topic=topic or (inherited_topic if inherit_topic else None))
    return goal


def build_task_goal(question, scope, plan, resolution, *, request_goal=None):
    """只绑定已解析的范围；plan 保留为调用兼容参数，不能推导或降低目标。"""
    request = request_goal if request_goal is not None else build_request_goal(question)
    if request is None:
        return None
    return {**asdict(request), 'levels': list(request.levels), 'version': 'review-request-goal-v1',
            'expected_scope': deepcopy(scope), 'scope_resolution': deepcopy(resolution or {})}


def validate_goal_plan(goal, plan):
    checks = []
    for level in goal['levels']:
        tools = CAPABILITIES[level]
        candidates = [request for request in plan.requirements if request['tool'] in tools]
        compatible = bool(candidates)
        if level != 'statistics':
            compatible &= all(all(not goal.get(key) or row['parameters'].get(key) == goal[key]
                                  for key in ('aspect', 'sentiment', 'topic')) for row in candidates)
        elif goal.get('aspect'):
            compatible &= all(row['parameters'].get('aspect') == goal['aspect'] for row in candidates
                              if row['tool'] in {'get_top_issues', 'get_positive_aspects', 'get_aspect_trend'})
        checks.append({'name': level, 'passed': bool(compatible)})
    for family in goal.get('statistic_families', ()):
        checks.append({'name': family, 'passed': bool(set(plan.required_tools) & STATISTIC_FAMILIES[family])})
    directed = set(plan.required_tools) & {'get_top_issues', 'get_positive_aspects', 'get_trend', 'get_aspect_trend',
                                          'compare_products', 'compare_time_windows'}
    if directed and goal.get('sentiment') in ('positive', 'negative'):
        checks.append({'name': 'sentiment', 'passed': goal['sentiment'] == plan.sentiment})
    return {'passed': bool(checks) and all(row['passed'] for row in checks), 'checks': checks}


def validate_task_completion(goal, scope, plan, selected):
    checks = []
    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    plan_check = validate_goal_plan(goal, plan)
    check('请求目标与计划能力', plan_check['passed'], '计划能力须满足编译前的原始请求，不能以计划类型降低目标。')
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
    from data_analysis_agent.agents.review_plan import TOOL_KINDS
    for level in goal['levels']:
        kinds = {TOOL_KINDS[tool] for tool in CAPABILITIES[level]}
        check('实际产物:' + level, any(row['kind'] in kinds for row in rows), '实际结果须包含用户要求的产物类型。')
    for family in goal.get('statistic_families', ()):
        kinds = {TOOL_KINDS[tool] for tool in STATISTIC_FAMILIES[family]}
        check('实际统计:' + family, any(row['kind'] in kinds for row in rows), '不可遗漏原始请求的统计能力。')
    from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
    try:
        snapshots = {row['result_id']: row for row in selected}
        verified = validate_report(snapshots, list(snapshots), sentiment_intent=plan.sentiment, query_plan=plan,
                                   expected_scopes=expected_result_scopes(snapshots, scope))['status'] == 'passed'
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        verified = False
    check('统计与引用校验', verified, '复用原 Validator 的数字、分母、范围、主题来源及精确引用校验。')
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
        complete = bool(topics) and all(data['analysis_status'] in ('complete', 'insufficient_data') for data in topics)
        check('分类批次完整', complete, '超时、失败或部分复核不能作为完整主题分析。')
        supported = bool(topics) and all(data['topics'] and all(
            topic['evidence_opinion_ids'] and topic['review_ids']
            and topic['review_count'] == len(set(topic['review_ids']))
            and topic['opinion_count'] == len(set(topic['opinion_ids'])) for topic in data['topics']) for data in topics)
        # 空集合是证据不足；错误类型或失败批次仍是执行/验收失败。
        missing = bool(topics) and all(not data['topics'] and data['analysis_status'] in ('complete', 'insufficient_data') for data in topics)
        check('子主题及去重证据', supported, '候选主题须有来源观点与去重计数，不得编造全量频次。')
    if set(goal['levels']) & {'evidence', 'themes', 'opinion_content'}:
        content = [row for row in rows if row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown')]
        check('观点查询', bool(content), '内容任务须执行观点、主题或原文查询。')
        check('观点筛选', all((not goal['aspect'] or row['data'].get('aspect') == goal['aspect'])
            and (not goal['sentiment'] or row['data'].get('sentiment') in (goal['sentiment'], None))
            and (not goal.get('topic') or row['data'].get('topic') == goal['topic']) for row in content),
            '观点查询须保留本轮维度和情感。')
        evidence = [item for row in rows if row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown')
                    for item in row['data'].get('items', [])]
        supported = any(item.get('evidence_ref') and item.get('opinions') for item in evidence)
        missing |= not supported and any(row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown') for row in rows)
        check('原文引用', supported, '须存在带原始评论来源的观点，RAG 候选或计数不替代引用。')
    hard_failures = [row for row in checks if not row['passed'] and row['name'] not in ('子主题及去重证据', '原文引用')]
    if not missing:
        hard_failures.extend(row for row in checks if not row['passed'] and row['name'] in ('子主题及去重证据', '原文引用'))
    status = 'failed' if hard_failures else 'insufficient_evidence' if missing else 'passed'
    content_rows = [row['data'] for row in rows if row['kind'] in ('evidence', 'opinion_breakdown', 'aspect_drilldown')]
    incomplete = any(data.get('analysis_status') in ('partial', 'failed') for data in content_rows)
    evidence_status = ('analysis_incomplete' if incomplete else 'no_matching_opinions'
        if missing and content_rows and all(data.get('total') == 0 for data in content_rows)
        else 'no_supported_evidence' if missing else 'available' if content_rows and verified
        else 'not_requested' if not set(goal['levels']) & {'evidence', 'themes', 'opinion_content'} else 'invalid_or_missing_result')
    return {'status': status, 'complete': status == 'passed', 'checks': checks, 'plan_check': plan_check,
            'evidence_status': evidence_status,
            'classification_status': 'analysis_incomplete' if incomplete else 'model_candidates_unreviewed' if 'themes' in goal['levels'] else 'not_requested',
            'message': '当前范围证据不足，尚未完成所请求的观点或子主题分析；没有证据不表示没有问题。'
                       if status == 'insufficient_evidence' else '本轮任务目标、范围与证据验收通过。' if status == 'passed'
                       else '本轮任务目标、范围或所需查询未通过验收，未发布分析结论。'}
