"""P6-D 白名单发布及确定性回答。仅消费服务端执行/准备对象，不执行 I/O。"""
from data_analysis_agent.agents.review_answers import PublicAnswer
from data_analysis_agent.agents.review_budget import P6InputIntegrityError
from data_analysis_agent.agents.review_execution import P6ExecutedAnalysis
from data_analysis_agent.agents.review_multi_aspect import MODE
from data_analysis_agent.agents.review_topics import topic_id
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.opinions import ASPECTS

VERSION = 'review-p6-publication-v1'
STATE_LABELS = {'complete': '已完成', 'partial': '部分完成', 'insufficient_evidence': '证据不足',
                'skipped_budget': '预算不足，未执行', 'failed': '执行失败'}
REASONS = {'complete': '已通过逐维证据验收', 'insufficient_evidence': '正确查询后没有足够支持主题的证据',
    'not_started': '共享预算不足，未开始', 'cost_limit': '共享费用预算不足',
    'cost_not_authorized': '未获得明确费用授权', 'execution_not_enabled': '服务端尚未授权执行',
    'unknown_price_token_bound_or_billing': '费用单价、token 上界或计费类别未明确，禁止调用',
    'real_provider_not_authorized': '真实模型请求未获得服务端授权',
    'input_body_limit': '冻结候选超出单次模型输入上限', 'attempt_or_stage_limit': '共享模型尝试额度已用完',
    'timeout': '主题模型请求超时，结果未被验收', 'request_deadline_exceeded': '模型结果迟到，已拒绝发布',
    'deadline_exceeded': '共享时间预算已用完',
    'provider_401': '模型服务鉴权失败，已停止后续请求', 'provider_402': '模型服务额度不足，已停止后续请求',
    'provider_403': '模型服务拒绝授权，已停止后续请求',
    'invalid_or_failed_discovery': '主题发现失败或响应未通过校验',
    'invalid_or_failed_batch': '观点归属复核失败或响应未通过校验',
    'independent_aspect_acceptance_failed': '逐维独立验收未通过'}
LIMITATIONS = [
    '主题为模型发现并复核的候选，未经人工确认。',
    '主题比例 N/S 仅描述已分析样本，不是全量投诉率；维度负面率为 M/D。',
    '各维度及主题可引用同一评论，计数不可相加作为独立评论数或用户数。',
    '有限候选采用最近评论及观点顺序的有界非随机抽样；未覆盖评论不能推断为无问题。',
]


def _support(data, topic, result_id):
    opinion = next(r for r in data['opinions'] if r['opinion_id'] == topic['evidence_opinion_ids'][0])
    item = next(r for r in data['items'] if r['review_id'] == opinion['review_id'])
    candidate = next(r for r in data['catalog']
        if topic_id(r['name'], data['aspect'], 'negative') == topic['topic_id'])
    assignment = next(r for r in data['assignments'] if r['opinion_id'] == opinion['unit_id'])
    match = next(r for r in assignment['matches'] if r['key'] == candidate['key'])
    start, end = opinion['quote_start'] + match['start'], opinion['quote_start'] + match['end']
    if not 0 <= start < end <= len(item['text']):
        raise P6InputIntegrityError('主题支持引用位置错误。')
    return {key: item[key] for key in ('review_id', 'product_id', 'published_at', 'source', 'dataset_version',
        'annotation_id', 'revision_id', 'opinion_source', 'file_sha256', 'line_number', 'evidence_ref', 'text')} | {
        'source_result_id': result_id, 'aspect': data['aspect'], 'topic_id': topic['topic_id'],
        'opinion_id': opinion['opinion_id'], 'sentiment': 'negative',
        'quote_start': start, 'quote_end': end, 'quote': item['text'][start:end]}


def _percent(value):
    return '—' if value is None else f'{value * 100:.1f}%'


def _literal(value):
    # 复制/Markdown 导出不能让评论或候选名称注入标题、链接或图片。
    text = str(value).replace('\\', '\\\\').replace('\n', ' ').replace('\r', ' ')
    for char in '`*_{}[]<>()#!|':
        text = text.replace(char, '\\' + char)
    return text


