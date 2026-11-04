"""Local human decisions and reference preparation; no inference or formal freeze."""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.e1_preparation import require
from data_analysis_agent.reviews.e1_review_protocol import validate_human_reference, review_context_view
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
from data_analysis_agent.reviews.query_contract import ScopeRef, validate_query_contract

SESSION_VERSION = 'e1-human-adjudication-session-v1'
BUNDLE_VERSION = 'e1-human-adjudication-bundle-v1'
REVIEW_FIELDS = ('reference_state','supported_target_fact_ids','missing_required_fact_ids',
                 'fact_findings','relation_findings','review_notes')


def new_session(bundle):
    return dict(version=SESSION_VERSION,bundle_sha256=content_sha256(bundle),
        reviewer_id=bundle['reviewer_id'],prior_relevance_exposure=None,context_ack=False,
        queries={q['query_id']:dict(payload=deepcopy(q['before']),revision=0,event_id=None,choice=None) for q in bundle['queries']},
        cases={c['case_id']:dict(review={**deepcopy(c['original']),'resolved_issue_ids':[],'decision_rationale':'',
            'context_observations':[],'contract_review_note':c.get('original_contract_notes','')},contract_revision=0,event_id=None) for c in bundle['cases']},
        pairs={p['query_id']:dict(rationale='',event_id=None) for p in bundle['pairs']},events=[],completion_event_id=None)


def blank_review(contract):
    relations=[dict(relation_id=f'relation_{i}',support='unknown',evidence_keys=[],rationale='')
               for i,_ in enumerate(contract['required_facts']['relations'])]
    if contract['polarity_structure']['contrast_fact_ids']:
        relations.append(dict(relation_id='contrast',support='unknown',evidence_keys=[],rationale=''))
    return dict(reference_state=None,supported_target_fact_ids=[],missing_required_fact_ids=[],
        fact_findings=[dict(fact_id=f['id'],support='unknown',evidence_refs=[],rationale='') for f in contract['required_facts']['items']],
        relation_findings=relations,review_notes='',context_observations=[],resolved_issue_ids=[],decision_rationale='',contract_review_note='')


def metadata(session):
    return {k:session[k] for k in ('reviewer_id','prior_relevance_exposure','context_ack')}


def case_payload(session,case_id):
    row=session['cases'][case_id]
    return dict(contract_revision=row['contract_revision'],review=row['review'])


def pair_payload(bundle,session,query_id):
    pair=next(p for p in bundle['pairs'] if p['query_id']==query_id)
    return dict(query_id=query_id,case_ids=pair['case_ids'],
        review_sha256=[content_sha256(case_payload(session,cid)) for cid in pair['case_ids']],
        rationale=session['pairs'][query_id]['rationale'])


def completion_payload(session):
    return dict(query_events={k:v['event_id'] for k,v in session['queries'].items()},
        case_events={k:v['event_id'] for k,v in session['cases'].items()},
        pair_events={k:v['event_id'] for k,v in session['pairs'].items()})


