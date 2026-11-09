import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ChatMessage from './ChatMessage';
import AgentResults from '../../AgentResults';
import type { AgentChat } from './useAgentChat';
import type { ChatTurn } from './chat-state';
import type { AgentResult, AgentToolResult, PublicAnswer, Task } from '../../types';
import { emptyTaskState } from '../../task-state';
import { useTask } from '../../useTask';
import { taskApi } from '../../shared/api/task-api';
import { buildAgentReport } from './report-export';
import { publicAnswerText } from './public-answer';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const scope = { model: 'PRIVATE-MODEL', base_url: 'https://private.invalid', prompt_version: 'PRIVATE-VERSION',
  product_ids: ['A'], source: 'test', dataset_version: 'v1', date_from: '2023-01-01', date_to: '2023-03-31',
  baseline_from: '2022-12-01', baseline_to: '2022-12-31' };
const answer: PublicAnswer = { schema_version: '1.0', status: 'ok', summary: '好评主要集中在使用效果和物流。',
  highlights: [{ finding_ref: 'finding:1', aspect_key: 'effectiveness', scope_ref: 'scope:1', source_result_ids: ['r1'], text: '使用效果：2 条，占正向观点评论的 66.7%。' }],
  metrics: [], sample_note: '本次分析基于 3 条成功标注评论。', caveats: ['部分样本覆盖率不足。'],
  evidence_refs: [], generation_status: 'template', source_result_ids: ['r1'], scope_summary: [], details_available: true };
const result: AgentResult = { agent: true, status: 'ok', question: '好评', scope, snapshot_at: '',
  answer: '[overview] [r1] PRIVATE-MODEL https://private.invalid', public_answer: answer,
  result_ids: [], results: {}, trace: [], model_calls: 2, tool_calls: 1, elapsed_seconds: 1, validation: { status: 'passed', checks: [] } };
const task: Task = { task_id: 'task', kind: 'analysis', status: 'succeeded', parameters: scope,
  result: result as unknown as Task['result'], error: null, parent_task_id: null, created_at: '', started_at: '', finished_at: '', updated_at: '',
  progress: { stage: 'agent_completed', completed: 1, total: 1, unit: 'tools' } };
