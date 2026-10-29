import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { analysisApi } from '../../shared/api/analysis-api';
import { useProfileSummaries } from '../analysis/use-profile-summaries';
import AssistantScopeBar, { type AssistantScope } from './AssistantScopeBar';
import type { AnalysisDraft } from '../../analysis-draft';
import type { Options, Summary } from '../../types';

vi.mock('../datasets', () => ({
  DatasetSelector: ({ id, ariaLabel, value, options, onChange }: any) => <select id={id} aria-label={ariaLabel}
    value={value} onChange={event => onChange(event.target.value)}>{options.map((item: any) =>
      <option key={item.value} value={item.value}>{item.label}</option>)}</select>,
  ProductSelector: ({ id, ariaLabel, value, options, onChange }: any) => <select id={id} aria-label={ariaLabel} multiple
    value={value} onChange={event => onChange([...event.target.selectedOptions].map(item => item.value))}>{options.map((item: any) =>
      <option key={item.value} value={item.value}>{item.label}</option>)}</select>,
}));
const summary: Summary = { review_count: 30, eligible_text_reviews: 25, successful_reviews: 20, failed_reviews: 2,
  unprocessed_reviews: 3, negative_review_count: 5, labeling_coverage: 0.8, negative_rate: 0.25 };
const options: Options = { profiles: [
  { profile_id: 1, requested_model: 'model-one', base_url: 'offline', prompt_version: 'v1' },
  { profile_id: 2, requested_model: 'model-two', base_url: 'offline', prompt_version: 'v2' },
], selected_profile_id: 1, products: [
  { key: 'A', source: 'source', dataset_version: 'data-a', product_id: 'A', product_title: '商品 A', review_count: 30, success_count: 20, pending_count: 3 },
  { key: 'B', source: 'source', dataset_version: 'data-b', product_id: 'B', product_title: '商品 B', review_count: 30, success_count: 0, pending_count: 30 },
], datasets: [
  { source: 'source', dataset_version: 'data-a', review_count: 30, product_count: 1, eligible_text_reviews: 25,
    published_at_start: '2023-02-01', published_at_end: '2023-02-28', profiles: [
      { profile_id: 1, successful_reviews: 20, labeling_coverage: 0.8 }, { profile_id: 2, successful_reviews: 0, labeling_coverage: 0 },
    ] },
  { source: 'source', dataset_version: 'data-b', review_count: 30, product_count: 1, eligible_text_reviews: 30,
    published_at_start: '2018-07-10', published_at_end: '2018-07-12', profiles: [
      { profile_id: 1, successful_reviews: 0, labeling_coverage: 0 }, { profile_id: 2, successful_reviews: 0, labeling_coverage: 0 },
    ] },
], aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };
const draft: AnalysisDraft = { dataset: '["source","data-a"]', productIds: ['A'], profileId: 1, note: '备注',
  current: ['2023-02-01', '2023-02-28'], baseline: ['2023-01-01', '2023-01-31'] };
