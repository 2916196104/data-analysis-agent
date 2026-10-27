import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api';
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
    expect(screen.getByText('查询未完成，请查看任务错误与执行记录。')).toBeTruthy();
    expect(screen.queryByText('查询结果已保存，可展开统计依据与原文证据查看结果。')).toBeNull();
  });
});

describe('聊天输入和 Markdown', () => {
  it('Enter 发送，Shift+Enter 与输入法组合输入不发送', () => {
    const send = vi.fn();
    render(<ChatInput value="查询趋势" onChange={vi.fn()} onSend={send} pending={false} disabled={false} />);
    const input = screen.getByRole('textbox', { name: '评论分析问题' });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(send).toHaveBeenCalledOnce();
  });

  it('请求进行中禁止重复发送', () => {
    const send = vi.fn();
    render(<ChatInput value="查询趋势" onChange={vi.fn()} onSend={send} pending disabled={false} />);
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
