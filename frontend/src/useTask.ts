import { useEffect, useReducer, useState } from 'react';
import { api, errorMessage } from './api';
import { emptyTaskState, isActive, taskReducer } from './task-state';
import type { Task, TaskSummary } from './types';

export function useTask(id: string | null, options?: { includeAudit?: boolean }) {
  const includeAudit = options?.includeAudit;
  const [state, dispatch] = useReducer(taskReducer, emptyTaskState);
  const [revision, refresh] = useState(0);
  useEffect(() => {
    dispatch({ type: 'select', id });
    if (!id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let source: EventSource | undefined;
    let latestTask: Task | undefined;
    let sseFailures = 0;
    let pollsUntilSseRetry = 0;

    function closeStream() {
      if (source) {
        source.close();
        source = undefined;
      }
    }

    function openStream() {
      if (controller.signal.aborted || source || typeof EventSource === 'undefined'
        || typeof api.taskEventsUrl !== 'function') {
        return false;
      }
      const stream = new EventSource(api.taskEventsUrl(id!));
      source = stream;
      stream.addEventListener('progress', event => {
        if (controller.signal.aborted || !latestTask) return;
        sseFailures = 0;
        pollsUntilSseRetry = 0;
        try {
          const summary = JSON.parse((event as MessageEvent<string>).data) as TaskSummary;
          latestTask = {
            ...latestTask,
            status: summary.status,
            progress: summary.progress,
            started_at: summary.started_at,
            finished_at: summary.finished_at,
            updated_at: summary.updated_at,
            error: summary.error,
          };
          dispatch({ type: 'task', id: id!, task: latestTask });
        } catch {
          // 忽略格式异常的事件帧，等待下一帧或终态拉取。
        }
      });
      stream.addEventListener('done', () => {
        sseFailures = 0;
        pollsUntilSseRetry = 0;
        closeStream();
        if (!controller.signal.aborted) void poll(true);
      });
      stream.addEventListener('error', () => {
        closeStream();
        sseFailures += 1;
        // 短暂降级为轮询并在指数退避周期（2 -> 4 -> 8 轮）后自动尝试恢复 SSE 推送。
        pollsUntilSseRetry = Math.min(8, 2 ** Math.min(sseFailures, 3));
        if (!controller.signal.aborted) {
          clearTimeout(timer);
          timer = setTimeout(() => void poll(false), 1500);
        }
      });
      return true;
    }

    async function poll(preferStream = true) {
      try {
        const task = includeAudit === false
          ? await api.task(id!, controller.signal, { includeAudit: false })
          : await api.task(id!, controller.signal);
        if (controller.signal.aborted) return;
        latestTask = task;
        dispatch({ type: 'task', id: id!, task });
        if (task.kind === 'analysis' && task.result?.run_id) {
          const run = await api.run(task.result.run_id, controller.signal);
          if (!controller.signal.aborted) dispatch({ type: 'run', id: id!, run });
        }
        if (isActive(task.status)) {
          if (preferStream) {
            if (!openStream()) {
              timer = setTimeout(() => void poll(true), 1500);
            }
          } else {
            pollsUntilSseRetry = Math.max(0, pollsUntilSseRetry - 1);
            if (pollsUntilSseRetry === 0 && openStream()) {
              return;
            }
            timer = setTimeout(() => void poll(false), 1500);
          }
        } else {
          closeStream();
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        closeStream();
        dispatch({ type: 'error', id: id!, error: errorMessage(error) });
        const status = typeof (error as { status?: unknown })?.status === 'number'
          ? (error as { status: number }).status
          : 0;
        const permanentClientError = status >= 400 && status < 500 && status !== 408 && status !== 429;
        if (!permanentClientError) {
          timer = setTimeout(() => void poll(true), 5000);
        }
      }
    }

    void poll(true);
    return () => {
      controller.abort();
      closeStream();
      clearTimeout(timer);
    };
  }, [id, revision, includeAudit]);
  return { ...state, refresh: () => refresh(v => v + 1) };
}
