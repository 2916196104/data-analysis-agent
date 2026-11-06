"""Runtime task goals derived from the existing plan; no frozen run/manifest identity."""
import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.parameters import AnalysisScope, BaselineScope
from data_analysis_agent.reviews.query_contract import ContractModel, TaskType, RequiredFact, RequiredFacts, FactRelation, Digest, SentimentIntent
from data_analysis_agent.agents.review_reported_facts import target

VERSION = 'review-runtime-contract-v1'
DATE_PREFIX = re.compile(r'^\s*(?:(?:请问|在|从)\s*)?(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～|—)\s*'
                         r'(\d{4}-\d{2}-\d{2})\s*(?:期间|之间|内)?\s*[，,:：]?\s*(.+)$', re.S)


def evidence_question(question):
    """Separate a leading explicit scope from the proposition, not from the embedding input."""
    match = DATE_PREFIX.fullmatch(question)
    return match[3] if match else question


def evidence_scope_matches(question, scope):
    match = DATE_PREFIX.fullmatch(question)
    return not match or (match[1], match[2]) == (scope['date_from'], scope['date_to'])


def task_limit(question):
    """Unsupported goals must not fall through to an unrelated successful SQL tool."""
    if question.startswith('执行本轮已确认的只读查询'):
        return None
    # Negated requests for explanation are not causal requests.
    active = re.sub(r'(?:不要|不用|无需|不必|不需要)(?:分析|判断|解释|推断|说明|核查)?(?:原因|根因|因果)[^，。；;]*', '', question)
    if re.search(r'为什么|什么原因|原因(?:是|是什么)|根因|因果|(?:是否|是不是|是否是|由).*(?:导致|造成)|(?:导致|造成).*(?:吗|么|？|\?)', active):
        return ('cause_check', '当前评论工具不能证明客观故障原因或因果关系；可以检索买家反馈，但不能用观点归组代替原因证明。')
    ratio = bool(re.search(r'占比|比例|百分比|占.*(?:多少|几成|百分之|全部|所有|评论)', question))
    if ratio:
        from data_analysis_agent.agents.review_semantics import opinion_request
        semantic = opinion_request(question)
        subtopic = semantic['topic'] or re.search(r'制冷|制热|噪音|噪声|降温|耐用|寿命|压缩机|降价|失眠', question)
        # A dimension's aggregate sentiment rate is supported; an attribute inside
        # that dimension needs its own classified numerator, not an SQL substitute.
        dimension = re.search(r'(?:使用效果|质量|价格|包装|物流|配送|到货|客服|服务)(.+?)(?=占比|比例|百分比|占)', question)
        if dimension and not re.fullmatch(r'(?:方面)?(?:正向|正面|负向|负面|好评|差评|问题)(?:评论|评价|反馈)?的?', dimension[1]):
            subtopic = True
        if not semantic['aspect'] and re.search(r'(?:反馈|抱怨).*(?:占比|比例|百分比|占)', question) and not re.search(r'好评|差评|正向|负面|正面|负向', question):
            subtopic = True
        if re.search(r'(?:全部|所有|全量)评论', question):
            subtopic = True  # Existing sentiment rates use successfully labelled reviews.
        if subtopic:
            return ('statistics', '当前没有该细分语义主题的完整分类分子，无法计算其总体占比；不能用上级维度、数据质量或 Top-K 候选比例替代。')
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


def build_task_contract(question, scope, plan):
    bound = RuntimeScope.model_validate_json(canonical_json(scope))
    q = evidence_question(question)
    request = target(q) if plan.analysis_type == 'semantic_evidence' else None
    facts = RequiredFacts()
    entity = subtopic = 'unknown'
    if request:
        # target() has already verified these complete clauses; no new intent inference.
        pair = re.fullmatch(r'同一(?:条)?评论(?:中)?既(?:说)?(.+?)又(?:说|抱怨)?(.+)', request['text'])
        clauses = pair.groups() if pair else (request['text'],)
        items = tuple(RequiredFact(id=fid, description=f'买家自述该商品的体验：{clause}')
                      for fid, clause in zip(request['facts'], clauses, strict=True))
        relations = (FactRelation(kind='same_review', fact_ids=tuple(request['facts'])),) if request['relation'] else ()
        facts = RequiredFacts(items=items, relations=relations)
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
    statistics = tuple(StatisticGoal(tool=req['tool'],metric=m,value_path=path,numerator=num,denominator=den,definition_key=key)
        for req in plan.requirements for m,path,num,den,key in METRICS.get(req['tool'],[]))
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
        directions={'negative' if re.search(r'_(?:poor|failure|slow|bad)$|^(?:price|noise)_high$',fid) else 'positive' for fid in request['facts']}
        sentiment=next(iter(directions)) if len(directions)==1 else 'mixed'
    task_type='evidence' if operation in ('fact_search','cause_check','opinion_summary') else 'statistics'
    if plan.analysis_type in ('composite','pros_cons') and plan.requires_evidence and statistics:task_type='mixed'
    return RuntimeQueryContract(original_question=question,evidence_question=q,scope=bound,sentiment_intent=sentiment,
        scope_sha256=content_sha256(bound.model_dump(mode='json')),task_type=task_type,
        operation=operation,target_entity=entity,subtopic=subtopic,required_facts=facts,statistics=statistics,
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
        if contract.support_status=='unsupported':return False
        bound=RuntimeScope.model_validate_json(canonical_json(scope))
        if contract.scope!=bound or contract.scope_sha256!=content_sha256(bound.model_dump(mode='json')):return False
        if task_limit(contract.original_question):return False
        from data_analysis_agent.agents.review_plan import TOOL_KINDS
        for goal in contract.statistics:
            matching=[r for r in selected if r['kind']==TOOL_KINDS[goal.tool]]
            if not matching:return False
            for item in matching:
                data=item['data'];_path(data,goal.value_path);_path(data,goal.numerator)
                if goal.definition_key and data.get('definitions',{}).get(goal.definition_key)!=goal.denominator:return False
        if contract.operation=='fact_search':
            from data_analysis_agent.agents.review_retrieval import validate_retrieval
            if len(selected)!=1 or selected[0]['kind']!='semantic_evidence':return False
            data=selected[0]['data']
            if not validate_retrieval(data,contract.original_question):return False
            a=data['assessment']
            if contract.support_status=='retrieval_only':return not a['permitted_keys'] and a['status']=='insufficient'
            if a['policy']=='reported_experience_v2':
                ids=tuple(f.id for f in contract.required_facts.items)
                relation=(FactRelation(kind='same_review',fact_ids=ids),) if a['relation'] else ()
                if (a['required_fact_ids']!=list(ids) or contract.required_facts.operator!='all_of'
                        or contract.required_facts.relations!=relation):return False
        return bool(selected)
    except (ValueError,KeyError,TypeError,AttributeError):return False
