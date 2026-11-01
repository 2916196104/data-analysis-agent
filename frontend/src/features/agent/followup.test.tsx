import { act, cleanup, render, renderHook, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { useTask } from '../../useTask';
import { emptyTaskState } from '../../task-state';
import type { AgentResult, AgentToolResult, AnalysisParameters, Task } from '../../types';
import { historyReference, resolveQuery, type QueryParent } from './followup';
import { useAgentChat } from './useAgentChat';
import { CHAT_KEY, readChatSession } from './chat-state';
import ChatMessage from './ChatMessage';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const scope: AnalysisParameters = { source: 'amazon', dataset_version: 'v1', model: 'model', base_url: 'offline', prompt_version: 'v1',
  product_ids: ['A'], date_from: '2023-02-01', date_to: '2023-02-28', baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const trend = (aspect = 'packaging'): AgentToolResult => ({ result_id: 'r1', kind: 'aspect_trend', parameters: { aspect, period: 'day', product_ids: ['A'] }, data: {
  scope, period: 'day', aspects: { [aspect]: aspect }, buckets: [], summary: { review_count: 12, eligible_text_reviews: 12,
    successful_reviews: 12, failed_reviews: 0, unprocessed_reviews: 0, negative_review_count: 2, labeling_coverage: 1, negative_rate: 2 / 12, aspects: {} },
} });
const evidence = (page = 1, total = 12): AgentToolResult => ({ result_id: 'r2', kind: 'evidence',
  parameters: { aspect: 'logistics', sentiment: 'negative', window: 'baseline', product_ids: ['A'], page, page_size: 5 }, data: { page, total, items: [] } });
const result = (...items: AgentToolResult[]): AgentResult => ({ agent: true, status: 'ok', scope, question: 'first', snapshot_at: '2026-10-30T00:00:00Z', validation: { status: 'passed', checks: [] },
  result_ids: items.map(item => item.result_id), results: Object.fromEntries(items.map(item => [item.result_id, item])), model_calls: 1, tool_calls: items.length, elapsed_seconds: 1, trace: [] });
const parent = (...items: AgentToolResult[]): QueryParent => ({ id: 'turn-1', taskId: 'task-1', parameters: scope, result: result(...items) });
const task = (id: string, value: AgentResult): Task => ({ task_id: id, kind: 'analysis', status: 'succeeded', parameters: { ...scope, question: '查询包装问题趋势', get conversation_id() { return JSON.parse(localStorage.getItem(CHAT_KEY) || '{}').activeSessionId; } },
  result: value as unknown as Task['result'], parent_task_id: null, error: null, created_at: '', started_at: '', finished_at: '', updated_at: '',
  progress: { stage: 'completed', completed: 1, total: 1, unit: 'task' } });

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? task(id, result(trend())) : null, refresh: vi.fn() }));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('追问解析使用真实查询参数', () => {
  it('上轮明确比较扩大过商品范围，普通新问题仍使用页面所选商品', () => {
    const expanded = { ...parent(trend()), selection: scope, parameters: { ...scope, product_ids: ['A', 'B'] } };
    const next = resolveQuery('好评主要集中在哪些方面？', scope, expanded);
    expect(next.parameters?.product_ids).toEqual(['A']);
    expect(expanded.parameters.product_ids).toEqual(['A', 'B']);
    const monthly = resolveQuery('换成按月', scope, expanded);
    expect(monthly.parameters?.product_ids).toEqual(['A']);
    expect(monthly.query.queries?.[0].parameters.product_ids).toEqual(['A']);
  });
  it('A：使用效果细分交给后端补全情感，并关联成功父任务', () => {
    const positive = { result_id: 'positive', kind: 'positive_aspects', parameters: { product_ids: ['A'] }, data: {} } as unknown as AgentToolResult;
    const next = resolveQuery('使用效果主要集中在哪些地方？', scope, parent(positive));
    expect(next.clarification).toBeUndefined();
    expect(next.query.parentTaskId).toBe('task-1');
    expect(next.query.effectiveQuestion).toBe('使用效果主要集中在哪些地方？');
    expect(next.parameters?.product_ids).toEqual(['A']);
  });

  it('C：那物流方面呢保留正向汇总或正向细分目标', () => {
    const positive = { result_id: 'positive', kind: 'positive_aspects', parameters: { product_ids: ['A'] }, data: {} } as unknown as AgentToolResult;
    expect(resolveQuery('那物流方面呢？', scope, parent(positive)).query.queries?.[0]).toMatchObject({
      kind: 'positive_aspects', parameters: { aspect: 'logistics', product_ids: ['A'] } });
    const detail = { result_id: 'detail', kind: 'opinion_breakdown', parameters: { product_ids: ['A'], aspect: 'effectiveness', sentiment: 'positive', topic: '制冷' }, data: {} } as unknown as AgentToolResult;
    expect(resolveQuery('那物流方面呢？', scope, parent(detail)).query.queries?.[0]).toMatchObject({
      kind: 'opinion_breakdown', parameters: { aspect: 'logistics', sentiment: 'positive', topic: null } });
  });
  it.each(['美的空调的使用效果主要集中在哪些方面？', '美的空调的使用效果主要集中在哪些地方？',
    '这款空调用起来怎么样？', '它的制冷效果怎么样？', '消费者主要吐槽什么？', '好评主要集中在哪些方面？',
    '使用效果里面，哪些问题被提及最多？', '和上一时间段相比有什么变化？', '这个商品有什么问题？'])('明确口语问题直接继承当前商品范围：%s', text => {
    for (const previous of [undefined, parent(trend())]) {
      const next = resolveQuery(text, scope, previous);
      expect(next.clarification).toBeUndefined();
      expect(next.parameters ?? scope).toMatchObject({ source: 'amazon', dataset_version: 'v1', product_ids: ['A'] });
      expect(next.query.effectiveQuestion).toBe(text);
    }
  });

  it('缺少另一款商品时澄清，已有明确两款时允许后端规划比较', () => {
    const text = '换成另一款商品后，用户的评价有什么不同？';
    expect(resolveQuery(text, scope, parent(trend())).clarification?.message).toContain('另一款商品');
    expect(resolveQuery(text, { ...scope, product_ids: ['A', 'B'] }, parent(trend())).clarification).toBeUndefined();
    expect(resolveQuery('对比这两款商品的差别', { ...scope, product_ids: ['A', 'B'] }).clarification).toBeUndefined();
  });
  it('好评后那差评呢会切换负向维度工具并继承冻结范围', () => {
    const positive = { result_id: 'positive', kind: 'positive_aspects', parameters: { product_ids: ['A'] }, data: {} } as unknown as AgentToolResult;
    const resolved = resolveQuery('那差评呢', scope, parent(positive));
    expect(resolved.clarification).toBeUndefined();
    expect(resolved.query.queries?.[0]).toEqual({ kind: 'issues', parameters: { product_ids: ['A'] } });
    expect(resolved.query.effectiveQuestion).toContain('get_top_issues');
    expect(resolved.query.effectiveQuestion).toContain('"product_ids":["A"]');
    expect(JSON.parse(resolved.query.effectiveQuestion.split('\n')[1]).question).toBe('那差评呢');
  });

  it('问题分布后只看物流会收敛到物流维度而不重复六维问题', () => {
    const issues = { result_id: 'issues', kind: 'issues', parameters: { product_ids: ['A'] }, data: {} } as unknown as AgentToolResult;
    const resolved = resolveQuery('只看物流方面', scope, parent(issues));
    expect(resolved.clarification).toBeUndefined();
    expect(resolved.query.queries?.[0]).toMatchObject({ kind: 'issues', parameters: { aspect: 'logistics', product_ids: ['A'] } });
  });

  it('最近半年继承商品和数据集并重算当前与基线窗口，前三个月请求澄清', () => {
    const current = { ...scope, date_from: '2023-01-01', date_to: '2023-12-31', baseline_from: '2022-01-01', baseline_to: '2022-12-31' };
    const resolved = resolveQuery('最近半年物流问题怎么样', current, parent(trend('logistics')), new Date('2023-12-31T12:00:00Z'));
    expect(resolved.clarification).toBeUndefined();
    expect(resolved.parameters).toMatchObject({ date_from: '2023-07-01', date_to: '2023-12-31' });
    expect(resolved.query.effectiveQuestion).toContain('物流');
    const choice = resolveQuery('前三个月', current, parent(trend('logistics')));
    expect(choice.clarification?.message).toContain('前三个月');
  });

  it('缺少严重程度和业务影响时不把趋势直接解释成优先级', () => {
    const resolved = resolveQuery('哪个更值得优先解决？', scope, parent(trend('packaging'), trend('logistics')));
    expect(resolved.clarification?.message).toContain('严重程度');
    expect(resolved.clarification?.choices).toEqual([]);
  });

  it('正面维度追问保留正面类型，证据切换情感重置页码但保留 page_size', () => {
    const positive = { result_id: 'positive', kind: 'positive_aspects', parameters: { product_ids: ['A'] }, data: {} } as unknown as AgentToolResult;
    expect(resolveQuery('物流方面呢', scope, parent(positive)).query.queries?.[0]).toMatchObject({ kind: 'positive_aspects', parameters: { aspect: 'logistics' } });
    expect(resolveQuery('那好评呢', scope, parent(evidence(2))).query.queries?.[0]).toMatchObject({ kind: 'evidence', parameters: { sentiment: 'positive', page: 1, page_size: 5, window: 'baseline' } });
  });

  it.each([{ product_ids: ['B'] }, { dataset_version: 'v2' }, { model: 'another-model' }, { date_to: '2023-02-20' }])('范围变更 %s 使用当前已确认范围，证据游标从第一页开始', changes => {
    const next = resolveQuery('下一页', { ...scope, ...changes }, parent(evidence(2)));
    expect(next.clarification).toBeUndefined();
    expect(next.parameters).toMatchObject(changes);
    expect(next.query.queries?.[0].parameters.page).toBe(1);
  });

  it('首轮最近半年按 UTC 参考时间计算，90 天和月底不会偷偷改为历史窗口', () => {
    const now = new Date('2024-03-31T12:00:00Z');
    const half = resolveQuery('最近半年物流问题怎么样', scope, undefined, now);
    expect(half.parameters).toMatchObject({ date_from: '2023-10-01', date_to: '2024-03-31', product_ids: ['A'] });
    const days = resolveQuery('最近90天物流问题怎么样', scope, undefined, now);
    expect(days.parameters).toMatchObject({ date_from: '2024-01-02', date_to: '2024-03-31' });
    expect(half.query.referenceTime).toBe(now.toISOString());
    const failed = { ...result(trend()), validation: { status: 'failed' as const, checks: [] } };
    expect(resolveQuery('那差评呢', scope, { ...parent(trend()), result: failed }).clarification).toBeTruthy();
  });

  it('明确最近三个月继承物流趋势；显式历史引用提取轮次和追问', () => {
    const resolved = resolveQuery('那最近三个月呢', scope, parent(trend('logistics')), new Date('2024-03-31T12:00:00Z'));
    expect(resolved.parameters).toMatchObject({ date_from: '2024-01-01', date_to: '2024-03-31' });
    expect(resolved.query.queries?.[0]).toMatchObject({ kind: 'aspect_trend', parameters: { aspect: 'logistics', period: 'day' } });
    expect(historyReference('沿用第1轮范围，只看物流')).toEqual({ index: 1, text: '只看物流' });
    expect(historyReference('参考第二轮：那差评呢')).toEqual({ index: 2, text: '那差评呢' });
  });

  it('时间限定保留好评和证据意图，不自动追加无关趋势', () => {
    const positive = resolveQuery('最近半年好评主要集中在哪些方面', scope);
    expect(positive.parameters).toBeTruthy();
    expect(positive.query.effectiveQuestion).toContain('好评');
    expect(positive.query.effectiveQuestion).not.toContain('按月');
    const evidenceQuery = resolveQuery('最近半年给我3条物流差评原文', scope);
    expect(evidenceQuery.query.effectiveQuestion).toContain('3条物流差评原文');
    expect(evidenceQuery.query.effectiveQuestion).not.toContain('变化');
  });

  it('依次缩小到物流、换成按月、查询原文时保留商品及维度', () => {
    const first = resolveQuery('只看物流', scope, parent(trend()));
    expect(first.clarification).toBeUndefined();
    expect(first.query.queries?.[0].parameters).toEqual({ aspect: 'logistics', period: 'day', product_ids: ['A'] });
    const logistics = trend('logistics');
    const monthly = resolveQuery('换成按月', scope, parent(logistics));
    expect(monthly.query.queries?.[0].parameters).toMatchObject({ aspect: 'logistics', period: 'month', product_ids: ['A'] });
    const original = resolveQuery('继续查原文', scope, parent(logistics));
    expect(original.query.queries?.[0]).toEqual({ kind: 'evidence', parameters: {
      aspect: 'logistics', product_ids: ['A'], sentiment: 'negative', window: 'current', page: 1, page_size: 3,
    } });
    expect(original.query.parentTaskId).toBe('task-1');
    expect(logistics.parameters.period).toBe('day');
  });

  it('继续原文翻页保留基线和情感，到末页停止而不是重复查询', () => {
    const nextExplicit = resolveQuery('继续查看下一页', scope, parent(evidence()));
    expect(nextExplicit.query.queries?.[0].parameters).toEqual({ ...evidence().parameters, page: 2 });
    const next = resolveQuery('继续查原文', scope, parent(evidence()));
    expect(next.query.queries?.[0].parameters).toEqual({ ...evidence().parameters, page: 2 });
    expect(resolveQuery('下一页', scope, parent(evidence(3))).clarification?.message).toContain('最后一页');
    expect(resolveQuery('继续查原文', scope, parent(evidence(1, 0))).clarification?.choices).toEqual([]);
    const multiPage = resolveQuery('继续查原文', scope, parent({ ...evidence(1), result_id: 'page1' }, evidence(2)));
    expect(multiPage.clarification).toBeUndefined();
    expect(multiPage.query.queries?.[0].parameters.page).toBe(3);
  });

  it('首轮追问和失败游标先澄清，有上下文的未知指代交给后端', () => {
    for (const text of ['只看物流', '换成按月', '继续查原文']) expect(resolveQuery(text, scope).clarification).toBeTruthy();
    expect(resolveQuery('那它呢？', scope).clarification).toBeTruthy();
    expect(resolveQuery('那它呢？', scope, parent(trend()))).toMatchObject({
      query: { effectiveQuestion: '那它呢？', parentTaskId: 'task-1' }, parameters: scope });
    expect(resolveQuery('只看物流', scope, { ...parent(trend()), result: { ...result(trend()), status: 'error' } }).clarification).toBeTruthy();
    expect(resolveQuery('按月查看包装和物流问题变化', scope).query.effectiveQuestion).toBe('按月查看包装和物流问题变化');
  });

  it('多个查询目标要求选择，单一目标的范围变化使用当前范围并重置页码', () => {
    const multiple = resolveQuery('只看物流', scope, parent(trend(), evidence()));
    expect(multiple.clarification?.choices).toHaveLength(2);
    const changed = { ...scope, dataset_version: 'v2', product_ids: ['B'] };
    const resolved = resolveQuery('继续查原文', changed, parent(evidence(2)));
    expect(resolved.clarification).toBeUndefined();
    expect(resolved.parameters?.dataset_version).toBe('v2');
    expect(resolved.query.queries?.[0].parameters).toMatchObject({ product_ids: ['B'], page: 1 });
    changed.product_ids.push('C');
    expect(resolved.parameters?.product_ids).toEqual(['B']);
  });

  it('从服务端历史任务承接旧范围时不重新提交服务端模型身份字段', () => {
    const restored = { ...scope, model_identity: { model: 'server-model' }, question: 'historical' };
    const resolution = resolveQuery('换成按月', { ...scope, product_ids: ['B'] }, { ...parent(trend()), parameters: restored });
    expect(resolution.parameters).toEqual({ ...scope, product_ids: ['B'] });
  });
});

