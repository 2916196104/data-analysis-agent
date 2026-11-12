import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { AnalysisDraft } from './analysis-draft';
import type { Options, Quality } from './types';
import { api } from './api';
import App from './App';

vi.mock('./app/AppLayout', () => ({ default: ({ children, onAgent, onRefresh, selectedDatasetId, datasetOptions, onDatasetChange }: {
  children: ReactNode; onAgent: () => void; onRefresh: () => void; selectedDatasetId: string;
  datasetOptions: { value: string; label: string }[]; onDatasetChange: (value: string) => void;
}) => <main><button onClick={onAgent}>通用AI入口</button><button onClick={onRefresh}>刷新目录</button>
  <select aria-label="全局数据集" value={selectedDatasetId} onChange={event => onDatasetChange(event.target.value)}>
    <option value="">未选择</option>{datasetOptions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
  </select>{children}</main> }));
vi.mock('./Composer', () => ({ default: ({ onAgent, setDraft }: { onAgent: () => void; setDraft: (next: (old: AnalysisDraft) => AnalysisDraft) => void }) =>
  <><button onClick={() => setDraft(old => ({ ...old, productIds: ['P1'] }))}>选择演示商品</button><button onClick={onAgent}>商品AI洞察</button></> }));
vi.mock('./features/agent/AiAssistantPage', () => ({ default: ({ mode, parameters }: { mode: string; parameters?: { source: string; dataset_version: string; product_ids: string[] } }) =>
  <div><output aria-label="当前问答模式">{mode}</output><output aria-label="当前分析范围">{JSON.stringify(parameters)}</output></div> }));

beforeEach(() => {
  localStorage.clear(); window.history.replaceState(null, '', '/analysis/new');
  vi.spyOn(api, 'options').mockResolvedValue({
    products: [{ product_id: 'P1', source: 'reviews_csv', dataset_version: 'reviews-csv', title: '合成商品', review_count: 5 }],
    profiles: [{ profile_id: 1, requested_model: 'test-model', base_url: 'https://synthetic.invalid', prompt_version: 'test' }],
    processing_statuses: {}, human_statuses: {},
  } as unknown as Options);
  vi.spyOn(api, 'quality').mockResolvedValue({ published_at_end: '2023-02-28' } as Quality);
  vi.spyOn(api, 'health').mockResolvedValue({ worker_available: true } as Awaited<ReturnType<typeof api.health>>);
  vi.spyOn(api, 'tasks').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it('商品AI洞察进入数据分析而不是基础聊天，并保留当前数据集和商品', async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: '选择演示商品' }));
  fireEvent.click(screen.getByRole('button', { name: '商品AI洞察' }));
  expect((await screen.findByLabelText('当前问答模式')).textContent).toBe('analysis');
  await waitFor(() => expect(JSON.parse(screen.getByLabelText('当前分析范围').textContent!)).toMatchObject({
    source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['P1'],
  }));
});

it('侧栏通用AI入口保留原基础聊天默认模式', async () => {
  render(<App />);
  fireEvent.click(screen.getByRole('button', { name: '通用AI入口' }));
  expect((await screen.findByLabelText('当前问答模式')).textContent).toBe('basic');
});

const csvKey = JSON.stringify(['reviews_csv', 'reviews-csv']);
const amazonKey = JSON.stringify(['amazon_reviews_2023', 'amazon-reviews-2023']);
const mixedOptions = (): Options => ({ profiles: [{ profile_id: 1, requested_model: 'test-model', base_url: 'offline', prompt_version: 'test' }],
  products: [{ product_id: 'B0ZZZZZZZZ', source: 'amazon_reviews_2023', dataset_version: 'amazon-reviews-2023', review_count: 5 },
    { product_id: '100013588055', source: 'reviews_csv', dataset_version: 'reviews-csv', review_count: 5 }],
  processing_statuses: {}, human_statuses: {}, aspects: {}, sentiments: {} } as Options);

it('P1：Amazon 在目录第一项时，助手默认仍选择 reviews_csv / reviews-csv', async () => {
  vi.mocked(api.options).mockResolvedValue(mixedOptions());
  window.history.replaceState(null, '', '/assistant');
  render(<App />);
  await waitFor(() => expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe(csvKey));
  expect(JSON.parse(screen.getByLabelText('当前分析范围').textContent!)).toMatchObject({ source: 'reviews_csv', dataset_version: 'reviews-csv' });
});

it('P1：没有 MVP 数据集时不回退到 Amazon', async () => {
  vi.mocked(api.options).mockResolvedValue({ ...mixedOptions(), products: [mixedOptions().products[0]] });
  window.history.replaceState(null, '', '/assistant');
  render(<App />);
  await waitFor(() => expect(api.options).toHaveBeenCalled());
  expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe('');
  expect(screen.getByLabelText('当前分析范围').textContent).toBe('');
});

it('P1：MVP 数据集移除后清空范围并提示，不分析目录中的 Amazon 商品', async () => {
  vi.mocked(api.options).mockResolvedValue(mixedOptions());
  window.history.replaceState(null, '', '/assistant');
  render(<App />);
  await waitFor(() => expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe(csvKey));
  vi.mocked(api.options).mockResolvedValue({ ...mixedOptions(), products: [mixedOptions().products[0]] });
  fireEvent.click(screen.getByRole('button', { name: '刷新目录' }));
  await screen.findByText(/原数据集已不可用/);
  await waitFor(() => expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe(''));
  expect(screen.getByLabelText('当前分析范围').textContent).toBe('');
});

it('P1：手动切换到其他数据源后旧商品清空，助手提交参数不可用', async () => {
  vi.mocked(api.options).mockResolvedValue(mixedOptions());
  window.history.replaceState(null, '', '/assistant');
  render(<App />);
  await waitFor(() => expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe(csvKey));
  fireEvent.change(screen.getByLabelText('全局数据集'), { target: { value: amazonKey } });
  await waitFor(() => expect((screen.getByLabelText('全局数据集') as HTMLSelectElement).value).toBe(amazonKey));
  expect(screen.getByLabelText('当前分析范围').textContent).toBe('');
});