def publication_text(publication):
    """页面、复制与导出共享同一已验证事实集；不生成解释或建议。"""
    p, scope = publication, publication['scope']
    lines = ['# 多维度负面主题分析', f"状态：{STATE_LABELS[p['parent_status']]}；完整任务完成：{'是' if p['complete'] else '否'}。",
        f"商品：{_literal('、'.join(scope['product_ids']))}；数据集：{_literal(scope['source'])} / {_literal(scope['dataset_version'])}。",
        f"当前窗口（UTC）：{scope['date_from']} — {scope['date_to']}；评论截止：{scope['as_of'] or '未限定'}。",
        f"标注版本：{_literal(scope['model'])} / {_literal(scope['prompt_version'])}；成功标注评论数 D={p['D']}。",
        '必做维度（冻结排名顺序）：' + '、'.join(ASPECTS[a] for a in p['required_aspects']) + '。']
    for dim in p['dimensions']:
        lines += [f"## {dim['rank']}. {dim['label']} · {STATE_LABELS[dim['status']]}",
            f"全量负面去重评论 M={dim['M_a']}；维度负面率 M/D={dim['M_a']}/{p['D']}（{_percent(dim['negative_rate'])}）。",
            f"实际分类样本 S={dim['S_a']}；冻结候选评论={dim['prepared_reviews']}；样本覆盖 S/M={_percent(dim['review_coverage'])}。"]
        if dim['coverage']:
            coverage = dim['coverage']
            lines.append(f"观点归属覆盖：{coverage['classified_opinions']}/{coverage['selected_opinions']}（{_percent(coverage['opinion_coverage'])}）；未归属观点仍保留在样本分母中。")
        if dim['status'] != 'complete':
            lines.append('未完成原因：' + dim['reason'] + f"（{dim['reason_code']}）。未发布该维度主题。")
        for topic in dim['topics']:
            lines += [f"### {_literal(topic['name'])}", _literal(topic['definition']),
                f"样本内去重评论 N={topic['N_a_t']}；样本内主题比例 N/S={topic['N_a_t']}/{dim['S_a']}（{_percent(topic['sample_rate'])}）。"]
            for ref in topic['evidence']:
                lines += [f"支持引用：{_literal(ref['quote'])}",
                    f"评论 {ref['review_id']}；原文位置 [{ref['quote_start']}, {ref['quote_end']})（Unicode 字符）；观点 {_literal(ref['opinion_id'])}。",
                    f"来源：{ref['evidence_ref']}；标注 {ref['annotation_id']}；人工修订 {ref['revision_id'] or '无'}；观点来源 {ref['opinion_source']}。",
                    '原文：' + _literal(ref['text'])]
        if dim['omitted_topic_count']:
            lines.append(f"展示限制：另有 {dim['omitted_topic_count']} 个已支持候选主题未展示；每维最多展示三个主题。")
    lines += ['## 口径与限制', *p['limitations']]
    return '\n\n'.join(lines) + '\n'


def public_answer(publication):
    return PublicAnswer(status=publication['status'], generation_status='template',
        summary=publication['answer_text'], caveats=publication['limitations'], details_available=True).model_dump()


