"""同一队列任务内的一次只读补查；不递归、不接收模型 SQL、不扩大原文外发。"""
from contextlib import closing
from copy import deepcopy
from time import perf_counter
import json
import re

from data_analysis_agent.agents.review_compression import CompactAnalysisContext, build_analysis_artifacts
from data_analysis_agent.agents.review_evidence import assess_evidence, TOOL_MODELS
from data_analysis_agent.agents.review_insights import analysis_state
from data_analysis_agent.agents.review_plan import ReviewQueryPlan
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import expected_result_scopes, validate_report
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.reviews.parameters import BaselineScope
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.core.database import PostgresConnection


def check_sample_continuity(compact,request,results):
    """分事务补查不能把已知样本计数变化静默混入同一个分析口径。"""
    scopes={row['scope_ref']:row for row in compact.scope_summary}
    bound=request['execution_scope'];expected=[]
    for sample in compact.sample_summary:
        scope=scopes[sample['scope_ref']]
        if all(scope.get(key)==bound.get(key)for key in ('source','dataset_version','date_from','date_to','product_ids','as_of')):expected.append(sample)
    if request['scope_resolution']=='verified_first_last_bucket':
        for stat in compact.statistics:
            for series in stat.get('trends',[]):
                for point in series['points']:
                    if point['bucket']==bound['date_from'][:7 if series['period']=='month'else 10]:expected.append(point['sample_size'])
    for row in results.values():
        sample=row['data'].get('summary')
        if sample and any(any(sample.get(key)!=previous[key]for key in ('review_count','eligible_text_reviews','successful_reviews','negative_review_count')if key in previous)for previous in expected):
            raise ReviewDataError('补查时有效样本与原统计不同，请重新执行完整分析。')


def resolve_followups(assessment, compact, scope):
    """只解决唯一、可证明的子范围；建议本身不是访问授权。"""
    if canonical_json(assessment)!=canonical_json(assess_evidence(compact)):
        raise ReviewDataError('补查建议与已验证上下文不一致。')
    proposals = assessment['followup_queries']
    if len(proposals)>2:raise ReviewDataError('补查超过两项预算。')
    requests=[]
    for proposal in proposals:
        tool=proposal['tool']
        if tool not in TOOL_MODELS:raise ReviewDataError('补查工具不在允许列表中。')
        params=TOOL_MODELS[tool].model_validate(proposal['parameters']).model_dump(exclude_none=True)
        products=params.get('product_ids')
        if not products or not scope['product_ids']:return None
        if not set(products)<=set(scope['product_ids']):raise ReviewDataError('补查商品越界。')
        bound=next((row for row in compact.scope_summary if row['scope_ref']==proposal.get('scope_ref')),None)
        if not bound:return None
        for key in ('source','dataset_version','date_from','date_to','as_of'):
            if bound.get(key)!=scope.get(key):return None
        window=proposal.get('requested_subwindow')
        if window:
            # 必须恰好等于诊断根据唯一时间序列产生的首/末桶，不能信任调用方传入的日期。
            from data_analysis_agent.agents.review_evidence import assess_evidence as recompute
            allowed=[q.get('requested_subwindow') for q in recompute(compact)['followup_queries']]
            if window not in allowed or len({row['aspect'] for stat in compact.statistics for row in stat.get('trends',[])})!=1:
                raise ReviewDataError('补查子窗口不是已验证的首末时间桶。')
            if not scope['date_from']<=window['date_from']<=window['date_to']<=scope['date_to']:raise ReviewDataError('补查日期越界。')
        elif proposal.get('requires_scope_confirmation'):
            return None
        local=BaselineScope.model_validate({**scope,**(window or {}),'product_ids':products}).model_dump()
        if tool in ('get_aspect_drilldown','get_opinion_breakdown','get_evidence'):
            if not params.get('aspect') or params.get('sentiment') not in ('positive','negative'):return None
            if compact.sentiment!=params['sentiment']:raise ReviewDataError('补查情感与原任务不一致。')
        original_tool=tool
        if tool=='get_aspect_drilldown':
            # 自动补查不新增原文外发；复用精确观点查询，不冒充完成了模型主题归并。
            tool='get_opinion_breakdown'
            params=TOOL_MODELS[tool].model_validate(params).model_dump(exclude_none=True)
        execution={key:value for key,value in local.items() if key not in ('baseline_from','baseline_to')}
        requests.append({'tool':tool,'parameters':params,'execution_scope':execution,'scope':local,
            'proposed_tool':original_tool,'privacy_mode':'local_opinions_no_new_external_text',
            'scope_resolution':'verified_first_last_bucket' if window else 'original_bound_scope'})
    identities=[canonical_json([row['tool'],row['parameters'],row['execution_scope']])for row in requests]
    if len(set(identities))!=len(identities):raise ReviewDataError('补查计划包含重复请求。')
    return requests


