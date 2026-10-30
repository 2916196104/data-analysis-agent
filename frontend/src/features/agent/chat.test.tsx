import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api';
import { useTask } from '../../useTask';
import { emptyTaskState } from '../../task-state';
import type { AnalysisParameters, Task } from '../../types';
import { CHAT_KEY, readChatSession } from './chat-state';
import { useAgentChat } from './useAgentChat';
import ChatInput from './ChatInput';
import ChatMessage from './ChatMessage';
import Markdown from './Markdown';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const parameters: AnalysisParameters = { model: 'fixture', base_url: 'offline', prompt_version: 'v1',
  source: 'fixture', dataset_version: 'reviews', product_ids: ['A'], date_from: '2023-02-01', date_to: '2023-02-28', baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const savedTask = (id: string): Task => ({ task_id: id, kind: 'analysis', status: 'succeeded', parameters: { ...parameters, question: '已有问题' },
  result: { agent: true, question: '已有问题', answer: '真实保存的回答' }, error: null, parent_task_id: null,
  created_at: '', started_at: null, finished_at: null, updated_at: '', progress: { stage: 'agent_completed', completed: 1, total: 1, unit: 'tasks' } });

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? savedTask(id) : null, refresh: vi.fn() }));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('AI 聊天提交与恢复', () => {
  it('提交时复制完整范围，草稿和商品数组变化不会改写内存、请求或刷新后的历史', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({ task: savedTask('frozen'), reused: false });
    const mutable = { ...parameters, product_ids: ['A'], as_of: '2023-02-28T23:59:59Z' };
    const frozen = structuredClone(mutable);
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(mutable, '固定范围'); });
    mutable.dataset_version = 'other'; mutable.product_ids.push('B'); mutable.date_from = '2024-01-01';
    mutable.baseline_to = '2023-12-31'; mutable.model = 'new-model';
    expect(hook.result.current.session.turns[0].parameters).toEqual(frozen);
    expect(submit.mock.calls[0][0]).toEqual({ kind: 'analysis', parameters: { ...frozen, question: '固定范围' } });
    hook.unmount();
    const restored = renderHook(() => useAgentChat());
    expect(restored.result.current.session.turns[0].parameters).toEqual(frozen);
    expect(submit).toHaveBeenCalledOnce();
  });

  it('提交未确认时重试复用幂等键，不增加重复消息', async () => {
    const submit = vi.spyOn(api, 'submit').mockRejectedValueOnce(new Error('连接中断'))
      .mockResolvedValueOnce({ task: savedTask('saved'), reused: true });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '查询趋势'); });
    expect(hook.result.current.session.turns).toHaveLength(1);
    expect(Object.values(hook.result.current.errors)).toContain('连接中断');
    const key = submit.mock.calls[0][1];
    await act(async () => { await hook.result.current.send(parameters, '查询趋势'); });
    expect(submit.mock.calls[1][1]).toBe(key);
    expect(hook.result.current.session.turns).toHaveLength(1);
    expect(hook.result.current.session.turns[0].taskId).toBe('saved');
  });

  it('并发点击只创建一次请求', async () => {
    let resolve!: (value: { task: Task; reused: boolean }) => void;
    const submit = vi.spyOn(api, 'submit').mockImplementation(() => new Promise(done => { resolve = done; }));
    const hook = renderHook(() => useAgentChat());
    let first!: Promise<boolean>;
    act(() => { first = hook.result.current.send(parameters, '查询趋势'); });
    await act(async () => { expect(await hook.result.current.send(parameters, '查询趋势')).toBe(false); });
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => { resolve({ task: savedTask('saved'), reused: false }); await first; });
  });

  it('多条问题仍按现有单轮协议提交，不添加 messages 或历史记忆', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: savedTask('one'), reused: false }).mockResolvedValueOnce({ task: savedTask('two'), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '第一个问题'); });
    await act(async () => { await hook.result.current.send(parameters, '第二个问题'); });
    expect(submit.mock.calls[1][0]).toEqual({ kind: 'analysis', parameters: { ...parameters, question: '第二个问题' } });
    expect(hook.result.current.session.turns.map(turn => turn.question)).toEqual(['第一个问题', '第二个问题']);
    expect(JSON.parse(localStorage.getItem(CHAT_KEY)!).turns[0]).not.toHaveProperty('result');
    hook.unmount();
    const refreshed = renderHook(() => useAgentChat());
    expect(refreshed.result.current.session.turns).toHaveLength(2);
    expect(useTask).toHaveBeenCalledWith('two');
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it('从历史任务 URL 恢复只读结果，不重复提交模型任务', async () => {
    const submit = vi.spyOn(api, 'submit');
    const hook = renderHook(() => useAgentChat('existing'));
    await waitFor(() => expect(hook.result.current.session.turns[0]?.taskId).toBe('existing'));
    expect(hook.result.current.session.turns[0].question).toBe('已有问题');
    expect(submit).not.toHaveBeenCalled();
  });

  it('失败任务通过既有 retry 接口重试，响应丢失时保留重试键和同一条对话', async () => {
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? { ...savedTask(id), status: id === 'failed' ? 'failed' : 'succeeded' } : null, refresh: vi.fn() }));
    vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: { ...savedTask('failed'), status: 'failed' }, reused: false });
    const retry = vi.spyOn(api, 'retry').mockRejectedValueOnce(new Error('响应丢失')).mockResolvedValueOnce({ task: savedTask('retried'), reused: true });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '查询趋势'); });
    await act(async () => { await hook.result.current.retry(hook.result.current.session.turns[0]); });
    const key = retry.mock.calls[0][1];
    await act(async () => { await hook.result.current.retry(hook.result.current.session.turns[0]); });
    expect(retry.mock.calls[1]).toEqual(['failed', key]);
    expect(hook.result.current.session.turns).toHaveLength(1);
    expect(hook.result.current.session.turns[0].taskId).toBe('retried');
  });

  it('损坏的本机记录可恢复为空状态并明确报告，不抛出未处理异常', () => {
    localStorage.setItem(CHAT_KEY, '{broken');
    expect(readChatSession()).toEqual({ session: { turns: [] }, error: true });
    localStorage.setItem(CHAT_KEY, JSON.stringify({ turns: [{ id: 'bad', question: 'x', parameters: null }] }));
    expect(readChatSession().error).toBe(true);
  });

  it('没有答复的失败结果显示失败说明，不伪装成查询成功', async () => {
    const failed: Task = { ...savedTask('failed-answer'), status: 'failed', result: { agent: true, status: 'error', question: '问题',
      scope: parameters, snapshot_at: '', results: {}, model_calls: 0, tool_calls: 0, elapsed_seconds: 0, trace: [] },
      error: { code: 'model_error', message: '模型请求失败' } };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? failed : null, refresh: vi.fn() }));
    vi.spyOn(api, 'submit').mockResolvedValue({ task: failed, reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '问题'); });
    render(<ChatMessage turn={hook.result.current.session.turns[0]} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(screen.getByText('分析未完成，未发布统计与原文。请重试任务。')).toBeTruthy();
    expect(screen.queryByText('查询结果已保存，可展开统计依据与原文证据查看结果。')).toBeNull();
  });
  it.each(['failed', 'partial', 'running', 'invalid'] as const)('%s 结果隐藏正文、证据与导出，保留失败/部分任务重试', async status => {
    const task: Task = { ...savedTask('unsafe'), status: status === 'invalid' ? 'succeeded' : status,
      result: { agent: true, status: status === 'invalid' ? 'ok' : 'error', answer: '未经确认的统计结论',
        validation: { status: 'failed', checks: [] }, scope: parameters, result_ids: ['r1'], results: {
          r1: { kind: 'evidence', data: { total: 1, items: [{ text: '未校验原文' }] } },
        } }, error: status === 'failed' ? { code: 'timeout', message: '模型调用超时' } : null };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? task : null, refresh: vi.fn() }));
    vi.spyOn(api, 'submit').mockResolvedValue({ task, reused: false });
    const retry = vi.spyOn(api, 'retry').mockResolvedValue({ task: savedTask('retry'), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '分析'); });
    render(<ChatMessage turn={hook.result.current.session.turns[0]} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(screen.queryByText('未经确认的统计结论')).toBeNull();
    expect(screen.queryByText('未校验原文')).toBeNull();
    expect(screen.queryByRole('button', { name: '导出分析报告' })).toBeNull();
    if (status === 'failed' || status === 'partial') {
      fireEvent.click(screen.getByRole('button', { name: '重试任务' }));
      await waitFor(() => expect(retry).toHaveBeenCalledWith('unsafe', expect.any(String)));
    }
    if (status === 'partial') expect(screen.getByText('任务部分完成')).toBeTruthy();
    if (status === 'failed') expect(screen.getByText('模型调用超时')).toBeTruthy();
  });

  it('历史成功任务无新版字段时保留详情与导出，聊天不回显旧正文', async () => {
    const task: Task = { ...savedTask('old'), result: { agent: true, status: 'ok', answer: '历史成功回答',
      scope: parameters, result_ids: [], results: {}, trace: [], elapsed_seconds: 1, tool_calls: 1, model_calls: 1 } };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? task : null, refresh: vi.fn() }));
    vi.spyOn(api, 'submit').mockResolvedValue({ task, reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '分析'); });
    render(<ChatMessage turn={hook.result.current.session.turns[0]} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(screen.queryByText('历史成功回答')).toBeNull();
    expect(screen.getByText('历史分析结果已保存，可展开统计详情查看已核验的指标与原文。')).toBeTruthy();
    expect(screen.getByRole('button', { name: '导出分析报告' })).toBeTruthy();
  });
});

