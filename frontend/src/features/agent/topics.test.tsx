import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AgentResult, AgentToolResult, AnalysisParameters } from '../../types';
import ExtendedResults from './ExtendedResults';
import AgentResults from '../../AgentResults';
import { resolveQuery } from './followup';

const scope: AnalysisParameters = { model: 'test', base_url: 'https://offline.invalid', prompt_version: 'test', source: 'test', dataset_version: 'test', product_ids: ['A'], date_from: '2024-01-01', date_to: '2024-01-31', baseline_from: '2023-12-01', baseline_to: '2023-12-31' };
const item: AgentToolResult = { kind: 'aspect_drilldown', result_id: 'r1', parameters: { product_ids: ['A'], aspect: 'effectiveness', sentiment: 'positive' }, data: {
  scope, aspect: 'effectiveness', sentiment: 'positive', items: [], grouping_version: 'aspect-topics-v1', grouping_model: 'test',
  summary: { review_count: 308, eligible_text_reviews: 308, successful_reviews: 308, failed_reviews: 0, unprocessed_reviews: 0, negative_review_count: 0, labeling_coverage: 1, negative_rate: 0 },
  population_mode: 'sample', analysis_status: 'complete', total: 308, total_opinions: 667,
  coverage: { selected_reviews: 4, selected_opinions: 6, evaluated_opinions: 6, classified_opinions: 4, unclassified_opinions: 2 },
  topics: [{ topic_id: 't1', name: '制冷速度', definition: '制冷快慢', aspect: 'effectiveness', sentiment: 'positive', review_count: 2, opinion_count: 3, review_rate: .5, review_denominator: 4, review_ids: [1, 2], opinion_ids: ['o1'], evidence_opinion_ids: ['o1'] }],
} };

beforeEach(() => { vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} })); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('主题表明确样本、去重分母及未归类，不冒充总体统计', () => {
  render(<ExtendedResults item={item} />);
  expect(screen.getByText(/非随机受限样本，占比不能代表总体/)).toBeTruthy();
  expect(screen.getByText('制冷速度')).toBeTruthy();
  expect(screen.getByText('分母 4 条')).toBeTruthy();
  expect(screen.getByText(/未归类 2 条/)).toBeTruthy();
  expect(screen.getByText(/未经人工确认/)).toBeTruthy();
});

it('主题追问保留分析目标和情感；部分结果不能作为可信父轮', () => {
  const result = { agent: true, status: 'ok', results: { r1: item }, result_ids: ['r1'], validation: { status: 'passed', checks: [] } } as unknown as AgentResult;
  const parent = { id: 'p', taskId: 'p', parameters: scope, result };
  expect(resolveQuery('那物流方面呢？', scope, parent).query.queries?.[0]).toEqual({ kind: 'aspect_drilldown', parameters: { product_ids: ['A'], aspect: 'logistics', sentiment: 'positive' } });
  expect(resolveQuery('那差评呢？', scope, parent).query.queries?.[0].parameters.sentiment).toBe('negative');
  expect(resolveQuery('只看物流', scope, { ...parent, result: { ...result, status: 'partial' } }).clarification).toBeTruthy();
});

it('部分任务不显示完整主题排名，保留明确的状态说明', () => {
  const result = { agent: true, status: 'partial', results: { r1: item }, result_ids: ['r1'], validation: { status: 'passed', checks: [] } } as unknown as AgentResult;
  render(<AgentResults result={result} onDetail={vi.fn()} />);
  expect(screen.getByText(/主题归属仅部分完成/)).toBeTruthy();
  expect(screen.queryByText('制冷速度')).toBeNull();
});
