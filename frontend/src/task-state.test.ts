import { describe, expect, it } from 'vitest';
import { canRetry, emptyTaskState, isActive, progressPercent, taskReducer } from './task-state';
import type { Task } from './types';

export const exampleTask = (status: Task['status']): Task => ({
  task_id: 'task-a', kind: 'analysis', status, parent_task_id: null,
  created_at: '2026-10-21T01:00:00Z', updated_at: '2026-10-21T01:00:00Z', started_at: null, finished_at: null,
  progress: { stage: status, completed: 0, total: null, unit: 'items' }, parameters: {}, result: null, error: null,
});

describe('真实任务状态', () => {
  it('只在后端给出有效总数时计算百分比', () => {
    const progress = { stage: 'importing_reviews', completed: 1000, total: null, unit: 'lines' };
    expect(progressPercent(progress)).toBeUndefined();
    expect(progressPercent({ ...progress, total: 0 })).toBeUndefined();
    expect(progressPercent({ ...progress, total: 2000 })).toBe(50);
    expect(progressPercent({ ...progress, total: 1 })).toBe(100);
  });
  it('排队和执行中轮询，失败与部分完成才允许显式重试', () => {
    expect(isActive('queued')).toBe(true);
    expect(isActive('running')).toBe(true);
    for (const status of ['failed', 'succeeded', 'partial'] as const) expect(isActive(status)).toBe(false);
    expect(canRetry('failed')).toBe(true);
    expect(canRetry('partial')).toBe(true);
    expect(canRetry('succeeded')).toBe(false);
    expect(canRetry('running')).toBe(false);
  });
  it('网络错误保留任务最后状态，重连后清除错误', () => {
    let state = taskReducer(emptyTaskState, { type: 'select', id: 'task-a' });
    state = taskReducer(state, { type: 'task', id: 'task-a', task: exampleTask('running') });
    state = taskReducer(state, { type: 'error', id: 'task-a', error: '网络中断' });
    expect(state.task?.status).toBe('running');
    expect(state.error).toBe('网络中断');
    state = taskReducer(state, { type: 'task', id: 'task-a', task: exampleTask('succeeded') });
    expect(state.error).toBeNull();
    expect(state.task?.status).toBe('succeeded');
  });
  it('切换历史时清除旧结果，忽略旧任务的迟到响应', () => {
    let state = taskReducer(emptyTaskState, { type: 'select', id: 'task-a' });
    state = taskReducer(state, { type: 'task', id: 'task-a', task: exampleTask('running') });
    state = taskReducer(state, { type: 'select', id: 'task-b' });
    expect(state.task).toBeNull();
    const next = taskReducer(state, { type: 'task', id: 'task-a', task: exampleTask('succeeded') });
    expect(next).toBe(state);
  });
  it('刷新同一任务时保留已有结果，避免网络异常清空已知状态', () => {
    let state = taskReducer(emptyTaskState, { type: 'select', id: 'task-a' });
    state = taskReducer(state, { type: 'task', id: 'task-a', task: exampleTask('succeeded') });
    state = taskReducer(state, { type: 'select', id: 'task-a' });
    expect(state.task?.status).toBe('succeeded');
    expect(state.loading).toBe(false);
  });
});
