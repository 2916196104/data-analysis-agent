"""固定合成回答案例：真实只读查询和校验、离线主题夹具，绝不读取生产库。"""
from contextlib import closing, ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_agent import SETTINGS
from test_review_answers import AnswerTests
from test_review_topics import ReviewTopicTests, semantic_response
from test_review_workbench import IDENTITY
from topic_fixture import topic_model

from data_analysis_agent.agents.review_compression import build_analysis_artifacts
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.core.serialization import content_sha256


def _seed(case):
    records = []
    def add(product, month, index, quotes):
        records.append({'parent_asin': product, 'user_id': f'{product}-{month}-{index}', 'rating': 3,
            'text': '。'.join(quotes), 'timestamp': int(datetime(2023, month, 10, tzinfo=timezone.utc).timestamp()*1000)})
    for product in ('S35-A', 'S35-B', 'S35-C', 'S35-N', 'S35-U'):
        for index in range(40):
            if product == 'S35-A':
                quotes = ['制冷很快' if index < 30 else '运行安静']
                if index < 8: quotes.append('包装破损')
                if index < 4: quotes.append('物流延迟')
                if index % 2 == 0: quotes.append('客服耐心')
            elif product == 'S35-B':
                quotes = ['制冷很快' if index < 20 else '运行安静']
                if index < 12: quotes.append('包装破损')
                if index < 16: quotes.append('物流延迟')
            elif product == 'S35-N': quotes = ['物流延迟']
            else: quotes = ['物流迅速']
            add(product, 4, index, quotes)
    for month in range(1, 4):
        for index in range(25):
            add('S35-FLAT', month, index, ['包装破损' if index < 5 else '包装完整', '物流延迟' if index < 5 else '物流迅速'])
    path = case.root / 'phase35-fixed.jsonl'
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in records), encoding='utf-8')
    import_amazon_jsonl(case.db, reviews_path=path, dataset_version='phase35-fixed')
    for review in pending_reviews(case.db, **IDENTITY, dataset_version='phase35-fixed',
                                  product_ids=['S35-A','S35-B','S35-C','S35-N','S35-FLAT'], limit=1000):
        opinions = []
        for quote in review['text'].split('。'):
            aspect = 'packaging' if '包装' in quote else 'logistics' if '物流' in quote else 'service' if '客服' in quote else 'effectiveness'
            opinions.append({'aspect': aspect, 'sentiment': 'negative' if quote in ('包装破损','物流延迟') else 'positive', 'quote': quote})
        save_annotation(case.db, review, **IDENTITY, status='success',
            opinions=validate_opinions(json.dumps({'opinions': opinions}), review['text']))


def _snapshot(case, question, scope, query=None, topic_limit=None):
    started = perf_counter()
    plan = build_query_plan(query or question, scope)
    if plan.unsupported_reason: raise AssertionError(plan.unsupported_reason)
    with ExitStack() as stack:
        if topic_limit:
            stack.enter_context(patch('data_analysis_agent.agents.review_topics.MAX_OPINIONS', topic_limit))
        stack.enter_context(topic_model(semantic_response))
        connection = stack.enter_context(closing(connect_review_database(case.db, readonly=True)))
        stack.enter_context(connection); connection.execute('BEGIN')
        context = ReviewToolContext(connection, scope, case.root, 30, settings=SETTINGS,
            query_plan=plan, sentiment_intent=plan.sentiment)
        registry = create_review_registry(context)
        registry.execute_tool('get_analysis_context','{}',automatic=True)
        for request in plan.requirements:
            result = registry.execute_tool(request['tool'],json.dumps(request['parameters'])).to_dict()
            if result['status'] != 'success': raise AssertionError(result)
        ids = [key for key in context.results if key != 'overview']
        validation = validate_report(context.results, ids, query_plan=plan)
        if validation['status'] != 'passed': raise AssertionError(validation)
        full = {'status':'ok','question':question,'analysis_question':question,'analysis_intent':plan.analysis_type,
            'sentiment_intent':plan.sentiment,'scope':scope,'result_ids':ids,'results':deepcopy(context.results),'validation':validation}
        artifacts = build_analysis_artifacts(full,plan)
    return full, artifacts['compact_context'], round(perf_counter()-started,6)


