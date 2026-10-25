import type { AnalysisScope, Run } from '../../types';
import { request } from './http-client';

export const analysisApi = {
  // 沿用已有只读统计接口，商品列表使用后端约定的重复查询参数。
  trend: (scope: AnalysisScope, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    const { product_ids, ...fields } = scope;
    Object.entries(fields).forEach(([key, value]) => {
      if (value != null) params.set(key, String(value));
    });
    product_ids.forEach(id => params.append('product_ids', id));
    params.set('period', 'month');
    return request<Run['results']['trend']>(`/analytics/trend?${params}`, { signal });
  },
  // 固定分析读取冻结快照；Agent 结果仍通过 task.result 恢复。
  run: (id: string, signal?: AbortSignal) => request<Run>(`/analysis-runs/${encodeURIComponent(id)}`, { signal }),
};
