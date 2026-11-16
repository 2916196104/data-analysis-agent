"""Bounded reported-experience grammar, not a general entailment model.

Only complete subject/predicate clauses supply facts. Context can veto but cannot
add facts. Explicit entailment edges never infer speed, causes or population rates.
"""
from hashlib import sha256
import re

POLICY = 'reported_experience_v3'
PATTERNS = {
    'cooling_failure': r'(?:空调)?(?:完全|根本)?(?:不制冷|不降温|降不下温|无法降温|不能降温)',
    'cooling_poor': r'(?:空调)?(?:制冷|降温)(?:效果|能力)?(?:不足|不行|差|很差|太差|不佳|不好|不太好|不理想|不给力|不够给力)|(?:空调)?冷气不足',
    'cooling_good': r'(?:空调)?(?:制冷|降温)(?:效果)?(?:也)?(?:好|很好|非常好|特别好|不错|很不错|很棒)',
    'cooling_slow': r'(?:空调)?(?:制冷|降温)(?:速度)?(?:慢|很慢|太慢|不快|较慢|缓慢|迟缓)',
    'cooling_fast': r'(?:空调)?(?:制冷|降温)(?:速度)?(?:快|很快|非常快|迅速)',
    'heating_failure': r'(?:空调)?(?:完全|根本)?不制热|(?:空调)?(?:无法升温|升不了温)',
    'heating_poor': r'(?:空调)?(?:制热|升温)(?:效果)?(?:不足|不行|差|很差|不佳|不好|不理想|不给力|不够给力)',
    'heating_good': r'(?:空调)?制热(?:效果)?(?:好|很好|不错)',
    'heating_fast': r'(?:空调)?制热(?:速度)?(?:快|很快)',
    'noise_high': (r'(?:空调)?(?:运行)?(?:噪音|噪声)(?:大|很大|太大|有点大|明显)'
                   r'|(?:(?:空调)?运行声音|空调声音)(?:大|很大|太大|有点大|很吵)|(?:空调)?声音很吵'),
    'noise_low': r'(?:空调)?(?:噪音|噪声)(?:小|很小|不大)|(?:空调)?很安静',
    'delivery_fast': r'(?:物流|配送|到货|送货|快递|快递到货)(?:速度)?(?:很快|快|迅速)',
    'delivery_slow': (r'(?:(?:物流|配送|到货|送货|快递|快递到货)(?:速度)?(?:很慢|太慢|慢|缓慢|迟缓)'
                      r'|(?:商品|快递|包裹)?到货时间(?:太|很|特别|过于)(?:长|久))(?:了|啊|呀|呢)?'),
    'service_good': r'客服(?:态度好|态度很好|很热情)',
    'service_bad': r'客服(?:态度差|态度很差|不耐烦)',
    'installer_good': r'安装师傅(?:态度好|态度很好|很热情)',
    'installer_bad': r'安装师傅(?:态度差|态度很差|不耐烦)',
    'packaging_bad': r'(?:包装|外包装)(?:破了|破损|损坏)',
    'packaging_good': r'(?:包装|外包装)(?:完好|没有破损|结实|很结实|严实|很严实)',
    'price_high': r'价格(?:太高|很高|偏高|贵|太贵)',
    'price_low': r'价格(?:便宜|很便宜|不贵)',
}
EDGES = {'cooling_failure': {'cooling_poor'}, 'heating_failure': {'heating_poor'}}
OPPOSITES = [('cooling_good','cooling_poor'),('cooling_fast','cooling_slow'),
             ('heating_good','heating_poor'),('noise_high','noise_low'),
             ('delivery_fast','delivery_slow'),('service_good','service_bad'),
             ('installer_good','installer_bad'),('packaging_good','packaging_bad'),('price_high','price_low')]
