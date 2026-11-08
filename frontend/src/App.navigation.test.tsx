import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { AnalysisDraft } from './analysis-draft';
import type { Options, Quality } from './types';
import { api } from './api';
import App from './App';

vi.mock('./app/AppLayout', () => ({ default: ({ children, onAgent }: { children: ReactNode; onAgent: () => void }) =>
  <main><button onClick={onAgent}>通用AI入口</button>{children}</main> }));
vi.mock('./Composer', () => ({ default: ({ onAgent, setDraft }: { onAgent: () => void; setDraft: (next: (old: AnalysisDraft) => AnalysisDraft) => void }) =>
  <><button onClick={() => setDraft(old => ({ ...old, productIds: ['P1'] }))}>选择演示商品</button><button onClick={onAgent}>商品AI洞察</button></> }));
vi.mock('./features/agent/AiAssistantPage', () => ({ default: ({ mode, parameters }: { mode: string; parameters?: { source: string; dataset_version: string; product_ids: string[] } }) =>
  <div><output aria-label="当前问答模式">{mode}</output><output aria-label="当前分析范围">{JSON.stringify(parameters)}</output></div> }));

beforeEach(() => {
  localStorage.clear(); window.history.replaceState(null, '', '/analysis/new');
  vi.spyOn(api, 'options').mockResolvedValue({
    products: [{ product_id: 'P1', source: 'reviews_csv', dataset_version: 'test', title: '合成商品', review_count: 5 }],
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
    source: 'reviews_csv', dataset_version: 'test', product_ids: ['P1'],
  }));
});

it('侧栏通用AI入口保留原基础聊天默认模式', async () => {
  render(<App />);
  fireEvent.click(screen.getByRole('button', { name: '通用AI入口' }));
  expect((await screen.findByLabelText('当前问答模式')).textContent).toBe('basic');
});
