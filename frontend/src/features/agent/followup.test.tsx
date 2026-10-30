import { act, cleanup, render, renderHook, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { useTask } from '../../useTask';
import { emptyTaskState } from '../../task-state';
import type { AgentResult, AgentToolResult, AnalysisParameters, Task } from '../../types';
import { resolveQuery, type QueryParent } from './followup';
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
const result = (...items: AgentToolResult[]): AgentResult => ({ agent: true, status: 'ok', scope, question: 'first', snapshot_at: '2026-10-30T00:00:00Z',
  results: Object.fromEntries(items.map(item => [item.result_id, item])), model_calls: 1, tool_calls: items.length, elapsed_seconds: 1, trace: [] });
const parent = (...items: AgentToolResult[]): QueryParent => ({ id: 'turn-1', taskId: 'task-1', parameters: scope, result: result(...items) });
const task = (id: string, value: AgentResult): Task => ({ task_id: id, kind: 'analysis', status: 'succeeded', parameters: { ...scope, question: '查询包装问题趋势' },
  result: value as unknown as Task['result'], parent_task_id: null, error: null, created_at: '', started_at: '', finished_at: '', updated_at: '',
  progress: { stage: 'completed', completed: 1, total: 1, unit: 'task' } });

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? task(id, result(trend())) : null, refresh: vi.fn() }));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('追问解析使用真实查询参数', () => {
  it('依次缩小到物流、换成按月、查询原文时保留商品及维度', () => {
    const first = resolveQuery('只看物流', scope, parent(trend()));
    expect(first.clarification).toBeUndefined();
    expect(first.query.queries?.[0].parameters).toEqual({ aspect: 'logistics', period: 'day', product_ids: ['A'] });
    const logistics = trend('logistics');
    const monthly = resolveQuery('换成按月', scope, parent(logistics));
    expect(monthly.query.queries?.[0].parameters).toMatchObject({ aspect: 'logistics', period: 'month', product_ids: ['A'] });
    const original = resolveQuery('继续查原文', scope, parent(logistics));
    expect(original.query.queries?.[0]).toEqual({ kind: 'evidence', parameters: {
      aspect: 'logistics', product_ids: ['A'], sentiment: 'negative', window: 'current', page: 1, page_size: 5,
    } });
    expect(original.query.parentTaskId).toBe('task-1');
    expect(logistics.parameters.period).toBe('day');
  });

  it('继续原文翻页保留基线和情感，到末页停止而不是重复查询', () => {
    const next = resolveQuery('继续查原文', scope, parent(evidence()));
    expect(next.query.queries?.[0].parameters).toEqual({ ...evidence().parameters, page: 2 });
    expect(resolveQuery('下一页', scope, parent(evidence(3))).clarification?.message).toContain('最后一页');
    expect(resolveQuery('继续查原文', scope, parent(evidence(1, 0))).clarification?.choices).toEqual([]);
    const multiPage = resolveQuery('继续查原文', scope, parent({ ...evidence(1), result_id: 'page1' }, evidence(2)));
    expect(multiPage.clarification).toBeUndefined();
    expect(multiPage.query.queries?.[0].parameters.page).toBe(3);
  });

  it('首轮追问、未知指代和失败结果先澄清，不制造默认查询', () => {
    for (const text of ['只看物流', '换成按月', '继续查原文']) expect(resolveQuery(text, scope).clarification).toBeTruthy();
    expect(resolveQuery('那它呢？', scope, parent(trend())).clarification).toBeTruthy();
    expect(resolveQuery('只看物流', scope, { ...parent(trend()), result: { ...result(trend()), status: 'error' } }).clarification).toBeTruthy();
    expect(resolveQuery('按月查看包装和物流问题变化', scope).query.effectiveQuestion).toBe('按月查看包装和物流问题变化');
  });

  it('多个查询目标要求选择，范围变化冻结两个选择并重置新范围的原文页码', () => {
    const multiple = resolveQuery('只看物流', scope, parent(trend(), evidence()));
    expect(multiple.clarification?.choices).toHaveLength(2);
    const changed = { ...scope, dataset_version: 'v2', product_ids: ['B'] };
    const resolved = resolveQuery('继续查原文', changed, parent(evidence(2)));
    expect(resolved.clarification?.choices).toHaveLength(2);
    const [oldScope, newScope] = resolved.clarification!.choices;
    expect(oldScope.parameters.dataset_version).toBe('v1');
    expect(oldScope.query.queries?.[0].parameters.page).toBe(3);
    expect(newScope.query.queries?.[0].parameters).toMatchObject({ product_ids: ['B'], page: 1 });
    changed.product_ids.push('C');
    expect(newScope.parameters.product_ids).toEqual(['B']);
  });

  it('从服务端历史任务承接旧范围时不重新提交服务端模型身份字段', () => {
    const restored = { ...scope, model_identity: { model: 'server-model' }, question: 'historical' };
    const resolution = resolveQuery('换成按月', { ...scope, product_ids: ['B'] }, { ...parent(trend()), parameters: restored });
    expect(resolution.clarification!.choices[0].parameters).toEqual(scope);
  });
});

describe('追问提交、澄清和快照恢复', () => {
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

  it('范围澄清后选择才提交，重复点击只提交一次，旧轮快照不变', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({ task: task('first', result(trend())), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(scope, '查询趋势'); });
    const before = structuredClone(hook.result.current.session.turns[0]);
    await act(async () => { await hook.result.current.send({ ...scope, product_ids: ['B'] }, '换成按月'); });
    expect(submit).toHaveBeenCalledTimes(1);
    const turn = hook.result.current.session.turns[1];
    render(<ChatMessage turn={turn} chat={hook.result.current} onPrepare={vi.fn()} />);
    const button = screen.getByRole('button', { name: /使用当前范围/ });
    await act(async () => { fireEvent.click(button); fireEvent.click(button); });
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1][0]).toMatchObject({ parameters: { product_ids: ['B'], question: expect.stringContaining('"period":"month"') } });
    expect(hook.result.current.session.turns[0]).toEqual(before);
    expect(hook.result.current.session.turns[1].clarification?.resolved).toBe(true);
    expect(JSON.parse(localStorage.getItem(CHAT_KEY)!).turns).toHaveLength(3);
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
