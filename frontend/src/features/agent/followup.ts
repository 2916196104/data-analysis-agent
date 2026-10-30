import type { AgentResult, AgentToolResult, AnalysisParameters } from '../../types';

export interface QueryIntent { kind: Exclude<AgentToolResult['kind'], 'context'>; parameters: Record<string, unknown> }
export interface QuerySnapshot {
  createdAt: string;
  effectiveQuestion: string;
  parentTurnId?: string;
  parentTaskId?: string;
  queries?: QueryIntent[];
}
export interface QueryChoice { label: string; parameters: AnalysisParameters; query: QuerySnapshot }
export interface Clarification { message: string; choices: QueryChoice[]; resolved?: boolean }
export interface QueryParent { id: string; taskId?: string; parameters: AnalysisParameters; result?: AgentResult }

const aspects: Record<string, string> = { 质量: 'quality', 效果: 'effectiveness', 包装: 'packaging', 物流: 'logistics', 客服: 'service', 服务: 'service', 价格: 'price' };
const names: Record<QueryIntent['kind'], string> = { trend: '评论趋势', aspect_trend: '维度趋势', evidence: '原文证据',
  comparison: '商品对比', issues: '问题分布', quality: '数据质量', ratings: '星级分布', window_comparison: '时间窗口对比' };
const tools: Record<QueryIntent['kind'], string> = { trend: 'get_trend', aspect_trend: 'get_aspect_trend', evidence: 'get_evidence',
  comparison: 'compare_products', issues: 'get_top_issues', quality: 'get_data_quality', ratings: 'get_rating_distribution', window_comparison: 'compare_time_windows' };
const scopeKeys = ['source', 'dataset_version', 'model', 'base_url', 'prompt_version', 'date_from', 'date_to', 'baseline_from', 'baseline_to', 'as_of'] as const;

function copyScope(scope: AnalysisParameters): AnalysisParameters {
  // 历史任务还含服务端 model_identity 等字段，不能把它们重新作为客户端参数提交。
  return structuredClone(Object.fromEntries([...scopeKeys.filter(key => key in scope).map(key => [key, scope[key]]),
    ['product_ids', scope.product_ids]])) as unknown as AnalysisParameters;
}

// 只解析有确定含义的追问；其他指代先要求补充，避免凭关键词猜商品或日期。
function operation(text: string) {
  const value = text.replace(/[，。！？!?\s]/g, '');
  const aspect = value.match(/^(?:只看|仅看|只查|换成|改成)?(质量|效果|包装|物流|客服|服务|价格)(?:问题|方面)?$/)?.[1];
  if (aspect) return { type: 'aspect' as const, value: aspects[aspect] };
  if (/^(?:换成|改成|改为)?按(月|日|天)(?:看|查看|统计)?$/.test(value)) return { type: 'period' as const, value: value.includes('月') ? 'month' : 'day' };
  if (/^(?:继续(?:查|查看)?(?:原文|证据)|(?:查|查看)(?:原文|证据)|(?:原文|证据)?下一页)$/.test(value)) return { type: 'evidence' as const, value: '' };
  if (/^(继续|换成|改成|改为|只看|仅看|再看|再查)|这个|那个|这些|那些|它|上面|上述|同样|那.*呢[？?]?$/.test(value)) return { type: 'ambiguous' as const, value: '' };
  return undefined;
}

export const isFollowup = (text: string) => !!operation(text);

export function sameScope(a: AnalysisParameters, b: AnalysisParameters) {
  return scopeKeys.every(key => (a[key] ?? null) === (b[key] ?? null))
    && JSON.stringify([...a.product_ids].sort()) === JSON.stringify([...b.product_ids].sort());
}

