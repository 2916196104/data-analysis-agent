import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ReviewDashboard from './ReviewDashboard';
import { analysisApi } from '../../shared/api/analysis-api';
import type { AnalysisParameters, Run } from '../../types';

const chartProps = vi.hoisted(() => vi.fn());
vi.mock('../../Chart', () => ({ default: (props: unknown) => {
  chartProps(props);
  return <div role="img" aria-label="趋势图" />;
} }));
const parameters: AnalysisParameters = { product_ids: ['A'], source: 'reviews_csv', dataset_version: 'v1',
  model: 'test', base_url: 'offline', prompt_version: 'v1', date_from: '2023-02-01', date_to: '2023-02-28',
  baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const trend = (rate: number | null): Run['results']['trend'] => ({ buckets: [], summary: {
  review_count: 10, eligible_text_reviews: 10, successful_reviews: rate === null ? 0 : 10,
  failed_reviews: 0, unprocessed_reviews: rate === null ? 10 : 0, negative_review_count: 0,
  labeling_coverage: rate === null ? 0 : 1, negative_rate: rate,
} });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('首页真实统计', () => {
  it('将接口月桶的总量和差评量绑定到同一重叠柱状图', async () => {
    const response = trend(0.5);
    response.buckets = [{ ...response.summary, bucket: '2023-02', negative_review_count: 5 }];
    vi.spyOn(analysisApi, 'trend').mockResolvedValue(response);
    render(<ReviewDashboard parameters={parameters} productCount={1} hasProducts />);
    await screen.findByRole('img', { name: '趋势图' });
    const { option, label } = chartProps.mock.calls.at(-1)![0];
    expect(label).toBe('每月总评论数和差评数的前后重叠柱状图');
    expect(option.xAxis.map((axis: { data: string[] }) => axis.data)).toEqual([['2023-02'], ['2023-02']]);
    expect(option.series.map((series: { type: string; data: number[] }) => [series.type, series.data])).toEqual([
      ['bar', [10]], ['bar', [5]],
    ]);
  });

  it('缺少版本不请求统计，不把未统计显示为零', () => {
    const fetcher = vi.spyOn(analysisApi, 'trend');
    const { container } = render(<ReviewDashboard productCount={2} hasProducts />);
    expect(fetcher).not.toHaveBeenCalled();
    expect([...container.querySelectorAll('.review-metric strong')].map(item => item.textContent)).toEqual(['—', '—', '—', '2']);
  });

  it('区分空分母和真实零负面占比，剔除基线任务字段', async () => {
    const fetcher = vi.spyOn(analysisApi, 'trend').mockResolvedValue(trend(null));
    const { container, rerender } = render(<ReviewDashboard parameters={parameters} productCount={1} hasProducts />);
    await waitFor(() => expect(container.querySelectorAll('.review-metric strong')[1].textContent).toBe('0'));
    expect(container.querySelectorAll('.review-metric strong')[2].textContent).toBe('—');
    expect(fetcher.mock.calls[0][0]).not.toHaveProperty('baseline_from');
    fetcher.mockResolvedValue(trend(0));
    rerender(<ReviewDashboard parameters={{ ...parameters, product_ids: ['B'] }} productCount={1} hasProducts />);
    await waitFor(() => expect(screen.getByText('0.0%')).toBeTruthy());
  });

  it('切换范围取消旧请求，迟到结果不覆盖新范围', async () => {
    let resolveFirst!: (value: Run['results']['trend']) => void;
    const first = new Promise<Run['results']['trend']>(resolve => { resolveFirst = resolve; });
    const fetcher = vi.spyOn(analysisApi, 'trend').mockReturnValueOnce(first).mockResolvedValueOnce(trend(0.2));
    const { rerender } = render(<ReviewDashboard parameters={parameters} productCount={1} hasProducts />);
    rerender(<ReviewDashboard parameters={{ ...parameters, product_ids: ['B'] }} productCount={1} hasProducts />);
    expect(fetcher.mock.calls[0][1]?.aborted).toBe(true);
    await screen.findByText('20.0%');
    await act(async () => { resolveFirst(trend(0.8)); });
    expect(screen.queryByText('80.0%')).toBeNull();
  });

  it('失败显示错误，重试后恢复真实结果', async () => {
    vi.spyOn(analysisApi, 'trend').mockRejectedValueOnce(new Error('统计服务不可用')).mockResolvedValueOnce(trend(0));
    render(<ReviewDashboard parameters={parameters} productCount={1} hasProducts />);
    await screen.findByText('统计服务不可用');
    fireEvent.click(screen.getByRole('button', { name: '重试统计' }));
    await screen.findByText('0.0%');
    expect(screen.queryByText('统计服务不可用')).toBeNull();
  });

  it('刷新目录后，即使筛选不变也重新读取统计', async () => {
    const fetcher = vi.spyOn(analysisApi, 'trend').mockResolvedValueOnce(trend(0.2)).mockResolvedValueOnce(trend(0.3));
    const { rerender } = render(<ReviewDashboard parameters={parameters} productCount={1} hasProducts refreshKey={1} />);
    await screen.findByText('20.0%');
    rerender(<ReviewDashboard parameters={parameters} productCount={1} hasProducts refreshKey={2} />);
    await screen.findByText('30.0%');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
