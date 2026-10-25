import type { ApiErrorBody } from '../../types';

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

export function query(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) params.set(key, String(value));
  });
  return `?${params}`;
}

/** 上传保留 XHR 字节进度和独立错误文案，发送完成不等于服务端确认。 */
export function uploadRequest<T>(path: string, file: File, onProgress?: (loaded: number) => void, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason); return; }
    const xhr = new XMLHttpRequest();
    const cancel = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener('abort', cancel);
    xhr.open('POST', `${API_BASE}${path}`);
    xhr.timeout = 10 * 60_000;
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = event => onProgress?.(Math.min(file.size, event.loaded));
    const networkError = () => {
      cleanup();
      reject(signal?.aborted ? signal.reason : new ApiError(0, { code: 'network_error', message: '文件上传失败或超时，请检查后端服务和网络后重试。' }));
    };
    xhr.onerror = networkError;
    xhr.ontimeout = networkError;
    xhr.onabort = networkError;
    xhr.onload = () => {
      cleanup();
      let payload: { status?: string; result?: T; error?: ApiErrorBody };
      try { payload = JSON.parse(xhr.responseText); }
      catch { reject(new ApiError(xhr.status, { code: 'invalid_response', message: '上传服务返回的内容不是有效 JSON。' })); return; }
      if (xhr.status < 200 || xhr.status >= 300 || payload?.status !== 'ok' || !payload.result) {
        reject(new ApiError(xhr.status, payload?.error ?? { code: 'http_error', message: `文件上传失败（HTTP ${xhr.status}）` }));
        return;
      }
      onProgress?.(file.size);
      resolve(payload.result);
    };
    signal?.addEventListener('abort', cancel, { once: true });
    try { xhr.send(file); } catch (error) { cleanup(); reject(error); }
  });
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.body.details?.length) {
    const details = error.body.details
      .map(detail => `${detail.location.join('.')}：${detail.message}`)
      .join('；');
    return `${error.message} ${details}`;
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}
