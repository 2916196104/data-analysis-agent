import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AgentResult, Task } from '../../types';
import { useTask } from '../../useTask';
import { emptyTaskState } from '../../task-state';
import { taskApi } from '../../shared/api/task-api';
import type { AgentChat } from './useAgentChat';
import ChatMessage from './ChatMessage';
import P6Results from './P6Results';
import { readP6Publication } from './p6-publication';
import { buildAgentReport } from './report-export';
import workerTasks from './__fixtures__/p6-publications.json';

// 由 tests/test_p6d_publication.py --fixtures 生成：真实准备/执行/独立验收/发布/Worker 的公开对象。
const tasks = workerTasks as unknown as Record<string, Task>;
const result = (mode = 'complete') => structuredClone(tasks[mode].result) as unknown as AgentResult;
vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
beforeEach(() => {
  vi.mocked(useTask).mockReturnValue({ ...emptyTaskState, refresh: vi.fn() });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(Object.keys(tasks))('真实 Worker %s 产物通过前端运行时契约并保留相同导出正文', mode => {
  const r = result(mode);
  expect(readP6Publication(r)).toBeTruthy();
  expect(buildAgentReport(r)).toBe(r.p6_publication!.answer_text);
  expect(buildAgentReport(r)).toContain('成功标注评论数 D=');
  expect(buildAgentReport(r)).toContain('模型发现并复核的候选，未经人工确认');
  expect(buildAgentReport(r)).toContain('不是全量投诉率');
});

it('三维各三个主题及九条引用均展示，第三维不受全局截断影响', () => {
  const r = result('many'); const { container } = render(<P6Results result={r} />);
  expect(container.querySelectorAll('.p6-dimension')).toHaveLength(3);
  expect(container.querySelectorAll('.p6-topics > li')).toHaveLength(9);
  expect(container.querySelectorAll('.p6-reference')).toHaveLength(9);
  expect(screen.getByRole('region', { name: '物流主题' }).querySelectorAll('.p6-topics > li')).toHaveLength(3);
  expect(screen.getAllByText(/另有 1 个已支持候选未展示/)).toHaveLength(3);
  const refs = r.p6_publication!.dimensions.slice(1).map(d => d.topics[0].evidence[0]);
  expect(refs[0].review_id).toBe(refs[1].review_id);
  expect(refs[0].opinion_id).not.toBe(refs[1].opinion_id);
});

it.each(['fail_service', 'fail_effectiveness', 'fail_logistics', 'empty_effectiveness'])('%s 只展示两维，并明确保留失败维度和原因', mode => {
  const r = result(mode); const { container } = render(<P6Results result={r} />);
  expect(container.querySelectorAll('.p6-dimension')).toHaveLength(2);
  expect(screen.getAllByText('部分完成').length).toBeGreaterThan(0);
  expect(screen.getByRole('region', { name: '未完成维度' }).textContent).toContain(r.p6_publication!.dimensions.find(d => d.status !== 'complete')!.reason);
  expect(buildAgentReport(r)).toContain('完整任务完成：否');
});

it.each(['empty', 'budget'])('%s 不展示空主题卡片或未经许可的引用', mode => {
  const r = result(mode); const { container } = render(<P6Results result={r} />);
  expect(container.querySelectorAll('.p6-dimension')).toHaveLength(0);
  expect(container.querySelectorAll('.p6-reference')).toHaveLength(0);
  expect(screen.getByRole('region', { name: '未完成维度' }).querySelectorAll('li')).toHaveLength(3);
  expect(container.textContent).toContain(mode === 'empty' ? '没有足够支持主题的证据' : '未发布主题结论');
});

it('M 与 S 不同时显示覆盖率限制，复制和导出保留完整统计口径与原文', async () => {
  const r = result('sample'); const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', Object.assign(Object.create(navigator), { clipboard: { writeText } }));
  render(<P6Results result={r} />);
  expect(screen.getByText('样本覆盖有限，主题频次不能外推至全量。')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '复制回答' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(buildAgentReport(r)));
  expect(writeText.mock.calls[0][0]).toContain('M=30');
  expect(writeText.mock.calls[0][0]).toContain('S=20');
  expect(writeText.mock.calls[0][0]).toContain('原文位置');
  expect(writeText.mock.calls[0][0]).toContain('sha256:');
});

it('partial 的复制明确未完成，不使用旧 public_answer 的完整成功分支', async () => {
  const r = result('fail_effectiveness'); const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', Object.assign(Object.create(navigator), { clipboard: { writeText } }));
  render(<P6Results result={r} />);
  fireEvent.click(screen.getByRole('button', { name: '复制回答' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(r.p6_publication!.answer_text));
  expect(writeText.mock.calls[0][0]).toContain('状态：部分完成');
  expect(writeText.mock.calls[0][0]).toContain('未完成原因');
});

it.each(['count', 'quote', 'source', 'scope', 'missing', 'duplicate', 'complete', 'status'])('%s 篡改时关闭主题、复制和导出事实', mutation => {
  const r = result('many'); const p = r.p6_publication!;
  if (mutation === 'count') p.dimensions[0].topics[0].N_a_t = 999;
  if (mutation === 'quote') p.dimensions[0].topics[0].evidence[0].quote = '伪造原文';
  if (mutation === 'source') p.dimensions[0].topics[0].evidence[0].evidence_ref = 'untrusted';
  if (mutation === 'scope') p.scope.source = 'changed';
  if (mutation === 'missing') p.dimensions.pop();
  if (mutation === 'duplicate') p.required_aspects[1] = p.required_aspects[0];
  if (mutation === 'complete') p.complete = false;
  if (mutation === 'status') p.status = 'partial';
  expect(readP6Publication(r)).toBeUndefined();
  const { container } = render(<P6Results result={r} />);
  expect(container.querySelectorAll('.p6-dimension')).toHaveLength(0);
  expect(screen.queryByRole('button', { name: '复制回答' })).toBeNull();
  expect(buildAgentReport(r)).not.toContain('支持引用');
});

it.each(['complete', 'fail_effectiveness', 'empty', 'budget'])('聊天实际门禁接受已验收的 %s Worker 状态', mode => {
  const task = tasks[mode];
  const chat = { session: { activeId: 'turn' }, activeState: { ...emptyTaskState, id: task.task_id, task },
    errors: {}, pending: false, posting: false, retry: vi.fn() } as unknown as AgentChat;
  render(<ChatMessage turn={{ kind: 'analysis', id: 'turn', taskId: task.task_id, question: '具体体现在哪里？',
    parameters: task.parameters as never, requestKey: 'key' }} chat={chat} onPrepare={vi.fn()} />);
  expect(screen.getByRole('region', { name: '分析回答' })).toBeTruthy();
  expect(screen.queryByText('完整回答尚未确认，统计与原文暂不展示。可以重试任务。')).toBeNull();
});

it('历史任务读取必须经过服务端 P6 公开复验，失败时不得回退使用原诊断对象', async () => {
  const task = tasks.fail_effectiveness;
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: task })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { p6_publication: null } })));
  vi.stubGlobal('fetch', fetcher);
  const returned = await taskApi.task(task.task_id);
  expect(fetcher.mock.calls[1][0]).toContain('/public/tasks/');
  expect((returned.result as unknown as AgentResult).validation?.status).toBe('failed');
  expect(readP6Publication(returned.result as unknown as AgentResult)).toBeUndefined();
});
