import type { AgentResult, AgentToolResult, AnalysisParameters } from '../../types';

export interface QueryIntent { kind: Exclude<AgentToolResult['kind'], 'context'>; parameters: Record<string, unknown> }
export interface QuerySnapshot {
  createdAt: string;
  effectiveQuestion: string;
  parentTurnId?: string;
  parentTaskId?: string;
  queries?: QueryIntent[];
  referenceTime?: string;
  conversationId?: string;
}
export interface QueryChoice { label: string; parameters: AnalysisParameters; query: QuerySnapshot }
export interface Clarification { message: string; choices: QueryChoice[]; resolved?: boolean }
export interface QueryParent { id: string; taskId?: string; question?: string; parameters: AnalysisParameters; selection?: AnalysisParameters; result?: AgentResult }

const aspects: Record<string, string> = { 质量: 'quality', 效果: 'effectiveness', 包装: 'packaging', 物流: 'logistics', 客服: 'service', 服务: 'service', 价格: 'price' };
const names: Record<QueryIntent['kind'], string> = { trend: '评论趋势', aspect_trend: '维度趋势', evidence: '原文证据',
  comparison: '商品对比', issues: '问题分布', quality: '数据质量', ratings: '星级分布', window_comparison: '时间窗口对比', positive_aspects: '正向维度', opinion_breakdown: '观点文本分析' };
const tools: Record<QueryIntent['kind'], string> = { trend: 'get_trend', aspect_trend: 'get_aspect_trend', evidence: 'get_evidence',
  comparison: 'compare_products', issues: 'get_top_issues', quality: 'get_data_quality', ratings: 'get_rating_distribution', window_comparison: 'compare_time_windows', positive_aspects: 'get_positive_aspects', opinion_breakdown: 'get_opinion_breakdown' };
const scopeKeys = ['source', 'dataset_version', 'model', 'base_url', 'prompt_version', 'date_from', 'date_to', 'baseline_from', 'baseline_to', 'as_of'] as const;

function copyScope(scope: AnalysisParameters): AnalysisParameters {
  // 历史任务还含服务端 model_identity 等字段，不能把它们重新作为客户端参数提交。
  return structuredClone(Object.fromEntries([...scopeKeys.filter(key => key in scope).map(key => [key, scope[key]]),
    ['product_ids', scope.product_ids]])) as unknown as AnalysisParameters;
}

// 只解析有确定含义的追问；其他指代先要求补充，避免凭关键词猜商品或日期。
function operation(text: string) {
  const value = text.replace(/[，。！？!?\s]/g, '');
  if (/^(?:那|那么|然后)?(?:差评|负面|缺点|投诉)(?:呢|吧)?$/.test(value)) return { type: 'sentiment' as const, value: 'negative' };
  if (/^(?:那|那么|然后)?(?:好评|正面|优点|满意方面)(?:呢|吧)?$/.test(value)) return { type: 'sentiment' as const, value: 'positive' };
  if (/^(?:哪个|哪一个|哪个方面).*(?:优先|先).*(?:解决|处理|改进)/.test(value)) return { type: 'priority' as const, value: '' };
  if (/前(?:三|3)个?月/.test(value)) return { type: 'ambiguous_range' as const, value: '' };
  const recent = value.match(/最近(?:半年|六个月|六个?月|三个月|3个月|90天)/);
  if (recent) return { type: 'range' as const, value: recent[0] };
  if (/(?:对比|比较|差别|区别|不同)/.test(value) && /这两款|这两个|它们|二者|两者/.test(value)) return { type: 'comparison' as const, value: '' };
  const aspect = value.match(/^(?:那|那么)?(?:只看|仅看|只查|换成|改成)?(质量|效果|包装|物流|客服|服务|价格)(?:问题|方面)?(?:呢)?$/)?.[1];
  if (aspect) return { type: 'aspect' as const, value: aspects[aspect] };
  if (/^(?:换成|改成|改为)?按(月|日|天)(?:看|查看|统计)?$/.test(value)) return { type: 'period' as const, value: value.includes('月') ? 'month' : 'day' };
  if (/^(?:继续(?:查|查看)?(?:原文|证据|下一页)|(?:查|查看)(?:原文|证据)|(?:原文|证据)?下一页)$/.test(value)) return { type: 'evidence' as const, value: '' };
  // 完整分析请求交给后端语义与能力校验，代词不等于缺少分析意图。
  if (/主要|集中|哪些方面|哪些地方|怎么样|如何|有什么问题|提及最多|吐槽|有什么不同|有什么变化|具体|细说|展开|啥|咋|夸/.test(value)) return { type: 'analysis' as const, value: '' };
  if (/^(继续|换成|改成|改为|只看|仅看|再看|再查)|这个|那个|这些|那些|它|上面|上述|同样|那.*呢[？?]?$/.test(value)) return { type: 'ambiguous' as const, value: '' };
  return undefined;
}

