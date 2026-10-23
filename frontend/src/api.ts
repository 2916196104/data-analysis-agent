import type {
  ApiErrorBody, Health, Options, Page, Quality, Review, Run, Submission, Task, TaskInput, TaskSummary,
  UploadedImportFile,
} from './types';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
  }
}

/** 统一处理超时、JSON 响应和后端错误结构，调用方只接收已解包的 result。 */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: init.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(30_000)])
        : AbortSignal.timeout(30_000),
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    const hint = init.method === 'POST' ? ' 提交结果暂未确认，可再次提交确认同一任务。' : '';
    throw new ApiError(0, {
      code: 'network_error',
      message: `无法连接后端，请检查服务、网络及允许的前端来源。${hint}`,
    });
  }

  let payload: { status?: string; result?: unknown; error?: ApiErrorBody };
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, {
      code: 'invalid_response',
      message: '服务返回的内容不是有效 JSON，请检查 API 地址。',
    });
  }
  if (!response.ok || payload.status !== 'ok') {
    throw new ApiError(response.status, payload.error ?? {
      code: 'http_error',
      message: `请求失败（HTTP ${response.status}）`,
    });
  }
  return payload.result as T;
}

function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) params.set(key, String(value));
  });
  return `?${params}`;
}

/** 工作台使用的只读查询和任务提交接口。 */
export const api = {
  health: () => request<Health>('/health'),
  options: () => request<Options>('/options'),
  quality: () => request<Quality>('/quality'),
  tasks: (page = 1) => request<Page<TaskSummary>>(`/tasks${query({ page, page_size: 15 })}`),
  task: (id: string, signal?: AbortSignal) => request<Task>(`/tasks/${encodeURIComponent(id)}`, { signal }),
  run: (id: string, signal?: AbortSignal) => request<Run>(`/analysis-runs/${encodeURIComponent(id)}`, { signal }),
  reviews: (
    productKey: string,
    profileId: number | undefined,
    page: number,
    signal?: AbortSignal,
  ) => request<Page<Review>>(
    `/reviews${query({ product_key: productKey, profile_id: profileId, page, page_size: 8 })}`,
    { signal },
  ),
  review: (id: number, profileId?: number) => request<Record<string, unknown>>(`/reviews/${id}${query({ profile_id: profileId })}`),
  submit: (input: TaskInput, key: string) => request<Submission>('/tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(input),
  }),
  retry: (id: string, key: string) => request<Submission>(
    `/tasks/${encodeURIComponent(id)}/retry`,
    { method: 'POST', headers: { 'Idempotency-Key': key } },
  ),
  uploadImportFile: (file: File, kind: UploadedImportFile['kind'], onProgress?: (loaded: number) => void): Promise<UploadedImportFile> => {
    const params = new URLSearchParams({ kind, filename: file.name });
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}/import-files?${params}`);
      xhr.timeout = 10 * 60_000;
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      xhr.upload.onprogress = event => onProgress?.(Math.min(file.size, event.loaded));
      const networkError = () => reject(new ApiError(0, { code: 'network_error', message: '文件上传失败或超时，请检查后端服务和网络后重试。' }));
      xhr.onerror = networkError;
      xhr.ontimeout = networkError;
      xhr.onabort = networkError;
      xhr.onload = () => {
        let payload: { status?: string; result?: UploadedImportFile; error?: ApiErrorBody };
        try { payload = JSON.parse(xhr.responseText); }
        catch { reject(new ApiError(xhr.status, { code: 'invalid_response', message: '上传服务返回的内容不是有效 JSON。' })); return; }
        if (xhr.status < 200 || xhr.status >= 300 || payload?.status !== 'ok' || !payload.result) {
          reject(new ApiError(xhr.status, payload?.error ?? { code: 'http_error', message: `文件上传失败（HTTP ${xhr.status}）` }));
          return;
        }
        onProgress?.(file.size);
        resolve(payload.result);
      };
      xhr.send(file);
    });
  },
  deleteTask: (id: string) => request<{ deleted_task_ids: string[]; deleted_tasks: number }>(
    `/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' },
  ),
  deleteImportedData: (id: string) => request<{ task: Task; deletion: Record<string, number | number[]>; reused: boolean }>(
    `/tasks/${encodeURIComponent(id)}/imported-data`, { method: 'DELETE' },
  ),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.body.details?.length) {
    const details = error.body.details
      .map(detail => `${detail.location.join('.')}：${detail.message}`)
      .join('；');
    return `${error.message} ${details}`;
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}
