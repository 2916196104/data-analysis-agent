import type { Run } from '../../types';
import { request } from './http-client';

export const analysisApi = {
  // 固定分析读取冻结快照；Agent 结果仍通过 task.result 恢复。
  run: (id: string, signal?: AbortSignal) => request<Run>(`/analysis-runs/${encodeURIComponent(id)}`, { signal }),
};