export function historyReference(text: string) {
  const match = text.match(/^(?:按|沿用|参考|回到|基于)?第([1-9]\d*|[一二三四五六七八九十])轮(?:分析|查询)?(?:的?范围)?[，,：:\s]*(.*)$/);
  if (!match) return undefined;
  const chinese = '一二三四五六七八九十';
  return { index: /^\d+$/.test(match[1]) ? Number(match[1]) : chinese.indexOf(match[1]) + 1, text: match[2] };
}

export const isFollowup = (text: string) => !!operation(text) || !!historyReference(text);

export function sameScope(a: AnalysisParameters, b: AnalysisParameters) {
  return scopeKeys.every(key => (a[key] ?? null) === (b[key] ?? null))
    && JSON.stringify([...a.product_ids].sort()) === JSON.stringify([...b.product_ids].sort());
}

function dateOnly(value: string) { return new Date(`${value}T00:00:00Z`); }
function isoDate(value: Date) { return value.toISOString().slice(0, 10); }
function shiftMonths(value: string, amount: number) {
  const date = dateOnly(value);
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + amount, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(date.getUTCDate(), lastDay));
  return isoDate(target);
}
function shiftDays(value: string, amount: number) {
  const date = dateOnly(value); date.setUTCDate(date.getUTCDate() + amount); return isoDate(date);
}
function rangeParameters(parameters: AnalysisParameters, label: string, referenceTime: Date): AnalysisParameters {
  const months = /半年|六个月|六个?月/.test(label) ? 6 : 3;
  const end = parameters.as_of ? parameters.as_of.slice(0, 10) : isoDate(referenceTime);
  const start = /90天/.test(label) ? shiftDays(end, -89) : shiftDays(shiftMonths(end, -months), 1);
  const days = Math.max(1, Math.round((dateOnly(end).getTime() - dateOnly(start).getTime()) / 86_400_000) + 1);
  const baselineTo = shiftDays(start, -1);
  const baselineFrom = shiftDays(baselineTo, -(days - 1));
  return { ...copyScope(parameters), date_from: start, date_to: end, baseline_from: baselineFrom, baseline_to: baselineTo };
}

