"""固定规则版本的完整快照分类；未知观点保留，统计不依赖在线模型预算。"""
from copy import deepcopy
import re

from data_analysis_agent.agents import review_reported_facts as facts
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.reviews.analytics import count_rate
from data_analysis_agent.reviews.opinions import ASPECTS

VERSION='review-subtopics-v1'
ASPECT_ALIASES={'质量':'quality','效果':'effectiveness','使用效果':'effectiveness','价格':'price',
                '包装':'packaging','物流':'logistics','客服':'service','服务':'service'}
# IDs express experiences, not objective diagnoses. All unrecognized clauses stay unresolved.
CATALOG={
    'cooling_failure':('effectiveness','negative','完全不制冷'),
    'cooling_poor':('effectiveness','negative','制冷不足'),
    'cooling_slow':('effectiveness','negative','降温慢'),
    'cooling_good':('effectiveness','positive','制冷效果好'),
    'cooling_fast':('effectiveness','positive','降温快'),
    'heating_poor':('effectiveness','negative','制热不足'),
    'heating_failure':('effectiveness','negative','完全不制热'),
    'heating_good':('effectiveness','positive','制热效果好'),
    'heating_fast':('effectiveness','positive','制热快'),
    'noise_high':('effectiveness','negative','运行噪声大'),
    'noise_low':('effectiveness','positive','运行安静'),
    'delivery_slow':('logistics','negative','送达慢'),
    'delivery_fast':('logistics','positive','送达快'),
    'delivery_dispatch_slow':('logistics','negative','发货慢'),
    'delivery_dispatch_fast':('logistics','positive','发货快'),
    'service_bad':('service','negative','客服态度差'),
    'service_good':('service','positive','客服态度好'),
    'service_response_slow':('service','negative','客服响应慢'),
    'installer_bad':('service','negative','安装服务态度差'),
    'installer_good':('service','positive','安装服务态度好'),
    'installer_slow':('service','negative','安装不及时'),
    'installer_charge':('service','negative','安装收费不满'),
    'packaging_bad':('packaging','negative','包装破损'),
    'packaging_good':('packaging','positive','包装完好'),
    'packaging_sturdy':('packaging','positive','包装结实'),
    'price_high':('price','negative','价格高'),
    'price_low':('price','positive','价格便宜'),
    'quality_damage':('quality','negative','外壳破损'),
    'quality_power':('quality','negative','无法开机'),
    'quality_build':('quality','positive','做工认可'),
}
EXTRA={
    'packaging_sturdy':r'(?:包装|外包装)(?:很结实|结实|严实|很严实)',
    'delivery_dispatch_slow':r'发货(?:速度)?(?:慢|很慢|太慢|迟缓|不及时)',
    'delivery_dispatch_fast':r'发货(?:速度)?(?:快|很快|及时)',
    'service_response_slow':r'客服(?:回复|响应)(?:慢|很慢|太慢|不及时)|客服(?:不回复|联系不上|一直不回复)',
    'installer_slow':r'安装(?:很慢|慢|太慢|不及时)',
    'installer_charge':r'安装(?:收费|费用)(?:高|太高|贵|太贵|不合理)|安装乱收费',
    'quality_damage':r'外壳(?:破裂|开裂|破损|损坏|易裂)',
    'quality_power':r'(?:空调)?(?:无法开机|开不了机|不能开机|无法启动)',
    'quality_build':r'做工(?:牢固|扎实|精细|好|不错)',
}
QUERY_ALIASES={
    'cooling_poor':('制冷不佳','制冷效果差','制冷不够给力','降温不给力','降温效果不好','冷气不足'),
    'cooling_failure':('不制冷','不降温','完全不降温','降不下温','无法降温'),
    'heating_failure':('不制热','无法升温','升不了温'),
    'cooling_good':('制冷好','降温效果好'),
    'cooling_slow':('制冷慢','制冷速度慢','降温速度慢'),
    'cooling_fast':('制冷快','制冷速度快','降温速度快'),
    'delivery_slow':('物流慢','配送慢','送货慢','到货慢','配送迟缓'),
    'delivery_fast':('物流快','配送快','送货快','到货快','物流很快'),
}
DOMAINS={**facts.DOMAINS,'quality':'外壳|开机|启动|做工','delivery':facts.DOMAINS['delivery']+'|发货',
         'installer':'安装'}
RULE_DIGEST=content_sha256([VERSION,CATALOG,EXTRA,QUERY_ALIASES,facts.POLICY,facts.PATTERNS,facts.HAZARDS,
                           facts.RETRACTIONS,facts.FORWARD_FRAMES,DOMAINS])


