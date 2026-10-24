import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { useTask } from './useTask';
import type { Run, Task } from './types';

vi.mock('./api', () => ({ api: { task: vi.fn(), run: vi.fn() }, errorMessage: (error: Error) => error.message }));
const task = (status: Task['status']): Task => ({ task_id: 'a', kind: 'analysis', status,
  parameters: {}, result: null, error: null, progress: { stage: 'analysis', completed: 0, total: 1, unit: 'windows' },
  parent_task_id: null, created_at: '', started_at: null, finished_at: null, updated_at: '',
});

describe('任务轮询生命周期', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });
  it('经过排队、执行、完成后读取快照并停止轮询', async () => {
    vi.mocked(api.task).mockResolvedValueOnce(task('queued')).mockResolvedValueOnce(task('running'))
      .mockResolvedValueOnce({ ...task('succeeded'), result: { run_id: 'run-a' } });
    vi.mocked(api.run).mockResolvedValue({ run_id: 'run-a' } as Run);
    const { result, unmount } = renderHook(() => useTask('a'));
    await act(async () => {});
    expect(result.current.task?.status).toBe('queued');
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(result.current.task?.status).toBe('running');
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(result.current.run?.run_id).toBe('run-a');
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.task).toHaveBeenCalledTimes(3);
    unmount();
  });
  it('网络失败后退避重连，不把网络故障写成任务失败', async () => {
    vi.mocked(api.task).mockResolvedValueOnce(task('running')).mockRejectedValueOnce(new Error('断网'))
      .mockResolvedValueOnce(task('failed'));
    const { result, unmount } = renderHook(() => useTask('a'));
    await act(async () => {});
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(result.current.error).toBe('断网');
    expect(result.current.task?.status).toBe('running');
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(result.current.error).toBeNull();
    expect(result.current.task?.status).toBe('failed');
    unmount();
  });
  it('离开任务后中止请求并停止计时器', async () => {
    vi.mocked(api.task).mockResolvedValue(task('running'));
    const { unmount } = renderHook(() => useTask('a'));
    await act(async () => {});
    const signal = vi.mocked(api.task).mock.calls[0][1];
    unmount();
    expect(signal?.aborted).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(api.task).toHaveBeenCalledTimes(1);
  });
  it('刷新恢复 Agent 持久结果，不请求结构化分析快照', async () => {
    const saved = { agent: true, answer: '已保存的评论分析', result_ids: ['r1'] };
    vi.mocked(api.task).mockResolvedValue({ ...task('succeeded'), result: saved });
    const { result, unmount } = renderHook(() => useTask('a'));
    await act(async () => {});
    expect(result.current.task?.result).toEqual(saved);
    expect(api.run).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.task).toHaveBeenCalledTimes(1);
    unmount();
  });
});