export function resolveQuery(text: string, parameters: AnalysisParameters, parent?: QueryParent, referenceTime = new Date(), intentParent?: QueryParent): { query: QuerySnapshot; parameters?: AnalysisParameters; clarification?: Clarification } {
  const query: QuerySnapshot = { createdAt: new Date().toISOString(), effectiveQuestion: text };
  const clarify = (message: string, choices: QueryChoice[] = []) => ({ query, clarification: { message, choices } });
  const op = operation(text);
  if (!op) return { query };
  if (op.type === 'comparison') {
    const target = intentParent || parent;
    if (!target?.taskId && parameters.product_ids.length !== 2) return clarify('请明确“这两款”指哪两款商品，或先提出包含商品名称的比较问题。');
    if (target) { query.parentTurnId = target.id; query.parentTaskId = target.taskId; }
    return { query, parameters: copyScope(parameters) };
  }
  if (op.type === 'analysis' || (op.type === 'ambiguous' && (intentParent || parent)?.taskId)) {
    if (/另一款|另一个商品/.test(text) && parameters.product_ids.length < 2) return clarify('请指定另一款商品，或在当前范围选择两款商品后比较。');
    const target = intentParent || parent;
    if (target) { query.parentTurnId = target.id; query.parentTaskId = target.taskId; }
    // 有任务引用的口语交给后端补全或具体澄清；前端不据代词否决语义解析。
    // 只有全局选择未变化时才继承上轮的语言日期；已确认的新筛选优先。
    const scope = target && sameScope(parameters, target.selection || target.parameters) ? target.parameters : parameters;
    return { query, parameters: copyScope(scope) };
  }
  if (op.type === 'ambiguous_range') return clarify('“前三个月”可能指窗口开始的三个月，也可能指最近三个月。请明确起止日期或改问“最近三个月”。');
  if (op.type === 'priority') return clarify('现有统计缺少严重程度、业务影响与成本信息，不能只凭负面占比认定商业优先级。可先复核相关原文，再结合业务记录决定。');
  if (op.type === 'range') {
    if (/安装|制冷|静音|不是|不看/.test(text)) return { query };
    const next = rangeParameters(parameters, op.value, referenceTime);
    query.referenceTime = next.as_of || referenceTime.toISOString();
    query.effectiveQuestion = text.replace(/最近(?:半年|六个?月|三个?月|3个月|90天)/g, '本次范围');
    if (!/主要|集中|方面|原文|证据|星级|评分|对比|比较|趋势|变化|按[月日天]|优点|缺点|好评|差评|满意|喜欢|投诉|负面|正面/.test(query.effectiveQuestion)) {
      query.effectiveQuestion = '按月查看变化：' + query.effectiveQuestion;
    }
    if (/^(?:那|那么)?最近(?:半年|六个?月|三个?月|3个月|90天)(?:呢|吧)?[？?。]*$/.test(text.replace(/\s/g, ''))) {
      if (!parent?.result || parent.result.validation?.status !== 'passed') return clarify('请明确这段时间要查看的指标或维度。');
      const previous = Object.values(parent.result.results).filter(item => item.kind !== 'context');
      if (previous.length !== 1) return clarify('上轮有多个分析目标，请明确本次时间范围对应哪个指标或维度。');
      const item = previous[0];
      const intent: QueryIntent = { kind: item.kind as QueryIntent['kind'], parameters: { ...item.parameters, product_ids: [...next.product_ids], ...(item.kind === 'evidence' ? { page: 1 } : {}) } };
      query.parentTurnId = parent.id; query.parentTaskId = parent.taskId;
      query.queries = [intent];
      query.effectiveQuestion = `执行本轮已确认的只读查询\n${JSON.stringify({ tool: tools[intent.kind], parameters: intent.parameters })}`;
    }
    return { query, parameters: next };
  }
  if (['aspect', 'sentiment', 'period'].includes(op.type) && intentParent?.taskId && intentParent.taskId !== parent?.taskId) {
    query.parentTurnId = intentParent.id; query.parentTaskId = intentParent.taskId;
    return { query, parameters: copyScope(parameters) };
  }
  if (!parent?.result || parent.result.status !== 'ok' || parent.result.validation?.status !== 'passed') return clarify('没有可承接的成功查询。请补充要查询的内容（如“按日查看物流问题趋势”），并确认下方数据范围。');
  query.parentTurnId = parent.id;
  query.parentTaskId = parent.taskId;
  if (op.type === 'ambiguous') return clarify('这次追问的对象或范围不明确。请写明要分析的商品、维度或统计方式；商品和日期请在下方“切换范围”中确认。');
  const results = Object.values(parent.result.results).filter(item => item.kind !== 'context');
  let candidates: QueryIntent[] = [];
  let multipleEvidenceTargets = false;
  const inheritedParameters = parent.parameters;
  if (op.type === 'sentiment') {
    const kind = op.value === 'positive' ? 'positive_aspects' : 'issues';
    const previous = results.find(item => ['positive_aspects', 'issues', 'evidence', 'opinion_breakdown'].includes(item.kind));
    candidates = previous?.kind === 'opinion_breakdown' ? [{ kind: 'opinion_breakdown', parameters: { ...previous.parameters, sentiment: op.value } }]
      : previous?.kind === 'evidence' ? [{ kind: 'evidence', parameters: { ...previous.parameters, sentiment: op.value, page: 1 } }]
      : [{ kind, parameters: { product_ids: parent.parameters.product_ids, ...(previous?.parameters.aspect ? { aspect: previous.parameters.aspect } : {}) } }];
  } else if (op.type === 'aspect') {
    candidates = results.filter(item => ['evidence', 'aspect_trend', 'trend'].includes(item.kind)).map(item => ({
      kind: item.kind === 'evidence' ? 'evidence' : 'aspect_trend',
      parameters: { ...item.parameters, aspect: op.value, ...(item.kind === 'evidence' ? { page: 1 } : {}) },
    }));
    if (!candidates.length) candidates = results.filter(item => item.kind === 'issues' || item.kind === 'positive_aspects')
      .map(item => ({ kind: item.kind as QueryIntent['kind'], parameters: { ...item.parameters, aspect: op.value } }));
    if (!candidates.length) candidates = results.filter(item => item.kind === 'opinion_breakdown')
      .map(item => ({ kind: 'opinion_breakdown', parameters: { ...item.parameters, aspect: op.value, topic: null } }));
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
        sentiment: 'negative', window: 'current', page: 1, page_size: 3,
      } }));
      if (!candidates.length) return clarify('要查哪一类原文？请明确维度和情感，例如“查询物流负面评论的原文”。');
    }
  }
  // 同一查询可能因多次工具调用重复出现，保留一个可解释的选择。
  candidates = candidates.filter((item, index) => candidates.findIndex(other => JSON.stringify(other) === JSON.stringify(item)) === index);
  if (!candidates.length) return clarify('上轮没有可转换的趋势或星级统计。请补充要按日/月查看的指标，例如“按月查看物流问题趋势”。');
  const scopes = [{ label: '', parameters: sameScope(parameters, parent.selection || inheritedParameters) ? inheritedParameters : parameters }];
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
  return { query: choices[0].query, parameters: choices[0].parameters };
}
