"""深度洞察合成判据；从临时导入/标注、只读工具和统一校验生成事实。"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
from test_review_agent import ReviewAgentTests
from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch
from data_analysis_agent.agents.review_plan import ReviewQueryPlan, build_query_plan, TOOL_KINDS
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_compression import build_analysis_artifacts
from data_analysis_agent.reviews.store import import_amazon_jsonl,connect_review_database
from data_analysis_agent.reviews.label_store import pending_reviews,save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.core.serialization import content_sha256

QUOTES = {
    '开启后很快感到凉意':('effectiveness','positive','cold'),
    '睡觉时运转声音很轻':('effectiveness','positive','quiet'),
    '大房间角落仍然不够凉':('effectiveness','negative','uneven'),
    '风直吹会觉得冷':('effectiveness','negative','draft'),
    '外箱压瘪但商品完好':('packaging','negative','box'),
    '内衬移位导致配件在盒内晃动':('packaging','negative','padding'),
    '封口开裂':('packaging','negative','seal'),
    '发货前等待时间长':('logistics','negative','dispatch'),
    '配送中物流更新停滞':('logistics','negative','tracking'),
    '预约时间与送达不一致':('logistics','negative','appointment'),
    '送达及时':('logistics','positive',None),
    '使用满意':('effectiveness','positive',None),
    '一会儿凉一会儿不凉':('effectiveness','negative',None),
}
THEMES = {
    'cold':('降温速度','买家报告开启后较快感到凉意，描述启动降温体验，不证明实验室制冷性能'),
    'quiet':('睡眠安静体验','买家报告睡觉时运行声音轻，描述夜间声音感受，不包含客观分贝测量'),
    'uneven':('角落降温不足','买家报告房间角落不够凉，涉及空间内的降温分布，不能据此判断压缩机故障'),
    'draft':('直吹冷感','买家报告直吹时觉得冷，涉及送风接触感受，与角落降温不足不是同一个体验'),
    'box':('外箱受压','买家同时报告外箱压瘪与商品完好，只能确认外包装现象，不能推断内件损坏'),
    'padding':('盒内固定体验','买家自述内衬移位、配件晃动，涉及内部固定体验，不能据此确认运输责任'),
    'seal':('封口开裂','买家报告封口开裂，材料中没有造成开裂的责任方信息'),
    'dispatch':('发货前等待','自述等待发生在发货之前，与发货后的运输追踪属于不同履约环节'),
    'tracking':('运输追踪停滞','自述停滞发生在配送过程中，不能等同于商家发货前等待'),
    'appointment':('预约履约偏差','自述约定送达时间与实际送达不一致，不包含责任主体核验'),
}


def semantic_fixture(settings,prompt,payload,schema,timeout):
    """AI 预设语义夹具，不是人工金标，也不计为真实主题模型评测。"""
    if schema is TopicCatalog:
        rows=[]
        for key,(name,definition) in THEMES.items():
            ids=[row['opinion_id'] for row in payload['opinions'] if QUOTES[row['quote']][2]==key]
            if ids:rows.append(dict(key='t'+str(len(rows)+1),name=name,definition=definition,support_ids=ids[:6]))
        return TopicCatalog(themes=rows)
    allowed={key:theme['key'] for key,(name,_) in THEMES.items() for theme in payload['themes'] if theme['name']==name}
    return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'],
        state='assigned' if QUOTES[row['quote']][2] in allowed else 'conflicting' if row['quote']=='一会儿凉一会儿不凉' else 'unclassified',
        matches=[dict(key=allowed[QUOTES[row['quote']][2]],start=0,end=len(row['quote']))] if QUOTES[row['quote']][2] in allowed else [])
        for row in payload['opinions']])


class EvidenceFixture(WorkbenchFixture,unittest.TestCase):
    scope = ReviewAgentTests.scope
    def seed(self):
        records=[]
        def add(product,index,quotes,month=2):
            records.append(dict(parent_asin=product,user_id=f'{product}-{month}-{index}',text='。'.join(quotes),rating=3,
                timestamp=int(datetime(2023,month,10,tzinfo=timezone.utc).timestamp()*1000)))
        for i in range(20):
            quotes=[]
            if i<8:quotes.append('开启后很快感到凉意')
            if i<5:quotes.append('睡觉时运转声音很轻')
            if 8<=i<14:quotes.append('大房间角落仍然不够凉')
            if 14<=i<17:quotes.append('风直吹会觉得冷')
            add('IE-EFFECT',i,quotes or ['使用满意'])
        for i in range(24):
            quote='外箱压瘪但商品完好' if i<6 else '内衬移位导致配件在盒内晃动' if i<10 else '封口开裂' if i<13 else '使用满意'
            add('IE-PACK',i,[quote])
        for product in ('IE-LOG-A','IE-LOG-B','IE-LOG-Z'):
            for i in range(30):
                quote='发货前等待时间长' if product=='IE-LOG-A' and i<8 else '配送中物流更新停滞' if product=='IE-LOG-B' and i<10 else '预约时间与送达不一致' if product=='IE-LOG-B' and i<15 else '送达及时'
                add(product,i,[quote])
        for product in ('IE-COMP-A','IE-COMP-B'):
            for i in range(24):
                quote=('开启后很快感到凉意' if i<(12 if product.endswith('A') else 6) else
                    '睡觉时运转声音很轻' if i<18 else '大房间角落仍然不够凉')
                add(product,i,[quote])
        for month,count in ((1,12),(2,6),(3,3)):
            for i in range(30):add('IE-TREND',i,[['发货前等待时间长','配送中物流更新停滞','预约时间与送达不一致'][month-1] if i<count else '送达及时'],month)
        for i,month in enumerate((1,3)):add('IE-SPARSE',i,['发货前等待时间长'],month)
        for i in range(5):add('IE-UNCERTAIN',i,['一会儿凉一会儿不凉'])
        for i in range(6):add('IE-UNLABELED',i,['外箱压瘪但商品完好'])
        path=self.root/'insight-evidence.jsonl'
        path.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in records),encoding='utf-8')
        import_amazon_jsonl(self.db,reviews_path=path,dataset_version='insight-evidence-v1')
        ids=sorted({row['parent_asin'] for row in records}-{'IE-UNLABELED'})
        for review in pending_reviews(self.db,**IDENTITY,dataset_version='insight-evidence-v1',product_ids=ids,limit=1000):
            opinions=[{'quote':quote,'aspect':QUOTES[quote][0],'sentiment':QUOTES[quote][1]} for quote in review['text'].split('。')]
            save_annotation(self.db,review,**IDENTITY,status='success',opinions=validate_opinions(json.dumps({'opinions':opinions}),review['text']))

    def snapshot(self,question,products,steps,*,sentiment='negative',sample_limit=None,partial=False):
        scope=self.scope(dataset_version='insight-evidence-v1',product_ids=products,date_from='2023-01-01',date_to='2023-03-31')
        results={};requests=[];expected={}
        for index,step in enumerate(steps):
            tool,params=step['tool'],step.get('parameters',{})
            local={**scope,**step.get('scope',{})}
            if not set(local['product_ids'])<=set(products) or local['date_from']<scope['date_from'] or local['date_to']>scope['date_to']:
                raise AssertionError('fixture expanded scope')
            plan=ReviewQueryPlan(TOOL_KINDS[tool],sentiment,local,requirements=[dict(tool=tool,parameters=params)],requires_evidence=True,max_evidence_items=10)
            def response(*args):
                if partial and args[3] is TopicBatch and args[2]['opinions'][0]['quote']=='内衬移位导致配件在盒内晃动':raise TimeoutError('synthetic partial batch')
                return semantic_fixture(*args)
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch('data_analysis_agent.agents.review_topics._call',side_effect=response))
                if sample_limit:stack.enter_context(patch('data_analysis_agent.agents.review_topics.MAX_OPINIONS',sample_limit))
                if partial:stack.enter_context(patch('data_analysis_agent.agents.review_topics.BATCH_SIZE',1))
                con=stack.enter_context(closing(connect_review_database(self.db,readonly=True)));stack.enter_context(con);con.execute('BEGIN')
                context=ReviewToolContext(con,local,self.root,30,query_plan=plan,sentiment_intent=sentiment,settings=SETTINGS)
                registry=create_review_registry(context);registry.execute_tool('get_analysis_context','{}',automatic=True)
                result=registry.execute_tool(tool,json.dumps(params)).to_dict()
                if result['status']!='success':raise AssertionError(result)
                item=deepcopy(context.results[result['data']['result_id']])
            key='r'+str(index+1);item['result_id']=key;results[key]=item;requests.append(dict(tool=tool,parameters=params));expected[key]=item['data']['scope']
        kinds={TOOL_KINDS[step['tool']] for step in steps}
        kind=next(iter(kinds)) if len(kinds)==1 else 'composite'
        plan=ReviewQueryPlan(kind,sentiment,scope,requirements=requests,requires_evidence=True,max_evidence_items=10)
        validation=validate_report(results,list(results),query_plan=plan,expected_scopes=expected)
        if validation['status']!='passed':raise AssertionError(validation)
        full=dict(status='ok',question=question,analysis_question=question,analysis_intent=kind,sentiment_intent=sentiment,
            scope=scope,result_ids=list(results),results=results,validation=validation)
        full.update(build_analysis_artifacts(full,plan))
        if partial:full['status']='partial'
        return full


def step(tool,aspect=None,sentiment=None,product=None,**extra):
    params={}
    if aspect:params['aspect']=aspect
    if sentiment:params['sentiment']=sentiment
    if product:params['product_ids']=[product]
    return {'tool':tool,'parameters':{**params,**extra}}


def cases():
    fixture=EvidenceFixture();fixture.setUp();fixture.seed();rows=[]
    try:
        def add(key,group,question,products,steps,wanted,sufficiency,allowed,unsupported,tools=(),candidate=None,**kwargs):
            full=fixture.snapshot(question,products,steps,**kwargs)
            plan=build_query_plan(question,full['scope'])
            rows.append({'case':key,'group':group,'question':question,'wanted':wanted,'expected_sufficiency':sufficiency,
                'allowed_interpretation':allowed,'unsupported_inferences':unsupported,'expected_additional_tools':list(tools),
                'reference_candidate':candidate,'reference_status':'AI_AUTHORED_RUBRIC_NOT_HUMAN_GOLD',
                'entry':'verified_existing_tool_snapshot','natural_plan_tools':plan.required_tools,'natural_plan_unsupported':plan.unsupported_reason,
                'seed_queries':steps,'full':full,'context':full['compact_context'],'source':'SYNTHETIC_SQLITE_ONLY',
                'snapshot_hash':content_sha256(full['compact_context'])})
        positive=step('get_aspect_drilldown','effectiveness','positive')
        negative=step('get_aspect_drilldown','effectiveness','negative')
        pack=step('get_aspect_drilldown','packaging','negative')
        add('E01','effectiveness','使用效果好评具体好在哪里？',['IE-EFFECT'],[step('get_positive_aspects','effectiveness')],
            '了解评价的实际体验内容而非一级计数','additional_query_available','补查后区分启动降温与睡眠声音体验','不能从总好评比例推断均匀降温或客观性能',['get_aspect_drilldown'],sentiment='positive')
        add('E02','effectiveness','这两个使用效果主题反映了哪些不同体验？',['IE-EFFECT'],[positive],
            '区分达到凉意的速度与睡眠环境声音','descriptive_evidence_available','启动效率和睡眠安静是不同体验侧面','不能推断实验室降温能力或分贝值',candidate='前者描述开始运行后的凉意出现速度，后者描述睡眠时的声音感受，属于不同的使用场景。',sentiment='positive')
        add('E03','effectiveness','使用效果负面主题具体反映什么问题？',['IE-EFFECT'],[negative],
            '区分房间内空间分布与人体直吹感受','descriptive_evidence_available','角落不凉与直吹觉得冷可以共存，并不互相否定','不能断言压缩机故障',candidate='角落降温不足与直吹冷感分别涉及空间分布和送风接触感受，不能简单合并成同一种制冷故障。')
        add('E04','effectiveness','这些评论能证实压缩机是制冷问题的根因吗？',['IE-EFFECT'],[negative],
            '确认硬件实际故障机制','external_evidence_required','只能说明买家报告的体验，不能确认硬件根因','压缩机故障或功率不足')
        add('E05','effectiveness','使用效果的这些样本能代表所有消费者偏好吗？',['IE-EFFECT'],[positive],
            '判断总体代表性','sample_descriptive_only','非随机纳入样本仅支持范围内描述','总体偏好排名',sample_limit=4,sentiment='positive')
        add('P01','packaging','包装差评具体反映了什么？',['IE-PACK'],[step('get_top_issues','packaging')],
            '确定实际包装问题类型','additional_query_available','补查外箱、固定和封口内容','不得从包装维度猜外观或内件损坏',['get_aspect_drilldown'])
        add('P02','packaging','这些包装主题分别反映哪个保护层次？',['IE-PACK'],[pack],
            '区分外箱现象、内部固定与封口','descriptive_evidence_available','外箱受压不等同内件损坏，内衬晃动是另一层固定体验','材料质量差、运输责任已确定',candidate='外箱受压描述外部状态，盒内固定体验描述内部约束；外箱受压的买家还明确报告商品完好，因此不能把两种现象都理解为内件损坏。')
        add('P03','packaging','包装差评的原文主要说的是外箱还是内件问题？请解释。',['IE-PACK'],[step('get_opinion_breakdown','packaging','negative')],
            '利用已经检索的真实原文区分现象','descriptive_evidence_available','原文同时提供外箱受压商品完好与内衬移位，不能混为内件破损','不能把外箱受压解释成商品已损坏',candidate='这些原文区分了外箱外观与盒内固定问题，其中外箱受压的描述并没有报告商品损坏。')
        add('P04','packaging','包装破损是否因为快递暴力，能确认责任吗？',['IE-PACK'],[pack],
            '确认外部责任原因','external_evidence_required','评论自述不能独立定责','物流商责任已证实')
        add('P05','packaging','包装负面主题具体反映哪些问题？',['IE-PACK'],[pack],
            '查看完整细分结论','analysis_incomplete','明确部分批次未完成，不发布完整结论','把部分主题当完整排名',partial=True)
        compare=[step('compare_products')]
        loggroups=[step('get_opinion_breakdown','logistics','negative',p) for p in ('IE-LOG-A','IE-LOG-B')]
        add('L01','logistics','两款商品物流评价差异主要是什么环节？',['IE-LOG-A','IE-LOG-B'],compare,
            '找履约环节内容差异','additional_query_available','需要双方物流观点，不把整体负面率当物流原因','不能按总负面率猜发货或运输',['get_opinion_breakdown'])
        add('L02','logistics','两款商品物流原文反映哪些不同环节？请解释。',['IE-LOG-A','IE-LOG-B'],loggroups,
            '区分发货前等待与配送/预约','descriptive_evidence_available','双方负面自述位于不同履约环节','不能推出谁真实运输天数更长',candidate='一方报告的是发货前等待，另一方报告的是配送追踪和预约偏差；两者需要核查的履约环节不同，不能仅用总负面占比代替。')
        mismatched=deepcopy(loggroups);mismatched[0]['scope']={'date_to':'2023-02-28'}
        add('L03','logistics','比较这两款商品的物流评价差异能直接得结论吗？',['IE-LOG-A','IE-LOG-B'],mismatched,
            '检验双方证据可比性','comparison_scope_mismatch','日期不同时先建立统一口径','不同窗口差异当商品固有差异',['compare_products'])
        add('L04','logistics','两款商品物流负面评价差异能说明没有问题吗？',['IE-LOG-A','IE-LOG-Z'],compare,
            '区分零匹配负面与数据缺失','external_evidence_required','零匹配不是未标注，也不能证明现实中没有物流问题','绝对无物流问题')
        add('L05','logistics','物流负面样本能证实配送责任吗？',['IE-LOG-B'],[step('get_aspect_drilldown','logistics','negative')],
            '从有限观点判定责任','external_evidence_required','样本内自述不等于责任核验','责任方已经确定',sample_limit=3)
        compgroups=[step('get_aspect_drilldown','effectiveness','positive',p) for p in ('IE-COMP-A','IE-COMP-B')]
        add('C01','comparison','两款商品使用效果正面主题有什么区别？',['IE-COMP-A','IE-COMP-B'],compgroups,
            '比较评价内容侧重','descriptive_evidence_available','在相同口径下说明启动凉意与睡眠声音的体验侧重','不等同真实性能或质量差距',candidate='双方都出现启动凉意和睡眠声音评价，主题反映的是不同体验侧面；评价数量的侧重不能直接当作客观性能测试。',sentiment='positive')
        add('C02','comparison','能证实哪款商品真实制冷性能更好吗？',['IE-COMP-A','IE-COMP-B'],compgroups,
            '判断真实产品性能','external_evidence_required','需要客观测试，不用正面评论比例替代','客观性能优劣已确认',sentiment='positive')
        add('C03','comparison','使用效果两个主题的评论数量能相加当总人数吗？',['IE-EFFECT'],[positive],
            '确认多主题统计的去重含义','descriptive_evidence_available','同一评论可同时涉及启动凉意和睡眠声音','主题评论数之和等于独立用户总数',sentiment='positive')
        add('C04','comparison','仅凭这些使用效果评价能确认哪款更值得买吗？',['IE-COMP-A','IE-COMP-B'],compgroups,
            '判断性价比和购买选择','external_evidence_required','需要价格、客观需求等额外信息','把评论数量当商业性价比',sentiment='positive')
        trend=[step('get_aspect_trend','logistics',period='month')]
        add('T01','trend','物流负面占比下降，具体内容发生了什么变化？',['IE-TREND'],trend,
            '解释问题类型随时间变化','additional_query_available','仅时间序列不能说明内容变化，应分窗补查','归因于仓储或运营优化',['get_opinion_breakdown'])
        windows=[step('get_opinion_breakdown','logistics','negative') for _ in range(2)]
        windows[0]['scope']={'date_from':'2023-01-01','date_to':'2023-01-31'}
        windows[1]['scope']={'date_from':'2023-03-01','date_to':'2023-03-31'}
        add('T02','trend','明确两个窗口的物流原文反映了什么内容变化？',['IE-TREND'],trend+windows,
            '将时序与具体报告内容联系','descriptive_evidence_available','早期发货前等待与后期预约偏差属于不同环节，下降不等于问题消失','运营优化造成下降',candidate='两个窗口的自述内容分别落在发货等待与预约履约环节，因此负面提及减少并不意味着剩余问题类型也保持不变。')
        add('T03','trend','物流样本这么少能解释稳定改善吗？',['IE-SPARSE'],trend,
            '确认稀疏时序是否充分','time_evidence_limited','缺少可比样本，质量查询只能诊断缺口','少量评论足以证明稳定趋势',['get_data_quality'])
        add('T04','trend','这些物流趋势能预测下个月一定继续改善吗？',['IE-TREND'],trend,
            '预测未来走势','external_evidence_required','历史描述不能保证未来','下月必然继续改善')
        add('T05','trend','包装差评没有标注，能解释为何没有问题吗？',['IE-UNLABELED'],[step('get_top_issues','packaging')],
            '区分缺少标注与真实零问题','annotations_missing','需要先核对数据质量','零标注等于零问题',['get_data_quality'])
        from data_analysis_agent.agents.review_evidence import assess_evidence
        from data_analysis_agent.agents.review_compression import CompactAnalysisContext
        digest=hashlib.sha256(fixture.db.read_bytes()).hexdigest()
        for row in rows:
            audit=assess_evidence(CompactAnalysisContext.model_validate(row['context']));checked=[]
            for query in audit['followup_queries']:
                if query['requires_scope_confirmation'] and not query.get('requested_subwindow'):
                    checked.append({'tool':query['tool'],'status':'scope_confirmation_required_not_executed'});continue
                request={'tool':query['tool'],'parameters':query['parameters'],'scope':query.get('requested_subwindow',{})}
                probe=fixture.snapshot(row['question'],row['full']['scope']['product_ids'],[request],
                    sentiment=row['context']['sentiment'])
                checked.append({'tool':query['tool'],'parameters':query['parameters'],'scope':query.get('requested_subwindow'),
                    'status':'verified_synthetic_only','validation':probe['validation']['status'],
                    'result_kinds':[item['kind'] for item in probe['results'].values()]})
            row['followup_validation']=checked
        assert hashlib.sha256(fixture.db.read_bytes()).hexdigest()==digest,'read-only proposals changed business database'
        assert len(rows)==24
        return rows
    finally:fixture.doCleanups()