HAZARDS = r'不是|并非|并不|未必|不一定|不能说|不代表|没有觉得|没觉得|没发生|不存在|不属实|不是真的|谣言|假的|别人|他人|朋友说|听说|据说|客服说|广告|宣传|号称|假如|如果|要是|可能|也许|以为|更正|纠正|撤回|误会|误解|玩笑|反话|另一台|别的|其他机器|过去|以前|曾经|后来|现在|不再|说法|[？?“”「」"]'
CONNECTIVES = r'但是|不过|然而|可是|并且|而且|但'
DOMAINS = {'cooling':'制冷|冷气|降温','heating':'制热|升温','noise':'噪音|噪声|声音',
           'delivery':'物流|配送|到货|送货|快递','service':'客服','installer':'安装师傅',
           'packaging':'包装','price':'价格'}
# Retractions and references can govern earlier clauses even without a topic noun.
RETRACTIONS = (r'忽略.*规则|给AI|提示词|系统指令|更正|纠正|撤回|误会|误解|玩笑|反话|反讽|另一台|其他机器'
               r'|不对|错了|不实|其实|实际上|上述|那是|那只是|这只是|才怪|否认|这话|此话|前一句'
               r'|不.*(?:认为|觉得|认同|认可)|没(?:有)?这回事|此事并未发生')
CONTEXT_HAZARDS = HAZARDS + r'|说|声称|表示|转述|传闻|假设|假定|倘若|至于|别的'
FORWARD_FRAMES = r'说|声称|表示|转述|传闻|听说|据说|广告|宣传|号称|假如|如果|要是|假设|假定|倘若|可能|也许|以为|过去|以前|曾经|后来|现在|不再'


def clauses(text):
    """Keep source offsets while separating punctuation and explicit conjunctions."""
    for match in re.finditer(r'[^，,。！!；;\n]+', text):
        cuts = [0] + [m.start() for m in re.finditer(CONNECTIVES, match[0]) if m.start()] + [len(match[0])]
        for left, right in zip(cuts, cuts[1:]):
            yield match.start() + left, match.start() + right, match[0][left:right]


def fact(clause):
    clause = clause.strip().rstrip('。！!')
    clause = re.sub(r'^(?:' + CONNECTIVES + r')\s*', '', clause)
    clause = re.sub(r'^(?:冷暖效果|使用效果|使用体验)[：:]\s*', '', clause)
    # These first-person frames assert the following experience, not objective truth.
    clause = re.sub(r'^(?:我觉得|我感觉|实际用下来|用起来)[，,]?', '', clause)
    hits = [key for key,pattern in PATTERNS.items() if re.fullmatch(pattern,clause)]
    return hits[0] if len(hits)==1 else None


def closure(facts):
    return set(facts) | {value for key in facts for value in EDGES.get(key,())}


def target(question):
    prefix=r'(?:请问|想问|想知道|想了解)?(?:(?:当前|这款|该)(?:商品|产品|空调))?'
    match = re.fullmatch(prefix +
        r'(?:有没有|是否有|有无|是否存在)(.+?)(?:的)?(?:类似)?(?:反馈|抱怨|评论|评价|证据)[？?。！!]*',question.strip())
    if not match:
        match = re.fullmatch(prefix + r'(?:有|有没有|是否有)(?:买家|用户)(?:反馈说|反映|提到|说)(.+?)(?:吗|么)[？?。！!]*', question.strip())
    if not match:
        match = re.fullmatch(r'(?:请帮我|帮我|请)?(?:找|查找|检索|搜索)(?:出|一下)?(.+?)的(?:反馈|抱怨|评论|评价|原文|证据)[？?。！!]*', question.strip())
    if not match:return None
    body=match[1].removesuffix('的').strip('“”「」" ')
    relation=None;operator='all_of'
    content=re.sub(r'^(?:买家|用户)(?:反馈说|反映|提到|说)', '', body)
    content=re.sub(r'^同一(?:条)?评论(?:中|里)?', '', content)
    pair=re.fullmatch(r'既(?:说)?(.+?)又(?:说|抱怨)?(.+)',content)
    if pair:
        texts=list(pair.groups());relation='same_review'
    else:
        content=re.sub(r'^(?:同时)?(?:提到|报告|反映|说)?', '', content)
        pair=re.fullmatch(r'(.+?)(或者|或|并且|而且|以及|且|和|但是|但|却)(?:提到|报告|反映|说|抱怨)?(.+)',content)
        if pair:
            texts=[pair[1],pair[3]]
            operator='any_of' if pair[2] in ('或者','或') else 'all_of'
            relation='same_review' if operator=='all_of' else None
        else:texts=[content]
    # A same-review wrapper must not be silently weakened into an OR query.
    if content != body and body.startswith('同一') and operator=='any_of':return None
    facts=[fact(text) for text in texts]
    if any(value is None for value in facts) or len(set(facts))!=len(facts):return None
    return {'facts':facts,'relation':relation,'text':body,'clauses':texts,'operator':operator}