def fixed_cases():
    """43 个正常案例；按各版本实际策略报告适用模型分母，异常控制另列。"""
    regular, topics = AnswerTests(), ReviewTopicTests()
    regular.setUp(); topics.setUp()
    rows = []
    try:
        _seed(regular)
        regular.strong_trend_context()  # 复用既有六个月受控序列，全部在临时库。
        common = {'dataset_version':'phase35-fixed','date_from':'2023-04-01','date_to':'2023-04-30'}
        def add(key, group, question, *, fixture=regular, query=None, topic_limit=None, **scope):
            full, context, elapsed = _snapshot(fixture, question, fixture.scope(**scope), query, topic_limit)
            rows.append({'case':key,'group':group,'question':question,'context':context,'full':full,
                'query_seconds':elapsed,'population_mode':next((s.get('filters',{}).get('population_mode') for s in context['statistics'] if s.get('filters')),None)})
        for key, product, question in [
            ('P01','S35-A','好评主要集中在哪些方面？'), ('P02','S35-B','好评集中在哪里？请解释评价重点。'),
            ('P03','S35-C','好评主要是什么？'), ('P04','S35-A','客服好评反映了什么？请解释。'),
            ('P05','S35-B','使用效果的好评反映哪些评价重点？为什么？')]:
            add(key,'positive',question,product_ids=[product],**common)
        for key, product, question in [
            ('N01','S35-A','主要有哪些差评？'), ('N02','S35-B','主要问题是什么？请综合分析。'),
            ('N03','S35-N','物流差评集中在哪里？'),
            ('N04','S35-B','包装差评反映了什么？请解释。'),('N05','S35-B','物流问题反映了什么？请解释。')]:
            add(key,'negative',question,product_ids=[product],**common)
        for key, products in [('C01',['S35-A','S35-B']),('C02',['S35-B','S35-A']),
                              ('C03',['S35-A','S35-C']),('C04',['S35-C','S35-B']),
                              ('C05',['S35-N','S35-A']),('C06',['S35-B','S35-N']),
                              ('C07',['S35-C','S35-N']),('C08',['S35-N','S35-C'])]:
            add(key,'comparison','比较这些商品的主要负面问题，请解释评价差异。',
                query='执行本轮已确认的只读查询\n'+json.dumps({'tool':'compare_products','parameters':{}}),product_ids=products,**common)
        for key, products in [('B01',['S35-A']),('B02',['S35-B']),('B03',['S35-A','S35-C'])]:
            add(key,'pros_cons','这些商品的优缺点都有哪些？请综合分析。',product_ids=products,**common)
        strong = {'dataset_version':'phase4-controlled','product_ids':['TREND-CONTROL'],'date_from':'2023-01-01','date_to':'2023-06-30'}
        for key, question, changes in [
            ('T01','包装和物流问题最近有没有改善？请解释。',{}),
            ('T02','物流负面评价按月有哪些变化？请解释。',{}),
            ('T03','包装负面评价按月有哪些变化？请解释。',{}),
            ('T04','包装和物流问题按月怎样变化？请解释。',{'date_from':'2023-04-01'}),
            ('T08','包装和物流年初按月怎样变化？请解释。',{'date_to':'2023-03-31'}),
            ('T09','包装和物流在中间时段按月怎样变化？请解释。',{'date_from':'2023-02-01','date_to':'2023-04-30'})]:
            query='按月查看包装和物流问题变化' if key in ('T01','T04') else question
            add(key,'trend_sufficient',question,query=query,**{**strong,**changes})
        add('T05','trend_sparse','包装和物流问题最近有没有改善？',query='按月查看包装和物流问题变化',
            product_ids=['P1'],date_to='2023-03-31')
        add('T06','trend_sparse','物流问题是否在改善？请解释。',query='物流按月趋势',product_ids=['P1'])
        add('T07','trend_sufficient','包装和物流问题按月有没有变化？请解释。',query='按月查看包装和物流问题变化',
            dataset_version='phase35-fixed',product_ids=['S35-FLAT'],date_from='2023-01-01',date_to='2023-03-31')
        for key, aspect, sentiment, limit, changes, question in [
            ('D01','effectiveness','positive',None,{},'使用效果具体好在哪里？'),
            ('D02','effectiveness','positive',None,{'date_from':'2021-06-08'},'近期匹配评论的使用效果具体好在哪里？'),
            ('D03','effectiveness','positive',3,{},'从纳入样本看，使用效果具体好在哪里？'),
            ('D04','effectiveness','positive',4,{},'样本里主要肯定哪些使用体验？请解释。'),
            ('D05','logistics','negative',None,{},'物流方面主要有哪些问题？'),
            ('D06','effectiveness','positive',None,{'date_from':'2021-06-08','date_to':'2021-06-08'},'使用效果这项评价具体是什么意思？'),
            ('D07','effectiveness','positive',None,{'date_to':'2021-06-04'},'能从这些使用效果评价中判断优点吗？'),
            ('D08','effectiveness','positive',None,{},'这些候选子主题分别描述什么体验，主要区别是什么？'),
            ('D09','effectiveness','positive',None,{'date_from':'2021-06-09'},'最新匹配评论同时关注什么使用体验？')]:
            query='执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_aspect_drilldown','parameters':{'aspect':aspect,'sentiment':sentiment}})
            add(key,'topics',question,fixture=topics,query=query,topic_limit=limit,**changes)
        for key, product, question in [('E01','S35-U','好评主要集中在哪里？'),('E02','S35-N','好评主要有哪些？')]:
            add(key,'insufficient',question,product_ids=[product],**common)
        for key, question, context in [
            ('A01','那物流方面呢？',{'validated':{'scope':regular.scope(product_ids=['P1']),'sentiment':'positive','queries':[
                {'kind':'positive_aspects','parameters':{}},{'kind':'evidence','parameters':{'sentiment':'positive'}}]}}),
            ('A02','另一款商品有什么差别？',None)]:
            started=perf_counter()
            with patch('openai.OpenAI',side_effect=AssertionError('no external calls in fixtures')):
                result=run_review_agent(regular.db,question,scope=regular.scope(product_ids=['P1']),
                    settings=replace(SETTINGS,semantic_parser_enabled=False),conversation_context=context)
            if result['status']!='unsupported': raise AssertionError(result['status'])
            rows.append({'case':key,'group':'ambiguous','question':question,'context':None,'full':result,
                'query_seconds':round(perf_counter()-started,6),'clarification':result['answer']})
        for row in rows:
            row['source']='SYNTHETIC_SQLITE_ONLY'
            if row['context']:
                row['snapshot_hash']=content_sha256({key:value for key,value in row['context'].items() if key!='question'})
        assert len(rows)==43 and len({row['case'] for row in rows})==43
        return rows
    finally:
        regular.doCleanups(); topics.doCleanups()