describe('追问提交、澄清和快照恢复', () => {
  it('含代词的口语下钻携带父任务，不被前端泛化澄清截断', async () => {
    const first = task('focused', result(trend('effectiveness')));
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id === 'focused' ? first : id ? task(id, result(trend())) : null, refresh: vi.fn() }));
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: first, reused: false })
      .mockResolvedValueOnce({ task: task('detail', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '按月看使用效果变化'); });
    await act(async () => { await hook.result.current.send(scope, '那再往里挖挖呢？'); });
    expect(submit.mock.calls[1][0]).toMatchObject({ parameters: {
      question: '那再往里挖挖呢？', context_task_id: 'focused', product_ids: ['A'] } });
    expect(hook.result.current.session.turns.at(-1)?.clarification).toBeUndefined();
  });

  it('未命中前端关键词的口语仍携带任务引用，交给后端结构化补全', async () => {
    const first = task('focused', result(trend('effectiveness')));
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id === 'focused' ? first : id ? task(id, result(trend())) : null, refresh: vi.fn() }));
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: first, reused: false })
      .mockResolvedValueOnce({ task: task('oral', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '按月看使用效果变化'); });
    await act(async () => { await hook.result.current.send(scope, '再往里挖挖呗'); });
    expect(submit.mock.calls[1][0]).toMatchObject({ parameters: { question: '再往里挖挖呗', context_task_id: 'focused' } });
    expect(submit.mock.calls[1][0].parameters).not.toHaveProperty('messages');
    expect(hook.result.current.session.turns.at(-1)?.clarification).toBeUndefined();
  });
  it('D：失败轮次保留用户比较对象引用，未验证结果不会成为成功上下文', async () => {
    const question = '美的空调与小米空调的优缺点都有哪些？';
    const failed: Task = { ...task('failed-comparison', result(trend())), status: 'failed',
      parameters: { ...scope, question, get conversation_id() { return JSON.parse(localStorage.getItem(CHAT_KEY) || '{}').activeSessionId; } }, result: { agent: true, status: 'error', results: { unsafe: { data: { review_count: 999999 } } } },
      error: { code: 'timeout', message: '模拟失败' } };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id === failed.task_id ? failed : id ? task(id, result(trend())) : null, refresh: vi.fn() }));
    vi.spyOn(api, 'task').mockResolvedValue(failed);
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: failed, reused: false })
      .mockResolvedValueOnce({ task: task('comparison-next', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, question); });
    expect(hook.result.current.session.context).toBeUndefined();
    expect(hook.result.current.session.intent).toMatchObject({ task_id: failed.task_id, question });
    expect(readChatSession().session.intent?.question).toBe(question);
    await act(async () => { await hook.result.current.send(scope, '对比这两款商品的差别。'); });
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1][0]).toMatchObject({ parameters: {
      question: '对比这两款商品的差别。', context_task_id: failed.task_id, product_ids: ['A'] } });
    expect(hook.result.current.session.turns.at(-1)?.clarification).toBeUndefined();
    expect(JSON.stringify(hook.result.current.session.intent)).not.toContain('999999');
  });
  it('自包含的问题不依赖历史读取，携带会话引用且以当前商品为准', async () => {
    localStorage.setItem(CHAT_KEY, JSON.stringify({ turns: [{ kind: 'analysis', id: 'old', taskId: 'old', question: '趋势', parameters: scope, requestKey: 'old' }] }));
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: null, refresh: vi.fn() }));
    vi.spyOn(api, 'task').mockRejectedValue(new Error('历史离线'));
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({ task: task('next', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    act(() => hook.result.current.release());
    await act(async () => { await hook.result.current.send({ ...scope, product_ids: ['B'] }, '这个商品有什么问题？'); });
    expect(submit.mock.calls[0][0]).toMatchObject({ parameters: { product_ids: ['B'], conversation_id: expect.any(String), question: '这个商品有什么问题？' } });
    expect(hook.result.current.session.turns.at(-1)?.clarification).toBeUndefined();
  });
  it('成功任务保存轻量上下文，失败任务不会覆盖最近成功上下文', async () => {
    const success = task('success', result(trend('logistics')));
    const failed = { ...task('failed', result(trend())), status: 'failed', result: { agent: true, status: 'error' } } as Task;
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id === 'success' ? success : id === 'failed' ? failed : null, refresh: vi.fn() }));
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: success, reused: false })
      .mockResolvedValueOnce({ task: failed, reused: false }).mockResolvedValueOnce({ task: task('third', result(trend())), reused: false });
    vi.spyOn(api, 'task').mockResolvedValue(success);
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '查询趋势'); });
    const firstContext = structuredClone(hook.result.current.session.context);
    await act(async () => { await hook.result.current.send(scope, '再次查询趋势'); });
    expect(hook.result.current.session.context).toEqual(firstContext);
    expect(firstContext).toMatchObject({ last_successful_task_id: 'success', dataset_id: 'amazon', product_ids: ['A'], aspect: 'logistics' });
    await act(async () => { await hook.result.current.send(scope, '只看物流'); });
    expect(hook.result.current.session.turns.at(-1)?.query?.parentTaskId).toBe('failed');
    expect(submit).toHaveBeenCalledTimes(3);
  });

  it('读取父任务期间范围改变会撤销迟到追问，不提交旧范围', async () => {
    localStorage.setItem(CHAT_KEY, JSON.stringify({ turns: [{ kind: 'analysis', id: 'old', taskId: 'old-task', question: '趋势', parameters: scope, requestKey: 'key' }] }));
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: null, refresh: vi.fn() }));
    let finish!: (value: Task) => void;
    vi.spyOn(api, 'task').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const submit = vi.spyOn(api, 'submit');
    const hook = renderHook(({ key }) => useAgentChat(undefined, key), { initialProps: { key: 'dataset-A' } });
    act(() => hook.result.current.release());
    let pending!: Promise<boolean>;
    act(() => { pending = hook.result.current.send(scope, '只看物流'); });
    hook.rerender({ key: 'dataset-B' });
    await act(async () => { finish(task('old-task', result(trend()))); expect(await pending).toBe(false); });
    expect(submit).not.toHaveBeenCalled();
  });

  it('显式引用旧历史不能悄悄覆盖当前选择范围', async () => {
    const first = task('first', result(trend()));
    localStorage.setItem(CHAT_KEY, JSON.stringify({ turns: [{ kind: 'analysis', id: 'first-turn', taskId: 'first', question: '趋势', parameters: scope, requestKey: 'key' }] }));
    vi.spyOn(api, 'task').mockResolvedValue(first);
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({ task: task('new', result(trend('logistics'))), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send({ ...scope, product_ids: ['B'] }, '沿用第1轮范围，只看物流'); });
    expect(submit).not.toHaveBeenCalled();
    expect(hook.result.current.activeTurn?.parameters.product_ids).toEqual(['B']);
    expect(hook.result.current.activeTurn?.clarification).toBeTruthy();
  });

  it('缺少上文的澄清不提交任务，刷新保留澄清且不会显示失败重试', async () => {
    const submit = vi.spyOn(api, 'submit');
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '只看物流'); });
    const turn = hook.result.current.session.turns[0];
    render(<ChatMessage turn={turn} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(screen.getByText('需要澄清')).toBeTruthy();
    expect(screen.queryByText('重试提交')).toBeNull();
    expect(submit).not.toHaveBeenCalled();
    expect(readChatSession().session.turns[0]).toEqual(turn);
    expect(readChatSession().error).toBe(false);
  });

  it('提交完整追问、关联父轮，失败后使用相同快照和幂等键重试', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: task('first', result(trend())), reused: false })
      .mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({ task: task('second', result(trend('logistics'))), reused: true });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '查询包装趋势'); });
    const first = structuredClone(hook.result.current.session.turns[0]);
    await act(async () => { await hook.result.current.send(scope, '只看物流'); });
    const followup = hook.result.current.session.turns[1];
    expect(followup.query?.parentTurnId).toBe(first.id);
    expect(submit.mock.calls[1][0]).toMatchObject({ parameters: { question: expect.stringContaining('"aspect":"logistics"') } });
    await act(async () => { await hook.result.current.retry(followup); });
    expect(submit.mock.calls[2]).toEqual(submit.mock.calls[1]);
    expect(hook.result.current.session.turns[0]).toEqual(first);
    expect(readChatSession().session.turns[1].query).toEqual(followup.query);
  });

  it('范围变更后提示重新明确问题，旧轮快照不变', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({ task: task('first', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '查询趋势'); });
    const before = structuredClone(hook.result.current.session.turns[0]);
    await act(async () => { await hook.result.current.send({ ...scope, product_ids: ['B'] }, '换成按月'); });
    expect(submit).toHaveBeenCalledTimes(1);
    expect(hook.result.current.activeTurn?.parameters.product_ids).toEqual(['B']);
    expect(hook.result.current.session.turns[0]).toEqual(before);
    expect(hook.result.current.session.turns[1].clarification?.message).toContain('当前选择范围');
    expect(JSON.parse(localStorage.getItem(CHAT_KEY)!).sessions[0].turns).toHaveLength(2);
  });

  it('无法读取历史结果时不猜测，并保留可见澄清', async () => {
    localStorage.setItem(CHAT_KEY, JSON.stringify({ turns: [{ kind: 'analysis', id: 'old', taskId: 'old', question: '趋势', parameters: scope, requestKey: 'old' }] }));
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: null, error: '', refresh: vi.fn() }));
    const submit = vi.spyOn(api, 'submit');
    vi.spyOn(api, 'task').mockRejectedValue(new Error('离线'));
    const hook = renderHook(() => useAgentChat());
    act(() => hook.result.current.release());
    await act(async () => { await hook.result.current.send(scope, '继续查原文'); });
    expect(submit).not.toHaveBeenCalled();
    expect(hook.result.current.session.turns.at(-1)?.clarification?.message).toContain('无法读取上轮查询');
  });
});
