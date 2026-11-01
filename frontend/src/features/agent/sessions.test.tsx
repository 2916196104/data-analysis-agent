import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { emptyTaskState } from '../../task-state';
import { useTask } from '../../useTask';
import type { AnalysisParameters, Task } from '../../types';
import { CHAT_KEY, type ChatStore } from './chat-state';
import { readChatStore } from './useChatSessions';
import { useAgentChat } from './useAgentChat';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const scope: AnalysisParameters = { model: 'fixture', base_url: 'offline', prompt_version: 'v1', product_ids: ['美的-ID'], source: 'reviews-csv', dataset_version: 'v1',
  date_from: '2023-02-01', date_to: '2023-02-28', baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const tasks = new Map<string, Task>();
function task(id: string, conversationId: string, parameters = scope): Task {
  return { task_id: id, kind: 'analysis', status: 'succeeded', parameters: { ...parameters, question: id, conversation_id: conversationId },
    result: { agent: true, status: 'ok', scope: parameters, validation: { status: 'passed', checks: [] }, result_ids: [], results: {} },
    error: null, progress: { stage: 'completed', completed: 1, total: 1, unit: 'task' }, parent_task_id: null,
    created_at: '', updated_at: '', started_at: null, finished_at: null };
}
const saved = (): ChatStore => JSON.parse(localStorage.getItem(CHAT_KEY)!);
beforeEach(() => {
  localStorage.clear(); tasks.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? tasks.get(id) || null : null, refresh: vi.fn() }));
  vi.spyOn(api, 'task').mockImplementation(async id => tasks.get(id)!);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('A/B/C/D/G：新建、切换、刷新保存独立消息和当前商品，空会话不重复创建', async () => {
  const submit = vi.spyOn(api, 'submit').mockImplementation(async body => {
    const id = String((body.parameters as AnalysisParameters).question);
    const item = task(id, String((body.parameters as AnalysisParameters).conversation_id), body.parameters as AnalysisParameters);
    tasks.set(id, item); return { task: item, reused: false };
  });
  const hook = renderHook(() => useAgentChat());
  const a = hook.result.current.activeSessionId;
  await act(async () => { await hook.result.current.send(scope, '会话A问题'); });
  act(() => hook.result.current.createSession());
  const b = hook.result.current.activeSessionId;
  expect(b).not.toBe(a); expect(hook.result.current.conversation.turns).toEqual([]);
  act(() => hook.result.current.createSession());
  expect(hook.result.current.sessions).toHaveLength(2);
  await act(async () => { await hook.result.current.send(scope, '会话B问题'); });
  expect(submit.mock.calls[1][0].parameters).toMatchObject({ product_ids: ['美的-ID'], conversation_id: b });
  expect((submit.mock.calls[1][0].parameters as AnalysisParameters).context_task_id).toBeUndefined();
  act(() => hook.result.current.selectSession(a));
  expect(hook.result.current.conversation.turns.map(turn => turn.question)).toEqual(['会话A问题']);
  act(() => hook.result.current.selectSession(b));
  hook.unmount();
  const restored = renderHook(() => useAgentChat());
  expect(restored.result.current.activeSessionId).toBe(b);
  expect(restored.result.current.conversation.turns.map(turn => turn.question)).toEqual(['会话B问题']);
  expect(submit).toHaveBeenCalledTimes(2);
});

