import { useEffect, useReducer, useState } from 'react';
import { api, errorMessage } from './api';
import { emptyTaskState, isActive, taskReducer } from './task-state';

export function useTask(id: string | null) {
  const [state, dispatch] = useReducer(taskReducer, emptyTaskState);
  const [revision, refresh] = useState(0);
  useEffect(() => {
    dispatch({ type: 'select', id });
    if (!id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const task = await api.task(id!, controller.signal);
        if (controller.signal.aborted) return;
        dispatch({ type: 'task', id: id!, task });
        if (task.kind === 'analysis' && task.result?.run_id) {
          const run = await api.run(task.result.run_id, controller.signal);
          if (!controller.signal.aborted) dispatch({ type: 'run', id: id!, run });
        }
        if (isActive(task.status)) timer = setTimeout(poll, 1500);
      } catch (error) {
        if (controller.signal.aborted) return;
        dispatch({ type: 'error', id: id!, error: errorMessage(error) });
        timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, revision]);
  return { ...state, refresh: () => refresh(v => v + 1) };
}
