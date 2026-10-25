import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, API_BASE, ApiError, errorMessage, request } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('阶段 0 API 请求契约', () => {
  it('已有趋势接口使用重复商品参数，保留 UTC 日期、模型身份和取消信号', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: { summary: {}, buckets: [] } })));
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    await api.trend({ model: 'model', base_url: 'offline', prompt_version: 'v1', product_ids: ['A&B', '中文/B'],
      source: 'reviews_csv', dataset_version: '版本 1', date_from: '2023-02-01', date_to: '2023-02-28', as_of: null }, controller.signal);
    const url = new URL(fetcher.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe(new URL(`${API_BASE}/analytics/trend`, 'http://localhost').pathname);
    expect(url.searchParams.getAll('product_ids')).toEqual(['A&B', '中文/B']);
    expect(url.searchParams.get('period')).toBe('month');
    expect(url.searchParams.get('date_to')).toBe('2023-02-28');
    expect(url.searchParams.has('as_of')).toBe(false);
    controller.abort();
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  });
  const product = '["a&b","版本 1","p/1"]';
  const cases: [string, () => Promise<unknown>, string, string, string?][] = [
    ['健康状态', () => api.health(), '/health', 'GET'],
    ['商品与标注目录', () => api.options(), '/options', 'GET'],
    ['数据质量', () => api.quality(), '/quality', 'GET'],
    ['默认任务页', () => api.tasks(), '/tasks?page=1&page_size=15', 'GET'],
    ['任务翻页', () => api.tasks(3), '/tasks?page=3&page_size=15', 'GET'],
    ['任务 ID 编码', () => api.task('task/a ?'), '/tasks/task%2Fa%20%3F', 'GET'],
    ['分析快照 ID 编码', () => api.run('run/a ?'), '/analysis-runs/run%2Fa%20%3F', 'GET'],
    ['评论范围', () => api.reviews(product, 2, 3), '/reviews?product_key=%5B%22a%26b%22%2C%22%E7%89%88%E6%9C%AC+1%22%2C%22p%2F1%22%5D&profile_id=2&page=3&page_size=8', 'GET'],
    ['评论省略版本', () => api.reviews('p', undefined, 1), '/reviews?product_key=p&page=1&page_size=8', 'GET'],
    ['评论详情', () => api.review(7, 0), '/reviews/7?profile_id=0', 'GET'],
    ['详情省略版本', () => api.review(7), '/reviews/7?', 'GET'],
    ['重试与幂等', () => api.retry('task/a ?', 'retry-key'), '/tasks/task%2Fa%20%3F/retry', 'POST', 'retry-key'],
    ['删除任务', () => api.deleteTask('task/a ?'), '/tasks/task%2Fa%20%3F', 'DELETE'],
    ['删除导入数据', () => api.deleteImportedData('task/a ?'), '/tasks/task%2Fa%20%3F/imported-data', 'DELETE'],
  ];

  it.each(cases)('%s 保留路径、方法、请求头和原始返回值', async (_name, call, path, method, key) => {
    const result = { preserved: ['原始字段', null, 0] };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result })));
    vi.stubGlobal('fetch', fetcher);
    expect(await call()).toEqual(result);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`${API_BASE}${path}`);
    expect(init.method ?? 'GET').toBe(method);
    expect(init.body).toBeUndefined();
    expect(init.headers).toEqual({ Accept: 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it('提交任务保留 JSON 原文、URL 和 Content-Type', async () => {
    const input = { kind: 'import' as const, parameters: {
      dataset_version: '版本 1', product_ids: ['p/1'], reviews_path: '.uploads/评论.csv', max_reviews: 2,
    } };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: { reused: true } })));
    vi.stubGlobal('fetch', fetcher);
    await expect(api.submit(input, 'same-key')).resolves.toEqual({ reused: true });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`${API_BASE}/tasks`);
    expect(init.method).toBe('POST');
    expect(init.body).toBe(JSON.stringify(input));
    expect(init.headers).toEqual({ Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': 'same-key' });
  });

  it.each(['task', 'run', 'reviews'] as const)('%s 取消传递到 fetch，保留原始中止异常', async kind => {
    const controller = new AbortController();
    const timeout = new AbortController();
    const timeoutFactory = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal);
    const fetcher = vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
    }));
    vi.stubGlobal('fetch', fetcher);
    const pending = kind === 'reviews' ? api.reviews('p', 1, 1, controller.signal) : api[kind]('id', controller.signal);
    const reason = new DOMException('离开当前任务', 'AbortError');
    const rejected = expect(pending).rejects.toBe(reason);
    controller.abort(reason);
    await rejected;
    expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(true);
    expect(timeoutFactory).toHaveBeenCalledWith(30_000);
    expect(timeout.signal.aborted).toBe(false);
  });

  it.each(['GET', 'POST'])('%s 的 30 秒超时仍返回原网络错误提示', async method => {
    const deadline = new AbortController();
    const timeoutFactory = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(deadline.signal);
    vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
    })));
    const pending = request('/tasks', { method });
    const rejected = expect(pending).rejects.toMatchObject({ status: 0, body: {
      code: 'network_error', message: '无法连接后端，请检查服务、网络及允许的前端来源。'
        + (method === 'POST' ? ' 提交结果暂未确认，可再次提交确认同一任务。' : ''),
    } });
    deadline.abort(new DOMException('超时', 'TimeoutError'));
    await rejected;
    expect(timeoutFactory).toHaveBeenCalledWith(30_000);
  });

  it.each([
    [200, { status: 'error', error: { code: 'business_error', message: '无法完成' } }, 'business_error', '无法完成'],
    [503, { status: 'ok', result: {} }, 'http_error', '请求失败（HTTP 503）'],
    [200, { items: [] }, 'http_error', '请求失败（HTTP 200）'],
  ])('HTTP %s 正确拒绝错误或非 v1 包络', async (status, payload, code, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status })));
    await expect(request('/health')).rejects.toMatchObject({ name: 'ApiError', status, body: { code, message } });
  });

  it('保留非 JSON 和通用错误的显示文本', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html/>', { status: 502 })));
    await expect(request('/health')).rejects.toMatchObject({ status: 502, body: {
      code: 'invalid_response', message: '服务返回的内容不是有效 JSON，请检查 API 地址。',
    } });
    expect(errorMessage(new ApiError(409, { code: 'conflict', message: '记录已变更' }))).toBe('记录已变更');
    expect(errorMessage(new Error('本地错误'))).toBe('本地错误');
    expect(errorMessage(null)).toBe('请求失败，请稍后重试。');
  });
});
