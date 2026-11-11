import type { Page, Submission, Task, TaskInput, TaskSummary, PublicAnswer } from '../../types';
import type { P6Publication } from '../../features/agent/p6-publication';
import { query, request } from './http-client';

export const taskApi = {
  tasks: (page = 1) => request<Page<TaskSummary>>(`/tasks${query({ page, page_size: 15 })}`),
  task: async (id: string, signal?: AbortSignal) => {
    const task = await request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal });
    if (task.result?.agent === true && task.result.p6_publication) {
      // P6 历史结果须经服务端封存复核；不能以原任务诊断 JSON 代替公开 DTO。
      try {
        const view = await request<{ p6_publication: P6Publication | null; public_answer: PublicAnswer }>(`/public/tasks/${encodeURIComponent(id)}`, { signal });
        if (view.p6_publication) return { ...task, result: { ...task.result, p6_publication: view.p6_publication, public_answer: view.public_answer } };
      } catch { /* 公开校验失败时关闭 P6 正文；任务状态和范围仍可查看。 */ }
      return { ...task, result: { ...task.result, validation: { status: 'failed' as const, checks: [] }, public_answer: undefined, answer: undefined } };
    }
    const scopedRanking = task.result?.agent === true && Object.values(task.result.results ?? {}).some(item =>
      ['positive_aspects', 'issues'].includes(item.kind) && Boolean(item.parameters.aspect));
    if (task.status === 'succeeded' && !task.error && task.result?.agent === true && (!task.result.public_answer || scopedRanking)) {
      try {
        const view = await request<{ public_answer: PublicAnswer }>(`/public/tasks/${encodeURIComponent(id)}`, { signal });
        return { ...task, result: { ...task.result, public_answer: view.public_answer } };
      } catch {
        // 历史公开视图不可用时保留详情读取，聊天不回显旧技术正文。
        if (scopedRanking) return { ...task, result: { ...task.result, public_answer: undefined, answer: undefined } };
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