def quote_facts(quote):
    # No arbitrary substring matching: each supporting clause must be complete.
    return {value for _, _, clause in clauses(quote) if (value:=fact(clause))}


def context_ok(row, facts, *, fact_parser=fact, domains=DOMAINS, opposites=OPPOSITES):
    text=row['review_text'];start=row['quote_start'];end=row['quote_end']
    if ('delivery_slow' in facts and re.search(r'等(?:待)?了[一二三四五六七八九十两\d]{1,3}(?:天|日|周|星期)才到货', row['quote'])
            and re.search(r'退货|退回|退件|返修|寄回|换货', text)):
        return False
    if re.search(RETRACTIONS,text):return False
    # A phrase cut out of a negated/reported/hypothetical clause is not an assertion.
    containing=[c for c in clauses(text) if c[0]<end and c[1]>start]
    if not containing:return False
    for left,right,_ in containing:
        before=text[left:start] if left<start else ''
        after=text[end:right] if right>end else ''
        if before.strip() and not re.fullmatch(r'(?:(?:' + CONNECTIVES + r')\s*)?(?:(?:冷暖效果|使用效果|使用体验)[：:]\s*)?',before):return False
        if after.strip():return False
    contextual=closure({value for _,_,clause in clauses(text) if (value:=fact_parser(clause))})
    for a,b in opposites:
        if (a.split('_')[0] in {f.split('_')[0] for f in facts}
                and a in contextual and b in contextual):return False
    # Unrecognized clauses mentioning the same target can qualify or retract it;
    # unrelated delivery/appearance context is not a reason to reject the quote.
    words='|'.join(domains[f.split('_')[0]] for f in facts)
    for left,_,clause in clauses(text):
        clause=re.sub(r'^(?:冷暖效果|使用效果|使用体验)[：:]\s*','',clause.strip())
        # A separate duration describes context, but cannot itself establish delay.
        neutral_wait = (facts == {'delivery_slow'} and re.fullmatch(
            r'等(?:待)?了[一二三四五六七八九十两\d]{1,3}(?:天|日|周|星期)才到货(?:了|啊|呀|呢)?', clause))
        if re.search(words,clause):
            if fact_parser(clause) is None and not neutral_wait:return False
            # An earlier reporting/conditional frame can govern coordinated
            # clauses until the sentence ends, even with an unrelated first topic.
            sentence_prefix=re.split(r'[。！!？?\n]',text[:left])[-1]
            if re.search(FORWARD_FRAMES,sentence_prefix):return False
        if re.search(CONTEXT_HAZARDS,clause) or re.match(CONNECTIVES,clause):
            # Ignore a frame only when its ENTIRE proposition has a known,
            # independent topic. Orphan frames/pronouns may govern the target.
            independent=re.sub(r'^(?:' + CONNECTIVES + r')', '', clause).strip()
            independent=re.sub(r'^(?:客服说|朋友说|别人说|听说|据说|后来|现在|以前|曾经)', '', independent)
            independent=re.sub(r'^(?:不是|并非|并不)', '', independent)
            if re.search(words,clause):
                if re.search(CONTEXT_HAZARDS,clause):return False
            elif fact_parser(independent) is None:return False
    return True


def licensed_reviews(request, groups):
    if not request:return set()
    wanted=set(request['facts'])
    return {rid for rid,covered in groups.items()
            if (bool(wanted & covered) if request['operator']=='any_of' else wanted<=covered)}


