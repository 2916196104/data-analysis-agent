import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import App from '../../App';
import { api, API_BASE, ApiError } from '../../api';
import { emptyTaskState } from '../../task-state';
import type { Options, Task } from '../../types';
import { useTask } from '../../useTask';
import ChatMessage from './ChatMessage';
import { useAgentChat } from './useAgentChat';
import { analysisApi } from '../../shared/api/analysis-api';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
// 导航布局已有独立单测和真实浏览器验收，此处聚焦 App 的模式分流与消息交互。
vi.mock('../../app/AppLayout', () => ({ default: ({ children }: { children: ReactNode }) => <main>{children}</main> }));
const emptyOptions: Options = { profiles: [], products: [], selected_profile_id: null,
  aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };
const task: Task = { task_id: 'analysis-task', kind: 'analysis', status: 'queued', parameters: {}, result: null,
  error: null, parent_task_id: null, created_at: '', started_at: null, finished_at: null, updated_at: '',
  progress: { stage: 'queued', completed: 0, total: null, unit: 'tools' } };

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() });
  window.history.replaceState(null, '', '/assistant');
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? task : null, refresh: vi.fn() }));
  vi.spyOn(api, 'options').mockResolvedValue(emptyOptions);
  vi.spyOn(api, 'quality').mockResolvedValue({ review_count: 0, product_count: 0, published_at_start: null, published_at_end: null });
  vi.spyOn(api, 'health').mockResolvedValue({ worker_available: true, business_database: 'ok' });
  vi.spyOn(api, 'tasks').mockResolvedValue({ total: 0, page: 1, page_size: 15, items: [] });
  vi.spyOn(api, 'submit').mockResolvedValue({ task, reused: false });
  vi.spyOn(api, 'retry');
  vi.spyOn(analysisApi, 'trend').mockResolvedValue({ summary: { review_count: 10, eligible_text_reviews: 10,
    successful_reviews: 10, failed_reviews: 0, unprocessed_reviews: 0, negative_review_count: 2,
    labeling_coverage: 1, negative_rate: 0.2 }, buckets: [] });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function seedAnalysis() {
  vi.mocked(api.options).mockResolvedValue({ ...emptyOptions, selected_profile_id: 1,
    profiles: [{ profile_id: 1, requested_model: 'fixture', base_url: 'offline', prompt_version: 'v1' }],
    products: [{ key: 'A', source: 'fixture', dataset_version: 'reviews', product_id: 'A', product_title: '测试商品',
      review_count: 10, success_count: 10, pending_count: 0 }] });
  localStorage.setItem(`review-workbench:${API_BASE}`, JSON.stringify({ selected: null, notes: {}, draft: {
    dataset: JSON.stringify(['fixture', 'reviews']), productIds: ['A'], profileId: 1, note: '',
    current: ['2023-02-01', '2023-02-28'], baseline: ['2023-01-01', '2023-01-31'],
  } }));
}

