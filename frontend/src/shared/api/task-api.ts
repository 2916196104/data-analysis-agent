import type {
  AgentResult,
  Page,
  PublicAnswer,
  ServerConversationDetail,
  ServerConversationSummary,
  Submission,
  Task,
  TaskInput,
  TaskSummary,
} from '../../types';
import type { P6Publication } from '../../features/agent/p6-publication';
import { API_BASE, query, request } from './http-client';

export interface TaskAuditRecord {
  task_id: string;
  stored_separately: boolean;
  storage_version: string | null;
  audit_sha256: string | null;
  audit_bytes: number;
  fields: string[];
  audit: Record<string, unknown> | null;
}

export const taskApi = {
  tasks: (page = 1) => request<Page<TaskSummary>>(`/tasks${query({ page, page_size: 15 })}`),
  conversations: (limit = 30, signal?: AbortSignal) => request<{ total: number; items: ServerConversationSummary[] }>(
    `/conversations${query({ limit })}`,
    { signal },
  ),
  conversationTasks: (conversationId: string, limit = 50, signal?: AbortSignal) => request<ServerConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}${query({ limit })}`,
    { signal },
  ),
  taskEventsUrl: (id: string) => `${API_BASE}/tasks/${encodeURIComponent(id)}/events`,
  taskAudit: (id: string, signal?: AbortSignal) => request<TaskAuditRecord>(
    `/tasks/${encodeURIComponent(id)}/audit`,
    { signal },
  ),
  task: async (id: string, signal?: AbortSignal, options?: { includeAudit?: boolean }) => {
    const suffix = options?.includeAudit === false ? '?include_audit=false' : '';
    const task = await request<Task>(`/tasks/${encodeURIComponent(id)}${suffix}`, { signal });
    if (task.result?.agent === true && task.result.p6_publication) {
      // P6 历史结果须经服务端封存复核；不能以原任务诊断 JSON 代替公开 DTO。
      try {
        const view = await request<{ p6_publication: P6Publication | null; public_answer: PublicAnswer }>(`/public/tasks/${encodeURIComponent(id)}`, { signal });
        if (view.p6_publication) return { ...task, result: { ...task.result, p6_publication: view.p6_publication, public_answer: view.public_answer } };
      } catch { /* 公开校验失败时关闭 P6 正文；任务状态和范围仍可查看。 */ }
      return { ...task, result: { ...task.result, validation: { status: 'failed' as const, checks: [] }, public_answer: undefined, answer: undefined } };
    }
    const result = task.result?.agent === true ? task.result as unknown as AgentResult : undefined;
    const scopedRanking = Object.values(result?.results ?? {}).some(item =>
      ['positive_aspects', 'issues'].includes(item.kind) && Boolean(item.parameters.aspect));
    const savedAnswer = result?.public_answer;
    const historicalTopicAnswer = savedAnswer?.status === 'ok' && savedAnswer.body_caveats == null
      && result?.result_ids?.some(id => result.results?.[id]?.kind === 'aspect_drilldown') ? savedAnswer : undefined;
    if (task.status === 'succeeded' && !task.error && result && (!savedAnswer || scopedRanking || historicalTopicAnswer)) {
      try {
        const view = await request<{ public_answer: PublicAnswer }>(`/public/tasks/${encodeURIComponent(id)}`, { signal });
        // 历史主题回答只更新说明的展示方式，保留原先发布的解读和引用。
        const answer = historicalTopicAnswer && !scopedRanking && view.public_answer.status === 'ok'
          ? { ...historicalTopicAnswer, body_caveats: view.public_answer.body_caveats }
          : view.public_answer;
        return { ...task, result: { ...task.result, public_answer: answer } };
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
  cancelTask: (id: string) => request<{ task: TaskSummary; cancelled: boolean; already_terminal: boolean }>(
    `/tasks/${encodeURIComponent(id)}/cancel`,
    { method: 'POST' },
  ),
  deleteTask: (id: string) => request<{ deleted_task_ids: string[]; deleted_tasks: number }>(
    `/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' },
  ),
};
