import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AGENT_DATASET, agentAnalysisParameters, datasetKey, emptyDraft, taskDataset } from '../../analysis-draft';
import { api } from '../../api';
import { emptyTaskState } from '../../task-state';
import type { AnalysisParameters, Options, Task } from '../../types';
import { useTask } from '../../useTask';
import { useAgentChat } from './useAgentChat';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
const csv: AnalysisParameters = { source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['100013588055'],
  model: 'offline', base_url: 'offline', prompt_version: 'offline', date_from: '2026-10-01', date_to: '2026-10-31',
  baseline_from: '2026-09-01', baseline_to: '2026-09-30' };
const amazon = { ...csv, source: 'amazon_reviews_2023', dataset_version: 'amazon-reviews-2023', product_ids: ['B0ZZZZZZZZ'] };
const options: Options = { profiles: [{ profile_id: 1, requested_model: 'offline', base_url: 'offline', prompt_version: 'offline' }],
  selected_profile_id: 1,
  products: [amazon, csv].map(scope => ({ key: scope.source!, source: scope.source!, dataset_version: scope.dataset_version!,
    product_id: scope.product_ids[0], product_title: '合成目录', review_count: 5, pending_count: 0, success_count: 5 })),
  aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };
const draft = { ...emptyDraft(), dataset: AGENT_DATASET, productIds: csv.product_ids, profileId: 1,
  current: [csv.date_from, csv.date_to] as [string, string], baseline: [csv.baseline_from, csv.baseline_to] as [string, string] };
const tasks = new Map<string, Task>();
const savedTask = (id: string, scope: AnalysisParameters): Task => ({ task_id: id, kind: 'analysis', status: 'succeeded',
  parameters: { ...scope, question: '已有差评查询' }, parent_task_id: null, error: null, created_at: '', updated_at: '',
  started_at: null, finished_at: null, progress: { stage: 'agent_completed', completed: 0, total: 0, unit: 'tools' }, result: { agent: true, status: 'ok', scope,
    validation: { status: 'passed', checks: [] }, result_ids: [], results: {} } });

beforeEach(() => {
  localStorage.clear(); tasks.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? tasks.get(id) || null : null, refresh: vi.fn() }));
  vi.spyOn(api, 'task').mockImplementation(async id => tasks.get(id)!);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('A：助手参数来自 CSV 目录，保留完整来源、版本和商品', () => {
  expect(agentAnalysisParameters(draft, options)).toEqual(csv);
  expect(agentAnalysisParameters({ ...draft, productIds: amazon.product_ids }, options)).toBeUndefined();
});

it('E：MVP 目录缺失或选择其他来源时参数不可用，不使用目录第一项', () => {
  expect(agentAnalysisParameters(draft, { ...options, products: [options.products[0]] })).toBeUndefined();
  expect(agentAnalysisParameters({ ...draft, dataset: datasetKey(amazon), productIds: amazon.product_ids }, options)).toBeUndefined();
});

it('G：历史 Amazon 身份取自任务参数，读取不改写任务', () => {
  const task = savedTask('amazon', amazon), original = structuredClone(task);
  expect(taskDataset(task)).toEqual({ source: amazon.source, dataset_version: amazon.dataset_version });
  expect(task).toEqual(original);
});

it('历史来源缺失时只接受一致的保存范围，不从当前目录或 Amazon 默认值猜测', () => {
  const task = savedTask('old', amazon);
  task.parameters = { dataset_version: amazon.dataset_version };
  expect(taskDataset(task)).toEqual({ source: amazon.source, dataset_version: amazon.dataset_version });
  task.result = null;
  expect(taskDataset(task)).toBeUndefined();
  task.result = { scope: { ...csv } };
  expect(taskDataset(task)).toBeUndefined();
});

it('H：绕过页面直接调用聊天提交也拒绝非 MVP 来源或版本', async () => {
  const submit = vi.spyOn(api, 'submit');
  const hook = renderHook(() => useAgentChat());
  for (const scope of [amazon, { ...csv, dataset_version: 'other' }]) {
    await act(async () => expect(await hook.result.current.send(scope, '查看差评')).toBe(false));
  }
  expect(submit).not.toHaveBeenCalled();
  expect(hook.result.current.conversation.turns).toEqual([]);
});

