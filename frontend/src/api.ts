import type { Health } from './types';
import { request } from './shared/api/http-client';
import { catalogApi } from './shared/api/catalog-api';
import { datasetApi } from './shared/api/dataset-api';
import { taskApi } from './shared/api/task-api';
import { reviewApi } from './shared/api/review-api';
import { analysisApi } from './shared/api/analysis-api';

export { API_BASE, ApiError, errorMessage, request } from './shared/api/http-client';

/** 兼容现有调用方；资源实现集中在 shared/api，页面和轮询逻辑无需迁移。 */
export const api = {
  health: () => request<Health>('/health'),
  ...catalogApi,
  ...datasetApi,
  ...taskApi,
  ...reviewApi,
  ...analysisApi,
};