export function resolveQuery(text: string, parameters: AnalysisParameters, parent?: QueryParent): { query: QuerySnapshot; clarification?: Clarification } {
  const query: QuerySnapshot = { createdAt: new Date().toISOString(), effectiveQuestion: text };
  const clarify = (message: string, choices: QueryChoice[] = []) => ({ query, clarification: { message, choices } });
  const op = operation(text);
  if (!op) return { query };
  if (!parent?.result || parent.result.status !== 'ok') return clarify('没有可承接的成功查询。请补充要查询的内容（如“按日查看物流问题趋势”），并确认下方数据范围。');
  query.parentTurnId = parent.id;
  query.parentTaskId = parent.taskId;
  if (op.type === 'ambiguous') return clarify('这次追问的对象或范围不明确。请写明要分析的商品、维度或统计方式；商品和日期请在下方“切换范围”中确认。');
  const results = Object.values(parent.result.results).filter(item => item.kind !== 'context');
  let candidates: QueryIntent[] = [];
  let multipleEvidenceTargets = false;
  if (op.type === 'aspect') {
    candidates = results.filter(item => ['evidence', 'aspect_trend', 'trend'].includes(item.kind)).map(item => ({
      kind: item.kind === 'evidence' ? 'evidence' : 'aspect_trend',
      parameters: { ...item.parameters, aspect: op.value, ...(item.kind === 'evidence' ? { page: 1 } : {}) },
    }));
    if (!candidates.length) return clarify('上轮查询不支持直接按维度筛选。请明确要查看趋势还是原文，例如“按月查看物流问题趋势”或“查询物流负面评论的原文”。');
  } else if (op.type === 'period') {
    candidates = results.filter(item => ['trend', 'aspect_trend', ...(op.value === 'month' ? ['ratings'] : [])].includes(item.kind))
      .map(item => ({ kind: item.kind as QueryIntent['kind'], parameters: { ...item.parameters,
        ...(item.kind === 'ratings' ? { group_by: 'month' } : { period: op.value }) } }));
  } else {
    const evidencePages = results.filter(item => item.kind === 'evidence');
    const filterKey = (item: AgentToolResult) => JSON.stringify(Object.entries(item.parameters).filter(([key]) => key !== 'page').sort(([a], [b]) => a.localeCompare(b)));
    // 同轮可能已查多页；相同筛选只能从最后一页继续，不能再次请求已展示的页。
    const evidence = evidencePages.filter(item => !evidencePages.some(other => filterKey(item) === filterKey(other) && other.data.page > item.data.page));
    if (evidence.length) {
      multipleEvidenceTargets = evidence.length > 1;
      candidates = evidence.filter(item => item.data.page * Number(item.parameters.page_size ?? 5) < item.data.total)
        .map(item => ({ kind: 'evidence', parameters: { ...item.parameters, page: item.data.page + 1 } }));
      if (!candidates.length) return clarify('上轮原文已经到最后一页。可以在下方调整范围，或明确指定新的维度重新查询原文。');
    } else {
      candidates = results.filter(item => item.kind === 'aspect_trend').map(item => ({ kind: 'evidence', parameters: {
        product_ids: item.parameters.product_ids ?? [], aspect: item.parameters.aspect ?? null,
        sentiment: 'negative', window: 'current', page: 1, page_size: 5,
      } }));
      if (!candidates.length) return clarify('要查哪一类原文？请明确维度和情感，例如“查询物流负面评论的原文”。');
    }
  }
  // 同一查询可能因多次工具调用重复出现，保留一个可解释的选择。
  candidates = candidates.filter((item, index) => candidates.findIndex(other => JSON.stringify(other) === JSON.stringify(item)) === index);
  if (!candidates.length) return clarify('上轮没有可转换的趋势或星级统计。请补充要按日/月查看的指标，例如“按月查看物流问题趋势”。');
  const scopes = sameScope(parameters, parent.parameters) ? [{ label: '', parameters: parent.parameters }]
    : [{ label: '沿用上轮范围 · ', parameters: parent.parameters }, { label: '使用当前范围 · ', parameters }];
  const choices: QueryChoice[] = scopes.flatMap(scope => candidates.map(intent => {
    const selected = structuredClone(intent);
    // 切换范围时使用新的商品范围，并重新开始证据分页。
    if (!sameScope(scope.parameters, parent.parameters)) {
      selected.parameters.product_ids = [...scope.parameters.product_ids];
      if (selected.kind === 'evidence') selected.parameters.page = 1;
    }
    const params = selected.parameters;
    const aspectName = Object.keys(aspects).find(name => aspects[name] === params.aspect);
    const sentiment = ({ negative: '负面', positive: '正面', neutral: '中性', all: '全部情感' } as Record<string, string>)[String(params.sentiment)] ?? '';
    const products = Array.isArray(params.product_ids) && params.product_ids.length ? params.product_ids.join('、') : '已选全部商品';
    const label = `${scope.label}${names[selected.kind]}${aspectName ? ` · ${aspectName}` : ''}${params.period ? ` · 按${params.period === 'month' ? '月' : '日'}` : ''}${selected.kind === 'evidence' ? ` · ${sentiment} · ${params.window === 'baseline' ? '基线' : '当前'} · 第 ${params.page} 页` : ''} · ${products}`;
    const effectiveQuestion = `执行本轮已确认的只读查询，仅查询下面的工具和参数，不重复上轮其他查询。数据集、标注版本、商品及日期以本次绑定范围为准。\n${JSON.stringify({ tool: tools[selected.kind], parameters: selected.parameters })}`;
    return { label, parameters: copyScope(scope.parameters), query: { ...query, effectiveQuestion, queries: [selected] } };
  }));
  if (choices.some(choice => choice.query.effectiveQuestion.length > 4000)) return clarify('追问包含的商品筛选过长。请缩小商品范围后重新提出完整问题。');
  if (choices.length > 1 || multipleEvidenceTargets) return clarify(scopes.length > 1
    ? '当前范围与上轮不同。请选择沿用上轮范围，还是在当前范围重新查询；若有多个查询目标，也请一并选择。'
    : '上轮包含多个查询目标。请选择本轮要继续的目标（已到末页的原文不再列出）。', choices);
  return { query: choices[0].query };
}
