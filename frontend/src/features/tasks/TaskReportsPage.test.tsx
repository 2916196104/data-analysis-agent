import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TaskReportsPage from './TaskReportsPage';
import type { TaskSummary } from '../../types';

const task: TaskSummary = { task_id: 'saved-task', kind: 'analysis', status: 'succeeded', parent_task_id: null,
  created_at: '2026-10-26T00:00:00Z', started_at: null, finished_at: null, updated_at: '',
  progress: { stage: 'completed', completed: 1, total: 1, unit: 'steps' }, error: null };
const props = () => ({ tasks: { items: [task], total: 1, page: 1, page_size: 15 }, page: 1,
  loading: false, error: '', notes: { 'saved-task': '已保存的分析' }, selectedId: task.task_id, deleteBusy: false,
  formatCreatedAt: (value: string) => value, onRefresh: vi.fn(), onPage: vi.fn(), onSelect: vi.fn(),
  onDelete: vi.fn(), onConfirmDelete: vi.fn(), onCancelDelete: vi.fn() });

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
  const original = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => original(element));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('任务与报告页面', () => {
  it('键盘打开任务，删除按钮不触发任务跳转', () => {
    const callbacks = props();
    render(<TaskReportsPage {...callbacks} />);
    const row = screen.getByText('已保存的分析').closest('[role="button"]')!;
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(callbacks.onSelect).toHaveBeenCalledExactlyOnceWith(task.task_id);
    const remove = screen.getByRole('button', { name: '删除已保存的分析' });
    fireEvent.keyDown(remove, { key: 'Enter' });
    fireEvent.click(remove);
    expect(callbacks.onSelect).toHaveBeenCalledTimes(1);
    expect(callbacks.onDelete).toHaveBeenCalledExactlyOnceWith(task.task_id, '已保存的分析');
  });

  it('删除必须经页面内确认，并允许取消', () => {
    const callbacks = props();
    render(<TaskReportsPage {...callbacks} pendingDelete={{ taskId: task.task_id, label: '已保存的分析' }} />);
    expect(callbacks.onConfirmDelete).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '取消删除任务' }));
    expect(callbacks.onCancelDelete).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '确认删除任务' }));
    expect(callbacks.onConfirmDelete).toHaveBeenCalledOnce();
  });

  it('请求失败仍保留已加载任务，并提供重试', () => {
    const callbacks = props();
    render(<TaskReportsPage {...callbacks} error="暂时无法连接服务" />);
    expect(screen.getByText('暂时无法连接服务')).toBeTruthy();
    expect(screen.getByText('已保存的分析')).toBeTruthy();
    expect(screen.queryByText('还没有任务记录')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '重试任务记录' }));
    expect(callbacks.onRefresh).toHaveBeenCalledOnce();
  });
});
