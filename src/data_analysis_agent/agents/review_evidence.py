"""评估快照能回答到哪一层，只建议已有只读工具，不执行查询或 SQL。"""
import re
from calendar import monthrange
from datetime import date

from data_analysis_agent.agents.review_answer_facts import selected_aspects
from data_analysis_agent.agents.review_tools import (
    AspectDrilldownInput, OpinionBreakdownInput, EvidenceInput, ProductSelection,
)

TOOL_MODELS = {'get_aspect_drilldown':AspectDrilldownInput, 'get_opinion_breakdown':OpinionBreakdownInput,
               'get_evidence':EvidenceInput, 'get_data_quality':ProductSelection, 'compare_products':ProductSelection}


def assess_evidence(context):
    question = context.question
    scopes = context.scope_summary
    root = max(scopes, key=lambda row:len(row.get('product_ids',[])), default={})
    products = list(dict.fromkeys(product for row in scopes for product in row.get('product_ids',[])))
    states = {stat.get('filters',{}).get('analysis_status') for stat in context.statistics}
    aspects = selected_aspects(question)
    if not aspects:
        aspects = {stat.get('filters',{}).get('aspect') for stat in context.statistics} - {None}
    aspect = next(iter(aspects)) if len(aspects)==1 else None
    sentiments = {stat.get('filters',{}).get('sentiment') for stat in context.statistics} & {'positive','negative'}
    sentiment = context.sentiment if context.sentiment in ('positive','negative') else next(iter(sentiments)) if len(sentiments)==1 else None
    content = bool(re.search(r'具体|好在哪|不好在哪|反映|解释|原因|为什么|区别|差异|环节|问题类型|自述|偏好|代表|相加|总人数|原文|主题|层次',question))
    causal = bool(re.search(r'根因|证实|真正.*原因|实际.*原因|导致|是否因为|因果|确认.*责任|没有.*问题',question))
    commercial = bool(re.search(r'更值得买|性价比',question))
    forecast = bool(re.search(r'下个月|未来|预测|一定继续|长期稳定',question))
    trends = any(stat.get('trends') for stat in context.statistics)
    topics = [stat for stat in context.statistics if stat['analysis_type']=='aspect_drilldown' and stat.get('filters',{}).get('topics')]
    groups = [stat for stat in context.statistics if stat['analysis_type']=='opinion_breakdown' and stat.get('filters',{}).get('groups')]
    codes = {row['code'] for row in context.caveats}
    assessment = {'status':'facts_sufficient','requested_level':'forecast' if forecast else 'causal' if causal else 'content' if content else 'statistics',
        'aspect':aspect,'sentiment':sentiment,'source_result_ids':list(context.source_result_ids),
        'scope_refs':[row['scope_ref'] for row in scopes],'gaps':[],'followup_queries':[],
        'max_additional_queries':2,'execution':'proposal_only','note':''}
    def propose(name, params, purpose):
        values = TOOL_MODELS[name].model_validate(params).model_dump(exclude_none=True)
        if not set(values.get('product_ids',[])) <= set(products):
            raise ValueError('补查建议越过当前商品范围。')
        bound = next((row for row in scopes if set(values.get('product_ids',[])) <= set(row.get('product_ids',[]))),None)
        assessment['followup_queries'].append({'tool':name,'parameters':values,'scope_ref':bound['scope_ref'] if bound else None,
            'scope_refs':[row['scope_ref'] for row in scopes if set(row.get('product_ids',[])) & set(values.get('product_ids',[]))],
            'requires_scope_confirmation':bound is None,'expected_support':purpose,'execution':'proposal_only'})
    if states & {'partial','failed'}:
        assessment.update(status='analysis_incomplete',gaps=['unverified_or_incomplete_analysis'],
            note='本次分析尚未完整通过发布条件，不能用未完成的主题解释问题。')
        return assessment
    if 'insufficient_data' in states and content:
        assessment.update(status='content_not_resolved',gaps=['no_verified_topic'],
            note='已有观点分析尚未得到可核验主题，不能强行归类或重复给出主题排名。匹配原文只能补充具体例子。')
        if not context.evidence and products and aspect and sentiment:
            propose('get_evidence',{'product_ids':products,'aspect':aspect,'sentiment':sentiment,'page':1,'page_size':3,'window':'current'},
                '检查模糊或无法归类的具体原文；不把例子当主题排名。')
        return assessment
    if not (content or causal or forecast or commercial):
        return assessment
    if commercial:
        assessment.update(status='external_evidence_required',requested_level='purchase_decision',gaps=['price_preferences_and_performance_missing'],
            note='评论内容不能单独确认性价比或购买优先级，还缺少价格、使用需求或客观性能等依据。')
        return assessment
    if forecast:
        assessment.update(status='external_evidence_required',gaps=['future_or_long_term_not_identified'],
            note='当前评论和历史变化不能证明未来或长期稳定改善；现有工具不能直接补足预测依据。')
        return assessment
    if causal:
        assessment.update(status='external_evidence_required',gaps=['causality_not_identified'],
            note='评论只能支持买家报告的现象或自述缘由，不能单独证实实际根因、责任方或措施效果。')
    if trends and codes & {'limited_bucket_samples','missing_time_buckets','no_successful_annotations'}:
        assessment['gaps'].append('insufficient_comparable_time_samples')
        if not causal:
            assessment.update(status='time_evidence_limited',note='可比较时段的有效样本或标注不足，补充少量原文不能证明稳定变化。')
        propose('get_data_quality',{'product_ids':products},'核对当前范围的标注和样本缺口，不将其当作趋势改善证据。')
        return assessment
    if 'no_successful_annotations' in codes:
        assessment.update(status='annotations_missing',gaps=assessment['gaps']+['no_valid_annotations'],
            note='存在缺少成功标注的分析范围，不能从零条匹配观点推断商品没有问题。')
        propose('get_data_quality',{'product_ids':products},'区分无评论、缺少正文和未完成标注。')
        return assessment
    if re.search(r'没有.*问题',question) and causal:
        assessment['note']='当前零匹配或有限评论不能证明现实中完全没有问题；也不能把零匹配误说成缺少成功标注。'
        return assessment
    if len(products)==2 and re.search(r'对比|比较|差异|差别',question):
        individual = [row for row in scopes if len(row.get('product_ids',[]))==1]
        identities = {tuple(row.get(key) for key in ('source','dataset_version','annotation_profile_ref','date_from','date_to','as_of')) for row in individual}
        if len(identities)>1:
            assessment.update(status='comparison_scope_mismatch',gaps=assessment['gaps']+['incomparable_scopes'],
                note='双方现有证据的日期或标注口径不同，不能把差异直接当作同口径商品差异。')
            propose('compare_products',{'product_ids':products},'先在当前绑定范围内建立可比统计，再决定是否补查双方观点。')
            return assessment
    if topics or groups:
        sampled = any(stat.get('filters',{}).get('population_mode')=='sample' for stat in topics)
        if not causal:
            assessment.update(status='sample_descriptive_only' if sampled else 'descriptive_evidence_available',
                note='现有观点支持描述本次匹配评论报告的具体内容；不能把自述当作客观根因。')
        if sampled:
            assessment['gaps'].append('nonrandom_sample_not_population')
        assessment['gaps'].append('verbatim_evidence_not_sent_to_answer_model')
        return assessment
    if context.evidence:
        if not causal:
            assessment.update(status='examples_only',note='已有原文支持展示具体例子，不能仅凭少量例子判断主要问题或总体原因。')
        assessment['gaps'].append('examples_not_population_ranking')
    else:
        assessment['gaps'].append('opinion_content_missing')
        if not causal:
            assessment.update(status='additional_query_available',note='当前结果没有足以解释具体问题的观点内容；可在原范围内补查已有观点工具。')
    if not products or aspect is None or sentiment is None:
        assessment['gaps'].append('need_specific_product_aspect_or_sentiment')
        if not causal:
            assessment.update(status='clarification_needed',note='补查具体内容前，需要唯一确定商品、一级维度和情感方向，不能擅自选择。')
        return assessment
    if len(products) > 2:
        assessment['gaps'].append('followup_query_budget_exceeded')
        return assessment
    if trends:
        series=next((series for stat in context.statistics for series in stat.get('trends',[]) if series['aspect']==aspect),None)
        valid=[point['bucket'] for point in series['points'] if point['metric']['value'] is not None] if series else []
        if len(valid)>=2:
            for bucket in (valid[0],valid[-1]):
                start=date.fromisoformat(bucket+'-01' if series['period']=='month' else bucket)
                end=start.replace(day=monthrange(start.year,start.month)[1]) if series['period']=='month' else start
                subwindow={'date_from':max(start.isoformat(),root['date_from']),'date_to':min(end.isoformat(),root['date_to'])}
                propose('get_opinion_breakdown',{'product_ids':products,'aspect':aspect,'sentiment':sentiment},
                    '在明确子窗口内比较问题表述，不能证明运营措施导致变化。')
                assessment['followup_queries'][-1].update(requested_subwindow=subwindow,requires_scope_confirmation=True)
            assessment['gaps'].append('period_specific_content_not_queried')
            assessment['note']+='建议在当前范围内明确首末两个可比窗口，分别建立只读补查任务；不自动改动日期。'
            return assessment
    for product in products:
        name = 'get_aspect_drilldown' if len(products)==1 and not trends else 'get_opinion_breakdown'
        propose(name,{'product_ids':[product],'aspect':aspect,'sentiment':sentiment},
            '补查匹配观点的具体问题类型；只支持报告内容，不证明客观因果。')
    if trends:
        assessment['gaps'].append('period_specific_content_not_queried')
        assessment['note'] += '若要比较变化前后具体内容，还需明确两个时间窗口；不自动改动当前日期。'
    return assessment
