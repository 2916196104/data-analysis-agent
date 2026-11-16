import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentResults from '../../AgentResults';
import type { AgentResult, AgentToolResult, AnalysisParameters, ComparisonSummary } from '../../types';
import { buildAgentReport } from './report-export';
import { resolveQuery } from './followup';
import { sectionSummary } from '../../agent-report';

const scope: AnalysisParameters = { source: 'reviews_csv', dataset_version: 'reviews-csv', model: 'offline', base_url: 'offline', prompt_version: 'v1',
  product_ids: ['A', 'B'], date_from: '2023-02-01', date_to: '2023-02-28', baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const summary: ComparisonSummary = { review_count: 4, eligible_text_reviews: 4, successful_reviews: 4, failed_reviews: 0,
  unprocessed_reviews: 0, negative_review_count: 2, negative_rate: .5, labeling_coverage: 1, positive_review_count: 3,
  comparison_count: 2, comparison_denominator: 3, comparison_rate: .666667 };
const comparison: AgentToolResult = { result_id: 'r1', kind: 'comparison', parameters: { sentiment: 'positive', aspect: 'logistics' },
  data: { sentiment: 'positive', aspect: 'logistics', rate_denominator: 'positive_review_count',
    products: [{ product_id: 'A', ...summary }, { product_id: 'B', ...summary, comparison_count: 0, comparison_denominator: 0, comparison_rate: null }],
    no_reviews_product_ids: ['C'] } };
const windows: AgentToolResult = { result_id: 'r2', kind: 'window_comparison', parameters: { sentiment: 'positive', aspect: 'logistics' },
  data: { sentiment: 'positive', aspect: 'logistics', rate_denominator: 'positive_review_count', scope,
    baseline_scope: { ...scope, date_from: scope.baseline_from, date_to: scope.baseline_to },
    current: summary, baseline: { ...summary, comparison_count: 1, comparison_denominator: 2, comparison_rate: .5 },
    current_days: 28, baseline_days: 31, changes: { comparison_rate_change_pp: 16.666667 }, issues: [] } };
const result: AgentResult = { agent: true, status: 'ok', question: '正向对比', scope, snapshot_at: '2026-11-16T00:00:00Z',
  validation: { status: 'passed', checks: [] }, result_ids: ['r1', 'r2'], results: { r1: comparison, r2: windows },
  trace: [], model_calls: 0, tool_calls: 3, elapsed_seconds: 1 };
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
  const original = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => original(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('正向商品和窗口表格显示实际分母、好评方向、空值与百分点变化', () => {
  expect(sectionSummary(comparison)).toContain('1 款可计算占比（其中 1 款样本有限）');
  render(<AgentResults result={result} onDetail={() => {}} />);
  expect(screen.getAllByRole('columnheader', { name: '好评占比' })).toHaveLength(2);
  expect(screen.getAllByRole('columnheader', { name: '含正向观点评论（分母）' })).toHaveLength(2);
  expect(screen.queryByRole('columnheader', { name: '负面占比' })).toBeNull();
  expect(screen.getByText(/当前较基线：\+16.7 个百分点/)).toBeTruthy();
  expect(screen.getByText('C', { exact: true })).toBeTruthy();
});

it('导出的正向统计表格与界面使用同一个口径，原始结果不变', () => {
  const before = JSON.stringify(result);
  const report = buildAgentReport(result).split('## 完整结果与来源附录')[0];
  expect(report).toContain('| 商品 | 正向评论数 | 含正向观点评论（分母） | 好评占比 |');
  expect(report).toContain('| A | 2 | 3 | 66.7% |');
  expect(report).toContain('| 基线 | 1 | 2 | 50.0% |');
  expect(report).toContain('| 好评占比 | 16.666667 |');
  expect(JSON.stringify(result)).toBe(before);
});

it('整体对比支持正负向追问；单维度切换到负向时明确澄清', () => {
  const previous = { ...comparison, parameters: {} };
  const parent = { id: 'turn', taskId: 'task', parameters: scope, result: { ...result, result_ids: ['r1'], results: { r1: previous } } };
  const next = resolveQuery('那好评呢', scope, parent);
  expect(next.clarification).toBeUndefined();
  expect(next.query.queries?.[0].kind).toBe('comparison');
  expect(next.query.queries?.[0].parameters.sentiment).toBe('positive');
  const negative = resolveQuery('那差评呢', scope, { ...parent, result: { ...parent.result, results: { r1: comparison } } });
  expect(negative.clarification?.message).toContain('负向对比目前按整体统计');
});
