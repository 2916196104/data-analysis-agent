"""Runtime task goals derived from the existing plan; no frozen run/manifest identity."""
import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.parameters import AnalysisScope, BaselineScope
from data_analysis_agent.reviews.query_contract import ContractModel, TaskType, RequiredFact, RequiredFacts, FactRelation, Digest, SentimentIntent
from data_analysis_agent.agents.review_reported_facts import POLICY, target
from data_analysis_agent.agents import review_delivery_timing_evidence as timing
from data_analysis_agent.agents import review_cooling_speed_evidence as cooling

VERSION = 'review-runtime-contract-v1'
DATE_PREFIX = re.compile(r'^\s*(?:(?:请问|在|从)\s*)?(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～|—)\s*'
                         r'(\d{4}-\d{2}-\d{2})\s*(?:期间|之间|内)?\s*[，,:：]?\s*(.+)$', re.S)


def evidence_question(question, scope=None, *, fact_policy=POLICY):
    """Separate redundant prefixes using an independently catalog-bound server scope, never text-inferred IDs."""
    match = DATE_PREFIX.fullmatch(question)
    text = match[3] if match else question
    if scope is not None:
        try:
            ids = scope['product_ids']
            if (isinstance(ids, (list, tuple)) and len(ids) == 1 and isinstance(ids[0], str) and ids[0]
                    and evidence_scope_matches(question, scope)):
                prefix = re.match(r'^(?:商品[ \t]*)?' + re.escape(ids[0]) + r'[ \t]*', text)
                if prefix:
                    remainder = text[prefix.end():]
                    # Only the existing complete proposition grammar qualifies; no numeric/clause deletion.
                    if fact_target(remainder, fact_policy) is not None:
                        return remainder
        except (KeyError, TypeError, AttributeError):
            pass
    return text


def fact_target(question, policy):
    if policy == timing.POLICY:
        return timing.target(question)
    if policy == 'reported_experience_v2':
        from data_analysis_agent.agents.review_reported_facts_v2 import target as old_target
        return old_target(question)
    return target(question)


def evidence_policy(question, scope):
    normalized = evidence_question(question, scope, fact_policy=timing.POLICY)
    if timing.target(normalized):
        return timing.POLICY
    request = target(evidence_question(question, scope))
    return cooling.POLICY if request and cooling.SLOW in request['facts'] else POLICY


def evidence_scope_matches(question, scope):
    match = DATE_PREFIX.fullmatch(question)
    return not match or (match[1], match[2]) == (scope['date_from'], scope['date_to'])


_GENERIC_MODIFIER = re.compile(
    r'^(?:请|请问|帮我|看看|查看|查询|统计|分析|计算|告诉我|只|仅|不要|不用|全部|所有|全量|整体|总体|目前|当前|该|这|那|本|所选|上述|上轮|'
    r'这款|这台|这个|那款|那台|那个|两款|两台|多款|商品|产品|空调|机器|机型|型号|设备|窗口|时间|范围|日期|本月|上月|今年|去年|'
    r'用户|买家|消费者|大家|使用效果|质量|效果|价格|包装|物流|客服|服务|方面|维度|正向|负向|正面|负面|好评|差评|有效|成功标注|带正文|空正文|缺失正文|'
    r'[A-Za-z0-9_\-/]+|\d{4}-\d{2}-\d{2}|的|中|里|内|上|下|\s)+$'
)
_FINE_SUBTOPIC_TERMS = re.compile(
    r'制冷|制热|噪音|噪声|降温|升温|耐用|寿命|压缩机|降价|失眠|掉漆|异响|漏水|破损|结实|不耐烦|迟缓|响应慢|发货慢|送货慢|到货慢|乱?收费'
)
_DIMENSION_TAIL = re.compile(
    r'(?:方面)?(?:正向|正面|负向|负面|好评|差评|问题)?(?:评论|评价|反馈)?(?:涉及|共|一共|总计|总共|有)?的?'
)


