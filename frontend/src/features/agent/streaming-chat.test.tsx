import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../api';
import { emptyTaskState } from '../../task-state';
import { useTask } from '../../useTask';
import type { Task } from '../../types';
import { basicHistory } from './chat-state';
import ChatMessage from './ChatMessage';
import { useAgentChat } from './useAgentChat';

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
  });
});