def fact(clause):
    text=re.sub(r'^(?:'+facts.CONNECTIVES+r')','',clause.strip()).strip()
    text=re.sub(r'^(?:质量|服务|安装|物流|冷暖效果|使用效果|使用体验)[：:]\s*','',text)
    text=re.sub(r'^(?:我觉得|我感觉|实际用下来|用起来)[，,]?','',text)
    if re.fullmatch(EXTRA['packaging_sturdy'],text):return 'packaging_sturdy'
    value=facts.fact(clause)
    if value:return value
    hits=[key for key,pattern in EXTRA.items() if re.fullmatch(pattern,text)]
    return hits[0] if len(hits)==1 else None


def topic_request(question, context=None):
    """Only explicit statistical/content goals; never turn a causal request into counting."""
    q=question.strip().rstrip('？?。！!')
    if q.startswith('执行本轮已确认的只读查询'):return None
    if re.search(r'为什么|原因|因果|导致|造成|预测|销量|销售额|按[月日天]|趋势|星级|数据质量|对比|比较|基线',q):return None
    from data_analysis_agent.agents.review_semantics import requested_denominator
    denominator=requested_denominator(q)
    if denominator not in (None,'review_count','successful_reviews'):return None
    ratio=re.fullmatch(r'(.+?)(?:的)?(?:反馈|评论)?占(?:全部|所有)评论(?:的)?(?:多少|比例|百分比|占比)',q)
    if ratio:
        body=ratio[1].removesuffix('的').strip()
        value=next((key for key,(_,_,name) in CATALOG.items() if body==name or body in QUERY_ALIASES.get(key,())),None)
        if value in CATALOG:
            return dict(aspect=None,sentiment=CATALOG[value][1],topic=value,denominator='review_count')
    if re.search(r'细分主题|各具体问题|主要抱怨什么|主要在抱怨什么',q):
        aspects=sorted({key for label,key in ASPECT_ALIASES.items() if label in q})
        from data_analysis_agent.agents.review_prompts import detect_sentiment_directions
        directions=detect_sentiment_directions(q)
        if len(aspects)>1 or len(directions)>1:return None
        return dict(aspect=aspects[0] if len(aspects)==1 else None,
                    sentiment=next(iter(directions)) if directions else 'negative',
                    topic=None,denominator=denominator or 'review_count')
    queries=((context or {}).get('validated') or {}).get('queries',[])
    if len(queries)==1 and queries[0]['kind']=='topic_statistics':
        previous=queries[0]['parameters']
        emotion=re.fullmatch(r'(?:那|那么|然后)?(好评|差评|正面|负面)(?:呢|吧)?',q)
        aspect_match=re.fullmatch(r'(?:那|那么|只看|换成|改成)?(使用效果|质量|效果|包装|物流|客服|服务|价格)(?:方面|问题)?(?:呢)?',q)
        if emotion or aspect_match:
            aspect=previous.get('aspect') or (CATALOG.get(previous.get('topic')) or (None,))[0]
            if aspect_match:aspect=ASPECT_ALIASES[aspect_match[1]]
            return dict(aspect=aspect,topic=None,denominator=previous.get('denominator','review_count'),
                sentiment=('positive' if emotion[1] in ('好评','正面') else 'negative') if emotion else previous['sentiment'])
    return None