def _has_fine_subtopic(question, semantic, *, boundary_pattern, check_dimension=True, check_modifier=False):
    """Detect fine-grained subtopics inside or outside the six macro dimensions."""
    if semantic['topic'] or _FINE_SUBTOPIC_TERMS.search(question):
        return True
    if check_dimension:
        dimension = re.search(
            r'(?:使用效果|质量|价格|包装|物流|配送|到货|客服|服务)([^，,。；;]+?)(?=' + boundary_pattern + r')',
            question,
        )
        if dimension:
            tail = re.sub(
                r'^(?:(?:以及|和|与|、|及)(?:使用效果|质量|价格|包装|物流|配送|到货|客服|服务))*',
                '',
                dimension[1],
            )
            if not _DIMENSION_TAIL.fullmatch(tail):
                return True
    if (not semantic['aspect']
            and re.search(r'(?:反馈|抱怨)[^，,。；;]*(?:' + boundary_pattern + r')', question)
            and not re.search(r'好评|差评|正向|负面|正面|负向', question)):
        return True
    if check_modifier and not semantic['aspect']:
        match = re.search(
            r'([^\s，,。；;：:]{2,16})的(?:差评|好评|负面|正面|评论|反馈|抱怨)[^，,。；;]*(?:' + boundary_pattern + r')',
            question,
        )
        if match and not _GENERIC_MODIFIER.fullmatch(match[1]):
            return True
    return False


def task_limit(question):
    """Unsupported goals must not fall through to an unrelated successful SQL tool."""
    if question.startswith('执行本轮已确认的只读查询'):
        return None
    from data_analysis_agent.agents.review_topic_statistics import topic_request
    if topic_request(question):return None
    if re.fullmatch(r'\s*(?:请)?(?:指定|选择|输入)(?:一个|一款)(?:当前)?数据集(?:中|内)?不存在的(?:商品|产品)\s*(?:ID|编号)\s*'
                    r'(?:再提问|重新提问|进行提问)?[。！？?!]*\s*', question, re.I):
        return ('missing_product', '这条测试说明尚未给出具体商品 ID 和查询内容。请填写要测试的商品 ID，再提出物流好评等具体问题。')
    forecast = re.sub(r'(?:不要|不用|无需|不必|不需要)(?:预测|预估|估算|估计|预判)[^，,。；;]*', '', question)
    if re.search(r'(?:预测|预估|估算|估计|预判|forecast)[^，,。；;！？!?]*(?:销量|销售量|销售额|销售收入|营收|营业额)|'
                 r'(?:销量|销售量|销售额|销售收入|营收|营业额)[^，,。；;！？!?]*(?:预测|预估|估算|forecast)', forecast, re.I):
        return ('forecast', '当前评论助手不支持销量或销售额预测。评论数量不能代替销量或成交金额；'
                '需要相应的销售历史数据和经过验证的预测方法。可以继续查询当前商品的评论统计、趋势或原文。')
    # Negated requests for explanation are not causal requests.
    active = re.sub(r'(?:不要|不用|无需|不必|不需要)(?:分析|判断|解释|推断|说明|核查)?(?:原因|根因|因果)[^，。；;]*', '', question)
    if re.search(r'为什么|什么原因|原因(?:是|是什么)|根因|因果|(?:是否|是不是|是否是|由).*(?:导致|造成)|(?:导致|造成).*(?:吗|么|？|\?)', active):
        return ('cause_check', '当前评论工具不能证明客观故障原因或因果关系；可以检索买家反馈，但不能用观点归组代替原因证明。')
    ratio = bool(re.search(r'占比|比例|百分比|占.*(?:多少|几成|百分之|全部|所有|评论)', question))
    if ratio:
        from data_analysis_agent.agents.review_semantics import opinion_request, requested_denominator
        if requested_denominator(question) == 'conflicting':
            return ('denominator_conflict', '本轮指定了多个分母，请分别提问并明确每个比例的分母。')
        semantic = opinion_request(question)
        subtopic = _has_fine_subtopic(question, semantic, boundary_pattern=r'占比|比例|百分比|占')
        if re.search(r'(?:全部|所有|全量)评论', question):
            subtopic = True  # Existing sentiment rates use successfully labelled reviews.
        if subtopic:
            return ('statistics', '当前没有该细分语义主题的完整分类分子，无法计算其总体占比；不能用上级维度、数据质量或 Top-K 候选比例替代。')
    examples = bool(re.search(r'原文|原话|证据|举例|例子|(?:给我|列出|展示|找|看|来)\s*(?:\d+|几)\s*条', question))
    comparison = bool(re.search(
        r'对比|相比|基线|窗口|比较.*(?:商品|产品|窗口|基线|差异|差别|不同|区别|两款|两个|二者|两者)|'
        r'(?:商品|产品|窗口|基线|两款|两个|二者|两者).*比较',
        question,
    ))
    if comparison and not examples:
        from data_analysis_agent.agents.review_semantics import opinion_request
        semantic = opinion_request(question)
        if _has_fine_subtopic(
            question,
            semantic,
            boundary_pattern=r'比较|对比|相比|基线|窗口|好评|差评|正面|负面',
            check_dimension=False,
            check_modifier=False,
        ):
            return ('subtopic_comparison', '当前仅支持整体或六大评价维度的商品/窗口对比，没有该细分语义主题的完整分类统计；不能用上级维度或整体对比替代。')
    trend = bool(re.search(r'趋势|变化|按[日月天]|按月份', question))
    if trend and not examples:
        from data_analysis_agent.agents.review_semantics import opinion_request
        semantic = opinion_request(question)
        if _has_fine_subtopic(
            question,
            semantic,
            boundary_pattern=r'趋势|变化|按[日月天]|按月份',
            check_dimension=False,
            check_modifier=True,
        ):
            return ('subtopic_trend', '当前仅支持整体或六大评价维度的趋势分析，没有该细分语义主题的完整分类时间序列；不能用整体或上级维度趋势替代。')
    count = bool(re.search(r'(?:有)?多少(?:条)?|几条|数量|多少.*评论|评论.*多少', question))
    if count and not examples and not ratio:
        from data_analysis_agent.agents.review_semantics import opinion_request
        semantic = opinion_request(question)
        if not semantic['theme_summary'] and not semantic['positive_summary']:
            if _has_fine_subtopic(
                question,
                semantic,
                boundary_pattern=r'(?:有)?多少(?:条)?|几条|数量|多少.*评论|评论.*多少',
                check_modifier=True,
            ):
                return ('subtopic_count', '当前没有该细分语义主题的完整分类计数，无法统计其准确评论条数；不能用上级维度数量、数据质量总评论数或 Top-K 检索条数替代。')
    return None