def complete_evidence(db, result, plan, context, settings, run_dir, report):
    """原结果独立通过后调用；异常交给原任务失败发布保护。"""
    if 'evidence_completion' in result:raise ReviewDataError('每个任务只允许一次补查周期。')
    if result.get('status')!='ok'or result.get('validation',{}).get('status')!='passed':
        raise ReviewDataError('原结果未通过独立校验，禁止补查合并。')
    compact=CompactAnalysisContext.model_validate(result['compact_context'])
    assessment=assess_evidence(compact)
    record={'cycles':0,'status':'not_needed','automatic':False,'assessment':assessment,'requests':[],
        'initial_result_ids':list(result['result_ids']),'initial_validation':deepcopy(result['validation']),
        'additional_tool_calls':0,'duration_ms':0.0}
    result['evidence_completion']=record
    if not re.search(r'具体|反映|解释|原因|为什么|环节|问题类型|区别|差异|不同|好在哪|不好在哪',compact.question):return plan
    if plan.requirements and all(request['tool']=='get_evidence' for request in plan.requirements):
        record['status']='explicit_examples_only'
        return plan
    if assessment['status']=='clarification_needed'or'followup_query_budget_exceeded'in assessment['gaps']:
        record.update(status='clarification_needed',clarification='请明确商品、维度和情感；本轮最多补查两项，请先限定不超过两款商品。')
        return plan
    if analysis_state(compact)!='complete' or not assessment['followup_queries']:return plan
    started=perf_counter()
    try:
        record['failed_stage']='eligibility'
        requests=resolve_followups(assessment,compact,result['scope'])
        if requests is None:
            record.update(status='clarification_needed',failed_stage=None,clarification='补查需要唯一确定商品、情感或共同日期范围，请明确后重试。')
            return plan
        if context.tool_calls+len(requests)>settings.max_tool_calls:
            raise ReviewDataError('原任务剩余工具预算不足，未执行补查。')
        record.update(cycles=1,status='running',automatic=True)
        merged=deepcopy(plan)
        record['requests']=deepcopy(requests)
        for request in record['requests']:
            record['failed_stage']='query'
            report({'stage':'evidence_supplement','completed':record['additional_tool_calls'],'total':len(requests),'unit':'tools'})
            with closing(connect_review_database(db,readonly=True)) as connection,connection:
                connection.execute('BEGIN')
                if isinstance(connection,PostgresConnection):
                    connection.execute("SELECT set_config('statement_timeout', LEAST(COALESCE(NULLIF(setting::int,0),30000),30000)::text,true) FROM pg_settings WHERE name='statement_timeout'")
                child_plan=ReviewQueryPlan('opinion_breakdown' if request['tool']=='get_opinion_breakdown' else 'analysis',
                    compact.sentiment,request['scope'],requirements=[{'tool':request['tool'],'parameters':request['parameters']}],
                    requires_evidence=True,max_evidence_items=10,max_evidence_pages=1)
                child=ReviewToolContext(connection,request['scope'],run_dir,settings.max_tool_calls-context.tool_calls,
                    sentiment_intent=compact.sentiment,query_plan=child_plan,settings=settings)
                registry=create_review_registry(child)
                try:
                    response=registry.execute_tool(request['tool'],json.dumps(request['parameters'],ensure_ascii=False),automatic=True).to_dict()
                    if response['status']!='success':raise ReviewDataError(response.get('message')or'补查工具失败。')
                    ids=list(child.results)
                    record['failed_stage']='independent_validation'
                    validation=validate_report(child.results,ids,sentiment_intent=compact.sentiment,query_plan=child_plan,
                        expected_scopes=expected_result_scopes(child.results,request['scope']))
                    request['validation']=validation
                    if validation['status']!='passed':raise ReviewDataError('补查结果独立校验失败。')
                    check_sample_continuity(compact,request,child.results)
                    request['result_ids']=[]
                    for old_id,row in child.results.items():
                        key='r'+str(len(context.results));row=deepcopy(row)
                        row.update(result_id=key,execution_scope=request['execution_scope'])
                        context.results[key]=row;result['result_ids'].append(key);request['result_ids'].append(key)
                        for event in child.trace:
                            if event.get('result_id')==old_id:event['result_id']=key
                    merged.requirements.append({key:request[key]for key in ('tool','parameters','execution_scope')})
                finally:
                    for name in ('tool_calls','query_executions','cache_hits','query_duration_ms','topic_model_calls'):
                        setattr(context,name,getattr(context,name)+getattr(child,name))
                    record['additional_tool_calls']+=child.tool_calls
                    context.trace.extend({**event,'phase':'evidence_supplement'}for event in child.trace)
        merged.analysis_type='composite'
        merged.requires_evidence=True;merged.max_evidence_items=max(merged.max_evidence_items,10)
        record['failed_stage']='merged_validation'
        combined=validate_report(context.results,result['result_ids'],sentiment_intent=compact.sentiment,query_plan=merged,
            expected_scopes=expected_result_scopes(context.results,result['scope']))
        record['merged_validation']=combined
        if combined['status']!='passed':raise ReviewDataError('原结果与补查结果合并校验失败。')
        result.update(validation=combined,analysis_intent=merged.analysis_type,query_plan=merged.to_dict())
        result.update(build_analysis_artifacts({**result,'results':context.results},merged))
        record.update(status='completed',failed_stage=None,final_result_ids=list(result['result_ids']),
            evidence_status='available'if result['compact_context']['evidence']else'insufficient_data',
            remaining_assessment=assess_evidence(CompactAnalysisContext.model_validate(result['compact_context'])))
        return merged
    except Exception as exc:
        record.update(status='failed',error_type=type(exc).__name__,failure_reason=str(exc)if isinstance(exc,ReviewDataError)else'补查查询或校验失败。')
        result['validation']={'status':'failed','checks':record['initial_validation']['checks']+
            [{'name':'证据补查','passed':False,'detail':record['failure_reason']}]}
        raise
    finally:record['duration_ms']=round((perf_counter()-started)*1000,3)