function Harness({ onApply = vi.fn(), initial = draft }: { onApply?: (scope: AssistantScope) => void; initial?: AnalysisDraft }) {
  const [value, setValue] = useState(initial);
  const [editing, setEditing] = useState(false);
  return <AssistantScopeBar draft={value} options={options} disabled={false} refreshKey={0} editing={editing} onEditing={setEditing}
    onApply={scope => { onApply(scope); setValue(previous => ({ ...previous, ...scope })); }} />;
}
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.spyOn(analysisApi, 'trend').mockResolvedValue({ summary, buckets: [] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('助手范围编辑', () => {
  it('显示已应用的五类范围以及真实标注版本覆盖率', async () => {
    render(<Harness />);
    await screen.findByText(/覆盖率 80.0%/);
    expect(screen.getByText('data-a · source')).toBeTruthy();
    expect(screen.getByText('model-one · v1')).toBeTruthy();
    expect(screen.getByText('商品 A')).toBeTruthy();
    expect(screen.getByText('2023-01-01 — 2023-01-31')).toBeTruthy();
    expect(analysisApi.trend).toHaveBeenCalledWith(expect.objectContaining({ model: 'model-one', dataset_version: 'data-a', product_ids: ['A'],
      date_from: '2023-02-01', date_to: '2023-02-28' }), expect.any(AbortSignal));
  });

  it('编辑和取消不改变已应用范围；确认后统一应用数据集、商品、标注版本和日期', async () => {
    const apply = vi.fn();
    render(<Harness onApply={apply} />);
    await screen.findByText(/覆盖率 80.0%/);
    fireEvent.click(screen.getByRole('button', { name: '切换范围' }));
    fireEvent.change(screen.getByLabelText('助手数据集'), { target: { value: '["source","data-b"]' } });
    expect(screen.queryByRole('option', { name: /商品 A/ })).toBeNull();
    expect(apply).not.toHaveBeenCalled();
    expect(analysisApi.trend).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.getByText('data-a · source')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '切换范围' }));
    fireEvent.change(screen.getByLabelText('助手数据集'), { target: { value: '["source","data-b"]' } });
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '助手标注版本' }));
    fireEvent.click(await screen.findByText('model-two · v2'));
    fireEvent.click(screen.getByRole('button', { name: '应用范围' }));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ dataset: '["source","data-b"]', productIds: [], profileId: 2,
      current: ['2018-07-10', '2018-07-12'], baseline: ['2018-06-01', '2018-06-30'] }));
    await waitFor(() => expect(analysisApi.trend).toHaveBeenLastCalledWith(expect.objectContaining({ dataset_version: 'data-b', model: 'model-two', product_ids: [], date_from: '2018-07-10', date_to: '2018-07-12' }), expect.any(AbortSignal)));
  });

  it('缺少版本或基线重叠时禁止应用', async () => {
    render(<Harness initial={{ ...draft, baseline: ['2023-02-01', '2023-02-28'] }} />);
    fireEvent.click(screen.getByRole('button', { name: '切换范围' }));
    expect((screen.getByRole('button', { name: '应用范围' }) as HTMLButtonElement).disabled).toBe(true);
    expect(analysisApi.trend).not.toHaveBeenCalled();
  });

  it('无评论、无有效正文和待标注分别显示空值与可用状态', async () => {
    const trend = vi.mocked(analysisApi.trend);
    trend.mockResolvedValue({ summary: { ...summary, review_count: 0, eligible_text_reviews: 0, successful_reviews: 0, labeling_coverage: null }, buckets: [] });
    const view = render(<Harness />);
    await screen.findByText('无评论');
    expect(screen.getByText(/覆盖率 —/)).toBeTruthy();
    view.unmount();
    trend.mockResolvedValue({ summary: { ...summary, eligible_text_reviews: 0, successful_reviews: 0, labeling_coverage: null }, buckets: [] });
    const next = render(<Harness />);
    await screen.findByText('无有效正文');
    next.unmount();
    trend.mockResolvedValue({ summary: { ...summary, successful_reviews: 0, unprocessed_reviews: 25, labeling_coverage: 0 }, buckets: [] });
    render(<Harness />);
    await screen.findByText('待标注');
    expect(screen.getByText(/覆盖率 0.0%/)).toBeTruthy();
  });

  it('覆盖率失败显示原因并允许重试', async () => {
    vi.mocked(analysisApi.trend).mockRejectedValueOnce(new Error('数据库不可用'));
    render(<Harness />);
    await screen.findByText('覆盖率读取失败：数据库不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试覆盖率' }));
    await screen.findByText(/覆盖率 80.0%/);
  });
});

describe('范围覆盖率请求隔离', () => {
  it('范围变化立即撤下旧覆盖率，取消旧请求并忽略迟到结果', async () => {
    let complete!: (value: { summary: Summary; buckets: [] }) => void;
    vi.mocked(analysisApi.trend).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
    const hook = renderHook(({ dataset }) => useProfileSummaries(options.profiles.slice(0, 1), dataset, [], '2023-02-01', '2023-02-28'),
      { initialProps: { dataset: '["source","data-a"]' } });
    const signal = vi.mocked(analysisApi.trend).mock.calls[0][1]!;
    hook.rerender({ dataset: '["source","data-b"]' });
    expect(signal.aborted).toBe(true);
    expect(hook.result.current.summaries).toEqual({});
    await waitFor(() => expect(hook.result.current.summaries[1]).toEqual(summary));
    await act(async () => { complete({ summary: { ...summary, successful_reviews: 999 }, buckets: [] }); });
    expect(hook.result.current.summaries[1].successful_reviews).toBe(20);
  });
});