class RuntimeScope(AnalysisScope):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    product_ids: tuple[Text, ...] = Field(default=(), max_length=100)
    baseline_from: str | None = None
    baseline_to: str | None = None
    timezone: Literal['UTC'] = 'UTC'

    @model_validator(mode='after')
    def baseline_is_valid(self):
        if self.baseline_from is not None or self.baseline_to is not None:
            BaselineScope.model_validate_json(canonical_json({name:getattr(self,name) for name in BaselineScope.model_fields}))
        return self


class StatisticGoal(ContractModel):
    tool: Text
    metric: Text
    value_path: Text
    numerator: Text
    denominator: Text | None = None
    definition_key: Text | None = None


class RuntimeQueryContract(ContractModel):
    version: Literal['review-runtime-contract-v1'] = VERSION
    original_question: str = Field(min_length=1,max_length=8000)
    evidence_question: str = Field(min_length=1,max_length=8000)
    scope: RuntimeScope
    scope_sha256: Digest
    task_type: TaskType
    operation: Literal['sql_statistics', 'fact_search', 'opinion_summary', 'cause_check', 'unsupported']
    target_entity: Text = 'unknown'
    subtopic: Text = 'unknown'
    sentiment_intent: SentimentIntent = 'unknown'
    required_facts: RequiredFacts = Field(default_factory=RequiredFacts)
    statistics: tuple[StatisticGoal, ...] = ()
    support_status: Literal['supported', 'retrieval_only', 'unsupported']
    unsupported_reason: str | None = None
    uncertainty: tuple[Text, ...] = ()


