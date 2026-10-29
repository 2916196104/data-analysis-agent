import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AppLayout from './AppLayout';

afterEach(cleanup);
beforeEach(() => { Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() }); });

const props = () => ({
  children: <div>内容</div>, connection: { status: 'success' as const, text: '服务已连接' }, loading: false,
  datasetSummary: '当前数据集', hasProducts: true, section: 'analysis' as const,
  onRefresh: vi.fn(), onHistory: vi.fn(), onImport: vi.fn(), onPrepare: vi.fn(), onAnnotation: vi.fn(),
  onPreview: vi.fn(), onNewAnalysis: vi.fn(), onDatasets: vi.fn(), onAgent: vi.fn(),
  selectedDatasetId: '["source","reviews-a"]', datasetOptions: [
    { value: '["source","reviews-a"]', label: 'reviews-a' }, { value: '["source","reviews-b"]', label: 'reviews-b' },
  ], onDatasetChange: vi.fn(),
});

describe('顶部全局数据集选择器', () => {
  it('展示真实选项并把选择交给工作台唯一状态', () => {
    const callbacks = props();
    render(<AppLayout {...callbacks} />);
    const selector = screen.getByRole('combobox', { name: '全局数据集' });
    expect(selector.getAttribute('aria-valuenow')).toBeNull();
    expect(screen.getByText('reviews-a')).toBeTruthy();
    fireEvent.mouseDown(selector);
    fireEvent.click(screen.getByText('reviews-b'));
    expect(callbacks.onDatasetChange).toHaveBeenCalledWith('["source","reviews-b"]');
  });
});