it('E：A 的延迟提交、任务完成和错误均不覆盖 B 的消息或请求锁', async () => {
  let finishA!: (value: { task: Task; reused: boolean }) => void;
  let finishB!: (value: { answer: string }) => void;
  vi.spyOn(api, 'submit').mockImplementation(() => new Promise(resolve => { finishA = resolve; }));
  vi.spyOn(api, 'chat').mockImplementation(() => new Promise(resolve => { finishB = resolve; }));
  const hook = renderHook(() => useAgentChat());
  const a = hook.result.current.activeSessionId;
  let requestA!: Promise<boolean>, requestB!: Promise<boolean>;
  act(() => { requestA = hook.result.current.send(scope, 'A分析'); });
  act(() => hook.result.current.createSession());
  const b = hook.result.current.activeSessionId;
  expect(hook.result.current.pending).toBe(false);
  act(() => { requestB = hook.result.current.sendBasic('B问答'); });
  const running = { ...task('task-A', a), status: 'running' as const };
  tasks.set(running.task_id, running);
  await act(async () => { finishA({ task: running, reused: false }); await requestA; });
  expect(hook.result.current.posting).toBe(true);
  expect(hook.result.current.conversation.turns.map(turn => turn.question)).toEqual(['B问答']);
  expect(saved().sessions.find(item => item.id === a)?.turns[0]).toMatchObject({ taskId: 'task-A' });
  tasks.set('task-A', task('task-A', a));
  act(() => hook.result.current.selectSession(a));
  await waitFor(() => expect(hook.result.current.conversation.context?.last_successful_task_id).toBe('task-A'));
  await act(async () => { finishB({ answer: 'B回答' }); await requestB; });
  expect(hook.result.current.conversation.turns).toHaveLength(1);
  expect(hook.result.current.pending).toBe(false);
  act(() => hook.result.current.selectSession(b));
  expect(hook.result.current.conversation.turns[0]).toMatchObject({ answer: 'B回答' });
});

it('F/H：新会话追问无父任务；切换商品后不继承历史范围，历史快照保持不变', async () => {
  vi.spyOn(api, 'submit').mockImplementation(async body => {
    const item = task('first', String((body.parameters as AnalysisParameters).conversation_id)); tasks.set('first', item);
    return { task: item, reused: false };
  });
  const hook = renderHook(() => useAgentChat());
  const a = hook.result.current.activeSessionId;
  await act(async () => { await hook.result.current.send(scope, '分析美的'); });
  act(() => hook.result.current.createSession());
  await act(async () => { await hook.result.current.send(scope, '那物流呢'); });
  expect(hook.result.current.activeTurn?.clarification).toBeTruthy();
  expect(hook.result.current.activeTurn?.query?.parentTaskId).toBeUndefined();
  act(() => hook.result.current.selectSession(a));
  await act(async () => { await hook.result.current.send({ ...scope, product_ids: ['小米-ID'] }, '那物流呢'); });
  expect(hook.result.current.activeTurn?.clarification?.message).toContain('当前选择范围');
  expect(hook.result.current.activeTurn?.query?.parentTaskId).toBeUndefined();
  expect(hook.result.current.session.turns[0].parameters.product_ids).toEqual(['美的-ID']);
  expect(api.submit).toHaveBeenCalledTimes(1);
});

it('I：迁移旧任务引用并保留原始备份，不自动重跑；损坏存储可导出且备份失败不覆盖', async () => {
  const raw = JSON.stringify({ turns: [{ id: 'old', question: '历史分析', requestKey: 'key', parameters: scope, taskId: 'old-task' }], activeId: 'old' });
  localStorage.setItem(CHAT_KEY, raw);
  const submit = vi.spyOn(api, 'submit');
  const hook = renderHook(() => useAgentChat());
  expect(hook.result.current.activeTurn?.taskId).toBe('old-task');
  act(() => hook.result.current.createSession());
  expect(saved().sessions[0].turns[0]).toMatchObject({ taskId: 'old-task', requestKey: 'key' });
  expect(Object.keys(localStorage).some(key => key.startsWith(`${CHAT_KEY}:backup:`) && localStorage.getItem(key) === raw)).toBe(true);
  expect(submit).not.toHaveBeenCalled(); hook.unmount();
  localStorage.setItem(CHAT_KEY, '{damaged');
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  vi.spyOn(api, 'chat').mockResolvedValue({ answer: '仍可临时使用' });
  const damaged = renderHook(() => useAgentChat());
  await act(async () => { await damaged.result.current.sendBasic('新消息'); });
  expect(damaged.result.current.storageError).toBe(true);
  expect(damaged.result.current.recoveryRaw).toBe('{damaged');
  expect(localStorage.getItem(CHAT_KEY)).toBe('{damaged');
});