# Paths and denominators are the existing analytics payload contract, not model choices.
METRICS = {
    'get_topic_statistics': [('topic_rate','topics[].rate','topics[].review_count','review_count',None)],
    'get_positive_aspects': [('positive_rate','aspects[].positive_rate','aspects[].positive_review_count','positive_review_count','positive_aspect_rate_denominator'),
                             ('positive_success_rate','aspects[].positive_success_rate','aspects[].positive_review_count','successful_reviews','positive_success_rate_denominator')],
    'get_top_issues': [('negative_rate','issues[].negative_rate','issues[].negative_review_count','successful_reviews','negative_rate_denominator')],
    'get_trend': [('negative_rate','summary.negative_rate','summary.negative_review_count','successful_reviews','negative_rate_denominator')],
    'get_aspect_trend': [('negative_rate','summary.negative_rate','summary.negative_review_count','successful_reviews','negative_rate_denominator')],
    'get_data_quality': [('labeling_coverage','summary.labeling_coverage','summary.successful_reviews','eligible_text_reviews','labeling_coverage_denominator'),
                         ('missing_text_rate','missing_text_rate','summary.empty_text_reviews','review_count',None)],
    'compare_products': [('negative_rate','products[].negative_rate','products[].negative_review_count','successful_reviews','negative_rate_denominator')],
    'compare_time_windows': [('negative_rate','current.negative_rate','current.negative_review_count','successful_reviews','negative_rate_denominator')],
    'get_rating_distribution': [('star_rate','groups[].stars[].rate','groups[].stars[].review_count','rated_reviews',None)],
    'get_opinion_breakdown': [('review_count','groups[].review_count','groups[].review_count',None,None)],
    'get_aspect_drilldown': [('review_count','topics[].review_count','topics[].review_count',None,None)],
    'get_evidence': [('matching_review_count','total','total',None,None)],
}


