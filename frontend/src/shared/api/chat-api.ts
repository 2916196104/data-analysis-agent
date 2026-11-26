import type { ApiErrorBody } from '../../types';
import { API_BASE, ApiError, request } from './http-client';

export interface ChatReply {
  answer: string;
}

export interface ChatHistoryTurn { question: string; answer: string }

export interface ChatStreamOptions {
  onDelta?: (delta: string) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
}

const CHAT_NETWORK_ERROR = '聊天请求失败或超时，请检查网络后重新发送。重新发送会发起新的模型请求。';

function parseSseBlock(block: string): { event: string; data: string } | null {
  const lines = block.split(/\r?\n/);
  let event = 'message';
  const dataLines: string[] = [];
  for (const rawLine of lines) {
    if (!rawLine || rawLine.startsWith(':')) continue;
    const colon = rawLine.indexOf(':');
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    let value = colon === -1 ? '' : rawLine.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join('\n') };
}

export const chatApi = {
  chat: (
    message: string,
    history: ChatHistoryTurn[] = [],
    options?: { signal?: AbortSignal; timeoutMs?: number },
  ) => request<ChatReply>('/chat', {
    method: 'POST',
    body: JSON.stringify({ message, ...(history.length ? { history } : {}) }),
    ...(options?.signal ? { signal: options.signal } : {}),
  }, {
    timeoutMs: options?.timeoutMs ?? 90_000,
    networkErrorMessage: CHAT_NETWORK_ERROR,
  }),

  chatStream: async (
    message: string,
    history: ChatHistoryTurn[] = [],
    options: ChatStreamOptions = {},
  ): Promise<ChatReply> => {
    const deadline = AbortSignal.timeout(options.timeoutMs ?? 90_000);
    const signal = options.signal ? AbortSignal.any([options.signal, deadline]) : deadline;
    const body = JSON.stringify({ message, ...(history.length ? { history } : {}) });
    const networkError = () => new ApiError(0, { code: 'network_error', message: CHAT_NETWORK_ERROR });

    let response: Response;
    try {
      response = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        body,
        signal,
        headers: {
          Accept: 'text/event-stream, application/json',
          'Content-Type': 'application/json',
        },
      });
    } catch (error) {
      if (options.signal?.aborted) throw error;
      throw networkError();
    }

    // 兼容未部署流式端点的旧后端，自动降级为原 POST /chat 并透传取消信号。
    if (response.status === 404 || response.status === 405) {
      return chatApi.chat(message, history, { signal: options.signal, timeoutMs: options.timeoutMs });
    }

    const contentType = response.headers.get('content-type') || '';
    if (!response.ok || contentType.includes('application/json')) {
      let payload: { status?: string; result?: ChatReply; error?: ApiErrorBody };
      try {
        payload = await response.json();
      } catch (error) {
        if (options.signal?.aborted) throw error;
        if (signal.aborted) throw networkError();
        throw new ApiError(response.status, {
          code: 'invalid_response',
          message: '服务返回的内容不是有效 JSON，请检查 API 地址。',
        });
      }
      if (!response.ok || payload.status !== 'ok' || !payload.result) {
        throw new ApiError(response.status, payload.error ?? {
          code: 'http_error',
          message: `请求失败（HTTP ${response.status}）`,
        });
      }
      if (payload.result.answer) options.onDelta?.(payload.result.answer);
      return payload.result;
    }

    if (!response.body) {
      throw new ApiError(response.status, {
        code: 'invalid_response',
        message: '模型流式响应通道不可用，请重新发送。',
      });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let assembled = '';
    let completedAnswer: string | null = null;

    const handleBlock = (rawBlock: string) => {
      const parsed = parseSseBlock(rawBlock);
      if (!parsed) return;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(parsed.data) as Record<string, unknown>;
      } catch {
        return;
      }
      if (parsed.event === 'delta' && typeof payload.delta === 'string' && payload.delta) {
        assembled += payload.delta;
        options.onDelta?.(payload.delta);
      } else if (parsed.event === 'done' && typeof payload.answer === 'string') {
        completedAnswer = payload.answer;
      } else if (parsed.event === 'error') {
        throw new ApiError(502, {
          code: typeof payload.code === 'string' ? payload.code : 'model_api_error',
          message: typeof payload.message === 'string' ? payload.message : '模型服务调用失败，请稍后重试。',
        });
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() ?? '';
        for (const block of parts) {
          if (block.trim()) handleBlock(block);
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) handleBlock(buffer);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (options.signal?.aborted) throw error;
      throw networkError();
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // 忽略释放锁时的非关键状态异常。
      }
    }

    if (completedAnswer === null) {
      throw networkError();
    }
    const finalAnswer = completedAnswer || assembled;
    return { answer: finalAnswer };
  },
};
