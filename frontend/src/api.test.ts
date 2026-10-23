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
  it('支持删除任务记录和导入数据', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { deleted_tasks: 1, deleted_task_ids: ['task-1'] } })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { task: {}, deletion: {}, reused: false } })));
    vi.stubGlobal('fetch', fetcher);
    await api.deleteTask('task/1');
    await api.deleteImportedData('import-1');
    expect(fetcher.mock.calls[0][0]).toContain('/tasks/task%2F1');
    expect(fetcher.mock.calls[0][1].method).toBe('DELETE');
    expect(fetcher.mock.calls[1][0]).toContain('/tasks/import-1/imported-data');
  });
  it('上传本地文件时使用原始字节和文件名查询参数', async () => {
    const file = new File(['{}\n'], '任意目录.jsonl', { type: 'application/json' });
    const xhr = fakeUpload();
    const progress = vi.fn();
    let resolved = false;
    const upload = api.uploadImportFile(file, 'reviews', progress).then(result => { resolved = true; return result; });
    const url = new URL(xhr.open.mock.calls[0][1], 'http://localhost');
    expect(url.searchParams.get('kind')).toBe('reviews');
    expect(url.searchParams.get('filename')).toBe(file.name);
    expect(xhr.send).toHaveBeenCalledWith(file);
    xhr.upload.onprogress!({ loaded: 1 });
    expect(progress).toHaveBeenLastCalledWith(1);
    xhr.upload.onprogress!({ loaded: file.size });
    await Promise.resolve();
    expect(resolved).toBe(false); // 已发送全部字节，仍须等待服务端确认。
    xhr.responseText = JSON.stringify({ status: 'ok', result: { kind: 'reviews', filename: file.name, size_bytes: 3, path: '.uploads/a.jsonl' } });
    xhr.onload!();
    const result = await upload;
    expect(result.path).toBe('.uploads/a.jsonl');
  });
  it('上传拒绝、无效响应、断网和超时都不会进入处理阶段', async () => {
    const file = new File(['{}\n'], 'reviews.jsonl');
    for (const failure of ['http', 'json', 'network', 'timeout'] as const) {
      const xhr = fakeUpload();
      const upload = api.uploadImportFile(file, 'reviews');
      if (failure === 'http') {
        xhr.status = 413;
        xhr.responseText = JSON.stringify({ status: 'error', error: { code: 'file_too_large', message: '文件过大' } });
        xhr.onload!();
      } else if (failure === 'json') { xhr.responseText = '<html>'; xhr.onload!(); }
      else if (failure === 'timeout') xhr.ontimeout!();
      else xhr.onerror!();
      await expect(upload).rejects.toBeInstanceOf(ApiError);
    }
  });
});

function fakeUpload() {
  const xhr = {
    open: vi.fn(), send: vi.fn(), setRequestHeader: vi.fn(), timeout: 0, status: 200, responseText: '',
    upload: { onprogress: null as null | ((event: { loaded: number }) => void) },
    onload: null as null | (() => void), onerror: null as null | (() => void),
    ontimeout: null as null | (() => void), onabort: null as null | (() => void),
  };
  vi.stubGlobal('XMLHttpRequest', class { constructor() { return xhr; } });
  return xhr;
}
