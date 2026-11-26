import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { emptyTaskState } from '../../task-state';
import { useTask } from '../../useTask';
import type { Task } from '../../types';
import { CHAT_KEY, basicHistory } from './chat-state';
import ChatMessage from './ChatMessage';
import { useAgentChat } from './useAgentChat';
import { readChatStore } from './useChatSessions';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: null, refresh: vi.fn() }));
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {} }));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('SSE 流式基础问答与实时任务进度展示', () => {
  it('流式增量渲染基础问答并在手动停止时保留未完成提示且不污染追问历史', async () => {
    let emitDelta!: (delta: string) => void;
    let streamSignal: AbortSignal | undefined;
    const streamSpy = vi.spyOn(api, 'chatStream').mockImplementation((_message, _history, options) => {
      emitDelta = options?.onDelta ?? (() => {});
      streamSignal = options?.signal;
      return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(options.signal!.reason), { once: true });
      });
    });

    const hook = renderHook(() => useAgentChat());
    let pendingPromise!: Promise<boolean>;
    act(() => { pendingPromise = hook.result.current.sendBasic('介绍一下观数系统'); });
    expect(streamSpy).toHaveBeenCalledTimes(1);

    act(() => { emitDelta('观数系统专注于商品评论分析。'); });
    const streamingTurn = hook.result.current.conversation.turns[0];
    expect(streamingTurn.kind).toBe('basic');
    if (streamingTurn.kind === 'basic') {
      expect(streamingTurn.status).toBe('streaming');
      expect(streamingTurn.answer).toBe('观数系统专注于商品评论分析。');
    }

    const { rerender } = render(
      <ChatMessage turn={streamingTurn} chat={hook.result.current} onPrepare={vi.fn()} />,
    );
    expect(screen.getByText('正在流式生成…')).toBeTruthy();
    expect(screen.getByText('观数系统专注于商品评论分析。')).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '停止生成' }));
      await pendingPromise;
    });
    expect(streamSignal?.aborted).toBe(true);
    const stoppedTurn = hook.result.current.conversation.turns[0];
    expect(stoppedTurn.kind === 'basic' && stoppedTurn.status).toBe('failed');
    expect(basicHistory(hook.result.current.conversation.turns)).toEqual([]);

    rerender(<ChatMessage turn={stoppedTurn} chat={hook.result.current} onPrepare={vi.fn()} />);
    expect(screen.getByText('已手动停止生成，可点击重新发送获取完整回答。')).toBeTruthy();
  });

  it('运行中的分析任务实时展示阶段文案、观点计数与进度条', () => {
    const runningTask: Task = {
      task_id: 'sse-live-1',
      kind: 'analysis',
      status: 'running',
      parameters: {
        model: 'm', base_url: 'https://offline.invalid/v1', prompt_version: 'v1',
        source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['P1'],
        date_from: '2023-01-01', date_to: '2023-01-31', baseline_from: '2022-12-01', baseline_to: '2022-12-31',
        question: '物流差评具体体现在哪里？',
      },
      result: null,
      error: null,
      parent_task_id: null,
      created_at: '2026-01-01T00:00:00Z',
      started_at: '2026-01-01T00:00:01Z',
      finished_at: null,
      updated_at: '2026-01-01T00:00:05Z',
      progress: { stage: 'topic_classifying', completed: 40, total: 60, unit: 'opinions' },
    };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? runningTask : null, refresh: vi.fn() }));
    const hook = renderHook(() => useAgentChat('sse-live-1'));

    render(
      <ChatMessage
        turn={{
          kind: 'analysis',
          id: 't1',
          taskId: 'sse-live-1',
          requestKey: 'k1',
          question: '物流差评具体体现在哪里？',
          parameters: runningTask.parameters as never,
        }}
        chat={hook.result.current}
        onPrepare={vi.fn()}
      />,
    );
    expect(screen.getByText('正在进行主题归属复核')).toBeTruthy();
    expect(screen.getByText('40/60 条观点（67%）')).toBeTruthy();
    expect(screen.getByLabelText('任务进度 67%')).toBeTruthy();
    expect(screen.getByRole('button', { name: '取消任务' })).toBeTruthy();
  });

  it('流式生成中途刷新不会破坏本地存储，而是将中断消息安全恢复为可重发状态', () => {
    const store = {
      version: 2,
      activeSessionId: 's-1',
      sessions: [
        {
          id: 's-1',
          title: '流式中断会话',
          createdAt: '2026-11-26T10:00:00.000Z',
          updatedAt: '2026-11-26T10:00:05.000Z',
          activeId: 'turn-1',
          turns: [
            { kind: 'basic', id: 'turn-1', question: '解释一下标注覆盖率', status: 'streaming', answer: '标注覆盖率是指' },
          ],
        },
        {
          id: 's-2',
          title: '已有历史会话',
          createdAt: '2026-11-26T09:00:00.000Z',
          updatedAt: '2026-11-26T09:00:05.000Z',
          turns: [{ kind: 'basic', id: 'turn-2', question: '你好', status: 'succeeded', answer: '你好！' }],
        },
      ],
    };
    localStorage.setItem(CHAT_KEY, JSON.stringify(store));
    const loaded = readChatStore();
    expect(loaded.error).toBe(false);
    expect(loaded.store.sessions).toHaveLength(2);
    const recovered = loaded.store.sessions[0].turns[0];
    expect(recovered.kind).toBe('basic');
    if (recovered.kind === 'basic') {
      expect(recovered.status).toBe('failed');
      expect(recovered.answer).toBe('标注覆盖率是指');
      expect(recovered.error).toContain('页面已刷新');
    }
  });

  it('高频 SSE 增量节流写盘，且多会话间 AbortController 隔离、删除会话时立即中止流', async () => {
    const signals: AbortSignal[] = [];
    const emitters: ((delta: string) => void)[] = [];
    const resolvers: ((reply: { answer: string }) => void)[] = [];
    vi.spyOn(api, 'chatStream').mockImplementation((_message, _history, options) => {
      signals.push(options!.signal!);
      emitters.push(options?.onDelta ?? (() => {}));
      return new Promise((resolve, reject) => {
        resolvers.push(resolve);
        options?.signal?.addEventListener('abort', () => reject(options.signal!.reason), { once: true });
      });
    });
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem');
    const hook = renderHook(() => useAgentChat());
    const sessionA = hook.result.current.activeSessionId;

    let pA!: Promise<boolean>;
    act(() => { pA = hook.result.current.sendBasic('会话 A 问题'); });
    const writesAfterStart = setItemSpy.mock.calls.length;

    // 同一毫秒内推送 5 个分片，只应触发 1 次节流持久化而非 5 次。
    act(() => {
      for (let i = 0; i < 5; i++) emitters[0](`片${i}`);
    });
    expect(setItemSpy.mock.calls.length - writesAfterStart).toBeLessThanOrEqual(1);

    // 切换到新会话 B 并发起流式问答，停止 B 不得误杀 A。
    act(() => { hook.result.current.createSession(); });
    let pB!: Promise<boolean>;
    act(() => { pB = hook.result.current.sendBasic('会话 B 问题'); });
    expect(signals).toHaveLength(2);

    await act(async () => {
      hook.result.current.stopBasicStream();
      await pB;
    });
    expect(signals[1].aborted).toBe(true);
    expect(signals[0].aborted).toBe(false);

    // 删除仍在流式输出的会话 A 时，必须立即中止其 AbortController。
    await act(async () => {
      hook.result.current.deleteSession(sessionA);
      await pA;
    });
    expect(signals[0].aborted).toBe(true);
  });

  it('点击取消任务按钮调用 api.cancelTask 并刷新任务状态', async () => {
    const refresh = vi.fn();
    const cancelSpy = vi.spyOn(api, 'cancelTask').mockResolvedValue({
      task: {
        task_id: 'cancel-1', kind: 'analysis', status: 'failed', parent_task_id: null,
        created_at: '', started_at: null, finished_at: '', updated_at: '',
        progress: { stage: 'starting', completed: 0, total: null, unit: 'items' },
        error: { code: 'task_cancelled', message: '任务已手动取消。' },
      },
      cancelled: true,
      already_terminal: false,
    });
    const runningTask: Task = {
      task_id: 'cancel-1',
      kind: 'analysis',
      status: 'running',
      parameters: {
        model: 'm', base_url: 'https://offline.invalid/v1', prompt_version: 'v1',
        source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['P1'],
        date_from: '2023-01-01', date_to: '2023-01-31', baseline_from: '2022-12-01', baseline_to: '2022-12-31',
        question: '差评主要集中在哪些方面？',
      },
      result: null, error: null, parent_task_id: null,
      created_at: '', started_at: '', finished_at: null, updated_at: '',
      progress: { stage: 'starting', completed: 0, total: null, unit: 'items' },
    };
    vi.mocked(useTask).mockImplementation(id => ({ ...emptyTaskState, id, task: id ? runningTask : null, refresh }));
    const hook = renderHook(() => useAgentChat('cancel-1'));
    render(
      <ChatMessage
        turn={{ kind: 'analysis', id: 'tc1', taskId: 'cancel-1', requestKey: 'kc1', question: '差评主要集中在哪些方面？', parameters: runningTask.parameters as never }}
        chat={hook.result.current}
        onPrepare={vi.fn()}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    });
    expect(cancelSpy).toHaveBeenCalledWith('cancel-1');
    expect(refresh).toHaveBeenCalled();
  });
});
