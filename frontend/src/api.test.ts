import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, errorMessage, request } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('统一 API 契约', () => {
  it('只提交后端接受的参数，并保留幂等键', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: { reused: false } })));
    vi.stubGlobal('fetch', fetcher);
    await api.submit({ kind: 'import', parameters: { reviews_path: 'test.jsonl', dataset_version: 'test', product_ids: [] } }, 'key-1');
    const init = fetcher.mock.calls[0][1];
    expect(init.headers['Idempotency-Key']).toBe('key-1');
    expect(JSON.parse(init.body)).toEqual({ kind: 'import', parameters: { reviews_path: 'test.jsonl', dataset_version: 'test', product_ids: [] } });
    expect(init.body).not.toContain('question');
  });
  it('显示后端字段错误，不把错误包装成成功', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'error', error: {
      code: 'validation_error', message: '参数错误', details: [{ location: ['body', 'date_from'], message: '必填', type: 'missing' }],
    } }), { status: 422 })));
    let failure;
    try { await request('/tasks'); } catch (error) { failure = error; }
    expect(failure).toBeInstanceOf(ApiError);
    expect(errorMessage(failure)).toContain('body.date_from：必填');
  });
  it('区分网络故障与非 JSON 响应', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(request('/health')).rejects.toMatchObject({ status: 0, body: { code: 'network_error' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>wrong server</html>')));
    await expect(request('/health')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
  });
  it('商品 key 等查询参数进行编码', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result: {} })));
    vi.stubGlobal('fetch', fetcher);
    await api.reviews('["a&b","v","p"]', 1, 2);
    const url = new URL(fetcher.mock.calls[0][0], 'http://localhost');
    expect(url.searchParams.get('product_key')).toBe('["a&b","v","p"]');
    expect(url.searchParams.get('page')).toBe('2');
  });
});
