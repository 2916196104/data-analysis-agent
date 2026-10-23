import type { Progress, Run, Task, TaskStatus } from './types';

export const statusLabels: Record<TaskStatus, string> = {
  queued: '等待执行', running: '正在执行', succeeded: '已完成', partial: '部分完成', failed: '执行失败',
};
export const kindLabels = { analysis: '评论分析', import: '数据导入', label: '评论标注', replay: '历史回放' };
export const isActive = (status: TaskStatus) => status === 'queued' || status === 'running';
export const canRetry = (status: TaskStatus) => status === 'failed' || status === 'partial';
export function progressPercent(progress: Progress): number | undefined {
  if (progress.total === null || progress.total <= 0 || !Number.isFinite(progress.total) || !Number.isFinite(progress.completed)) return undefined;
  return Math.max(0, Math.min(100, Math.round(progress.completed / progress.total * 100)));
}
export const stageLabels: Record<string, string> = {
  queued: '任务已进入队列', starting: '准备执行', analysis: '计算统计并保存分析快照',
  hashing_reviews: '校验评论文件', hashing_meta: '校验商品文件',
  importing_reviews: '导入评论', importing_meta: '导入商品',
  verifying_reviews: '核对评论文件完整性', verifying_meta: '核对商品文件完整性',
  committing_import: '提交数据到数据库',
  imported: '导入完成', labeling: '处理评论标注', replaying: '按月回放',
};
export interface TaskState { id: string | null; task: Task | null; run: Run | null; loading: boolean; error: string | null }
export const emptyTaskState: TaskState = { id: null, task: null, run: null, loading: false, error: null };
export type TaskAction = { type: 'select'; id: string | null } | { type: 'task'; id: string; task: Task }
  | { type: 'run'; id: string; run: Run } | { type: 'error'; id: string; error: string };
export function taskReducer(state: TaskState, action: TaskAction): TaskState {
  if (action.type === 'select') return action.id && action.id === state.id
    ? { ...state, loading: !state.task, error: null }
    : { ...emptyTaskState, id: action.id, loading: !!action.id };
  // 此前选中任务的延迟响应绝不能替换当前结果。
  if (action.id !== state.id) return state;
  if (action.type === 'task') return { ...state, task: action.task, loading: false, error: null };
  if (action.type === 'run') return { ...state, run: action.run, loading: false, error: null };
  return { ...state, loading: false, error: action.error };
}
