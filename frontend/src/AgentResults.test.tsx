import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AgentResults from './AgentResults';
import LabelForm from './LabelForm';
import { sampleState } from './agent-report';
import type { AgentResult, Options, Summary } from './types';

const zero: Summary = { review_count: 10, eligible_text_reviews: 10, successful_reviews: 0,
  failed_reviews: 0, unprocessed_reviews: 10, negative_review_count: 0, labeling_coverage: 0, negative_rate: null };
const scope = { source: 'saved-source', dataset_version: 'saved-dataset', product_ids: ['A', 'B'],
  model: 'saved-model', base_url: 'offline', prompt_version: 'v1', date_from: '2023-01-01', date_to: '2023-01-31' };
const result: AgentResult = { agent: true, status: 'ok', scope, question: '商品对比', answer: '旧版很长的商品段落',
  snapshot_at: '', model_calls: 2, tool_calls: 2, elapsed_seconds: 1, trace: [], result_ids: ['r1'], results: {
    overview: { result_id: 'overview', parameters: {}, kind: 'context', data: { summary: zero, products: [{ product_id: 'A', title: '待准备商品' }] } },
    r1: { result_id: 'r1', parameters: {}, kind: 'comparison', data: { no_reviews_product_ids: ['C'], products: [
      { ...zero, product_id: 'A' },
      { ...zero, product_id: 'B', successful_reviews: 10, unprocessed_reviews: 0, labeling_coverage: 1, negative_rate: 0 },
    ] } },
  } };

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
  const original = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => original(element));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Agent 可读结果与标注补齐', () => {
  it('区分待标注、失败、无正文、无评论和真实零占比', () => {
    expect(sampleState(zero).label).toBe('待标注');
    expect(sampleState({ ...zero, unprocessed_reviews: 0, failed_reviews: 10 }).label).toBe('标注失败');
    expect(sampleState({ ...zero, eligible_text_reviews: 0 }).label).toBe('无有效正文');
    expect(sampleState({ ...zero, review_count: 0 }).label).toBe('无评论');
    expect(sampleState({ ...zero, successful_reviews: 10, negative_rate: 0, labeling_coverage: 1 }).label).toBe('样本有限');
  });

  it('历史长段落改为摘要表格，显示真实零占比并按商品搜索和准备', () => {
    const prepare = vi.fn();
    render(<AgentResults result={result} onDetail={vi.fn()} onPrepare={prepare} />);
    expect(screen.queryByText(result.answer!)).toBeNull();
    expect(screen.getAllByText(/对比 3 款商品/).length).toBe(2);
    expect(screen.getAllByText('0.0%').length).toBeGreaterThan(0);
    expect(screen.getByText('0 / 10')).toBeTruthy();
    expect(screen.getAllByText('无评论').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '准备待处理商品（1）' }));
    expect(prepare).toHaveBeenCalledWith(['A']);
    fireEvent.click(screen.getByRole('button', { name: '补齐当前范围标注' }));
    expect(prepare).toHaveBeenLastCalledWith(['A', 'B']);
    fireEvent.change(screen.getByRole('textbox', { name: '搜索对比商品' }), { target: { value: '待准备' } });
    expect(screen.queryByText('B')).toBeNull();
    expect(screen.getByText('待准备商品')).toBeTruthy();
  });

  it('准备入口不自动提交，确认后保留当前窗口、截止时间和失败重试', async () => {
    const submit = vi.fn();
    const options: Options = { profiles: [], selected_profile_id: null, aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {},
      products: [{ key: 'A', source: scope.source, dataset_version: scope.dataset_version, product_id: 'A', product_title: '商品', review_count: 10, success_count: 0, pending_count: 10 }] };
    const window = { date_from: scope.date_from, date_to: scope.date_to, as_of: '2023-01-30T00:00:00Z' };
    render(<LabelForm options={options} initialDataset={JSON.stringify([scope.source, scope.dataset_version])} initialProducts={['A']}
      initialScope={window} initialRetryFailed busy={false} onSubmit={submit} />);
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox', { name: '标注数据集' }).hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '提交标注任务' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ ...window, source: scope.source, dataset_version: scope.dataset_version,
      product_ids: ['A'], limit: 100, workers: 2, retry_failed: true }));
  });
});