def build_task_contract(question, scope, plan, *, fact_policy=None):
    bound = RuntimeScope.model_validate_json(canonical_json(scope))
    if fact_policy is None:
        fact_policy = evidence_policy(question, scope) if plan.analysis_type == 'semantic_evidence' else POLICY
    q = evidence_question(question, scope, fact_policy=fact_policy)
    request = fact_target(q,fact_policy) if plan.analysis_type == 'semantic_evidence' else None
    facts = RequiredFacts()
    entity = subtopic = 'unknown'
    if request:
        # target() has already verified these complete clauses; no new intent inference.
        clauses = request.get('clauses')
        if clauses is None:
            pair = re.fullmatch(r'同一(?:条)?评论(?:中)?既(?:说)?(.+?)又(?:说|抱怨)?(.+)', request['text'])
            clauses = pair.groups() if pair else (request['text'],)
        items = tuple(RequiredFact(id=fid, description=f'买家自述该商品的体验：{clause}')
                      for fid, clause in zip(request['facts'], clauses, strict=True))
        relations = (FactRelation(kind='same_review', fact_ids=tuple(request['facts'])),) if request['relation'] else ()
        facts = RequiredFacts(operator=request.get('operator','all_of'), items=items, relations=relations)
        domains = {fid.split('_')[0] for fid in request['facts']}
        entity = 'device' if domains <= {'cooling','heating','noise'} else next(iter(domains)) if len(domains)==1 else 'multiple'
        subtopic = next(iter(domains)) if len(domains)==1 else 'multiple'
    elif plan.analysis_type=='semantic_evidence':
        body=re.fullmatch(r'(?:有没有|是否有|有无|是否存在)(.+?)(?:的)?(?:反馈|评论|抱怨|证据)[？?]*',q)
        parts=re.fullmatch(r'(.+?)(?:或者|或)(.+)',body[1].removesuffix('的')) if body else None
        before=re.fullmatch(r'先(.+?)(?:后来|之后|后|再)(.+)',body[1].removesuffix('的')) if body else None
        if parts or before:
            clauses=(parts or before).groups()
            items=tuple(RequiredFact(id=f'f{i}',description=clause) for i,clause in enumerate(clauses))
            facts=RequiredFacts(operator='any_of' if parts else 'all_of',items=items,
                relations=() if parts else (FactRelation(kind='before',fact_ids=('f0','f1')),))
    statistics = []
    for req in plan.requirements:
        metrics = METRICS.get(req['tool'], [])
        if req['tool']=='get_topic_statistics':
            metrics=[('topic_rate','topics[].rate','topics[].review_count',req['parameters'].get('denominator','review_count'),None)]
        if req['tool'] in ('compare_products', 'compare_time_windows') and req['parameters'].get('sentiment') == 'positive':
            from data_analysis_agent.reviews.parameters import comparison_denominator
            den = comparison_denominator('positive', req['parameters'].get('aspect'), req['parameters'].get('denominator'))
            prefix = 'products[]' if req['tool'] == 'compare_products' else 'current'
            metrics = [('positive_comparison_rate', prefix + '.comparison_rate', prefix + '.comparison_count', den, 'comparison_rate_denominator')]
        if req['tool'] in ('get_trend', 'get_aspect_trend') and req['parameters'].get('sentiment') == 'positive':
            if req['tool'] == 'get_trend':
                metrics = [('positive_rate', 'summary.positive_rate', 'summary.positive_review_count',
                            'successful_reviews', 'positive_success_rate_denominator')]
            else:
                from data_analysis_agent.reviews.opinions import ASPECTS
                aspects = [req['parameters']['aspect']] if req['parameters'].get('aspect') else list(ASPECTS)
                success = req['parameters'].get('denominator') == 'successful_reviews'
                metric = 'positive_success_rate' if success else 'positive_rate'
                metrics = [(metric, f'summary.aspects.{aspect}.{metric}',
                    f'summary.aspects.{aspect}.positive_review_count', 'successful_reviews' if success else 'positive_review_count',
                    'positive_success_rate_denominator' if success else 'positive_aspect_rate_denominator') for aspect in aspects]
        statistics.extend(StatisticGoal(tool=req['tool'],metric=m,value_path=path,numerator=num,denominator=den,definition_key=key)
                          for m,path,num,den,key in metrics)
    limit = task_limit(question)
    operation = 'fact_search' if plan.analysis_type=='semantic_evidence' else 'sql_statistics' if statistics else 'unsupported'
    if plan.analysis_type in ('pros_cons','opinion_breakdown','aspect_drilldown','evidence'):operation='opinion_summary'
    if operation in ('sql_statistics','opinion_summary'):
        entity='review_opinion' if operation=='opinion_summary' else 'review_population'
        topics={req['parameters'].get('topic') or req['parameters'].get('aspect') for req in plan.requirements} - {None}
        subtopic=next(iter(topics)) if len(topics)==1 else 'multiple' if topics else 'unknown'
    reason = plan.unsupported_reason
    if limit:
        operation = 'cause_check' if limit[0]=='cause_check' else 'sql_statistics'
        reason = limit[1]
    status = 'unsupported' if reason else 'retrieval_only' if operation=='fact_search' and not request else 'supported'
    if operation=='fact_search' and not request and re.search(r'同一|同时|既.+又|不仅|而且|但|却|又|或者|或|和|以及|与|先.+后|之前|之后|而是|不是|并非', q):
        status='unsupported';reason='当前无法可靠解析该复合事实、否定或必要关系，请拆分问题或明确各项条件。'
    sentiment=plan.sentiment if plan.sentiment in ('positive','negative','neutral') else 'mixed' if plan.analysis_type=='pros_cons' else 'unknown'
    if request:
        directions={'negative' if fid in (timing.NOT_RECEIVED, timing.INSTALL_PENDING)
                    or re.search(r'_(?:poor|failure|slow|bad)$|^(?:price|noise)_high$',fid) else 'positive'
                    for fid in request['facts']}
        sentiment=next(iter(directions)) if len(directions)==1 else 'mixed'
    task_type='evidence' if operation in ('fact_search','cause_check','opinion_summary') else 'statistics'
    if plan.analysis_type in ('composite','pros_cons') and plan.requires_evidence and statistics:task_type='mixed'
    return RuntimeQueryContract(original_question=question,evidence_question=q,scope=bound,sentiment_intent=sentiment,
        scope_sha256=content_sha256(bound.model_dump(mode='json')),task_type=task_type,
        operation=operation,target_entity=entity,subtopic=subtopic,required_facts=facts,statistics=tuple(statistics),
        support_status=status,unsupported_reason=reason,
        uncertainty=('目标语义未被当前规则覆盖；仅允许检索和明确说明不足，不能许可确定结论。',) if status=='retrieval_only' else ())