def review_errors(review,contract,independent,case):
    """Human-label consistency only; this function cannot prove semantic entailment."""
    errors=[];required={f['id'] for f in contract['required_facts']['items']}
    facts={f['fact_id']:f for f in review['fact_findings']};relations={r['relation_id']:r for r in review['relation_findings']}
    hits={h['key']:h for h in case['candidates']}
    if set(facts)!=required or len(facts)!=len(review['fact_findings']):return ['事实ID不完整或重复']
    expected={f'relation_{i}':r['fact_ids'] for i,r in enumerate(contract['required_facts']['relations'])}
    if contract['polarity_structure']['contrast_fact_ids']:expected['contrast']=contract['polarity_structure']['contrast_fact_ids']
    if set(relations)!=set(expected) or len(relations)!=len(review['relation_findings']):errors.append('必要关系ID不完整或重复')
    for f in facts.values():
        if f['support'] not in ('direct','partial','not_supported'):errors.append(f"{f['fact_id']} 尚未完成判断")
        if not str(f['rationale']).strip():errors.append(f"{f['fact_id']} 缺少理由")
        if f['support']=='direct' and not f['evidence_refs']:errors.append(f"{f['fact_id']} 直接支持缺少引用")
        seen=set()
        for w in f['evidence_refs']:
            h=hits.get(w.get('key'));span=(w.get('key'),w.get('quote_start'),w.get('quote_end'))
            if span in seen:errors.append('事实引用重复')
            seen.add(span)
            if h is None or type(w.get('quote_start')) is not int or type(w.get('quote_end')) is not int or not h['quote_start']<=w['quote_start']<w['quote_end']<=h['quote_end']:
                errors.append('引用不属于当前候选quote区间')
    direct={fid for fid,f in facts.items() if f['support']=='direct'}
    for rid,r in relations.items():
        if r['support'] not in ('supported','not_supported'):errors.append(rid+' 尚未判断')
        if not str(r['rationale']).strip():errors.append(rid+' 缺少理由')
        keys=r['evidence_keys']
        if len(set(keys))!=len(keys) or not set(keys)<=set(hits):errors.append(rid+' 来源键非法')
        if r['support']=='supported' and rid in expected:
            if not keys or not all(fid in direct and any(w['key'] in keys for w in facts[fid]['evidence_refs']) for fid in expected[rid]):errors.append(rid+' 缺少各事实的直接来源')
            if rid!='contrast' and contract['required_facts']['relations'][int(rid.split('_')[1])]['kind']=='same_review':
                if len({hits[k]['review_id'] for k in keys if k in hits})!=1:errors.append(rid+' 不能跨评论拼接')
    for name in ('supported_target_fact_ids','missing_required_fact_ids'):
        values=review[name]
        if len(values)!=len(set(values)) or not set(values)<=required:errors.append(name+' 非法事实ID')
    complete=bool(required) and (required<=direct if contract['required_facts']['operator']=='all_of' else bool(direct))
    complete=complete and all(rid in relations and relations[rid]['support']=='supported' for rid in expected)
    missing=set() if contract['required_facts']['operator']=='any_of' and complete else required-direct
    if set(review['missing_required_fact_ids'])!=missing:errors.append('缺失事实集合与逐事实判断不一致')
    blocked={fid for rid,ids in expected.items() if rid not in relations or relations[rid]['support']!='supported' for fid in ids}
    independent_direct=(set(independent)&direct)-blocked;licensed=set(review['supported_target_fact_ids'])
    state=review['reference_state']
    if not licensed<=direct:errors.append('许可事实必须为直接支持')
    if state=='sufficient':
        if not complete or not licensed or licensed!=direct:errors.append('sufficient 需要全部核心条件及正确许可集合')
    elif state=='limited':
        if complete or not licensed or licensed!=independent_direct:errors.append('limited 仅可许可未被必要关系阻断的独立目标')
    elif state=='insufficient':
        if complete or independent_direct or licensed:errors.append('insufficient 不可许可目标或拒绝已完整支持的核心')
    else:errors.append('尚未选择参考状态')
    if set(review['resolved_issue_ids'])!={i['issue_id'] for i in case['issues']}:errors.append('请逐项处理审计问题')
    if not str(review['decision_rationale']).strip():errors.append('缺少本次决定理由')
    for a in facts.values():
        for b in facts.values():
            if a['fact_id']<b['fact_id'] and a['support']!=b['support'] and a['rationale'].strip()==b['rationale'].strip():errors.append('不同判断的事实理由完全重复，请核对')
    return sorted(set(errors))


def semantic_labels(review):
    return dict(state=review['reference_state'],licensed=sorted(review['supported_target_fact_ids']),missing=sorted(review['missing_required_fact_ids']),
        facts=sorted((f['fact_id'],f['support']) for f in review['fact_findings']),relations=sorted((r['relation_id'],r['support']) for r in review['relation_findings']))


def prepare_reference(case,query,review):
    fields=deepcopy(case['base_reference']);contract=query['payload']['contract']
    fields.update(contract_sha256=content_sha256(contract),independent_target_fact_ids=query['payload']['independent_target_fact_ids'],
        required_fact_ids=[f['id'] for f in contract['required_facts']['items']],reference_state=review['reference_state'],
        supported_target_fact_ids=review['supported_target_fact_ids'],missing_required_fact_ids=review['missing_required_fact_ids'],
        fact_findings=deepcopy(review['fact_findings']),relation_findings=deepcopy(review['relation_findings']),
        context_observations=review['context_observations'],human_confirmed=False,contract_confirmed=False,review_status='draft',
        reviewer_decisions=[],disagreements=[],adjudication=None)
    hits={h['key']:h for h in case['candidates']}
    for finding in fields['fact_findings']:
        finding['evidence_refs']=[dict(fact_id=finding['fact_id'],**{k:hits[w['key']][k] for k in (
            'query_id','mode','rank','key','review_id','annotation_id','effective_snapshot_sha256','embedding_input_sha256','evidence_ref')},
            quote_start=w['quote_start'],quote_end=w['quote_end']) for w in finding['evidence_refs']]
    batch=EvidenceBatch.model_validate_json(json.dumps(case['batch']))
    ref=validate_human_reference(dict(version='e1-human-reference-v1',origin='independent_human_reference',**fields),
        contract=contract,trusted_batch=batch,expected_scope=batch.scope_ref,expected_batch_sha256=case['batch_sha256'],expected_context_policy='quote_full_review_veto')
    return {k:v for k,v in ref.model_dump(mode='json').items() if k not in ('version','origin')}