const turn: ChatTurn = { kind: 'analysis', id: 'turn', question: '好评', parameters: scope, requestKey: 'key', taskId: 'task' };
function show(saved: Task = task, queryTurn: ChatTurn = turn) {
  const chat = { session: { activeId: 'turn' }, activeState: { ...emptyTaskState, id: 'task', task: saved },
    errors: {}, pending: false, retry: vi.fn() } as unknown as AgentChat;
  return render(<ChatMessage turn={queryTurn} chat={chat} onPrepare={vi.fn()} />);
}
beforeEach(() => {
  vi.mocked(useTask).mockReturnValue({ ...emptyTaskState, refresh: vi.fn() });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('新聊天使用公开回答，不显示旧编号/配置，不重复背景，保留统计详情入口', () => {
  const { container } = show();
  const body = screen.getByRole('region', { name: '分析回答' });
  expect(within(body).getByText(answer.summary)).toBeTruthy();
  expect(body.textContent).not.toMatch(/overview|r1|PRIVATE|https:/);
  expect(within(body).getAllByText(answer.sample_note)).toHaveLength(1);
  expect((container.querySelector('.chat-turn-scope') as HTMLDetailsElement).open).toBe(false);
  fireEvent.click(screen.getByText('查看统计、证据与执行记录'));
  expect(screen.getByText('执行与校验')).toBeTruthy();
  expect(screen.getByRole('button', { name: '导出分析报告' })).toBeTruthy();
});

it('降级显示可靠摘要与明确生成状态，不显示模型错误原文', () => {
  show({ ...task, result: { ...result, public_answer: { ...answer, generation_status: 'fallback' } } as unknown as Task['result'] });
  expect(screen.getByText('解释生成未完成，已根据核验统计生成可靠摘要。')).toBeTruthy();
  expect(screen.getByText(answer.summary)).toBeTruthy();
});

it('本地编码失败展示可操作提示且不发布证据；首次准备显示等待说明', () => {
  const failed = show({ ...task, status: 'failed', result: null, error: { code: 'business_error', message: 'embedding_failed' } });
  expect(screen.getByText('本地语义检索暂不可用，请检查模型运行配置后重试。')).toBeTruthy();
  expect(screen.queryByRole('region', { name: '分析回答' })).toBeNull();
  failed.unmount();
  show({ ...task, status: 'running', result: null, progress: { stage: 'starting', completed: 0, total: null, unit: 'items' } });
  expect(screen.getByText('正在准备分析，首次本地检索可能需要加载模型，请稍候…')).toBeTruthy();
});

it('把事实、可能解释和分析建议分开展示，复制保留性质标签', () => {
  const enriched: PublicAnswer = { ...answer, insights: [
    { kind: 'interpretation', text: '这是对已有评价关系的可能理解。', finding_refs: ['finding:1'], evidence_ids: [] },
    { kind: 'suggestion', text: '可先复核对应原文。', finding_refs: ['finding:1'], evidence_ids: [] },
  ] };
  show({ ...task, result: { ...result, public_answer: enriched } as unknown as Task['result'] });
  expect(screen.getByText('可能的解释：')).toBeTruthy();
  expect(screen.getByText('分析建议：')).toBeTruthy();
  expect(publicAnswerText(enriched)).toContain('可能的解释：这是对已有评价关系的可能理解。');
  expect(publicAnswerText(enriched)).toContain('分析建议：可先复核对应原文。');
});

it('复制操作只复制公开回答正文，不包含内部结果或配置', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const { default: View } = await import('./PublicAnswerView');
  render(<View answer={answer} />);
  fireEvent.click(screen.getByRole('button', { name: '复制回答' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(publicAnswerText(answer)));
  expect(writeText.mock.calls[0][0]).not.toContain('PRIVATE-MODEL');
  expect(writeText.mock.calls[0][0]).not.toContain('https://private.invalid');
});

it('回答默认显示精简范围，完整范围保持折叠', () => {
  const { container } = show();
  expect(screen.getByText('当前数据集：v1')).toBeTruthy();
  expect(screen.getByText('商品：A')).toBeTruthy();
  expect((container.querySelector('.chat-turn-scope') as HTMLDetailsElement).open).toBe(false);
  expect((container.querySelector('.chat-developer-scope') as HTMLDetailsElement).open).toBe(false);
});

it('失败任务显示实际执行商品及历史主题超时原因，仍隐藏未发布内容', () => {
  const failed = { ...task, status: 'failed', parameters: { ...scope, product_ids: [] },
    error: { code: 'business_error', message: '二级主题归并未完成' }, result: { ...result, status: 'error',
      scope: { ...scope, product_ids: ['100013588053'] },
      results: { r1: { result_id: 'r1', kind: 'aspect_drilldown', parameters: {}, data: {
        aspect: 'service', analysis_status: 'failed', failures: [{ batch: 1, code: 'timeout' }],
        topics: [{ name: '不得发布的主题' }], items: [{ text: '不得发布的评论原文' }],
      } } },
    } } as unknown as Task;
  show(failed, { ...turn, parameters: { ...scope, product_ids: [] } });
  expect(screen.getByText('商品：100013588053')).toBeTruthy();
  expect(screen.getByText('本轮实际执行范围')).toBeTruthy();
  expect(screen.getByText('客服主题归属复核第 1 批请求超时。')).toBeTruthy();
  expect(screen.queryByText('不得发布的主题')).toBeNull();
  expect(screen.queryByText('不得发布的评论原文')).toBeNull();
  expect(screen.queryByRole('region', { name: '分析回答' })).toBeNull();
  expect(screen.queryByRole('button', { name: '导出分析报告' })).toBeNull();
});

it('未执行查询的澄清显示已解析单商品范围，不标为实际执行', () => {
  const summary = '当前商品为 A，已保留差评条件。请选择要展开的维度。';
  show({ ...task, parameters: { ...scope, product_ids: [] }, result: { ...result,
    status: 'unsupported', query_executions: 0, public_answer: { ...answer, status: 'unsupported', summary, highlights: [] },
  } as unknown as Task['result'] }, { ...turn, parameters: { ...scope, product_ids: [] } });
  expect(screen.getByText('商品：A')).toBeTruthy();
  expect(screen.getByText('本轮解析范围（未执行查询）')).toBeTruthy();
  expect(screen.queryByText('本轮实际执行范围')).toBeNull();
  expect(screen.getByText(summary)).toBeTruthy();
});

it('证据详情只提供下一页入口，不自动展开更多原文', () => {
  const next = vi.fn();
  const evidenceResult = { ...result, public_answer: answer, result_ids: ['e1'], results: {
    e1: { result_id: 'e1', parameters: { page: 1 }, kind: 'evidence', data: {
      total: 3, page: 1, items: [{ review_id: 1, product_id: 'A', text: '片段', published_at: '2023-01-01', evidence_ref: 'e:1', opinion_source: 'model', opinions: [] }],
    } },
  } } as unknown as AgentResult;
  render(<AgentResults result={evidenceResult} onDetail={vi.fn()} onEvidenceNext={next} />);
  fireEvent.click(screen.getByText('继续查看下一页'));
  expect(next).toHaveBeenCalledOnce();
});

it.each(['failed', 'partial', 'running', 'queued'] as const)('%s 即使携带成功公开回答也不发布正文和导出', status => {
  show({ ...task, status });
  expect(screen.queryByRole('region', { name: '分析回答' })).toBeNull();
  expect(screen.queryByRole('button', { name: '导出分析报告' })).toBeNull();
});

it('成功任务携带失败公开回答时关闭发布', () => {
  show({ ...task, result: { ...result, public_answer: { ...answer, status: 'failed' } } as unknown as Task['result'] });
  expect(screen.queryByRole('region', { name: '分析回答' })).toBeNull();
  expect(screen.getByText('分析结果未通过校验，未发布回答。')).toBeTruthy();
});

it('报告正文优先公开回答，完整旧数据继续保存在审计附录', () => {
  const report = buildAgentReport(result);
  expect(report.split('## 统计口径与来源')[0]).toContain(answer.summary);
  expect(report.split('## 统计口径与来源')[0]).not.toContain('[overview]');
  expect(report).toContain('[overview]');
});

it('数值校验通过时仍独立显示业务任务证据不足', () => {
  render(<AgentResults result={{ ...result, task_acceptance: { status: 'insufficient_evidence', complete: false,
    message: '当前没有足够的子主题证据。', checks: [{ name: '子主题及去重证据', passed: false, detail: '没有可支持主题。' }] } }} onDetail={vi.fn()} />);
  expect(screen.getByText('本轮任务验收：未完成')).toBeTruthy();
  expect(screen.getByText('结果校验通过')).toBeTruthy();
  expect(screen.getByText('当前没有足够的子主题证据。')).toBeTruthy();
});

it('历史任务客户端从公开接口转换，既不重查统计也不重新提交模型任务', async () => {
  const oldResult = { ...result, public_answer: undefined };
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { ...task, result: oldResult } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { public_answer: answer } })));
  vi.stubGlobal('fetch', fetcher);
  const saved = await taskApi.task('task');
  expect(saved.result?.public_answer).toEqual(answer);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toContain('/public/tasks/task');
  expect(oldResult.public_answer).toBeUndefined();
});