def build_statistics(corpus, *, aspect=None, topic=None, denominator='review_count'):
    if aspect is not None and aspect not in ASPECTS:raise ValueError('invalid_topic_aspect')
    if topic is not None and topic not in CATALOG:raise ValueError('invalid_topic_id')
    if denominator not in ('review_count','successful_reviews'):raise ValueError('invalid_topic_denominator')
    scope=corpus['scope']; summary=corpus['summary']; sentiment=corpus['sentiment']
    if (any(type(value) is not int or value<0 for value in
            (corpus['total'],corpus['total_opinions'],summary['successful_reviews'],summary['review_count']))
            or len(scope['product_ids'])!=1 or sentiment not in ('positive','negative')
            or not 0<=corpus['total']<=summary['successful_reviews']<=summary['review_count']
            or corpus['total_opinions']<corpus['total']):raise ValueError('invalid_topic_population')
    assignments=[]; found={}; unresolved=set(); all_reviews=set(); complete_reviews=set()
    for item in corpus['items']:
        rid=item['review_id']
        if type(rid) is not int or rid<=0 or rid in all_reviews:raise ValueError('invalid_or_duplicate_topic_review')
        all_reviews.add(rid); complete=True
        if (item['product_id'] not in scope['product_ids'] or item['source']!=scope['source']
                or item['dataset_version']!=scope['dataset_version']
                or not scope['date_from']<=item['published_at'][:10]<=scope['date_to']):raise ValueError('topic_source_scope')
        for index,op in enumerate(item['opinions']):
            start,end=op['quote_start'],op['quote_end']
            if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(item['text'])
                    or item['text'][start:end]!=op['quote'] or op['sentiment']!=sentiment):raise ValueError('topic_source_span')
            oid='opinion:'+content_sha256([rid,item['annotation_id'],item['revision_id'],item['opinion_source'],index,op])[:24]
            parsed=[fact(clause) for _,_,clause in facts.clauses(op['quote'])]
            accepted=set(); rejected=False
            for value in {value for value in parsed if value}:
                key=value
                if (key not in CATALOG or CATALOG[key][:2]!=(op['aspect'],sentiment)
                        or not facts.context_ok(dict(review_text=item['text'],quote=op['quote'],quote_start=start,quote_end=end),
                            {value},fact_parser=fact,domains=DOMAINS)):
                    rejected=True;continue
                for key in facts.closure({value}) & CATALOG.keys():
                    accepted.add(key)
                    entry=found.setdefault(key,{'review_ids':set(),'opinion_ids':set(),'example':(rid,index)})
                    entry['review_ids'].add(rid);entry['opinion_ids'].add(oid)
            state=('assigned' if accepted and all(parsed) and not rejected else 'partial' if accepted
                   else 'uncertain' if rejected else 'unclassified')
            if state!='assigned':unresolved.add(rid);complete=False
            assignments.append(dict(opinion_id=oid,review_id=rid,opinion_index=index,
                annotation_id=item['annotation_id'],revision_id=item['revision_id'],opinion_source=item['opinion_source'],
                quote_start=start,quote_end=end,quote_sha256=content_sha256(op['quote']),
                state=state,topic_ids=sorted(accepted)))
        if complete:complete_reviews.add(rid)
    if len(all_reviews)!=corpus['total'] or len(assignments)!=corpus['total_opinions']:raise ValueError('incomplete_topic_corpus')
    classified=sum(row['state']=='assigned' for row in assignments)
    den=summary[denominator]; unlabelled=summary['review_count']-summary['successful_reviews'] if denominator=='review_count' else 0
    topics=[];items={}
    keys=[key for key in CATALOG if (not aspect or CATALOG[key][0]==aspect)
          and (not topic or key==topic) and CATALOG[key][1]==sentiment]
    if topic is None:keys=[key for key in keys if key not in ('cooling_failure','heating_failure')]
    for key in keys:
        entry=found.get(key,dict(review_ids=set(),opinion_ids=set()))
        rids=sorted(entry['review_ids']);oids=sorted(entry['opinion_ids']);count=len(rids)
        upper=min(den,count+len(unresolved-set(rids))+unlabelled)
        topics.append(dict(topic_id=key,name=CATALOG[key][2],aspect=CATALOG[key][0],sentiment=sentiment,
            review_ids=rids,opinion_ids=oids,review_count=count,opinion_count=len(oids),
            denominator=den,denominator_name=denominator,rate=count_rate(count,den),upper_rate=count_rate(upper,den),
            rate_state='exact' if upper==count else 'lower_bound'))
    topics.sort(key=lambda row:(-row['review_count'],-row['opinion_count'],row['topic_id']))
    for row in topics[:3]:
        if row['review_count']:
            rid,index=found[row['topic_id']]['example'];source=next(item for item in corpus['items'] if item['review_id']==rid)
            saved=items.setdefault(rid,{**deepcopy(source),'opinions':[]})
            op=source['opinions'][index]
            if op not in saved['opinions']:saved['opinions'].append(deepcopy(op))
    return dict(version=VERSION,rule_sha256=RULE_DIGEST,scope=deepcopy(scope),summary=deepcopy(summary),
        aspect=aspect,sentiment=sentiment,topic=topic,denominator=denominator,
        coverage=dict(processed_opinions=len(assignments),total_opinions=corpus['total_opinions'],
            classified_opinions=classified,unresolved_opinions=len(assignments)-classified,
            classified_reviews=len(complete_reviews),unresolved_reviews=len(unresolved),
            classification_coverage=count_rate(classified,len(assignments)),
            annotation_coverage=summary['labeling_coverage'],unlabelled_reviews=summary['review_count']-summary['successful_reviews']),
        assignments=assignments,topics=topics,items=list(items.values()),corpus=deepcopy(corpus))


def validate_statistics(data, *, verified_payloads=None):
    try:
        payload = canonical_json(data)
        key = (VERSION, RULE_DIGEST, payload)
        if verified_payloads is not None and key in verified_payloads:
            return True
        rebuilt = build_statistics(data['corpus'], aspect=data['aspect'], topic=data['topic'],
                                   denominator=data['denominator'])
        valid = payload == canonical_json(rebuilt)
        if valid and verified_payloads is not None:
            verified_payloads.add(key)
        return valid
    except (ValueError,KeyError,TypeError,AttributeError,IndexError):return False


def compact_statistics(data):
    output={key:deepcopy(data[key]) for key in ('version','rule_sha256','coverage','denominator')}
    evidence={item['review_id'] for item in data['items']}
    output['topics']=[{**{key:deepcopy(value) for key,value in row.items() if key not in ('review_ids','opinion_ids')},
                      'evidence_review_ids':sorted(set(row['review_ids'])&evidence)} for row in data['topics']]
    return output
