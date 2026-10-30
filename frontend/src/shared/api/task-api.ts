import type { Page, Submission, Task, TaskInput, TaskSummary, PublicAnswer } from '../../types';
import { query, request } from './http-client';

export const taskApi = {
  tasks: (page = 1) => request<Page<TaskSummary>>(`/tasks${query({ page, page_size: 15 })}`),
  task: async (id: string, signal?: AbortSignal) => {
    const task = await request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal });
    if (task.status === 'succeeded' && !task.error && task.result?.agent === true && !task.result.public_answer) {
      try {
        const view = await request<{ public_answer: PublicAnswer }>(`/public/tasks/${encodeURIComponent(id)}`, { signal });
        return { ...task, result: { ...task.result, public_answer: view.public_answer } };
      } catch {
        // 历史公开视图不可用时保留详情读取，聊天不回显旧技术正文。
      }
    }
    return task;
  },
  submit: (input: TaskInput, key: string) => request<Submission>('/tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(input),
  }),
  retry: (id: string, key: string) => request<Submission>(
    `/tasks/${encodeURIComponent(id)}/retry`,
    { method: 'POST', headers: { 'Idempotency-Key': key } },
  ),
  deleteTask: (id: string) => request<{ deleted_task_ids: string[]; deleted_tasks: number }>(
    `/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' },
  ),
};