def publish_p6_execution(executed, prepared, *, settings, **authorization):
    """发布授权与执行授权独立；客户端 JSON 和历史诊断不能进入此入口。"""
    if (type(executed) is not P6ExecutedAnalysis or not settings.p6_preparation_enabled
            or not settings.p6_execution_enabled or not settings.p6_publication_enabled):
        raise P6InputIntegrityError('P6 发布尚未由服务端独立启用。')
    value = executed.revalidate_for_publication(prepared, **authorization)
    accepted, source = value['acceptance'], prepared.validate()
    dimensions = []
    D = source['current_snapshot']['denominator']['value']
    for rank, outcome in enumerate(value['outcomes'], 1):
        aspect = outcome['aspect']
        frozen = prepared.get_input(aspect)
        publishable = aspect in accepted['publishable_aspects']
        data = outcome['results'][0]['data'] if outcome['results'] else None
        M = source['current_snapshot']['negative_review_counts'][aspect]
        consumed = data is not None and outcome['model_attempts'] > 0
        if publishable and not consumed:
            raise P6InputIntegrityError('已完成维度缺少实际分类记录，禁止发布。')
        S = data['coverage']['selected_reviews'] if consumed else 0
        dim = {'aspect': aspect, 'label': ASPECTS[aspect], 'rank': rank, 'status': outcome['status'],
            'reason_code': outcome['reason'], 'reason': REASONS.get(outcome['reason'],
                '共享预算不足，未开始' if outcome['status'] == 'skipped_budget' else '工具或分类过程未通过验收'),
            'M_a': M, 'S_a': S, 'prepared_reviews': frozen['coverage']['selected_reviews'],
            'negative_rate': round(M / D, 6) if D else None,
            'review_coverage': round(S / M, 6) if M else None,
            'coverage': data['coverage'] if consumed else None, 'sampling': frozen['sampling'],
            'population_mode': data['population_mode'] if consumed else 'not_classified',
            'topics_total': len(data['topics']) if publishable else 0,
            'omitted_topic_count': max(0, len(data['topics']) - 3) if publishable else 0,
            'truncation_reason': 'per_aspect_top3' if publishable and len(data['topics']) > 3 else None,
            'topics': []}
        if publishable:
            for topic in data['topics'][:3]:
                dim['topics'].append({'topic_id': topic['topic_id'], 'name': topic['name'],
                    'definition': topic['definition'], 'N_a_t': topic['review_count'],
                    'sample_denominator': topic['review_denominator'], 'sample_rate': topic['review_rate'],
                    'opinion_count': topic['opinion_count'], 'semantic_status': topic['semantic_status'],
                    'evidence': [_support(data, topic, outcome['results'][0]['result_id'])]})
        dimensions.append(dim)
    publication = {'version': VERSION, 'analysis_mode': MODE, 'status': accepted['agent_status'],
        'parent_status': accepted['status'], 'complete': accepted['complete'], 'scope': prepared.goal.expected_scope,
        'required_aspects': accepted['required_aspects'], 'publishable_aspects': accepted['publishable_aspects'],
        'D': D, 'dimensions': dimensions, 'limitations': LIMITATIONS.copy(),
        'ranking_source': prepared.goal.ranking_source, 'selection_basis_sha256': prepared.goal.selection_basis_sha256}
    publication['answer_text'] = publication_text(publication)
    publication['publication_sha256'] = content_sha256(publication)
    checks = [{'name': 'p6_publication_integrity', 'passed': True,
               'detail': '完整冻结目标、全部子维度、来源、原文与重算计数已重新验收；仅发布允许的维度。'}]
    acceptance = {**accepted, 'status': 'passed' if accepted['complete'] else
        'partial' if accepted['status'] == 'partial' else 'insufficient_evidence' if accepted['status'] == 'insufficient_evidence' else 'failed',
        'parent_status': accepted['status'], 'message': STATE_LABELS[accepted['status']], 'checks': checks}
    return {'status': publication['status'], 'p6_publication': publication, 'task_acceptance': acceptance,
        'validation': {'status': 'passed', 'checks': checks}, 'answer': publication['answer_text'],
        'public_answer': public_answer(publication), 'result_ids': [],
        'answer_generation': {'generation_status': 'template', 'attempts': 0}}


def read_p6_publication(task):
    """历史读取要求服务端任务完整性封存；不能仅凭客户端 DTO 的 hash 授权。"""
    from data_analysis_agent.jobs.analysis_state import has_saved_integrity
    try:
        result = task['result']
        p = result['p6_publication']
        if not has_saved_integrity(task) or p['version'] != VERSION:
            return None
        saved_hash = p['publication_sha256']
        if saved_hash != content_sha256({k: v for k, v in p.items() if k != 'publication_sha256'}):
            return None
        expected_task = {'ok': 'succeeded', 'partial': 'partial', 'unsupported': 'succeeded', 'error': 'failed'}[p['status']]
        if (task['status'] != expected_task or result['status'] != p['status'] or p['scope'] != result['scope']
                or p['answer_text'] != publication_text(p)
                or result['task_acceptance']['complete'] != p['complete']):
            return None
        return p
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