describe('助手界面模式接入', () => {
  it('默认基础问答，无商品和标注版本也能发送，分析词句不触发任务', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ answer: '**当前模式**不读取业务数据。' });
    render(<App />);
    await waitFor(() => expect(api.options).toHaveBeenCalled());
    expect((screen.getByRole('radio', { name: '基础问答' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByText('请先选择可用标注版本与分析范围')).toBeNull();
    const input = screen.getByRole('textbox', { name: '基础问答问题' });
    fireEvent.change(input, { target: { value: '差评率是多少？' } });
    fireEvent.click(screen.getByRole('button', { name: '提交问题' }));
    await screen.findByText('当前模式');
    expect(chat).toHaveBeenCalledWith('差评率是多少？');
    expect(api.submit).not.toHaveBeenCalled();
    expect(vi.mocked(useTask).mock.calls.every(([id]) => id === null)).toBe(true);
    expect(document.querySelector('.chat-report')).toBeNull();
    expect(Object.values(localStorage).join('')).toContain('差评率是多少？');
    expect((input as HTMLTextAreaElement).value).toBe('');
  });

  it('缺少范围只限制评论分析，切换模式保留各自输入草稿', async () => {
    render(<App />);
    await waitFor(() => expect(api.options).toHaveBeenCalled());
    fireEvent.change(screen.getByRole('textbox', { name: '基础问答问题' }), { target: { value: '普通草稿' } });
    fireEvent.click(screen.getByRole('radio', { name: '评论分析' }));
    expect(screen.queryByText('请先选择可用标注版本与分析范围')).toBeNull();
    expect((screen.getByRole('textbox', { name: '评论分析问题' }) as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '提交问题' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: '基础问答' }));
    const input = screen.getByRole('textbox', { name: '基础问答问题' }) as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
    expect(input.value).toBe('普通草稿');
  });

  it('明确选择评论分析后，即使是普通问题也沿用分析任务协议', async () => {
    seedAnalysis();
    const chat = vi.spyOn(api, 'chat');
    render(<App />);
    await waitFor(() => expect(api.options).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('radio', { name: '评论分析' }));
    const input = screen.getByRole('textbox', { name: '评论分析问题' });
    await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false));
    fireEvent.change(input, { target: { value: '你能做什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '提交问题' }));
    await waitFor(() => expect(api.submit).toHaveBeenCalled());
    expect(vi.mocked(api.submit).mock.calls[0][0]).toMatchObject({ kind: 'analysis', parameters: {
      question: '你能做什么？', product_ids: ['A'], source: 'fixture', dataset_version: 'reviews',
      date_from: '2023-02-01', date_to: '2023-02-28', baseline_from: '2023-01-01', baseline_to: '2023-01-31',
    } });
    expect(chat).not.toHaveBeenCalled();
    expect(vi.mocked(useTask).mock.calls.some(([id]) => id === task.task_id)).toBe(true);
  });

  it('普通问答失败显示错误，重新发送走聊天接口并保留原问题', async () => {
    const chat = vi.spyOn(api, 'chat').mockRejectedValueOnce(new ApiError(504, { code: 'model_timeout', message: '模型回答超时' }))
      .mockResolvedValueOnce({ answer: '恢复后的回答' });
    render(<App />);
    fireEvent.change(screen.getByRole('textbox', { name: '基础问答问题' }), { target: { value: '原问题' } });
    fireEvent.click(screen.getByRole('button', { name: '提交问题' }));
    await screen.findByText('模型回答超时');
    expect(screen.queryByText('问题尚未确认提交')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '重新发送' }));
    await screen.findByText('恢复后的回答');
    expect(chat.mock.calls).toEqual([['原问题'], ['原问题']]);
    expect(api.retry).not.toHaveBeenCalled();
    expect(api.submit).not.toHaveBeenCalled();
  });

  it('普通请求等待期间锁定模式和重复发送，迟到回答不清除新输入', async () => {
    let resolve!: (reply: { answer: string }) => void;
    const chat = vi.spyOn(api, 'chat').mockImplementation(() => new Promise(done => { resolve = done; }));
    render(<App />);
    const input = screen.getByRole('textbox', { name: '基础问答问题' });
    fireEvent.change(input, { target: { value: '第一个问题' } });
    fireEvent.click(screen.getByRole('button', { name: '提交问题' }));
    await screen.findByText('正在等待回答…');
    expect((screen.getByRole('radio', { name: '评论分析' }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(chat).toHaveBeenCalledTimes(1);
    fireEvent.change(input, { target: { value: '下一条草稿' } });
    await act(async () => { resolve({ answer: '回答' }); });
    await screen.findByText('回答');
    expect((input as HTMLTextAreaElement).value).toBe('下一条草稿');
  });

  it('基础消息组件复用 Markdown，完全不调用任务读取 hook', () => {
    const hook = renderHook(() => useAgentChat());
    const callsBefore = vi.mocked(useTask).mock.calls.length;
    const view = render(<ChatMessage turn={{ kind: 'basic', id: 'basic', question: '问题', status: 'succeeded',
      answer: '**文本**\n\n<script>alert(1)</script>' }} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(view.container.querySelector('strong')?.textContent).toBe('文本');
    expect(view.container.querySelector('script')).toBeNull();
    expect(view.container.querySelector('.chat-turn-scope')).toBeNull();
    expect(vi.mocked(useTask).mock.calls.length).toBe(callsBefore);
  });
});