it('维度追问的统计详情只展示被选中的维度，完整审计数据仍保留', () => {
  const scoped = { ...result, result_ids: ['issues'], results: { issues: {
    result_id: 'issues', kind: 'issues', parameters: { aspect: 'logistics' }, data: { issues: [
      { rank: 1, aspect: 'packaging', aspect_label: '包装', negative_review_count: 2, negative_rate: 0.5, negative_rate_change_pp: null },
      { rank: 2, aspect: 'logistics', aspect_label: '物流', negative_review_count: 1, negative_rate: 0.25, negative_rate_change_pp: null },
    ] },
  } } } as unknown as AgentResult;
  render(<AgentResults result={scoped} onDetail={vi.fn()} />);
  expect(screen.queryByRole('cell', { name: '包装' })).toBeNull();
  expect(screen.getByRole('cell', { name: '物流' })).toBeTruthy();
  const body = buildAgentReport(scoped).split('## 完整结果与来源附录')[0];
  expect(body).toContain('| 物流 | 1 | 25.0% |');
  expect(body).not.toContain('| 包装 |');
  expect(body).toContain('汇总 1 个维度');
  expect((scoped.results.issues as Extract<AgentToolResult, { kind: 'issues' }>).data.issues).toHaveLength(2);
});

it.each(['positive_aspects', 'issues'])('已有公开摘要的 %s 维度任务也从快照重建回答', async kind => {
  const saved = { ...task, result: { ...result, results: { r1: { kind, parameters: { aspect: 'logistics' } } } } };
  const corrected = { ...answer, summary: '本轮仅统计物流。', highlights: [] };
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: saved })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { public_answer: corrected } })));
  vi.stubGlobal('fetch', fetcher);
  const rebuilt = await taskApi.task('task');
  expect(rebuilt.result?.public_answer).toEqual(corrected);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(saved.result.public_answer).toEqual(answer);
});

it('维度任务公开视图读取失败时不复用旧摘要，保留原始统计详情', async () => {
  const saved = { ...task, result: { ...result, results: { r1: { kind: 'positive_aspects', parameters: { aspect: 'logistics' } } } } };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: saved })))
    .mockResolvedValueOnce(new Response('', { status: 503 })));
  const rebuilt = await taskApi.task('task');
  expect(rebuilt.result?.public_answer).toBeUndefined();
  expect(rebuilt.result?.answer).toBeUndefined();
  expect(rebuilt.result?.results).toEqual(saved.result.results);
});
