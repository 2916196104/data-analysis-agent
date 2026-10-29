import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DatasetSummary from './DatasetSummary';
import type { Options } from '../../../types';

const options: Options = { profiles: [
  { profile_id: 1, requested_model: 'model-one', base_url: 'https://one.invalid', prompt_version: 'v1' },
  { profile_id: 2, requested_model: 'model-two', base_url: 'https://two.invalid', prompt_version: 'v2' },
], selected_profile_id: 1, products: [], aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {},
  datasets: [
    { source: 'one', dataset_version: 'shared', review_count: 30, product_count: 3, eligible_text_reviews: 25,
      published_at_start: '2018-07-10T00:00:00Z', published_at_end: '2018-07-20T00:00:00Z', profiles: [
        { profile_id: 1, successful_reviews: 20, labeling_coverage: 0.8 },
        { profile_id: 2, successful_reviews: 0, labeling_coverage: 0 },
      ] },
    { source: 'two', dataset_version: 'shared', review_count: 1, product_count: 1, eligible_text_reviews: 0,
      published_at_start: '2023-01-01', published_at_end: '2023-01-01', profiles: [
        { profile_id: 1, successful_reviews: 0, labeling_coverage: null },
        { profile_id: 2, successful_reviews: 0, labeling_coverage: null },
      ] },
  ],
};
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('数据集级摘要', () => {
  it('显示评论数、商品数、UTC首末日期及所有版本各自的分子分母与覆盖率', () => {
    const view = render(<DatasetSummary dataset='["one","shared"]' options={options} />);
    const scope = screen.getByRole('region', { name: '数据集摘要' });
    expect(scope.textContent).toContain('评论数 30');
    expect(scope.textContent).toContain('商品数 3');
    expect(scope.textContent).toContain('2018-07-10 — 2018-07-20');
    const row = screen.getByText('model-one').closest('tr')!;
    expect(within(row).getByText('20')).toBeTruthy();
    expect(within(row).getByText('25')).toBeTruthy();
    expect(within(row).getByText('80.0%')).toBeTruthy();
    expect(screen.getByText('model-two').closest('tr')!.textContent).toContain('0.0%');
    view.rerender(<DatasetSummary dataset='["two","shared"]' options={options} />);
    expect(scope.textContent).toContain('2023-01-01 — 2023-01-01');
    expect(scope.textContent).not.toContain('2018-07-10');
    expect(scope.textContent).not.toContain('80.0%');
    expect(scope.textContent).not.toContain('0.0%');
    expect(screen.getByText('暂无有效正文，覆盖率无法计算。')).toBeTruthy();
  });

  it('未选择、摘要缺失和没有标注版本时保留明确状态，折叠时不挂载版本表', async () => {
    const view = render(<DatasetSummary dataset="" options={options} />);
    expect(screen.getByRole('status').textContent).toContain('选择数据集');
    view.rerender(<DatasetSummary dataset="missing" options={options} />);
    expect(screen.getByRole('status').textContent).toContain('摘要暂不可用');
    view.rerender(<DatasetSummary dataset='["one","shared"]' options={{ ...options, profiles: [] }} compact />);
    expect(screen.queryByText('暂无标注版本，准备数据后可查看覆盖率。')).toBeNull();
    fireEvent.click(screen.getByText(/各标注版本覆盖率/));
    expect(await screen.findByText('暂无标注版本，准备数据后可查看覆盖率。')).toBeTruthy();
    expect(screen.getByText(/各标注版本覆盖率/).textContent).toContain('全数据集');
  });
});

