import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AgentResult, AgentToolResult, Summary } from '../../types';
import { buildAgentReport } from './report-export';
import ExtendedResults from './ExtendedResults';

const scope = { model: 'saved-label-model', base_url: 'https://offline.invalid/v1', prompt_version: 'saved-v1',
  source: 'saved-source', dataset_version: 'saved-dataset', product_ids: ['P1'], date_from: '2023-02-01', date_to: '2023-02-03',
  baseline_from: '2023-01-01', baseline_to: '2023-01-05' };
const summary: Summary = { review_count: 3, eligible_text_reviews: 2, successful_reviews: 0, failed_reviews: 1,
  unprocessed_reviews: 1, empty_text_reviews: 1, negative_review_count: 0, negative_rate: null, labeling_coverage: 0 };
const quality: AgentToolResult = { result_id: 'q', kind: 'quality', parameters: {}, data: { scope, summary,
  missing_text_rate: 0.333333, failed_rate: 0.5, failures: [{ error_code: 'invalid_output', review_count: 1 }],
  sources: [{ file_sha256: 'abc123', review_count: 3 }] } };
const ratings: AgentToolResult = { result_id: 'r', kind: 'ratings', parameters: {}, data: { scope, group_by: 'product', groups: [
  { source: scope.source, dataset_version: scope.dataset_version, group: 'P1', review_count: 3, rated_reviews: 0,
    missing_rating_reviews: 2, other_rating_reviews: 1, stars: [1, 2, 3, 4, 5].map(star => ({ star, review_count: 0, rate: null })) },
] } };
const result: AgentResult = { agent: true, status: 'ok', question: '分析 <script>bad</script> ```', scope, snapshot_at: '2026-10-29T01:00:00Z',
  answer: '[q] 查询窗口共 3 条评论。', result_ids: ['q', 'r'], results: { q: quality, r: ratings },
  agent_model_identity: { model: 'saved-agent', base_url: 'https://agent.invalid/v1', prompt_version: 'review-agent-v2' },
  trace: [], model_calls: 2, tool_calls: 3, elapsed_seconds: 1, warnings: ['样本有限'] };

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
  const original = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => original(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('导出固定范围、执行与标注身份、实际数字、文件指纹及完整附录，不读当前草稿', () => {
  const before = JSON.stringify(result);
  localStorage.setItem('current-dataset', 'different-dataset');
  const report = buildAgentReport(result, 'saved-task');
  for (const text of ['saved-task', 'saved-source', 'saved-dataset', '2023-02-01 — 2023-02-03', '2023-01-01 — 2023-01-05',
    'saved-label-model', 'saved-agent', 'review-agent-v2', 'abc123', '样本有限', '| 0.0% | — |', '````json']) {
    expect(report).toContain(text);
  }
  expect(report).not.toContain('different-dataset');
  expect(report).toContain('&lt;script&gt;');
  expect(JSON.stringify(result)).toBe(before);
  const appendix = JSON.parse(report.split('````json\n')[1].split('\n````')[0]);
  expect(appendix.results).toEqual(result.results);
  expect(buildAgentReport({ ...result, scope: { ...scope, baseline_from: undefined, baseline_to: undefined } })).toContain('未记录 — 未记录');
});

it('数据质量与星级表保留不同分母，缺失评分显示空占比', () => {
  const { rerender } = render(<ExtendedResults item={quality} />);
  expect(screen.getByText('33.3%')).toBeTruthy();
  expect(screen.getByText('50.0%')).toBeTruthy();
  expect(screen.getByText('0.0%')).toBeTruthy();
  expect(screen.getByText(/invalid_output 1 条/)).toBeTruthy();
  rerender(<ExtendedResults item={ratings} />);
  expect(screen.getAllByText('—')).toHaveLength(5);
  expect(screen.queryByText('0.0%')).toBeNull();
  expect(screen.getByText('有效星级（分母）', { selector: 'thead th' })).toBeTruthy();
});

it('维度趋势呈现月份和分母，窗口表使用后端给出的百分点', () => {
  const trend: AgentToolResult = { result_id: 'a', kind: 'aspect_trend', parameters: {}, data: { scope, period: 'month',
    aspects: { packaging: '包装' }, summary: { ...summary, aspects: {} }, buckets: [{ ...summary, bucket: '2023-02',
      aspects: { packaging: { negative_review_count: 0, negative_rate: null } } }] } };
  const { rerender } = render(<ExtendedResults item={trend} />);
  expect(screen.getByText('包装')).toBeTruthy();
  expect(screen.getByText('2023-02')).toBeTruthy();
  rerender(<ExtendedResults item={{ result_id: 'w', kind: 'window_comparison', parameters: {}, data: {
    scope, baseline_scope: { ...scope, date_from: scope.baseline_from, date_to: scope.baseline_to }, current: summary,
    baseline: summary, current_days: 3, baseline_days: 5, changes: { labeling_coverage_pp: 12.345, negative_rate_pp: null }, issues: [],
  } }} />);
  expect(screen.getByText('+12.3 个百分点')).toBeTruthy();
  expect(screen.getByText(/2023-01-01 — 2023-01-05/)).toBeTruthy();
});
