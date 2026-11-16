import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AgentResult, AgentToolResult } from '../../types';
import ExtendedResults from './ExtendedResults';
import { buildAgentReport } from './report-export';
import { resolveQuery } from './followup';

const scope = { source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['P1'], model: 'fixture', base_url: 'offline',
  prompt_version: 'v1', date_from: '2023-01-01', date_to: '2023-02-28', baseline_from: '2022-12-01', baseline_to: '2022-12-31' };
const summary = { review_count: 10, eligible_text_reviews: 10, successful_reviews: 8, failed_reviews: 1, unprocessed_reviews: 1,
  negative_review_count: 3, negative_rate: .375, labeling_coverage: .8, positive_review_count: 4, positive_rate: .5,
  aspects: { logistics: { negative_review_count: 1, negative_rate: .125, positive_review_count: 2, positive_rate: .5, positive_success_rate: .25 } } };
const item: AgentToolResult = { result_id: 'r1', kind: 'aspect_trend', parameters: { aspect: 'logistics', sentiment: 'positive', period: 'month' },
  data: { scope, sentiment: 'positive', period: 'month', aspects: { logistics: '物流' }, summary,
    buckets: [{ ...summary, bucket: '2023-01' }, { ...summary, positive_review_count: 0,
      aspects: { logistics: { ...summary.aspects.logistics, positive_review_count: 0, positive_rate: null } }, bucket: '2023-02' }] } };
const result: AgentResult = { agent: true, status: 'ok', question: '按月分析物流好评占比趋势', scope,
  snapshot_at: '2026-11-15T00:00:00Z', sentiment_intent: 'positive', result_ids: ['r1'], results: { r1: item },
  validation: { status: 'passed', checks: [] }, trace: [], model_calls: 0, tool_calls: 1, elapsed_seconds: 0 };

beforeEach(() => vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} })));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('正向趋势表展示正向计数和正向分母，空分母保留空值', () => {
  render(<ExtendedResults item={item} />);
  expect(screen.getAllByText('正向观点评论（分母）').length).toBeGreaterThan(0);
  expect(screen.getAllByText('好评占比').length).toBeGreaterThan(0);
  expect(screen.getAllByText('50.0%').length).toBeGreaterThan(0);
  expect(screen.queryByText('负面占比')).toBeNull();
  expect(screen.getByText(/无正向观点时不计算占比/)).toBeTruthy();
});

it('导出保留正向分母并避免导出成负面趋势', () => {
  const report = buildAgentReport(result);
  expect(report).toContain('正向观点评论（分母）');
  expect(report).toContain('| 2023-01 | 物流 | 2 | 4 | 50.0% | 8 | 80.0% |');
  expect(report).toContain('| 2023-02 | 物流 | 0 | 0 | — | 8 | 80.0% |');
});

it('日月、情感和原文追问保留趋势维度与正向筛选', () => {
  const parent = { id: 'parent', taskId: 'task', question: result.question, parameters: scope, result };
  expect(resolveQuery('改为按日', scope, parent).query.queries?.[0]).toEqual({ kind: 'aspect_trend', parameters: { ...item.parameters, period: 'day' } });
  expect(resolveQuery('那差评呢', scope, parent).query.queries?.[0]).toEqual({ kind: 'aspect_trend', parameters: { ...item.parameters, sentiment: 'negative' } });
  expect(resolveQuery('继续查原文', scope, parent).query.queries?.[0].parameters.sentiment).toBe('positive');
});
