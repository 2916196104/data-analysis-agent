"""Versioned local mapping-review materials. No confirmation issuer or execution hook."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

from pydantic import Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT/'src'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from experiments import text_evidence_runtime_contract as compiler
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.reviews.query_contract import ContractModel

VERSION = 'p5-typed-mapping-human-review-v1'
PARENT = ROOT/'outputs/rag-semantic-retrieval/real-evaluation-review-preflight-v1'
DESTINATION = PARENT/'typed-mapping-review-v1'
JSON_NAME = 'typed-mapping-human-review.json'
MD_NAME = 'typed-mapping-human-review.md'
SESSION = next(p for p in compiler._PATHS if p.endswith('source-session.original.json'))
MAX_BYTES = 512 * 1024

# Sentence indices refer to the pinned confirmation, not a relevance label or new gold.
BASIS = {
    'Q02': {'customer_support': (1,3), 'impatient': (2,), 'poor_attitude': (2,), 'attitude_not_delay': (4,)},
    'Q04': {'post_purchase_issue': (0,2), 'continuous_no_followup': (2,4), 'unresolved_not_unfollowed': (3,4)},
    'Q05': {'order_to_receipt_fast': (0,2), 'direct_receipt': (2,3), 'no_installation_inference': (3,4)},
    'Q06': {'prolonged_nonreceipt': (0,2,3), 'clear_delivery_delay': (2,4),
            'ordinary_nonreceipt_insufficient': (3,4), 'receipt_not_dispatch': (4,)},
    'Q09': {'main_device': (1,), 'use_started': (0,1), 'early_after_use': (0,3), 'damage': (0,),
            'malfunction': (0,), 'no_accessories': (1,), 'no_preuse_damage': (1,), 'no_generic_quality': (4,)},
    'Q11': {'hot_environment': (2,3), 'startup': (2,), 'cooling_mode': (0,2,5),
            'room_cooling': (2,5), 'rapid_cooling': (2,4), 'same_episode': (2,)},
    'Q12': {'long_running': (2,3,6), 'room_not_cool': (2,4), 'cooling_mode': (6,), 'no_short_run_or_wind': (5,6)},
    'Q18': {'receipt_completed': (3,4), 'after_receipt': (3,4), 'prolonged_no_installation': (3,),
            'no_booking_substitute': (5,), 'classification_not_new_fact': (2,6)},
}

RISKS = {
    'customer_support': '不要把泛称服务猜成客服，也不要把角色确认误变为必须出现“客服”字面词。',
    'impatient': '内部OR的一个分支；不能要求与态度差同时成立，也不能把单纯回复慢算作不耐烦。',
    'poor_attitude': '保留原确认的敷衍/不礼貌边界，不扩大成所有售后不满或要求另一分支同时成立。',
    'attitude_not_delay': '排除单一时效/结果推断，不能否决同时存在的明确态度描述。',
    'post_purchase_issue': '保留购买后具体问题，不增加故障类型，也不把所有安装问题排除在售后之外。',
    'continuous_no_followup': '持续性按原自然语言，不增加固定天数；长期拖延与完全无人回应边界待核对。',
    'unresolved_not_unfollowed': '不能因曾有一次沟通而一概否决之后持续无人跟进的事实。',
    'order_to_receipt_fast': '是下单至实际收货，不是出库或安装时效；不私设速度阈值。',
    'direct_receipt': '直接性不应变成必须给出精确签收日期；原明确实际到货的速度评价仍须保留。',
    'no_installation_inference': '不能由安装日期推定直接收货；有独立收货表述的混合评论不应整体排除。',
    'prolonged_nonreceipt': '内部OR的未收货分支；不能把普通尚未收货自动认作长期，也不要求另一分支成立。',
    'clear_delivery_delay': '保留已收到但明显延迟的情况，不能被未收货分支的否定全局排除。',
    'ordinary_nonreceipt_insufficient': '原文分别要求时间语义和区分错误签收；新组合可能被误读为排除所有错误签收场景。',
    'receipt_not_dispatch': '区分单纯发货/安装与实际配送，不应排除同时提供了明确到货延误的混合体验。',
    'main_device': '按原确认仅主机；不要把仅写设备而未点名主机的表达机械排除。',
    'use_started': '须实际使用后，不把仅购买时间当使用起点；不要新增使用证明格式要求。',
    'early_after_use': '原自然语言早期条件，不增加固定天数，也不把购买不久自动当使用不久。',
    'damage': '损坏是内部OR一支，不要求同时功能故障；不能以泛质量差替代。',
    'malfunction': '功能故障是内部OR一支，不要求同时物理损坏；不能抹去使用后早期条件。',
    'no_accessories': '不自动包含配件，不应把主机与配件都被提到的评论整体排除。',
    'no_preuse_damage': '排除未实际使用的收到即坏，不应扩大到否决已实际使用后的早期故障。',
    'no_generic_quality': '单一泛质量评价不足，但不能否决同quote已经提供的具体早期故障。',
    'hot_environment': '原明确炎热条件不能由季节推断，也不要求温度数值或固定关键词。',
    'startup': '保留开机后体验，不增加开机步骤细节或开机时间戳要求。',
    'cooling_mode': '区分制冷与制热；不增加必须出现“制冷”字面词的要求。Q11为由室内降温目标展开的解释。',
    'room_cooling': '保留室内降温，不把仅风凉替代；“房间很快凉下来”可共同表达效果与速度。',
    'rapid_cooling': '保留很快的自然语义，不设分钟阈值，不变成一般制冷效果好。',
    'same_episode': '同次体验约束不能要求另加日期/事件编号，但也不能拼接不同经历补齐条件。',
    'long_running': '持续较久按原自然语言，不新增小时阈值或把零散开机时段自动累加。',
    'room_not_cool': '与较长运行共同成立，不以单一风温或一般效果差替代。',
    'no_short_run_or_wind': '只排除单要素替代，不应否决同quote已经表达完整长运行与降温不足的情况。',
    'receipt_completed': 'f0中性到货事实，不应被安装服务这一总体对象限制成必须由安装人员叙述。',
    'after_receipt': '真实到货先于安装等待，不用文字先后或评论日期代替事件时序。',
    'prolonged_no_installation': '保留长时间等待，不把普通尚未安装等同长期，也不私设天数。',
    'no_booking_substitute': '安装态度/预约变更本身不足，有完整到货后长期等待事实时不能整体排除。',
    'classification_not_new_fact': '这是任务解释和许可边界；放在f1树中可能被误读为quote还须证明分类，应重点核对。',
}

STRUCTURE_FOCUS = {
    'Q02': '客服角色 AND（不耐烦 OR 服务态度差），内部OR不拆成两项必需事实。',
    'Q04': '购买后问题 AND 持续无人跟进；已处理未解决不自动等同无人跟进。',
    'Q05': '实际收货与下单速度；不得用安装日期推定直接收货。',
    'Q06': '长期未收货 OR 明显配送延迟；否定仅作用于未收货分支，不吞掉已收到但延迟的分支。',
    'Q09': '主机、实际使用后、早期、损坏或故障；配件和使用前损坏边界。',
    'Q11': '明确炎热、开机后室内快速降温、同次体验，非数值阈值。',
    'Q12': '持续运行较久 AND 室内仍未有效降温，区分短时、制热和单一风温。',
    'Q18': 'f0中性到货、f1负面持续安装等待，all_of及same_review、before(f0,f1)均保留；不新增分类事实。',
}


class ReviewError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ReviewError(code)


class HumanOpinion(ContractModel):
    decision: Literal['accept', 'needs_revision', 'reject'] | None = None
    comment: str | None = Field(default=None, max_length=4000)

    @model_validator(mode='after')
    def revision_reason(self):
        if self.decision in ('needs_revision','reject') and not (self.comment and self.comment.strip()):
            raise ValueError('revision_reason_required')
        return self


def code_fingerprints():
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in (Path(compiler.__file__), Path(__file__))}


def leaves(expr, pointer, ancestors=()):
    if expr.kind == 'atom':
        yield pointer, expr.predicate, list(ancestors)
    else:
        for i,child in enumerate(expr.children):
            yield from leaves(child, f'{pointer}/children/{i}', (*ancestors,dict(operator=expr.kind,child_index=i)))


def basis_excerpt(text, *, source_file, pointer, event_id, start, end, sentence_index=None):
    require(0 <= start < end <= len(text), 'invalid_basis_range')
    return dict(source_file=source_file, json_pointer=pointer, event_id=event_id,
        field_sha256=content_sha256(text), coordinate_unit='unicode_code_point_in_json_field',
        start=start,end=end,excerpt=text[start:end],sentence_index=sentence_index)


def _normalize_opinions(material):
    value=deepcopy(material)
    opinions={}
    try:
        require(value['mapping_human_confirmed'] is False and value['human_final_confirmation'] is None,
                'untrusted_confirmation_claim')
        for target in value['targets']:
            items=[(r['condition_id'],r) for r in target['conditions']]
            items.append((target['query_id']+'.structure',target['structural_review']))
            for identity,item in items:
                require(identity not in opinions, 'duplicate_review_item')
                raw=item['human_opinion']
                require(type(raw) is dict and set(raw)=={'decision','comment'}, 'invalid_human_opinion')
                opinion=HumanOpinion.model_validate_json(canonical_json(raw))
                opinions[identity]=opinion.model_dump(mode='json')
                item['human_opinion']=dict(decision=None,comment=None)
    except ReviewError:
        raise
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ReviewError('invalid_review_shape_or_opinion') from None
    return value,opinions


def material_digest(material):
    value,_=_normalize_opinions(material)
    value['material_sha256']=None
    return content_sha256(value)


def build_material(origins):
    data=compiler._origin_data(origins)
    targets=[]
    for qid in compiler.DEFINITIONS:
        checked=compiler.compile_target(origins,compiler.propose_spec(origins,qid))
        spec=checked.spec;record=data['records'][qid]
        rationale=record['rationale']
        sentences=list(re.finditer(r'[^。]+(?:。|$)',rationale))
        source_file=SESSION if origins._kind=='historical_pinned' else 'SYNTHETIC-CONFIRMATION.json'
        rows=[]
        seen=set()
        for fi,fact in enumerate(spec.facts):
            for pointer,predicate,ancestors in leaves(fact.expression,f'/facts/{fi}/expression'):
                require(predicate in BASIS[qid] and predicate in RISKS, 'missing_condition_review_metadata')
                indexes=BASIS[qid][predicate]
                require(all(i<len(sentences) for i in indexes), 'original_basis_sentence_missing')
                basis=[basis_excerpt(rationale,source_file=source_file,pointer=f'/queries/{qid}/rationale',
                    event_id=spec.origin.event_id,start=sentences[i].start(),end=sentences[i].end(),sentence_index=i) for i in indexes]
                description=fact.original.description
                basis.insert(0,basis_excerpt(description,source_file=source_file,
                    pointer=f'/queries/{qid}/payload/contract/required_facts/items/{fi}/description',
                    event_id=spec.origin.event_id,start=0,end=len(description)))
                identity=f'{qid}.{fact.original.id}.{predicate}'
                require(identity not in seen, 'duplicate_condition_id');seen.add(identity)
                rows.append(dict(condition_id=identity,query_id=qid,fact_id=fact.original.id,
                    original_question=spec.original_question,original_fact=fact.original.model_dump(mode='json'),
                    target_entity=spec.original_contract.target_entity,
                    fact_subject='买家处的货物' if qid=='Q18' and fact.original.id=='f0' else spec.original_contract.target_entity,
                    predicate=predicate,meaning=compiler.LABELS[predicate],tree_pointer=pointer,
                    top_operator=spec.original_contract.required_facts.operator,ancestors=ancestors,
                    sentiment=fact.original.sentiment,
                    applicable_negations=[n.model_dump(mode='json') for n in spec.scoped_negations
                                          if n.fact_id==fact.original.id and n.predicate==predicate],
                    fact_negations=[n.model_dump(mode='json') for n in spec.scoped_negations if n.fact_id==fact.original.id],
                    required_relations=[r.model_dump(mode='json') for r in spec.original_contract.required_facts.relations],
                    original_basis=basis,basis_alignment='pending_human_alignment_check',
                    basis_relation='interpretation_requiring_alignment' if (qid,predicate) in (
                        ('Q06','ordinary_nonreceipt_insufficient'),('Q11','cooling_mode')) else 'original_field_and_confirmation',
                    new_explicit_content='将原事实或确认边界拆为显式检查项：'+compiler.LABELS[predicate],
                    potential_semantic_risk=RISKS[predicate],risk_authorship='codex_proposed_not_human_ruling',
                    engineering_structure='passed',typed_interpretation_status='pending_human_confirmation',
                    human_opinion=dict(decision=None,comment=None)))
        require(len(rows)==len(spec.coverage), 'review_coverage_mismatch')
        targets.append(dict(query_id=qid,original_question=spec.original_question,
            original_contract_version=spec.origin.version,original_contract_revision=spec.origin.revision,
            original_confirmation=spec.origin.model_dump(mode='json'),
            normalized_spec_sha256=content_sha256(spec.model_dump(mode='json')),
            runtime_projection_sha256=content_sha256(checked.runtime_projection.model_dump(mode='json')),
            original_facts=[f.original.model_dump(mode='json') for f in spec.facts],
            fact_trees=[dict(fact_id=f.original.id,expression=f.expression.model_dump(mode='json')) for f in spec.facts],
            polarity=spec.original_contract.polarity_structure.model_dump(mode='json'),
            relations=[r.model_dump(mode='json') for r in spec.original_contract.required_facts.relations],
            note_resolutions=[n.model_dump(mode='json') for n in spec.resolutions],
            independent_target_fact_ids=[],conditions=rows,
            structural_review=dict(focus=STRUCTURE_FOCUS[qid],human_opinion=dict(decision=None,comment=None))))
    require(sum(len(t['conditions']) for t in targets)==37,'expected_37_conditions')
    value=dict(version=VERSION,material_revision=1,origin_kind=origins._kind,
        code_fingerprints=code_fingerprints(),
        original_file_pins=dict(compiler._PATHS) if origins._kind=='historical_pinned' else {},
        targets=targets,material_sha256=None,mapping_human_confirmed=False,human_final_confirmation=None,
        p0_binding_present=False,executable_requests=0,publication_allowed=False,semantic_verified=False,
        permitted_target_fact_ids=[])
    value['material_sha256']=material_digest(value)
    return value


def check_review(origins, submitted):
    """Only a conservative local draft checker. File claims never identify a human."""
    try:
        value=strict_json_loads(submitted) if isinstance(submitted,str) else deepcopy(submitted)
        normalized,opinions=_normalize_opinions(value)
        expected=build_material(origins)
        require(normalized==expected and material_digest(value)==expected['material_sha256'],
                'stale_or_changed_review_material')
    except ReviewError:
        raise
    except (ValueError,TypeError,RecursionError):
        raise ReviewError('invalid_review_json') from None
    states={}
    for target in expected['targets']:
        ids=[r['condition_id'] for r in target['conditions']]+[target['query_id']+'.structure']
        decisions=[opinions[i]['decision'] for i in ids]
        states[target['query_id']]=('semantic_difference_requires_revision' if any(d in ('needs_revision','reject') for d in decisions)
            else 'awaiting_explicit_owner_confirmation' if all(d=='accept' for d in decisions)
            else 'pending_per_item_human_review')
    return dict(material_version=VERSION,material_sha256=expected['material_sha256'],
        normalized_specs_sha256=content_sha256({t['query_id']:t['normalized_spec_sha256'] for t in expected['targets']}),
        original_confirmations_sha256=content_sha256({t['query_id']:t['original_confirmation'] for t in expected['targets']}),
        opinions_sha256=content_sha256(opinions),per_item_opinions=opinions,target_states=states,
        condition_opinions=37,structural_opinions=8,filled_opinions=sum(o['decision'] is not None for o in opinions.values()),
        opinion_actor='unattributed_file_draft',mapping_human_confirmed=False,
        incremental_human_confirmation='not_issued',four_state_review_admitted_targets=[],
        p0_binding_present=False,executable_requests=0,publication_allowed=False,semantic_verified=False,
        permitted_target_fact_ids=[])


def render_markdown(material):
    lines=['# 八目标类型化映射增量人工核对（私有）','',
        f'版本：{material["version"]} / revision {material["material_revision"]}',
        f'材料摘要：`{material["material_sha256"]}`','',
        '工程结构校验已通过；37项类型化解释和8项整体结构意见均未确认。',
        '每项human_opinion.decision/comment默认null。可填accept、needs_revision或reject；后两者须说明原因。',
        '仅填写JSON中的human_opinion两字段；其他状态由只读检查器计算，不修改原文、树、摘要或预填状态。',
        '文件中的意见无法自行证明填写者身份。全部accept仍须本人在对话中明确确认具体版本和绑定摘要；本检查器不会签发确认。',
        '本材料不是27行评论四状态金标准，不建立P0，也不授权数据发送或发布。','']
    for target in material['targets']:
        qid=target['query_id']
        lines += [f'## {qid}：{target["original_question"]}',
            '原版本：'+target['original_contract_version']+' / revision '+str(target['original_contract_revision']),
            '规范化规格摘要：`'+target['normalized_spec_sha256']+'`',
            '原事实：'+canonical_json(target['original_facts']),
            '原否定：'+canonical_json(target['polarity'])+'；必要关系：'+canonical_json(target['relations']),
            '完整类型化树：','```json',json.dumps(target['fact_trees'],ensure_ascii=False,indent=2),'```',
            '整体结构额外核对：'+target['structural_review']['focus'],
            '整体结构人工意见：结论____；说明____（均未确认）。','']
        for row in target['conditions']:
            lines += [f'### {row["condition_id"]}',
                f'原问题：{row["original_question"]}；原事实：{row["original_fact"]["description"]}',
                f'对象：{row["target_entity"]}；事实主体：{row["fact_subject"]}；谓词：{row["predicate"]}',
                '条件含义：'+row['meaning'],
                '树位置：`'+row['tree_pointer']+'`；顶层'+row['top_operator']+'；祖先：'+canonical_json(row['ancestors']),
                '方向：'+str(row['sentiment'])+'；本项否定：'+canonical_json(row['applicable_negations']),
                '同事实否定：'+canonical_json(row['fact_negations'])+'；必要关系：'+canonical_json(row['required_relations']),
                '原确认精确依据（范围相对于JSON字段解码后的Unicode字符串，不是业务评论坐标）：']
            for basis in row['original_basis']:
                lines += [f'- `{basis["source_file"]}#{basis["json_pointer"]}`；事件{basis["event_id"]}；'
                    f'[{basis["start"]},{basis["end"]})；字段摘要`{basis["field_sha256"]}`',
                    '  精确片段：'+basis['excerpt']]
            lines += ['新增显式表达：'+row['new_explicit_content'],
                '需裁决风险（Codex建议，未作人工裁决）：'+row['potential_semantic_risk'],
                '依据对齐状态：'+row['basis_alignment']+'；依据关系：'+row['basis_relation'],
                '工程结构：passed；类型化解释：pending_human_confirmation',
                '人工意见：结论____；说明____（JSON中decision=null、comment=null）。','']
    return '\n\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','check'))
    args=parser.parse_args()
    from contextlib import ExitStack
    from unittest.mock import patch
    from data_analysis_agent.agents import review_shadow_diagnostics as privacy
    with ExitStack() as guard:
        for name in ('socket.socket.connect','socket.create_connection','psycopg.connect','sqlite3.connect',
            'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request','openai.OpenAI',
            'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder',
            'data_analysis_agent.reviews.semantic_retrieval.SemanticRetrievalService.search'):
            guard.enter_context(patch(name,side_effect=AssertionError('offline_mapping_review_only')))
        origins=compiler.load_confirmed_origins()
        if args.action=='prepare':
            require(not DESTINATION.exists(),'review_version_already_exists')
            require(subprocess.run(['git','check-ignore','-q','--',str(DESTINATION/JSON_NAME)],cwd=ROOT).returncode==0,
                    'review_path_not_git_ignored')
            material=build_material(origins)
            status=check_review(origins,material)
            texts={JSON_NAME:json.dumps(material,ensure_ascii=False,indent=2)+'\n',MD_NAME:render_markdown(material)}
            require(all(len(t.encode('utf-8'))<=MAX_BYTES for t in texts.values()),'private_review_too_large')
            destination=privacy.destination(str(DESTINATION))
            privacy._private_mkdir(destination)
            for name,text in texts.items():
                with (destination/name).open('x',encoding='utf-8') as out:
                    out.write(text)
        else:
            raw=(DESTINATION/JSON_NAME).read_bytes()
            require(len(raw)<=MAX_BYTES,'private_review_too_large')
            status=check_review(origins,raw.decode('utf-8'))
    safe={k:v for k,v in status.items() if k!='per_item_opinions'}
    print(json.dumps(safe,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
