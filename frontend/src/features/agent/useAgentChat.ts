import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../../api';
import { isActive } from '../../task-state';
import { useTask } from '../../useTask';
import type { AnalysisParameters } from '../../types';
import { CHAT_KEY, isAgentTask, readChatSession, type ChatSession, type ChatTurn } from './chat-state';

export function useAgentChat(routeTaskId?: string) {
  const [initial] = useState(readChatSession);
  const [session, setSession] = useState(initial.session);
  const sessionRef = useRef(session);
  const [storageError, setStorageError] = useState(initial.error);
  const [posting, setPosting] = useState(false);
  const postLock = useRef(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const activeTurn = session.turns.find(turn => turn.id === session.activeId);
  const taskId = routeTaskId ?? activeTurn?.taskId ?? null;
  const activeState = useTask(taskId);
  const task = activeState.id === taskId ? activeState.task : null;
  const restoreError = routeTaskId && task && !isAgentTask(task) ? '该任务不是 AI 助手查询，请从任务与报告查看。' : '';
  const pending = posting || (!!taskId && (!task || isActive(task.status)) && !restoreError);

  const commit = useCallback((update: (current: ChatSession) => ChatSession) => {
    const next = update(sessionRef.current);
    sessionRef.current = next;
    setSession(next);
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(next)); setStorageError(false); }
    catch { setStorageError(true); }
  }, []);

  useEffect(() => {
    if (!routeTaskId || !task || !isAgentTask(task)) return;
    const existing = sessionRef.current.turns.find(turn => turn.taskId === task.task_id);
    if (existing && sessionRef.current.activeId === existing.id) return;
    commit(current => {
      const turn: ChatTurn = existing ?? { id: crypto.randomUUID(), taskId: task.task_id,
        requestKey: crypto.randomUUID(), question: String(task.parameters.question ?? task.result?.question ?? ''),
        parameters: { ...task.parameters, product_ids: Array.isArray(task.parameters.product_ids) ? task.parameters.product_ids : [] } as unknown as AnalysisParameters };
      return { turns: existing ? current.turns : [...current.turns, turn], activeId: turn.id };
    });
  }, [routeTaskId, task, commit]);

  async function submitTurn(turn: ChatTurn, retry = false): Promise<boolean> {
    if (postLock.current || pending) return false;
    postLock.current = true;
    setPosting(true);
    setErrors(current => ({ ...current, [turn.id]: '' }));
    commit(current => ({ ...current, activeId: turn.id }));
    try {
      const result = retry && turn.taskId
        ? await api.retry(turn.taskId, turn.retry!.key)
        : await api.submit({ kind: 'analysis', parameters: { ...turn.parameters, question: turn.question } }, turn.requestKey);
      commit(current => ({ ...current, turns: current.turns.map(item => item.id === turn.id ? { ...item, taskId: result.task.task_id, retry: undefined } : item) }));
      return true;
    } catch (error) {
      setErrors(current => ({ ...current, [turn.id]: errorMessage(error) }));
      return false;
    } finally { postLock.current = false; setPosting(false); }
  }

  async function send(parameters: AnalysisParameters, question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || pending) return false;
    const previous = sessionRef.current.turns.at(-1);
    const turn: ChatTurn = previous && !previous.taskId && previous.question === text
      && JSON.stringify(previous.parameters) === JSON.stringify(parameters) ? previous
      : { id: crypto.randomUUID(), requestKey: crypto.randomUUID(), question: text, parameters };
    if (turn !== previous) commit(current => ({ turns: [...current.turns, turn], activeId: turn.id }));
    return submitTurn(turn);
  }

  async function retry(turn: ChatTurn): Promise<void> {
    if (postLock.current || pending) return;
    const next = turn.taskId ? { ...turn, retry: turn.retry?.taskId === turn.taskId ? turn.retry : { taskId: turn.taskId, key: crypto.randomUUID() } } : turn;
    commit(current => ({ ...current, turns: current.turns.map(item => item.id === next.id ? next : item) }));
    await submitTurn(next, !!next.taskId);
  }

  return { session, activeTurn, activeState, pending, posting, errors, storageError, restoreError, send, retry, focusedTaskId: routeTaskId,
    release: () => commit(current => ({ ...current, activeId: undefined })) };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