def validate_session(bundle,session):
    require(bundle['version']==BUNDLE_VERSION and session['version']==SESSION_VERSION,'adjudication_version_mismatch')
    require(session['bundle_sha256']==content_sha256(bundle),'adjudication_bundle_mismatch')
    require(bundle['history_status']=='history_unproven' and bundle['context_policy']=='quote_full_review_veto' and bundle['context_version']=='1','adjudication_boundary_changed')
    qs={q['query_id']:q for q in bundle['queries']};cs={c['case_id']:c for c in bundle['cases']};ps={p['query_id']:p for p in bundle['pairs']}
    require(set(session['queries'])==set(qs) and set(session['cases'])==set(cs) and set(session['pairs'])==set(ps),'adjudication_item_coverage_changed')
    events={};previous=None
    for index,event in enumerate(session['events'],1):
        require(event['event_id']==f'E{index:04d}' and event['previous_event_sha256']==previous,'adjudication_event_chain_invalid')
        require(event['bundle_sha256']==session['bundle_sha256'] and event['payload_sha256']==content_sha256(event['payload']),'adjudication_event_payload_invalid')
        require(event['metadata_sha256']==content_sha256(event['metadata']) and event['metadata']['reviewer_id']==event['reviewer_id'],
                'adjudication_event_metadata_invalid')
        try:stamp=datetime.fromisoformat(event['recorded_at'].replace('Z','+00:00'));require(stamp.utcoffset() is not None,'adjudication_event_timezone')
        except (ValueError,AttributeError):raise ValueError('adjudication_event_time_invalid') from None
        require(event['origin']=='human_ui_action' and event['kind'] in ('contract','case','pair','completion'),'adjudication_event_kind_invalid')
        if events:require(event['recorded_at']>=list(events.values())[-1]['recorded_at'],'adjudication_event_time_order')
        events[event['event_id']]=event;previous=content_sha256(event)
    errors=[];query_errors={};case_issues={};pair_issues={};valid_q=set();valid_c=set();valid_p=set();prepared=[]
    if type(session['reviewer_id']) is not str or not session['reviewer_id'].strip():errors.append('请填写评审人代号')
    if session['prior_relevance_exposure'] not in ('not_seen','seen','unsure'):errors.append('请选择既往相关性评分接触情况')
    if session['context_ack'] is not True:errors.append('请确认上下文B与历史未证明边界')
    def event_ok(event_id,kind,subject,payload):
        event=events.get(event_id)
        return bool(event and event['kind']==kind and event['subject_id']==subject and event['reviewer_id']==session['reviewer_id']
            and event['metadata_sha256']==content_sha256(metadata(session)) and event['payload']==payload)
    for qid,source in qs.items():
        row=session['queries'][qid];problems=[]
        try:
            c=validate_query_contract(row['payload']['contract'],expected_scope=ScopeRef.model_validate_json(json.dumps(bundle['scope_ref'])))
            require(c.proposed_filters.model_dump()==source['before']['contract']['proposed_filters'],'adjudication_filters_changed')
            ids=row['payload']['independent_target_fact_ids'];require(len(ids)==len(set(ids)) and set(ids)<={f.id for f in c.required_facts.items},'adjudication_independent_ids_invalid')
            require(type(row['revision']) is int and row['revision']>0,'adjudication_query_not_reviewed')
            payload=dict(payload=row['payload'],revision=row['revision'],choice=row['choice'],rationale=row.get('rationale',''))
            require(payload['choice'] in ('original','proposed','custom') and bool(payload['rationale'].strip()),'adjudication_contract_decision_missing')
            if payload['choice'] in ('original','proposed'):require(row['payload']==source['before' if payload['choice']=='original' else 'proposed'],'adjudication_contract_choice_mismatch')
            require(event_ok(row['event_id'],'contract',qid,payload),'adjudication_contract_record_missing_or_stale')
        except (ValueError,KeyError,TypeError) as exc:problems.append(str(exc) if isinstance(exc,ValueError) else '契约结构无效')
        query_errors[qid]=problems
        if not problems:valid_q.add(qid)
    for cid,case in cs.items():
        row=session['cases'][cid];query=session['queries'][case['query_id']];problems=[]
        if case['query_id'] not in valid_q:problems.append('先确认共享查询契约')
        if row['contract_revision']!=query['revision']:problems.append('契约版本变化，必须重新评审')
        try:
            problems+=review_errors(row['review'],query['payload']['contract'],query['payload']['independent_target_fact_ids'],case)
            if not problems:
                fields=prepare_reference(case,query,row['review'])
                require(event_ok(row['event_id'],'case',cid,case_payload(session,cid)),'adjudication_case_record_missing_or_stale')
                prepared.append(dict(case_id=cid,target_format='e1-human-reference-v1',prepared_fields=fields,
                    contract_payload_sha256=content_sha256(query['payload']),review_confirmation_record=events[row['event_id']],
                    contract_confirmation_record=events[query['event_id']],input_bundle_sha256=session['bundle_sha256'],
                    observed_sources_sha256=bundle.get('observed_sources_sha256'),prior_relevance_exposure=session['prior_relevance_exposure'],
                    formal_confirmation_record=None,usable_for_evaluation=False,history_status='history_unproven',reviewer_count=1,
                    single_reviewer_limitation='单人复核，不构成双人盲审或独立裁决'))
        except (ValueError,KeyError,TypeError) as exc:problems.append(str(exc) if isinstance(exc,ValueError) else '评审字段结构无效')
        case_issues[cid]=sorted(set(problems))
        if not problems:valid_c.add(cid)
    for qid,pair in ps.items():
        a,b=pair['case_ids'];problems=[]
        if a not in valid_c or b not in valid_c:problems.append('先完成两份CASE复核')
        if semantic_labels(session['cases'][a]['review'])!=semantic_labels(session['cases'][b]['review']):problems.append('同输入下的事实、关系或许可判断不一致')
        if pair['required_decision']:
            if not session['pairs'][qid]['rationale'].strip():problems.append('缺少配对裁决理由')
            if not event_ok(session['pairs'][qid]['event_id'],'pair',qid,pair_payload(bundle,session,qid)):problems.append('配对裁决记录缺失或已失效')
        pair_issues[qid]=problems
        if not problems:valid_p.add(qid)
    precomplete=not errors and len(valid_q)==len(qs) and len(valid_c)==len(cs) and len(valid_p)==len(ps)
    completion=event_ok(session.get('completion_event_id'),'completion','session',completion_payload(session))
    if not completion:errors.append('尚未提交本轮人工裁决完成记录')
    ready=precomplete and completion
    return dict(version='e1-adjudication-check-v1',ready_for_formal_reference_freeze=ready,
        counts=dict(queries=len(qs),queries_reviewed=len(valid_q),cases=len(cs),cases_reviewed=len(valid_c),pairs=len(ps),pairs_consistent=len(valid_p)),
        global_errors=errors,query_errors=query_errors,case_errors=case_issues,pair_errors=pair_issues,
        pending_references=prepared if ready else None,formal_human_confirmed=False,history_status='history_unproven')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--expected-bundle-sha256',required=True);args=parser.parse_args()
    bundle=strict_json_loads(args.bundle.read_text(encoding='utf-8'));session=strict_json_loads(args.session.read_text(encoding='utf-8-sig'))
    require(content_sha256(bundle)==args.expected_bundle_sha256,'expected_bundle_identity_mismatch')
    result=validate_session(bundle,session);args.output.mkdir(exist_ok=False,parents=True)
    (args.output/'consistency-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if result['ready_for_formal_reference_freeze']:
        (args.output/'references.pending.json').write_text(json.dumps(result['pending_references'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report='# 人工裁决校验结果\n\n'+('具备进入待正式确认参考冻结阶段的资格。' if result['ready_for_formal_reference_freeze'] else '仍有未完成或不一致项，未生成待冻结参考。')+'\n\n'+json.dumps({k:v for k,v in result.items() if k!='pending_references'},ensure_ascii=False,indent=2)+'\n\n历史状态仍为 history_unproven；未执行真实E1或正式确认。\n'
    (args.output/'report.md').write_text(report,encoding='utf-8');print(json.dumps(result['counts'],ensure_ascii=False))
    raise SystemExit(0 if result['ready_for_formal_reference_freeze'] else 2)


if __name__=='__main__':main()
