import type { Page, Submission, Task, TaskInput, TaskSummary } from '../../types';
import { query, request } from './http-client';

export const taskApi = {
  tasks: (page = 1) => request<Page<TaskSummary>>(`/tasks${query({ page, page_size: 15 })}`),
  task: (id: string, signal?: AbortSignal) => request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal }),
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
