"""本地有限语义关系：逐字证据驱动，不聚类、不猜未知表达、不向模型提供衍生内容。"""
from collections import defaultdict
import re

from data_analysis_agent.core.serialization import content_sha256

VERSION='local_evidence_spans_v1'
# 只接受完整的明确自述。扩展表达前应增加正反例，不把关键词命中视为通用语义理解。
RULES=[
    ('packaging','outer_state','外箱状态',r'外箱(?:被)?(?:压瘪|压扁|受压|变形)(?:但(?:商品|内件)(?:完好|无损))?'),
    ('packaging','internal_retention','盒内固定',r'内衬移位(?:导致|造成)?(?:配件在盒内晃动|盒内晃动)'),
    ('packaging','closure','封口完整性',r'封口(?:开裂|破开)'),
    ('logistics','pre_dispatch','发货前等待',r'发货前(?:等待|等候)(?:时间)?(?:长|很久)'),
    ('logistics','in_transit','运输追踪',r'(?:配送中|运输中)(?:物流)?(?:更新)?(?:停滞|不更新)'),
    ('logistics','appointment','预约履约',r'预约时间与送达不一致|约定(?:送达)?时间与实际送达不一致'),
]


def evidence_relations(context,catalog):
    if not re.search(r'具体|反映|解释|环节|问题类型|原因|差异|区别|变化|不同',context.question):return []
    if any(stat.get('filters',{}).get('analysis_status') in ('partial','failed','insufficient_data')for stat in context.statistics):return []
    scopes={row['scope_ref']:row for row in context.scope_summary}
    nodes=[];unresolved=0
    for fact in catalog:
        if fact['kind'] not in ('opinion_group','topic'):continue
        stat=next(row for row in context.statistics if row['source_result_id']in fact['result_ids'])
        filters=stat['filters']
        if filters.get('sentiment')!='negative' or filters.get('aspect')not in ('packaging','logistics'):continue
        if fact['kind']=='opinion_group':
            target=next((row for i,row in enumerate(filters['groups'])if 'finding:'+content_sha256([stat['source_result_id'],i,row['quote'],row['sentiment']])[:20]==fact['fact_id']),None)
        else:
            target=next((row for row in filters['topics']if 'finding:'+content_sha256([stat['source_result_id'],row['topic_id']])[:20]==fact['fact_id']),None)
        if not target:continue
        for ref in context.evidence:
            if ref.source_result_id not in fact['result_ids']or ref.review_id!=target['review_id']or ref.evidence_ref!=target['evidence_ref']:continue
            evidence_id='evidence:'+content_sha256([ref.source_result_id,ref.review_id,ref.evidence_ref])[:20]
            if evidence_id not in fact['evidence_ids']:continue
            for opinion in ref.opinions:
                if opinion.truncated or opinion.quote!=target['quote']or opinion.aspect!=filters['aspect']or opinion.sentiment!='negative':continue
                matches=[(key,label)for aspect,key,label,pattern in RULES if aspect==opinion.aspect and re.fullmatch(pattern,opinion.quote)]
                if len(matches)!=1:
                    unresolved+=1;continue
                role,label=matches[0];scope=scopes[fact['scope_ref']]
                if len(scope.get('product_ids',[]))!=1 or ref.product_id!=scope['product_ids'][0]:continue
                if not ref.published_at or not scope['date_from']<=ref.published_at[:10]<=scope['date_to']:continue
                nodes.append({'finding_ref':fact['fact_id'],'source_result_id':ref.source_result_id,'scope_ref':fact['scope_ref'],
                    'product_id':scope['product_ids'][0],'date_from':scope['date_from'],'date_to':scope['date_to'],
                    'aspect':opinion.aspect,'sentiment':'negative','role':role,'label':label,'evidence_id':evidence_id,
                    'review_id':ref.review_id,'evidence_ref':ref.evidence_ref,'quote_sha256':content_sha256(opinion.quote),
                    'quote_start':opinion.quote_start,'quote_end':opinion.quote_end,
                    'reported_contents_intact':bool(re.fullmatch(r'外箱(?:被)?(?:压瘪|压扁|受压|变形)但商品完好',opinion.quote))})
    findings=[]
    for aspect in ('packaging','logistics'):
        selected=[node for node in nodes if node['aspect']==aspect]
        if not selected:continue
        groups=defaultdict(list)
        for node in selected:groups[(node['product_id'],node['date_from'],node['date_to'])].append(node)
        if aspect=='packaging':
            if len(groups)!=1:continue
            labels=list(dict.fromkeys(node['label']for node in selected))
            text='已检索的包装负向原文涉及'+'、'.join(labels)+'，这些描述对应不同的包装现象。'if len(labels)>1 else '已检索的包装负向原文涉及'+labels[0]+'。'
            if any(node['reported_contents_intact']for node in selected):
                text+='其中有买家明确自述外箱受压但商品完好，不能把包装负向提及全部解释为内件损坏。'
        else:
            if len(groups)<2:continue
            group_scopes=[scopes[rows[0]['scope_ref']]for rows in groups.values()]
            if any(len({scope.get(key)for scope in group_scopes})!=1 for key in ('source','dataset_version','annotation_profile_ref','as_of')):continue
            products={key[0]for key in groups};windows={(key[1],key[2])for key in groups}
            if len(products)>1 and len(windows)>1:continue
            if len(products)==1:
                ordered=sorted(windows)
                if len(ordered)!=2 or ordered[0][1]>=ordered[1][0]:continue
            phrases=[]
            for (product,start,end),rows in sorted(groups.items()):
                phrases.append(f'商品 {product}（{start} 至 {end}）的已检索负向原文涉及'+ '、'.join(dict.fromkeys(row['label']for row in rows)))
            text='；'.join(phrases)+'。'
            if len(products)>1:text+='这区分了当前评论报告的履约环节，不代表已经确认责任方或真实服务水平差距。'
            else:text+='这描述了两个窗口中已检索问题的内容区别，不能据此确认负面占比变化的实际原因。'
        refs=list(dict.fromkeys(node['finding_ref']for node in selected))
        findings.append({'relation_id':'relation:'+content_sha256([VERSION,selected])[:20],'version':VERSION,
            'status':'locally_verified_descriptive','aspect':aspect,'text':text,'finding_refs':refs,
            'source_result_ids':list(dict.fromkeys(node['source_result_id']for node in selected)),
            'evidence_ids':list(dict.fromkeys(node['evidence_id']for node in selected)),
            'bindings':selected,'unresolved_evidence_count':unresolved,'external_model_input':False,
            'limitation':'仅解释已检索且能够明确识别的表述；不是总体问题分类、标准主题标签或客观因果结论。'})
    return findings


def apply_evidence_relations(context,answer,catalog):
    relations=evidence_relations(context,catalog)
    answer.evidence_relations=relations
    if not relations:
        if re.search(r'具体|环节|问题类型|现象层次',context.question)and any(stat.get('filters',{}).get('groups')and stat.get('filters',{}).get('aspect')in('packaging','logistics')for stat in context.statistics):
            answer.summary='本次已取得对应观点原文，但本地证据表示尚不能可靠区分所问的具体问题或环节。以下保留可核验的表述，不猜测现象关系或实际原因。'
        return answer
    text=''.join(row['text']for row in relations)
    trends=[row for row in catalog if row['kind']=='trend']
    if trends:
        from data_analysis_agent.agents.review_insights import lead_summary
        text=lead_summary(context,trends)+text
    answer.summary=text
    return answer