def matches_saved_task_contract(stored, current):
    """Old v1 records used fact IDs as descriptions; preserve their original bytes."""
    expected = current.model_dump(mode='json')
    try:
        old_items = stored['required_facts']['items']
        for old, new in zip(old_items, expected['required_facts']['items'], strict=True):
            if old['id'] == new['id'] and old['description'] == old['id']:
                new['description'] = old['description']
        return stored == expected
    except (ValueError, KeyError, TypeError):
        return False


def _path(data, path):
    values=[data]
    for part in path.split('.'):
        array=part.endswith('[]');key=part.removesuffix('[]');following=[]
        for value in values:
            if not isinstance(value,dict) or key not in value:raise ValueError('missing_task_metric')
            item=value[key]
            if array:
                if not isinstance(item,list):raise ValueError('invalid_task_metric')
                following.extend(item)
            else:following.append(item)
        values=following
    return values


def task_matches(contract, selected, *, scope):
    """Additional goal check; existing numeric/source/permission validators still run."""
    try:
        if contract.support_status=='unsupported' or contract.operation in ('cause_check', 'unsupported'):return False
        bound=RuntimeScope.model_validate_json(canonical_json(scope))
        if contract.scope!=bound or contract.scope_sha256!=content_sha256(bound.model_dump(mode='json')):return False
        if task_limit(contract.original_question):return False
        from data_analysis_agent.agents.review_plan import TOOL_KINDS
        for goal in contract.statistics:
            matching=[r for r in selected if r['kind']==TOOL_KINDS[goal.tool]]
            if goal.tool == 'get_aspect_trend' and goal.value_path.startswith('summary.aspects.'):
                aspect = goal.value_path.split('.')[2]
                matching = [r for r in matching if r['parameters'].get('aspect') in (None, aspect)]
            if not matching:return False
            for item in matching:
                data=item['data'];_path(data,goal.value_path);_path(data,goal.numerator)
                if goal.definition_key and data.get('definitions',{}).get(goal.definition_key)!=goal.denominator:return False
        if contract.operation == 'sql_statistics':
            stat_rows = [r for r in selected if r['kind'] != 'context']
            if not stat_rows:
                return False
            if all(r['kind'] == 'quality' for r in stat_rows) and (
                contract.sentiment_intent in ('positive', 'negative') or contract.subtopic != 'unknown'
            ):
                return False
            if contract.sentiment_intent in ('positive', 'negative'):
                def _matches_direction(row):
                    kind = row['kind']
                    if kind == 'positive_aspects':
                        return contract.sentiment_intent == 'positive'
                    if kind == 'issues':
                        return contract.sentiment_intent == 'negative'
                    if kind in ('trend', 'aspect_trend', 'comparison', 'window_comparison'):
                        return row['data'].get('sentiment', 'negative') == contract.sentiment_intent
                    if kind in ('topic_statistics', 'evidence', 'opinion_breakdown', 'aspect_drilldown'):
                        return row['data'].get('sentiment') == contract.sentiment_intent
                    return False
                if not any(_matches_direction(row) for row in stat_rows):
                    return False
        if contract.operation=='fact_search':
            from data_analysis_agent.agents.review_retrieval import validate_retrieval
            if len(selected)!=1 or selected[0]['kind']!='semantic_evidence':return False
            data=selected[0]['data']
            if not validate_retrieval(data,contract.original_question):return False
            a=data['assessment']
            if contract.support_status=='retrieval_only':return not a['permitted_keys'] and a['status']=='insufficient'
            from data_analysis_agent.agents.review_retrieval import FACT_POLICIES
            if a['policy'] in FACT_POLICIES:
                ids=tuple(f.id for f in contract.required_facts.items)
                relation=(FactRelation(kind='same_review',fact_ids=ids),) if a['relation'] else ()
                if (a['required_fact_ids']!=list(ids) or contract.required_facts.operator!=a.get('operator','all_of')
                        or contract.required_facts.relations!=relation):return False
        return bool(selected)
    except (ValueError,KeyError,TypeError,AttributeError):return False
