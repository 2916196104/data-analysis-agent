import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, API_BASE, ApiError, errorMessage, request } from '../../api';

const chatFailure = '聊天请求失败或超时，请检查网络后重新发送。重新发送会发起新的模型请求。';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('基础聊天 API', () => {
  it('通过公共入口只提交 message，并解包返回的 answer 字段', async () => {
    const result = { answer: '来自接口的回答。' };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', result })));
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(new AbortController().signal);
    vi.stubGlobal('fetch', fetcher);

    expect(await api.chat('你能做什么？')).toEqual(result);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(`${API_BASE}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message: '你能做什么？' }),
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      signal: expect.any(AbortSignal),
    });
    expect(timeout).toHaveBeenCalledTimes(1);
    expect(timeout).toHaveBeenCalledWith(90_000);
  });

  it('聊天的 90 秒设置不改变其他请求的 30 秒默认值', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ status: 'ok', result: {} })),
    )));
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(new AbortController().signal);
    await api.chat('第一个问题');
    await api.health();
    await api.chat('第二个问题');
    await api.submit({ kind: 'import', parameters: {
      reviews_path: 'reviews.csv', dataset_version: 'fixture', product_ids: [],
    } }, 'existing-key');
    expect(timeout.mock.calls.map(([milliseconds]) => milliseconds)).toEqual([90_000, 30_000, 90_000, 30_000]);
  });

  it('同时请求时普通接口 30 秒中止，聊天在 90 秒中止且不自动重试', async () => {
    mockTimeouts();
    const fetcher = vi.fn((_url, init: RequestInit) => waitForAbort(init.signal!));
    vi.stubGlobal('fetch', fetcher);
    const chat = api.chat('问题');
    const health = api.health();
    let chatSettled = false;
    void chat.catch(() => { chatSettled = true; });
    const chatRejected = expect(chat).rejects.toMatchObject({ status: 0,
      body: { code: 'network_error', message: chatFailure } });
    const healthRejected = expect(health).rejects.toMatchObject({ status: 0,
      body: { code: 'network_error', message: '无法连接后端，请检查服务、网络及允许的前端来源。' } });

    await vi.advanceTimersByTimeAsync(30_000);
    await healthRejected;
    expect(chatSettled).toBe(false);
    await vi.advanceTimersByTimeAsync(59_999);
    expect(chatSettled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await chatRejected;
    expect(chatSettled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('断网提示重新发送新请求，不承诺确认同一任务', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetcher);
    let failure: unknown;
    try { await api.chat('问题'); } catch (error) { failure = error; }
    expect(failure).toBeInstanceOf(ApiError);
    expect(errorMessage(failure)).toBe(chatFailure);
    expect(errorMessage(failure)).not.toContain('同一任务');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('保留模型错误的 HTTP 状态、错误码和后端提示', async () => {
    const error = { code: 'model_timeout', message: '模型回答超时，请稍后重新发送。' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: 'error', error }), { status: 504 },
    )));
    await expect(api.chat('问题')).rejects.toMatchObject({ status: 504, body: error });
  });

  it('收到响应头后读取正文超时，仍显示聊天失败而非 JSON 错误', async () => {
    mockTimeouts();
    vi.stubGlobal('fetch', vi.fn(async (_url, init: RequestInit) => {
      const response = new Response();
      vi.spyOn(response, 'json').mockImplementation(() => waitForAbort(init.signal!));
      return response;
    }));
    const rejected = expect(api.chat('问题')).rejects.toMatchObject({ status: 0,
      body: { code: 'network_error', message: chatFailure } });
    await vi.advanceTimersByTimeAsync(90_000);
    await rejected;
  });

  it('非 JSON 响应仍按公共协议报错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>wrong server</html>')));
    await expect(api.chat('问题')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
  });

  it('正文读取阶段主动取消，保留原始取消异常', async () => {
    mockTimeouts();
    const controller = new AbortController();
    let bodyStarted!: () => void;
    const ready = new Promise<void>(resolve => { bodyStarted = resolve; });
    vi.stubGlobal('fetch', vi.fn(async (_url, init: RequestInit) => {
      const response = new Response();
      vi.spyOn(response, 'json').mockImplementation(() => { bodyStarted(); return waitForAbort(init.signal!); });
      return response;
    }));
    const pending = request('/tasks', { signal: controller.signal });
    const reason = new DOMException('离开页面', 'AbortError');
    const rejected = expect(pending).rejects.toBe(reason);
    await ready;
    controller.abort(reason);
    await rejected;
  });

  it('chatStream 按 SSE 分片增量回调 onDelta 并在 done 返回完整回答', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: start\ndata: {"mode":"basic"}\n\n'));
        controller.enqueue(encoder.encode('event: delta\ndata: {"delta":"第一段"}\n\nevent: del'));
        controller.enqueue(encoder.encode('ta\ndata: {"delta":"，第二段"}\n\n'));
        controller.enqueue(encoder.encode('event: done\ndata: {"answer":"第一段，第二段"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
    })));
    const deltas: string[] = [];
    const reply = await api.chatStream('你好', [], { onDelta: chunk => deltas.push(chunk) });
    expect(deltas).toEqual(['第一段', '，第二段']);
    expect(reply).toEqual({ answer: '第一段，第二段' });
  });

  it('chatStream 收到 SSE error 事件时抛出带后端错误码的 ApiError，404 自动降级到 POST /chat', async () => {
    const encoder = new TextEncoder();
    const errorStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: error\ndata: {"code":"model_timeout","message":"模型超时"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(errorStream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })));
    await expect(api.chatStream('超时')).rejects.toMatchObject({
      body: { code: 'model_timeout', message: '模型超时' },
    });

    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response('Not Found', { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok', result: { answer: '兼容降级回答' } })));
    vi.stubGlobal('fetch', fetcher);
    expect(await api.chatStream('旧后端')).toEqual({ answer: '兼容降级回答' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][0]).toBe(`${API_BASE}/chat`);

    // 404 降级到 /chat 时透传取消信号，点击停止生成仍可中止降级请求。
    const controller = new AbortController();
    const fallbackFetch = vi.fn()
      .mockResolvedValueOnce(new Response('Not Found', { status: 404 }))
      .mockImplementationOnce((_url, init: RequestInit) => waitForAbort(init.signal!));
    vi.stubGlobal('fetch', fallbackFetch);
    const reason = new DOMException('已停止生成', 'AbortError');
    const pending = api.chatStream('可取消降级', [], { signal: controller.signal });
    await Promise.resolve();
    await Promise.resolve();
    controller.abort(reason);
    await expect(pending).rejects.toBe(reason);
  });
});

function mockTimeouts() {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, 'timeout').mockImplementation(milliseconds => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException('超时', 'TimeoutError')), milliseconds);
    return controller.signal;
  });
}

function waitForAbort(signal: AbortSignal): Promise<never> {
  return new Promise((_resolve, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
}
