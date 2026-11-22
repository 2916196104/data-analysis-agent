"""Agent adapter for P0; literal reported statements are not general entailment.

No R0 dependency, model-generated support decision, distance threshold or SQL fallback.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import math
import re
from time import perf_counter

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.agents.review_reported_facts import COMPARATIVE_ADVERB, POLICY, assess as assess_reported_facts
from data_analysis_agent.agents import review_reported_facts_v2, review_delivery_timing_evidence as timing
from data_analysis_agent.agents import review_cooling_speed_evidence as cooling
from data_analysis_agent.reviews.embedding import LocalBgeM3Embedder
from data_analysis_agent.reviews.embedding_store import SPACE_ID
from data_analysis_agent.reviews.rag_corpus import _document, RagOpinionDocument, RagReviewState
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticQuery, SemanticRetrievalService, PostgresSemanticRepository, SemanticRetrievalError, _scope, _in_scope,
    require_source_validated_result,
)

TOOL = 'search_semantic_evidence'
LEGACY_POLICY = 'literal_reported_statement_v1'
FACT_POLICIES = (POLICY, review_reported_facts_v2.POLICY, timing.POLICY, *cooling.POLICIES)
STATISTICS = r'占比|比例|百分比|多少|几条|数量|统计|趋势|排名|分布|覆盖率|数据质量|对比|比较|主要|最多'
V2_COMPLEX = r'同时|同一|既.+又|不仅|而且|但|却|又|而是|不是|并非|或者|还是|之前|之后|先.+再|导致|原因|为什么|长期|耐用|睡眠|所有|每个|一定|和|以及|与|[、；;，,]'
COMPLEX = V2_COMPLEX + r'|或|且'
LITERAL_STATEMENT = (r'(?:空调)?(?:制冷|制热)(?:不足|不行|效果差|效果好|很快|很慢|不快)|'
                     r'(?:物流|到货|配送)(?:很快|很慢)|(?:客服|安装师傅)(?:态度好|态度差)|'
                     r'包装(?:破了|完好)|价格(?:太高|便宜)|噪音(?:很大|很小)')


def wants_semantic_search(question):
    # “制冷比较慢”中的“比较”修饰程度，整个问题仍须通过证据意图检查。
    if question.startswith('执行本轮已确认的只读查询') or re.search(STATISTICS, COMPARATIVE_ADVERB.sub('', question)):
        return False
    from data_analysis_agent.agents.review_reported_facts import target
    from data_analysis_agent.agents.review_task_contract import evidence_question
    fact_question = evidence_question(question)
    if target(fact_question) or timing.target(fact_question):
        return True
    return bool(re.search(r'语义检索|类似.*(?:评论|反馈|抱怨|证据)|(?:有没有|是否有|有无|是否存在).*(?:评论|评价|反馈|抱怨|证据)|'
                         r'(?:有|有没有|是否有)(?:买家|用户)(?:反馈说|反映|提到|说).*(?:吗|么)[？?]*|'
                         r'(?:找|检索|搜索).*(?:评论|原文|反馈|证据)', question))


def retrieval_request(question, *, policy=POLICY):
    """Keep the original question byte-for-byte. Filters are only optional hints."""
    from data_analysis_agent.agents.review_prompts import detect_sentiment_directions
    topics = {word for word in ('制冷','制热','静音','噪音','客服','物流','安装','包装','价格','质量') if word in question}
    # The existing negation detector treats the existential 没 in 有没有 as a
    # negation of later sentiment words; strip only this question prefix.
    direction_text = re.sub(r'^(\s*(?:请问)?)(?:有没有|是否有)', r'\1', question)
    directions = detect_sentiment_directions(direction_text)
    current_query_policy = policy in (POLICY, timing.POLICY, *cooling.POLICIES)
    uncertain = bool(re.search(COMPLEX if current_query_policy else V2_COMPLEX, question)) or len(topics) > 1 or len(directions) > 1
    from data_analysis_agent.agents.review_reported_facts import target
    request = target(question) if current_query_policy else None
    uncertain = uncertain or bool(request and len(request['facts']) > 1)
    aspect = None
    # Only explicit category names; a subtopic alone is not an approved SQL label.
    categories = {value for word,value in {'质量':'quality','使用效果':'effectiveness','价格':'price',
                  '包装':'packaging','物流':'logistics','客服':'service','服务':'service'}.items() if word in question}
    if not uncertain and len(categories) == 1:
        aspect = next(iter(categories))
    return SemanticQuery(question=question, top_k=5, aspect=aspect,
        sentiment=next(iter(directions)) if not uncertain and len(directions)==1 else None)


def create_semantic_service(connection, scope, settings):
    """Lazy local-only construction, never a download or a change of model space."""
    cache = getattr(settings, 'rag_model_cache', None)
    if not cache:
        raise SemanticRetrievalError('local_encoder_not_configured')
    try:
        if getattr(settings,'rag_python',None):
            from data_analysis_agent.reviews.embedding_process import shared_embedding_process
            encoder = shared_embedding_process(settings.rag_python,cache,settings.rag_timeout_seconds)
        else:
            encoder = LocalBgeM3Embedder(cache, batch_size=1)
    except Exception:
        raise SemanticRetrievalError('local_encoder_unavailable') from None
    return SemanticRetrievalService(encoder=encoder, repository=PostgresSemanticRepository(connection), scope=scope)


def _literal_target(question):
    # Only a single explicit reported proposition. No synonym inference or lost clauses.
    match = re.fullmatch(r'(?:请问)?(?:(?:当前|这款|该)(?:商品|产品|空调))?(?:有没有|是否有)[“「"]?(.+?)[”」"]?的?(?:类似)?(?:反馈|抱怨|评论|证据)[？?。！!]*', question.strip())
    if not match:
        return None
    target = match[1].rstrip('的').strip('“”「」" ')
    if not 2 <= len(target) <= 120 or re.search(COMPLEX, target):
        return None
    # A subject/topic alone is not a proposition; accept a small explicit grammar.
    # Text must still equal the ENTIRE quote: these are not substring support rules.
    if not re.fullmatch(LITERAL_STATEMENT, target):
        return None
    return target


def assess_literal_retrieval(question, evidence):
    """Only license 'a buyer explicitly reported X', never objective/population X.

    Exact whole-quote assertions plus conservative context veto. Unknown paraphrases,
    complex relations and corrections remain unknown, not negative ground truth.
    """
    target = _literal_target(question)
    supported = []
    judgments = []
    for row in evidence:
        quote, text = row['quote'], row['review_text']
        assertion = quote.rstrip('。！! ')
        assertion_end = row['quote_start'] + len(assertion)
        sentence = next((m.group().strip() for m in re.finditer(r'[^。！？!?；;\n]+', text)
                         if m.start() <= row['quote_start'] and assertion_end <= m.end()), None)
        # Outside text can only veto. Never borrow an assertion from outside the quote.
        veto = bool(re.search(r'不是|并非|并不|没有|不对|假的|没发生|别人|他人|广告|宣传|如果|假如|听说|'
                              r'撤回|更正|误会|误解|实际上|玩笑|以为|号称|说法|客服说|但|不过|然而|后来|曾经|以前|不再|？|\?', text))
        # A deny-list cannot prove arbitrary context harmless. Only an understood
        # sequence of standalone reported clauses can pass this minimal local gate.
        clauses = [part.strip() for part in re.split(r'[。！!；;\n]+',text) if part.strip()]
        context_known = bool(clauses) and all(re.fullmatch(LITERAL_STATEMENT,part) for part in clauses)
        by_subject = {}
        if context_known:
            for clause in clauses:
                subject = re.match(r'(?:空调)?(制冷|制热|物流|到货|配送|客服|安装师傅|包装|价格|噪音)',clause)[1]
                subject = 'delivery' if subject in ('物流','到货','配送') else subject
                by_subject.setdefault(subject,set()).add(clause.removeprefix('空调'))
        # Differing assertions about the same subject may require time/contrast
        # reasoning, which this gate does not perform.
        veto = veto or not context_known or any(len(values)>1 for values in by_subject.values())
        direct = target is not None and assertion == target and sentence == assertion and not veto
        if direct:
            supported.append(row['key'])
        judgments.append({'key': row['key'], 'support': 'direct_report' if direct else 'unknown',
                          'reason': 'literal_assertion' if direct else 'context_unresolved' if veto else 'semantic_not_verified'})
    return {'policy': LEGACY_POLICY, 'status': 'sufficient' if supported else 'insufficient',
        'target': target, 'supported_target_fact_ids': ['reported_statement'] if supported else [],
        'missing_required_fact_ids': [] if supported else ['reported_statement'],
        'permitted_keys': supported, 'candidate_judgments': judgments,
        'reason': 'explicit_report_only' if supported else 'no_candidates' if not evidence else
                  'query_semantics_uncovered' if target is None else 'no_verified_direct_report',
        'scope_of_claim': 'retrieved_buyer_report_only', 'general_entailment': False}


def assess_retrieval(question, evidence, *, policy=POLICY):
    if policy == POLICY:
        return assess_reported_facts(question, evidence)
    if policy == timing.POLICY:
        return timing.assess(question, evidence)
    if policy in cooling.POLICIES:
        return cooling.assess(question, evidence, policy=policy)
    if policy == review_reported_facts_v2.POLICY:
        return review_reported_facts_v2.assess(question, evidence)
    if policy == LEGACY_POLICY:
        return assess_literal_retrieval(question, evidence)
    raise ValueError('unknown evidence permission policy')


def _cooling_keyword_fallback(fact_question, request, snapshot, baseline):
    rows = [row.to_dict() for row in baseline]
    if cooling.assess(fact_question, rows)['permitted_keys']:
        return ()
    excluded = {row.key for row in baseline}
    # Stable key breaks ties between equally recent observations. The quote parser
    # nominates candidates. Citation permission still needs the full review.
    documents = sorted((doc for doc in snapshot.corpus().documents
        if doc.stable_versioned_opinion_key not in excluded
        and (request.aspect is None or doc.aspect == request.aspect)
        and (request.sentiment is None or doc.sentiment == request.sentiment)
        and cooling.SLOW in cooling._quote_facts_v3(doc.quote)),
        key=lambda doc: (doc.published_at, doc.stable_versioned_opinion_key), reverse=True)
    return tuple(documents[:request.top_k - bool(baseline)])


def execute_retrieval(context, scope):
    if not context.question or not wants_semantic_search(context.question):
        raise SemanticRetrievalError('semantic_query_not_bound')
    expected = _scope(scope)
    from data_analysis_agent.agents.review_task_contract import evidence_policy, evidence_question, evidence_scope_matches
    if not evidence_scope_matches(context.question, scope):
        raise SemanticRetrievalError('question_scope_mismatch')
    service = (context.semantic_service_factory(context.connection, deepcopy(scope))
               if context.semantic_service_factory else create_semantic_service(context.connection, scope, context.settings))
    if not isinstance(service, SemanticRetrievalService) or service.scope != expected:
        raise SemanticRetrievalError('service_scope_mismatch')
    request = retrieval_request(context.question)
    policy = evidence_policy(context.question, scope)
    fact_question = evidence_question(context.question, scope, fact_policy=policy)
    if (getattr(context.settings, 'rag_cooling_keyword_fallback', False)
            and policy == cooling.POLICY and cooling.is_single_slow(fact_question)):
        result = service.search(request, keyword_fallback=lambda query, snapshot, baseline:
                                _cooling_keyword_fallback(fact_question, query, snapshot, baseline))
    else:
        result = service.search(request)
    require_source_validated_result(result)
    if result.scope != expected or result.query != request:
        raise SemanticRetrievalError('result_scope_or_question_mismatch')
    payload = result.to_dict()
    started=perf_counter()
    assessment=assess_retrieval(fact_question, payload['evidence'], policy=policy)
    data = {'scope': expected.model_dump(mode='json'), 'retrieval': payload,
            'assessment_question':fact_question,
            'assessment': assessment, 'timings':dict(service.timings,
                permission_ms=(perf_counter()-started)*1000)}
    if not validate_retrieval(data, context.question):
        raise SemanticRetrievalError('semantic_result_validation_failed')
    return data


def validate_retrieval(data, question):
    """Consumer check, including history replay; never trust a stored permission flag.

    P0 already checked the complete effective opinion set in one source transaction.
    This checks transported candidates/spans/keys, not historical-source certification.
    """
    try:
        scope = _scope(data['scope']); raw = data['retrieval']; query = retrieval_request(question,policy=data['assessment']['policy'])
        hybrid = raw['version'] == 'semantic-retrieval-v2'
        if raw['query'] != query.model_dump(mode='json') or raw['version'] not in (
                'semantic-retrieval-v1', 'semantic-retrieval-v2'): return False
        if hybrid != ('vector_baseline' in raw): return False
        if raw['scope'] != scope.model_dump(mode='json', exclude={'base_url'}): return False
        if (raw['embedding_space_id'], raw['text_builder_version'], raw['source_policy'], raw['context_policy'], raw['context_version']) != (
            SPACE_ID, 'quote-utf8-v1', 'current_effective_readonly_v1', 'quote_full_review_veto', '1'): return False
        if raw['snapshot']['read_only'] is not True or raw['snapshot']['isolation_level'] not in ('repeatable read','serializable'): return False
        identity = (scope.model,scope.base_url,scope.prompt_version); profile = content_sha256(list(identity))
        if raw['label_profile_key'] != profile: return False
        evidence = raw['evidence']
        baseline = raw.get('vector_baseline', [])
        for rows, channels in ((baseline, False), (evidence, hybrid)):
            if len(rows)>query.top_k or len({row['key'] for row in rows}) != len(rows): return False
            for rank,row in enumerate(rows,1):
                if type(row['rank']) is not int or row['rank'] != rank or row['source'] != scope.source or row['dataset_version'] != scope.dataset_version: return False
                if not _in_scope(RagReviewState(**row['review_state']),scope): return False
                distance = row['cosine_distance']
                if channels and row['channel'] == 'keyword':
                    if distance is not None: return False
                elif (channels and row['channel'] != 'vector') or type(distance) not in (int,float) or not math.isfinite(distance):
                    return False
                fields = dict(row, base_url=scope.base_url, embedding_input=row['quote'], stable_versioned_opinion_key=row['key'])
                doc = RagOpinionDocument(**{key:fields[key] for key in RagOpinionDocument.__dataclass_fields__})
                if _document(dict(**asdict(doc),review_text=row['review_text'],
                                  processing_status=row['review_state']['processing_status']),identity,profile) != doc: return False
                if sha256(row['review_text'].encode('utf-8')).hexdigest() != row['review_text_sha256']: return False
                if row['effective_snapshot_sha256'] != row['review_state']['effective_snapshot_sha256']: return False
                if any(row[key] != row['review_state'][key] for key in ('review_id','annotation_id','human_revision_id',
                        'source','dataset_version','product_id','review_content_sha256','published_at','opinion_source')): return False
                if query.aspect is not None and doc.aspect != query.aspect: return False
                if query.sentiment is not None and doc.sentiment != query.sentiment: return False
        fact_question=question
        if 'assessment_question' in data:
            from data_analysis_agent.agents.review_task_contract import evidence_question, evidence_scope_matches
            fact_question=evidence_question(question, data['scope'],fact_policy=data['assessment']['policy'])
            if data['assessment_question']!=fact_question or not evidence_scope_matches(question,data['scope']):return False
        if hybrid and not _validate_keyword_fallback(fact_question, data['assessment']['policy'], query, baseline, evidence):
            return False
        return data['assessment'] == assess_retrieval(fact_question,evidence,policy=data['assessment']['policy'])
    except (KeyError,ValueError,TypeError,AttributeError,UnicodeError,SemanticRetrievalError):
        return False


def _validate_keyword_fallback(question, policy, query, baseline, evidence):
    if (policy != cooling.POLICY or not cooling.is_single_slow(question)
            or cooling.assess(question, baseline)['permitted_keys']):
        return False
    keys = {row['key'] for row in baseline}
    keywords = [row for row in evidence if row['channel'] == 'keyword']
    if not 0 < len(keywords) <= query.top_k - bool(baseline):
        return False
    if any(row['key'] in keys or type(row['channel_rank']) is not int or row['channel_rank'] != rank
           or cooling.SLOW not in cooling._quote_facts_v3(row['quote'])
           for rank, row in enumerate(keywords, 1)):
        return False
    if keywords != sorted(keywords, key=lambda row: (row['published_at'], row['key']), reverse=True):
        return False
    vector = [dict(row, channel='vector', channel_rank=row['rank']) for row in baseline]
    expected = (vector[:1] + keywords + vector[1:])[:query.top_k]
    return evidence == [dict(row, rank=rank) for rank, row in enumerate(expected, 1)]


def permitted_references(data, result_id):
    from data_analysis_agent.agents.review_results import EvidenceReference, EvidenceQuote
    allowed = set(data['assessment']['permitted_keys'])
    return [EvidenceReference(source_result_id=result_id, review_id=row['review_id'], product_id=row['product_id'],
        published_at=row['published_at'], evidence_ref=f"sha256:{row['file_sha256']}#line={row['line_number']}",
        opinion_source=row['opinion_source'], opinions=[EvidenceQuote(**{key:row[key] for key in
            ('aspect','sentiment','quote','quote_start','quote_end')}, opinion_key=row['key'],
            annotation_id=row['annotation_id'], effective_snapshot_sha256=row['effective_snapshot_sha256'])])
        for row in data['retrieval']['evidence'] if row['key'] in allowed]