describe('基础问答与分析消息隔离', () => {
  it('普通问答只调用 chat，记录内存回答，不创建任务或写入本机历史', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ answer: '普通回答' });
    const submit = vi.spyOn(api, 'submit');
    const retry = vi.spyOn(api, 'retry');
    const save = vi.spyOn(Storage.prototype, 'setItem');
    const hook = renderHook(() => useAgentChat());
    await act(async () => { expect(await hook.result.current.sendBasic('  你能做什么？  ')).toBe(true); });
    expect(chat).toHaveBeenCalledTimes(1);
    expect(chat).toHaveBeenCalledWith('你能做什么？');
    expect(submit).not.toHaveBeenCalled();
    expect(retry).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
    expect(useTask).toHaveBeenLastCalledWith(null);
    expect(hook.result.current.conversation.turns).toEqual([{
      kind: 'basic', id: expect.any(String), question: '你能做什么？', status: 'succeeded', answer: '普通回答',
    }]);
    expect(hook.result.current.activeMessage).toEqual(hook.result.current.conversation.turns[0]);
    expect(hook.result.current.session.turns).toEqual([]);
    expect(hook.result.current.activeTurn).toBeUndefined();
    expect(hook.result.current.pending).toBe(false);
    expect(hook.result.current.posting).toBe(false);
  });

  it('普通问答等待期间阻止重复发送和交叉提交分析，完成后解除等待', async () => {
    let resolve!: (reply: { answer: string }) => void;
    const chat = vi.spyOn(api, 'chat').mockImplementation(() => new Promise(done => { resolve = done; }));
    const submit = vi.spyOn(api, 'submit');
    const hook = renderHook(() => useAgentChat());
    let pending!: Promise<boolean>;
    act(() => { pending = hook.result.current.sendBasic('问题'); });
    expect(hook.result.current.activeMessage).toMatchObject({ kind: 'basic', status: 'pending' });
    expect(hook.result.current.pending).toBe(true);
    expect(hook.result.current.posting).toBe(true);
    await act(async () => {
      expect(await hook.result.current.sendBasic('重复问题')).toBe(false);
      expect(await hook.result.current.send(parameters, '分析问题')).toBe(false);
    });
    expect(chat).toHaveBeenCalledTimes(1);
    expect(submit).not.toHaveBeenCalled();
    await act(async () => { resolve({ answer: '回答' }); expect(await pending).toBe(true); });
    expect(hook.result.current.pending).toBe(false);
    expect(hook.result.current.posting).toBe(false);
    expect(hook.result.current.conversation.turns).toHaveLength(1);
  });

  it('分析提交和后台执行期间都不允许普通问答抢占', async () => {
    let resolve!: (value: { task: Task; reused: boolean }) => void;
    vi.spyOn(api, 'submit').mockImplementation(() => new Promise(done => { resolve = done; }));
    const chat = vi.spyOn(api, 'chat');
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id,
      task: id ? { ...savedTask(id), status: 'running' } : null, refresh: vi.fn() }));
    const hook = renderHook(() => useAgentChat());
    let pending!: Promise<boolean>;
    act(() => { pending = hook.result.current.send(parameters, '分析'); });
    await act(async () => { expect(await hook.result.current.sendBasic('普通问题')).toBe(false); });
    await act(async () => { resolve({ task: { ...savedTask('running'), status: 'running' }, reused: false }); await pending; });
    expect(hook.result.current.posting).toBe(false);
    expect(hook.result.current.pending).toBe(true);
    await act(async () => { expect(await hook.result.current.sendBasic('普通问题')).toBe(false); });
    expect(chat).not.toHaveBeenCalled();
    expect(useTask).toHaveBeenLastCalledWith('running');
  });

  it('普通问答失败保留错误，手动重发创建新消息且不使用分析重试接口', async () => {
    const chat = vi.spyOn(api, 'chat').mockRejectedValueOnce(new ApiError(504, {
      code: 'model_timeout', message: '模型回答超时，请稍后重新发送。',
    })).mockResolvedValueOnce({ answer: '重新发送后的回答' });
    const retry = vi.spyOn(api, 'retry');
    const hook = renderHook(() => useAgentChat());
    await act(async () => { expect(await hook.result.current.sendBasic('问题')).toBe(false); });
    expect(hook.result.current.activeMessage).toMatchObject({ kind: 'basic', status: 'failed',
      error: '模型回答超时，请稍后重新发送。' });
    expect(hook.result.current.activeMessage).not.toHaveProperty('answer');
    expect(hook.result.current.pending).toBe(false);
    await act(async () => { expect(await hook.result.current.sendBasic('问题')).toBe(true); });
    const turns = hook.result.current.conversation.turns;
    expect(turns).toHaveLength(2);
    expect(turns[0].id).not.toBe(turns[1].id);
    expect(turns[1]).toMatchObject({ kind: 'basic', status: 'succeeded', answer: '重新发送后的回答' });
    expect(chat.mock.calls).toEqual([['问题'], ['问题']]);
    expect(retry).not.toHaveBeenCalled();
    expect(localStorage.getItem(CHAT_KEY)).toBeNull();
  });

  it('空问题及超长问题不产生消息，空回答也不能标记为成功', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ answer: '   ' });
    const hook = renderHook(() => useAgentChat());
    for (const question of ['', ' \n\t ', '问'.repeat(4001)]) {
      await act(async () => { expect(await hook.result.current.sendBasic(question)).toBe(false); });
    }
    expect(chat).not.toHaveBeenCalled();
    expect(hook.result.current.conversation.turns).toEqual([]);
    await act(async () => { expect(await hook.result.current.sendBasic('有效问题')).toBe(false); });
    expect(hook.result.current.activeMessage).toMatchObject({ kind: 'basic', status: 'failed',
      error: '模型未返回有效回答，请重新发送。' });
    expect(hook.result.current.posting).toBe(false);
  });

  it('两类消息保持内存顺序，后续分析保存时也不会把普通问答写入历史', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValueOnce({ task: savedTask('one'), reused: false })
      .mockResolvedValueOnce({ task: savedTask('two'), reused: false });
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ answer: '仅在内存中的回答' });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '第一个分析'); });
    const savedBeforeBasic = localStorage.getItem(CHAT_KEY);
    await act(async () => { await hook.result.current.sendBasic('仅在内存中的普通问题'); });
    expect(localStorage.getItem(CHAT_KEY)).toBe(savedBeforeBasic);
    const basicId = hook.result.current.activeMessage!.id;
    act(() => { hook.result.current.release(); });
    expect(JSON.parse(localStorage.getItem(CHAT_KEY)!).activeId).toBeUndefined();
    await act(async () => { await hook.result.current.send(parameters, '第二个分析'); });
    expect(hook.result.current.conversation.turns.map(turn => turn.kind)).toEqual(['analysis', 'basic', 'analysis']);
    expect(hook.result.current.session.turns.map(turn => turn.taskId)).toEqual(['one', 'two']);
    const stored = localStorage.getItem(CHAT_KEY)!;
    expect(JSON.parse(stored).turns.map((turn: { kind: string }) => turn.kind)).toEqual(['analysis', 'analysis']);
    expect(stored).not.toContain('仅在内存');
    expect(stored).not.toContain(basicId);
    expect(submit.mock.calls[1][0]).toEqual({ kind: 'analysis', parameters: { ...parameters, question: '第二个分析' } });
    hook.unmount();
    const restored = renderHook(() => useAgentChat());
    expect(restored.result.current.conversation.turns.map(turn => turn.kind)).toEqual(['analysis', 'analysis']);
    expect(restored.result.current.activeTurn?.taskId).toBe('two');
    expect(chat).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it('无 kind 的旧历史恢复为分析消息，不重新提交请求', () => {
    localStorage.setItem(CHAT_KEY, JSON.stringify({ activeId: 'old', turns: [{
      id: 'old', question: '旧提问', parameters, requestKey: 'old-key', taskId: 'old-task',
    }] }));
    const submit = vi.spyOn(api, 'submit');
    const chat = vi.spyOn(api, 'chat');
    const hook = renderHook(() => useAgentChat());
    expect(hook.result.current.activeTurn).toMatchObject({ kind: 'analysis', id: 'old', taskId: 'old-task', requestKey: 'old-key' });
    expect(useTask).toHaveBeenCalledWith('old-task');
    expect(submit).not.toHaveBeenCalled();
    expect(chat).not.toHaveBeenCalled();
  });

  it('普通问答插入后，未确认分析仍复用原幂等键和消息', async () => {
    const submit = vi.spyOn(api, 'submit').mockRejectedValueOnce(new Error('提交未确认'))
      .mockResolvedValueOnce({ task: savedTask('confirmed'), reused: true });
    vi.spyOn(api, 'chat').mockResolvedValue({ answer: '普通回答' });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '同一分析'); });
    const analysisId = hook.result.current.activeTurn!.id;
    await act(async () => { await hook.result.current.sendBasic('普通问题'); });
    await act(async () => { await hook.result.current.send(parameters, '同一分析'); });
    expect(submit.mock.calls[1][1]).toBe(submit.mock.calls[0][1]);
    expect(hook.result.current.session.turns).toHaveLength(1);
    expect(hook.result.current.activeTurn).toMatchObject({ id: analysisId, taskId: 'confirmed' });
    expect(hook.result.current.conversation.turns).toHaveLength(2);
  });

  it('普通问答后重试失败分析，继续使用原 retry 接口且不持久化普通消息', async () => {
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id,
      task: id ? { ...savedTask(id), status: id === 'failed' ? 'failed' : 'succeeded' } : null, refresh: vi.fn() }));
    vi.spyOn(api, 'submit').mockResolvedValue({ task: { ...savedTask('failed'), status: 'failed' }, reused: false });
    vi.spyOn(api, 'chat').mockResolvedValue({ answer: '内存回答' });
    const retry = vi.spyOn(api, 'retry').mockResolvedValue({ task: savedTask('retried'), reused: false });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { await hook.result.current.send(parameters, '分析'); });
    const turn = hook.result.current.session.turns[0];
    await act(async () => { await hook.result.current.sendBasic('普通问题'); });
    await act(async () => { await hook.result.current.retry(turn); });
    expect(retry).toHaveBeenCalledWith('failed', expect.any(String));
    expect(hook.result.current.activeTurn?.taskId).toBe('retried');
    expect(hook.result.current.conversation.turns[1]).toMatchObject({ kind: 'basic', answer: '内存回答' });
    expect(localStorage.getItem(CHAT_KEY)).not.toContain('内存回答');
  });

  it('存储不可用时普通问答仍能完成，但刷新不会恢复它', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('不可读取'); });
    const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('不可写入'); });
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ answer: '内存回答' });
    const hook = renderHook(() => useAgentChat());
    await act(async () => { expect(await hook.result.current.sendBasic('问题')).toBe(true); });
    expect(hook.result.current.activeMessage).toMatchObject({ kind: 'basic', status: 'succeeded', answer: '内存回答' });
    expect(hook.result.current.storageError).toBe(true);
    expect(save).not.toHaveBeenCalled();
    hook.unmount();
    const restored = renderHook(() => useAgentChat());
    expect(restored.result.current.conversation.turns).toEqual([]);
    expect(chat).toHaveBeenCalledTimes(1);
  });

  it('从历史 URL 发起普通问答不轮询该消息，切换历史 URL 后能重新恢复分析', async () => {
    vi.spyOn(api, 'chat').mockResolvedValue({ answer: '回答' });
    const hook = renderHook(({ taskId }) => useAgentChat(taskId), { initialProps: { taskId: 'existing' as string | undefined } });
    await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('existing'));
    await act(async () => { await hook.result.current.sendBasic('普通问题'); });
    expect(useTask).toHaveBeenLastCalledWith(null);
    expect(hook.result.current.activeMessage?.kind).toBe('basic');
    hook.rerender({ taskId: undefined });
    hook.rerender({ taskId: 'existing' });
    await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('existing'));
    expect(hook.result.current.session.turns).toHaveLength(1);
    expect(hook.result.current.conversation.turns).toHaveLength(2);
    expect(useTask).toHaveBeenLastCalledWith('existing');
  });

  it('普通回答延迟返回时不会覆盖用户后来选中的历史任务', async () => {
    let resolve!: (reply: { answer: string }) => void;
    vi.spyOn(api, 'chat').mockImplementation(() => new Promise(done => { resolve = done; }));
    const hook = renderHook(({ taskId }) => useAgentChat(taskId), { initialProps: { taskId: undefined as string | undefined } });
    let pending!: Promise<boolean>;
    act(() => { pending = hook.result.current.sendBasic('普通问题'); });
    hook.rerender({ taskId: 'history' });
    await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('history'));
    await act(async () => { resolve({ answer: '延迟回答' }); await pending; });
    expect(hook.result.current.activeTurn?.taskId).toBe('history');
    expect(hook.result.current.conversation.turns[0]).toMatchObject({ kind: 'basic', status: 'succeeded', answer: '延迟回答' });
    expect(localStorage.getItem(CHAT_KEY)).not.toContain('延迟回答');
  });
});

