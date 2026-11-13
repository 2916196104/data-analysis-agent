import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { emptyTaskState } from '../../task-state';
import type { AgentResult, AnalysisParameters, Task } from '../../types';
import { useTask } from '../../useTask';
import ChatMessage from './ChatMessage';
import type { ChatTurn } from './chat-state';
import { useAgentChat, type AgentChat } from './useAgentChat';
import fixture from './__fixtures__/p7c-rag-offline.json';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const success = fixture.success as unknown as Task;
const empty = fixture.empty as unknown as Task;
const failed = fixture.failed as unknown as Task;
const scope = success.parameters.selection_scope as unknown as AnalysisParameters;
const question = '商品100013588055有没有送货慢的反馈？';

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useTask).mockReturnValue({ ...emptyTaskState, refresh: vi.fn() });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function show(task: Task) {
  const turn: ChatTurn = { kind: 'analysis', id: 'p7c-turn', question, parameters: scope,
    requestKey: 'p7c-SYNTHETIC-request', taskId: task.task_id };
  const chat = { session: { activeId: turn.id }, activeState: { ...emptyTaskState, id: task.task_id, task },
    errors: {}, pending: false, retry: vi.fn() } as unknown as AgentChat;
  return render(<ChatMessage turn={turn} chat={chat} onPrepare={vi.fn()} />);
}

it('P7-C 原问题、固定范围和会话经真实 HTTP 适配器提交一次，等待任务时不重复提交', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: { task: success, reused: false } }), { status: 202 }));
  vi.stubGlobal('fetch', fetchMock);
  const chat = renderHook(() => useAgentChat(undefined, 'p7c-scope', scope));
  await act(async () => { expect(await chat.result.current.send(scope, question)).toBe(true); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toMatch(/\/tasks$/);
  const body = JSON.parse(init.body as string);
  expect(body.kind).toBe('analysis');
  expect(body.parameters.question).toBe(question);
  expect(body.parameters.selection_scope).toEqual(scope);
  expect(body.parameters).toMatchObject({ source: 'reviews_csv', dataset_version: 'reviews-csv',
    product_ids: ['100013588055'], date_from: '2021-09-11', date_to: '2022-08-26' });
  expect(body.parameters.conversation_id).toBeTruthy();
  expect(init.headers['Idempotency-Key']).toBeTruthy();
  await act(async () => { expect(await chat.result.current.send(scope, question)).toBe(false); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('P7-C 后端合成原生任务经 API JSON 解析后只渲染三个许可引用和精确区间', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: success }))));
  const task = await api.task(success.task_id);
  const { container } = show(task);
  const answer = screen.getByRole('region', { name: '分析回答' });
  const result = task.result as unknown as AgentResult;
  expect(within(answer).getByText(result.public_answer!.summary)).toBeTruthy();
  expect(within(answer).getAllByText(/查看原文 · 评论/)).toHaveLength(3);
  expect(answer.textContent).not.toContain('送货到时挺快的');
  expect(answer.textContent).not.toContain('物流送货态度太差');
  for (const ref of result.public_answer!.evidence_refs) for (const opinion of Array.isArray(ref.opinions) ? ref.opinions : []) {
    expect(answer.textContent).toContain(opinion.quote);
    expect(answer.textContent).toContain(`原文区间：[${opinion.quote_start}, ${opinion.quote_end}) · 标注：${opinion.annotation_id}`);
    expect(answer.textContent).toContain(ref.evidence_ref);
  }
  fireEvent.click(within(answer).getAllByText(/查看原文 · 评论/)[0]);
  expect(container.querySelector('.answer-evidence-item')?.getAttribute('open')).not.toBeNull();
  expect(container.querySelectorAll('.answer-evidence mark')).toHaveLength(3);
  expect(answer.textContent).toContain('语义Top-K候选不作为统计样本或总体分母');
});

it('P7-C 空检索显示证据限制，不把无候选写成全量不存在', () => {
  show(empty);
  const answer = screen.getByRole('region', { name: '分析回答' });
  expect(answer.textContent).toContain('本次检索没有返回候选证据');
  expect(answer.textContent).toContain('不表示全量评论不存在相关反馈');
  expect(within(answer).queryByLabelText('回答原文引用')).toBeNull();
});

it('P7-C 编码失败的原生任务显示失败说明且不发布回答和原文', () => {
  show(failed);
  expect(screen.getByText('本地语义检索暂不可用，请检查模型运行配置后重试。')).toBeTruthy();
  expect(screen.queryByRole('region', { name: '分析回答' })).toBeNull();
  expect(screen.queryByLabelText('回答原文引用')).toBeNull();
});

it('P7-C 冷启动 running 状态提示加载，不自动请求重试', () => {
  const pending = { ...success, status: 'running', result: null, error: null,
    progress: { stage: 'starting', completed: 0, total: null, unit: 'items' } } as Task;
  const retry = vi.spyOn(api, 'retry');
  show(pending);
  expect(screen.getByText(/首次本地检索可能需要加载模型/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull();
  expect(retry).not.toHaveBeenCalled();
});