it('刷新中断基础问答时标记可重发，不自动调用模型', async () => {
  vi.spyOn(api, 'chat').mockImplementation(() => new Promise(() => {}));
  const hook = renderHook(() => useAgentChat());
  act(() => { void hook.result.current.sendBasic('未完成'); });
  const restored = readChatStore();
  expect(restored.store.sessions[0].turns[0]).toMatchObject({ status: 'failed', error: expect.stringContaining('页面已刷新') });
  expect(api.chat).toHaveBeenCalledTimes(1);
});

it('延迟失败只保留在原会话；切换期间的历史读取被取消，不在新会话创建追问', async () => {
  let fail!: (error: Error) => void;
  vi.spyOn(api, 'submit').mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
  const hook = renderHook(() => useAgentChat());
  const a = hook.result.current.activeSessionId;
  let pending!: Promise<boolean>;
  act(() => { pending = hook.result.current.send(scope, 'A分析'); });
  act(() => hook.result.current.createSession());
  await act(async () => { fail(new Error('A提交断网')); await pending; });
  expect(hook.result.current.errors).toEqual({}); expect(hook.result.current.posting).toBe(false);
  act(() => hook.result.current.selectSession(a));
  expect(Object.values(hook.result.current.errors)).toEqual(['A提交断网']);
  const completed = task('recovered', a); tasks.set('recovered', completed);
  vi.mocked(api.submit).mockResolvedValue({ task: completed, reused: true });
  await act(async () => { await hook.result.current.retry(hook.result.current.activeTurn!); });
  act(() => hook.result.current.release());
  let read!: (value: Task) => void;
  vi.mocked(api.task).mockImplementation(() => new Promise(resolve => { read = resolve; }));
  act(() => { pending = hook.result.current.send(scope, '那物流呢'); });
  act(() => hook.result.current.createSession());
  await act(async () => { read(completed); expect(await pending).toBe(false); });
  expect(hook.result.current.conversation.turns).toHaveLength(0);
  expect(hook.result.current.sessions.find(item => item.id === a)?.turns).toHaveLength(1);
});

it('历史任务 URL 恢复到任务所属会话，切换会话不再被旧 URL 拉回', async () => {
  vi.spyOn(api, 'chat').mockResolvedValue({ answer: '普通回答' });
  const hook = renderHook(({ route }) => useAgentChat(route), { initialProps: { route: undefined as string | undefined } });
  const a = hook.result.current.activeSessionId;
  await act(async () => { await hook.result.current.sendBasic('A内容'); });
  act(() => hook.result.current.createSession());
  const b = hook.result.current.activeSessionId;
  await act(async () => { await hook.result.current.sendBasic('B内容'); });
  tasks.set('a-task', task('a-task', a));
  hook.rerender({ route: 'a-task' });
  await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('a-task'));
  expect(hook.result.current.activeSessionId).toBe(a);
  act(() => hook.result.current.selectSession(b));
  expect(hook.result.current.activeSessionId).toBe(b);
  expect(hook.result.current.conversation.turns.map(turn => turn.question)).toEqual(['B内容']);
  expect(hook.result.current.focusedTaskId).toBeUndefined();
});

it('切换全局范围后不能点击历史澄清选项提交旧范围', async () => {
  const choice = { label: '旧范围查询', parameters: scope, query: { createdAt: new Date().toISOString(), effectiveQuestion: '查看数据质量' } };
  localStorage.setItem(CHAT_KEY, JSON.stringify({ activeId: 'clarify', turns: [{ kind: 'analysis', id: 'clarify',
    question: '继续', parameters: scope, selection: scope, requestKey: 'key', clarification: { message: '选择目标', choices: [choice] } }] }));
  const submit = vi.spyOn(api, 'submit');
  const hook = renderHook(() => useAgentChat(undefined, 'scope-B', { ...scope, product_ids: ['小米-ID'] }));
  const turn = hook.result.current.activeTurn!;
  await act(async () => { await hook.result.current.clarify(turn, turn.clarification!.choices[0]); });
  expect(submit).not.toHaveBeenCalled();
  expect(hook.result.current.activeTurn?.clarification?.message).toContain('当前选择范围已改变');
});