it('F/G：历史 Amazon 任务可恢复查看，新的 CSV 请求不携带旧目标或任务引用', async () => {
  tasks.set('amazon', savedTask('amazon', amazon));
  const submit = vi.spyOn(api, 'submit').mockImplementation(async input => {
    const item = savedTask('csv', input.parameters as AnalysisParameters); tasks.set('csv', item);
    return { task: item, reused: false };
  });
  const hook = renderHook(() => useAgentChat('amazon', AGENT_DATASET, csv, AGENT_DATASET));
  await waitFor(() => expect(hook.result.current.activeTurn?.parameters.source).toBe(amazon.source));
  expect(hook.result.current.session.context).toBeUndefined();
  await act(async () => { await hook.result.current.send(csv, '商品 100013588055 有哪些差评？'); });
  expect(submit).toHaveBeenCalledOnce();
  expect(submit.mock.calls[0][0].parameters).toMatchObject(csv);
  expect((submit.mock.calls[0][0].parameters as AnalysisParameters).context_task_id).toBeUndefined();
  expect(hook.result.current.session.turns[0].parameters.source).toBe(amazon.source);
});

it('F：切走再切回数据集不恢复旧待完成目标，历史消息仍保留', async () => {
  const submit = vi.spyOn(api, 'submit').mockImplementation(async input => {
    const id = String(tasks.size), scope = input.parameters as AnalysisParameters;
    const item = { ...savedTask(id, scope), parameters: { ...input.parameters } };
    tasks.set(id, item); return { task: item, reused: false };
  });
  const hook = renderHook(({ dataset }) => useAgentChat(undefined, dataset,
    dataset === AGENT_DATASET ? csv : amazon, dataset), { initialProps: { dataset: AGENT_DATASET } });
  await act(async () => { await hook.result.current.send(csv, '商品 100013588055 有哪些差评？'); });
  expect(hook.result.current.session.intent).toBeDefined();
  hook.rerender({ dataset: datasetKey(amazon) });
  await waitFor(() => expect(hook.result.current.session.intent).toBeUndefined());
  expect(hook.result.current.session.context).toBeUndefined();
  hook.rerender({ dataset: AGENT_DATASET });
  await act(async () => { await hook.result.current.send(csv, '商品 100013588055 有哪些差评？'); });
  expect((submit.mock.calls[1][0].parameters as AnalysisParameters).context_task_id).toBeUndefined();
  expect(hook.result.current.session.turns).toHaveLength(1);
  expect(hook.result.current.sessions.flatMap(session => session.turns)).toHaveLength(2);
  expect(hook.result.current.sessions[0].turns[0]).toMatchObject({ parameters: { source: 'reviews_csv' } });
  hook.unmount();
  const refreshed = renderHook(() => useAgentChat(undefined, AGENT_DATASET, csv, AGENT_DATASET));
  expect(refreshed.result.current.session.turns).toHaveLength(1);
  expect(refreshed.result.current.sessions.flatMap(session => session.turns)).toHaveLength(2);
});

it('不可信历史结果与任务来源不一致时不恢复成功上下文', async () => {
  const forged = savedTask('forged', csv);
  forged.result = { ...forged.result, scope: amazon };
  tasks.set('forged', forged);
  const hook = renderHook(() => useAgentChat('forged', AGENT_DATASET, csv, AGENT_DATASET));
  await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('forged'));
  expect(hook.result.current.session.context).toBeUndefined();
  expect(hook.result.current.activeTurn?.parameters.source).toBe('reviews_csv');
});

it('目录加载后初始化 MVP 选择不关闭正在查看的历史 Amazon 任务', async () => {
  tasks.set('amazon', savedTask('amazon', amazon));
  const hook = renderHook(({ selected }) => useAgentChat('amazon', selected, selected ? csv : undefined, selected),
    { initialProps: { selected: '' } });
  await waitFor(() => expect(hook.result.current.activeTurn?.taskId).toBe('amazon'));
  hook.rerender({ selected: AGENT_DATASET });
  expect(hook.result.current.activeTurn?.taskId).toBe('amazon');
  expect(hook.result.current.activeTurn?.parameters.source).toBe('amazon_reviews_2023');
  expect(hook.result.current.session.context).toBeUndefined();
});
