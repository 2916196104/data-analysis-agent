"""Author the independent business oracle; never import or execute the Agent."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'tests/fixtures/agent_stage1'
A, B = '100013588053', '100013588055'
ASPECTS = ('packaging', 'logistics', 'service', 'quality', 'effectiveness', 'price')
LABELS = {'service':'客服回复缓慢','effectiveness':'降温速度慢','logistics':'物流很慢',
          'quality':'外壳开裂','price':'价格太高','packaging':'包装破了'}
SCOPE = dict(source='reviews_csv', dataset_version='stage1-synthetic-v1', model='stage1-labels',
    base_url='https://labels.stage1.invalid/v1', prompt_version='stage1-labels-v1',
    product_ids=[A,B], date_from='2023-02-01', date_to='2023-02-28',
    baseline_from='2023-01-01', baseline_to='2023-01-31', as_of=None)


def turn(question, *, kind='themes', aspect=None, sentiment='negative', product=A,
         ranking=False, pending=False, next_pending=False, status='ok', complete=True,
         mode=None, dates=None, selection=None, session=None, parent=None, fault=None,
         human=False, p6_aspects=None):
    mapping = {'statistics':('aspect_summary','issues','get_top_issues'),
        'positive_statistics':('aspect_summary','positive_aspects','get_positive_aspects'),
        'themes':('aspect_drilldown','aspect_drilldown','get_aspect_drilldown'),
        'evidence':('evidence','evidence','get_evidence'),
        'rag':('evidence','semantic_evidence','search_semantic_evidence')}
    qt, ai, tool = mapping[kind]
    blocked = status == 'clarify'
    levels = ['statistics'] if 'statistics' in kind else ['themes'] if kind=='themes' else ['evidence']
    goal = None if kind == 'rag' else dict(levels=levels, aspect=aspect, sentiment=sentiment)
    intent = dict(query_type=qt, aspect=aspect, sentiment=sentiment)
    if kind == 'rag': intent.update(aspect=None,sentiment=None)
    if blocked: intent = {'needs_clarification':True}
    acceptance = dict(complete=complete, status='passed' if complete else
        'insufficient_evidence' if status == 'insufficient' else 'failed' if status in ('error','partial') else 'not_completed')
    if kind=='rag': acceptance={'contract':'frozen_RAG_separate','permitted_evidence':False if mode=='rag_gap' else True}
    if p6_aspects:
        goal.update(version='review-request-goal-p6-v1',required_aspects=p6_aspects)
    return dict(input=question, kind=kind, data_mode=mode, fault=fault, selection_changes=selection,
        conversation_override=session,parent_override=parent, adjudication='PENDING_HUMAN' if human else None,
        expected_scope=dict(product_ids=[product] if product else [A,B],
            date_from=(dates or ['2023-02-01','2023-02-28'])[0],date_to=(dates or ['2023-02-01','2023-02-28'])[1]),
        expected_analysis_state={'ranking_basis':'present' if ranking else 'absent','pending_goal':'present' if pending else 'absent'},
        expected_pending_goal='awaiting_aspect' if next_pending else 'absent',
        expected_request_goal=goal,expected_user_intent=intent,
        expected_analysis_intent='not_finalized' if blocked else ai,
        expected_query_plan={'blocked':blocked,'analysis_type':None if blocked else ai,
            'aspects':p6_aspects or ([aspect] if aspect else []),'sentiment':sentiment},
        expected_required_tools=[] if blocked else [tool],expected_task_acceptance=acceptance,
        expected_final_status={'agent':'unsupported' if status in ('clarify','insufficient','stale') else status,
            'worker':'failed' if status=='error' else 'partial' if status=='partial' else 'succeeded'},
        clarification_must_execute_zero=blocked,
        strict_business_completion=complete and mode!='rag_gap')


def rank(product=A, **kw):
    return turn(product+'的差评主要集中在哪些方面？',kind='statistics',product=product,**kw)


def author():
    groups=[]
    def add(title,turns,*,p6=True,notes=''):
        groups.append(dict(id=f'G{len(groups)+1:02d}',title=title,p6_enabled=p6,notes=notes,turns=turns))
    add('053 可信排名后多维具体表现',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'])])
    add('已完成负面统计切换总体好评',[rank(),turn('那好评呢？',kind='positive_statistics',sentiment='positive',ranking=True)])
    add('明确物流主题与同义表达',[turn(A+'物流差评主要体现在哪里？',aspect='logistics'),turn('物流差评主要在抱怨哪些事情？',aspect='logistics')])
    add('055 单维主题后客服短追问',[turn(B+'物流差评主要体现在哪里？',aspect='logistics',product=B),turn('客服呢？',aspect='service',product=B)])
    add('否定旧维度并显式改为质量',[turn(A+'物流差评主要体现在哪里？',aspect='logistics'),turn('不要分析物流了，改看商品质量。',aspect='quality')])
    add('显式修改商品 ID',[rank(),turn('换成'+B+'，差评主要集中在哪些方面？',kind='statistics',product=B)])
    add('本轮文字明确缩小日期',[rank(),turn('2023-02-15到2023-02-28，'+A+'的差评主要集中在哪些方面？',kind='statistics',dates=['2023-02-15','2023-02-28'])])
    add('页面商品变化不能继承旧目标',[rank(),turn('那好评呢？',kind='positive_statistics',sentiment='positive',product=B,selection={'product_ids':[B]})])
    add('页面日期变化后必须刷新可信排名',[rank(),turn('具体体现在哪里？',status='clarify',complete=False,next_pending=True,selection={'date_from':'2023-02-15'},dates=['2023-02-15','2023-02-28'])])
    add('标注身份变化导致状态失效',[rank(),turn('具体体现在哪里？',status='clarify',complete=False,sentiment=None,product=None,selection={'model':'changed-label-profile'})])
    add('跨会话不继承可信排名',[rank(),turn('具体体现在哪里？',status='clarify',complete=False,sentiment=None,product=None,session='new-session')])
    add('没有可信历史的具体追问',[turn(A+'具体体现在哪里？',status='clarify',complete=False,sentiment=None,next_pending=False),turn('具体体现在哪里？',status='clarify',complete=False,sentiment=None,product=None,next_pending=False)])
    add('有观点但没有被支持的主题',[turn(A+'客服差评主要体现在哪里？',aspect='service',mode='empty_catalog',status='insufficient',complete=False),turn('只统计客服差评有多少条。',kind='statistics',aspect='service')])
    add('目标维度观点缺失与零计数区分',[turn(A+'物流差评主要体现在哪里？',aspect='logistics',mode='missing_logistics_opinions',status='insufficient',complete=False),turn('物流差评有多少条？',kind='statistics',aspect='logistics',mode='missing_logistics_opinions')])
    add('分类失败后明确改为统计',[turn(A+'物流差评主要体现在哪里？',aspect='logistics',mode='fail_logistics',status='error',complete=False),turn('不用主题分析，只告诉我物流差评多少条。',kind='statistics',aspect='logistics')])
    add('多维全部证据不足',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'],mode='empty_catalog',status='insufficient',complete=False)])
    add('多维中间维度失败后继续单维',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'],mode='fail_effectiveness',status='partial',complete=False),turn('只看物流，具体有哪些问题？',aspect='logistics',ranking=True)])
    add('多维第一维失败保留全部目标',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'],mode='fail_service',status='partial',complete=False)])
    add('多维最后一维失败保留全部目标',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'],mode='fail_logistics',status='partial',complete=False)])
    add('当前计数与历史排名冲突必须阻断',[rank(),turn('具体体现在哪里？',ranking=True,p6_aspects=['service','effectiveness','logistics'],mode='stale_counts',status='stale',complete=False)])
    add('用户纠正情感方向',[turn(A+'物流差评主要体现在哪里？',aspect='logistics'),turn('我不是要看差评，要看物流好评具体表现。',aspect='logistics',sentiment='positive')])
    add('用户纠正统计层级为主题',[turn(A+'物流差评有多少条？',kind='statistics',aspect='logistics'),turn('不是问数量，我想知道物流差评具体在抱怨什么。',aspect='logistics')])
    add('明确取消主题仅保留数量',[turn(A+'客服差评具体体现在哪里？',aspect='service'),turn('不要归纳主题了，只统计客服差评数量。',kind='statistics',aspect='service')])
    add('053 RAG 覆盖缺口不伪造证据',[turn(A+'有没有物流很慢的反馈？',kind='rag',sentiment=None,mode='rag_gap'),turn('客服呢？',kind='rag',sentiment=None,status='clarify',complete=False,mode='rag_gap')],notes='053 空候选模拟用户已说明的覆盖缺口；外层检索成功不代表业务事实完成。')
    add('055 合成 RAG 后切换统计',[turn(B+'有没有物流很慢的反馈？',kind='rag',sentiment=None,product=B),turn('只统计物流差评数量。',kind='statistics',aspect='logistics',product=B)])
    add('按条件浏览原文不应误走 RAG',[turn('给我三条'+A+'物流差评原文。',kind='evidence',aspect='logistics'),turn('有多少条？',kind='statistics',aspect='logistics')])
    add('评论内指令不污染用户目标',[turn('给我三条'+A+'物流差评原文。',kind='evidence',aspect='logistics',mode='instruction_in_review'),turn('那好评呢？',kind='evidence',aspect='logistics',sentiment='positive',mode='instruction_in_review')])
    add('Planner 被故意降级的防护反例',[rank(),turn('物流差评主要体现在哪里？',aspect='logistics',ranking=True,fault='statistics_plan',status='error',complete=False)],notes='主动注入；不计为自然 Planner 失误，独立记录是否在工具前拦截。')
    add('工具来源被故意篡改的防护反例',[turn(A+'物流差评主要体现在哪里？',aspect='logistics',fault='wrong_result_product',status='error',complete=False),rank()],notes='主动注入；不得发布错误商品范围的成功主题。')
    add('默认开关关闭时的待完成主题与短情感歧义',[rank(),turn('具体体现在哪里？',ranking=True,status='clarify',complete=False,next_pending=True),turn('那好评呢？',kind='themes',sentiment='positive',ranking=True,pending=True,human=True,complete=False)],p6=False,notes='第三轮业务层次留待人工裁决；不强行计 PASS，也不以当前输出回填预期。')
    assert len(groups)==30 and all(2<=len(g['turns'])<=4 for g in groups)
    corpus=[]
    counts={A:{'service':5,'effectiveness':4,'logistics':3,'quality':2,'price':1,'packaging':0},B:{'service':2,'effectiveness':1,'logistics':4,'quality':3,'price':1,'packaging':1}}
    for product in (A,B):
        for i in range(8):
            opinions=[dict(aspect=a,sentiment='negative',quote=LABELS[a]) for a in ASPECTS if i<counts[product][a]] if i<6 else [dict(aspect=a,sentiment='positive',quote=q) for a,q in [('logistics','物流很快'),('service','客服态度好'),('quality','质量可靠')]]
            text='合成评论：'+'；'.join(o['quote'] for o in opinions) if opinions else '合成评论：暂无明确维度观点。'
            for o in opinions:o.update(quote_start=text.index(o['quote']),quote_end=text.index(o['quote'])+len(o['quote']))
            corpus.append(dict(review_id=(1 if product==A else 101)+i,product_id=product,text=text,
                published_at=f'2023-02-{(10+i*3) if i<6 else (26+i-6):02d}T00:00:00+00:00',opinions=opinions))
    return {'version':'agent-stage1-independent-oracle-v1','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
        'authorship':'Business expectations authored before any target execution; no Agent outputs used as gold.',
        'scope':SCOPE,'state_contract':'expected_analysis_state is the scope-filtered trusted input state; expected_pending_goal is the post-turn restorable pending goal.',
        'clarification_contract':'No final plan is required; no business tool/query execution; acceptance absent is allowed but complete=true is forbidden.',
        'rag_contract':'Frozen RAG contract separate from P0-B RequestGoal; empty candidates may have outer ok but cannot count as factual task completion.',
        'groups':groups}, {'version':'agent-stage1-synthetic-corpus-v1','provenance':'New fictional offline records; not production comments or existing human/RAG gold assets.','rows':corpus}


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    oracle,corpus=author()
    digests={}
    for name,value in [('oracle.v1.json',oracle),('corpus.v1.json',corpus)]:
        raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        with (DEST/name).open('xb') as file:file.write(raw)
        digests[name]=sha256(raw).hexdigest()
    source_files={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for folder in ('agents','jobs','reviews') for p in (ROOT/'src/data_analysis_agent'/folder).glob('*.py')}
    manifest={'oracle_files':digests,'production_source_sha256':source_files,'frozen_before_execution':True,
              'groups':30,'turns':sum(len(g['turns']) for g in oracle['groups'])}
    with (DEST/'freeze-manifest.json').open('x',encoding='utf-8') as file:json.dump(manifest,file,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in manifest.items() if k!='production_source_sha256'},ensure_ascii=False))


if __name__=='__main__':main()