def supported_targets(wanted, bindings):
    covered={fid for binding in bindings for fid in binding['fact_ids']}
    return [fid for fid in wanted if fid in covered]


def assess(question,evidence):
    request=target(question);wanted=request['facts'] if request else []
    judgments=[];groups={};bindings=[]
    for row in evidence:
        explicit=quote_facts(row['quote'])
        supported=closure(explicit)&set(wanted)
        safe=bool(supported) and context_ok(row,supported)
        judgments.append({'key':row['key'],'support':'direct_report' if safe else 'unknown',
            'reason':'explicit_entailment' if safe else 'context_unresolved' if supported else 'semantic_not_verified',
            'explicit_fact_ids':sorted(explicit),'supported_fact_ids':sorted(supported) if safe else []})
        if safe:
            groups.setdefault(row['review_id'],set()).update(supported)
            bindings.append({'key':row['key'],'review_id':row['review_id'],
                'quote_start':row['quote_start'],'quote_end':row['quote_end'],
                'quote_sha256':sha256(row['quote'].encode('utf-8')).hexdigest(),
                'fact_ids':sorted(supported)})
    valid_reviews=licensed_reviews(request,groups)
    chosen=[b for b in bindings if b['review_id'] in valid_reviews]
    keys=[r['key'] for r in evidence if any(b['key']==r['key'] for b in chosen)]
    sufficient=bool(keys)
    return {'policy':POLICY,'status':'sufficient' if sufficient else 'insufficient',
        'target':request['text'] if request else None,'required_fact_ids':wanted,
        'operator':request['operator'] if request else 'all_of',
        'supported_target_fact_ids':supported_targets(wanted,chosen),
        'missing_required_fact_ids':[] if sufficient else wanted or ['unresolved_query'],
        'permitted_keys':keys,'fact_bindings':chosen,'candidate_judgments':judgments,
        'relation':{'type':request['relation'],'supported':bool(valid_reviews),'review_ids':sorted(valid_reviews)} if request and request['relation'] else None,
        'reason':'explicit_report_only' if sufficient else 'no_candidates' if not evidence else
                 'query_semantics_uncovered' if not request else 'no_verified_direct_report',
        'scope_of_claim':'retrieved_buyer_report_only','general_entailment':False}


def validate_answer_bindings(question, assessment, evidence):
    """Recheck compact answer facts/spans. Full source/context is checked upstream."""
    request=target(question)
    wanted=request['facts'] if request else []
    bindings=[]; groups={}; keys=[]
    for ref in evidence:
        for quote in ref.opinions:
            supported=closure(quote_facts(quote.quote)) & set(wanted)
            if not supported or quote.quote_end-quote.quote_start != len(quote.quote):return False
            keys.append(quote.opinion_key)
            bindings.append({'key':quote.opinion_key,'review_id':ref.review_id,
                'quote_start':quote.quote_start,'quote_end':quote.quote_end,
                'quote_sha256':sha256(quote.quote.encode('utf-8')).hexdigest(),'fact_ids':sorted(supported)})
            groups.setdefault(ref.review_id,set()).update(supported)
    valid_reviews=licensed_reviews(request,groups)
    if groups.keys()!=valid_reviews:return False
    sufficient=bool(keys)
    return (assessment['target']==(request['text'] if request else None)
        and assessment['required_fact_ids']==wanted
        and assessment['operator']==(request['operator'] if request else 'all_of')
        and assessment['status']==('sufficient' if sufficient else 'insufficient')
        and assessment['supported_target_fact_ids']==supported_targets(wanted,bindings)
        and assessment['missing_required_fact_ids']==([] if sufficient else wanted or ['unresolved_query'])
        and assessment['permitted_keys']==keys and assessment['fact_bindings']==bindings
        and assessment['relation']==({'type':request['relation'],'supported':bool(valid_reviews),
            'review_ids':sorted(valid_reviews)} if request and request['relation'] else None))
