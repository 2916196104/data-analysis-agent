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

  it('浏览器支持 EventSource 时优先使用 SSE 推送进度，收到 done 后拉取完整结果并关闭连接', async () => {
    const listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
    const close = vi.fn();
    class FakeEventSource {
      url: string;
      constructor(url: string) { this.url = url; }
      addEventListener(type: string, handler: (event: MessageEvent<string>) => void) {
        listeners.set(type, [...(listeners.get(type) ?? []), handler]);
      }
      close = close;
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    (api as Record<string, unknown>).taskEventsUrl = (id: string) => `/api/v1/tasks/${id}/events`;
    vi.mocked(api.task)
      .mockResolvedValueOnce(task('running'))
      .mockResolvedValueOnce({ ...task('succeeded'), result: { run_id: 'run-sse' } });
    vi.mocked(api.run).mockResolvedValue({ run_id: 'run-sse' } as Run);
    try {
      const { result, unmount } = renderHook(() => useTask('a'));
      await act(async () => {});
      expect(api.task).toHaveBeenCalledTimes(1);
      await act(async () => {
        const progress = { ...task('running'), progress: { stage: 'agent_tool', completed: 2, total: 3, unit: 'tools' } };
        listeners.get('progress')?.forEach(fn => fn({ data: JSON.stringify(progress) } as MessageEvent<string>));
      });
      expect(result.current.task?.progress.completed).toBe(2);
      // SSE 连接期间不触发 1.5s 轮询
      await act(() => vi.advanceTimersByTimeAsync(6000));
      expect(api.task).toHaveBeenCalledTimes(1);
      await act(async () => {
        listeners.get('done')?.forEach(fn => fn({ data: JSON.stringify(task('succeeded')) } as MessageEvent<string>));
      });
      expect(close).toHaveBeenCalled();
      expect(api.task).toHaveBeenCalledTimes(2);
      expect(result.current.run?.run_id).toBe('run-sse');
      unmount();
    } finally {
      delete (api as Record<string, unknown>).taskEventsUrl;
      vi.unstubAllGlobals();
    }
  });

  it('SSE 连接异常时自动降级回轮询，不中断任务状态更新', async () => {
    const listeners = new Map<string, ((event: Event) => void)[]>();
    const close = vi.fn();
    class FakeEventSource {
      addEventListener(type: string, handler: (event: Event) => void) {
        listeners.set(type, [...(listeners.get(type) ?? []), handler]);
      }
      close = close;
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    (api as Record<string, unknown>).taskEventsUrl = (id: string) => `/api/v1/tasks/${id}/events`;
    vi.mocked(api.task)
      .mockResolvedValueOnce(task('running'))
      .mockResolvedValueOnce(task('succeeded'));
    try {
      const { result, unmount } = renderHook(() => useTask('a'));
      await act(async () => {});
      act(() => {
        listeners.get('error')?.forEach(fn => fn(new Event('error')));
      });
      expect(close).toHaveBeenCalled();
      await act(() => vi.advanceTimersByTimeAsync(1500));
      expect(result.current.task?.status).toBe('succeeded');
      expect(api.task).toHaveBeenCalledTimes(2);
      unmount();
    } finally {
      delete (api as Record<string, unknown>).taskEventsUrl;
      vi.unstubAllGlobals();
    }
  });
});