describe('聊天输入和 Markdown', () => {
  it('Enter 发送，Shift+Enter 与输入法组合输入不发送', () => {
    const send = vi.fn();
    render(<ChatInput value="查询趋势" onChange={vi.fn()} onSend={send} pending={false} disabled={false} mode="analysis" onMode={vi.fn()} />);
    const input = screen.getByRole('textbox', { name: '评论分析问题' });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(send).toHaveBeenCalledOnce();
  });

  it('请求进行中禁止重复发送', () => {
    const send = vi.fn();
    render(<ChatInput value="查询趋势" onChange={vi.fn()} onSend={send} pending disabled={false} mode="analysis" onMode={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole('textbox', { name: '评论分析问题' }), { key: 'Enter' });
    expect(screen.getByRole('button', { name: '提交问题' }).hasAttribute('disabled')).toBe(true);
    expect(send).not.toHaveBeenCalled();
  });

  it('呈现列表、强调、表格和代码，HTML 与危险链接作为文本', () => {
    const { container } = render(<Markdown text={'## 分析\n**重点** 与 `代码`\n- 第一项\n- 第二项\n| 商品 | 数量 |\n| --- | --- |\n| A | 2 |\n```js\n<script>alert(1)</script>\n```\n<img src=x onerror=alert(1)>\n[危险](javascript:alert)\n[安全](https://example.com)'} />);
    expect(container.querySelector('strong')?.textContent).toBe('重点');
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.querySelector('table')).toBeTruthy();
    expect(container.querySelector('pre')?.textContent).toContain('<script>');
    expect(container.querySelectorAll('img,script')).toHaveLength(0);
    expect(container.querySelectorAll('a')).toHaveLength(1);
    expect(screen.getByRole('link', { name: '安全' }).getAttribute('href')).toBe('https://example.com');
  });
});
