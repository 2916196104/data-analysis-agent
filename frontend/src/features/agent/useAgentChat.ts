import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from '../../api';
import { isActive } from '../../task-state';
import { useTask } from '../../useTask';
import type { AnalysisParameters } from '../../types';
import { analysisSession, CHAT_KEY, isAgentTask, readChatSession, type BasicChatTurn, type Conversation, type ChatTurn } from './chat-state';

export function useAgentChat(routeTaskId?: string) {
  const [initial] = useState(readChatSession);
  const [conversation, setConversation] = useState<Conversation>(initial.session);
  const sessionRef = useRef(conversation);
  const session = useMemo(() => analysisSession(conversation), [conversation]);
  const previousRoute = useRef(routeTaskId);
  const [storageError, setStorageError] = useState(initial.error);
  const [posting, setPosting] = useState(false);
  const postLock = useRef(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const activeMessage = conversation.turns.find(turn => turn.id === conversation.activeId);
  const activeTurn = activeMessage?.kind === 'analysis' ? activeMessage : undefined;
  const taskId = activeMessage?.kind === 'basic' ? null : routeTaskId ?? activeTurn?.taskId ?? null;
  const activeState = useTask(taskId);
  const task = activeState.id === taskId ? activeState.task : null;
  const restoreError = routeTaskId && task && !isAgentTask(task) ? '该任务不是 AI 助手查询，请从任务与报告查看。' : '';
  const pending = posting || (!!taskId && (!task || isActive(task.status)) && !restoreError);

  const commit = useCallback((update: (current: Conversation) => Conversation, persist = true) => {
    const next = update(sessionRef.current);
    if (next === sessionRef.current) return;
    sessionRef.current = next;
    setConversation(next);
    if (!persist) return;
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(analysisSession(next))); setStorageError(false); }
    catch { setStorageError(true); }
  }, []);

  useEffect(() => {
    if (previousRoute.current === routeTaskId) return;
    previousRoute.current = routeTaskId;
    if (routeTaskId) commit(current => current.turns.some(turn => turn.id === current.activeId && turn.kind === 'basic')
      ? { ...current, activeId: undefined } : current, false);
  }, [routeTaskId, commit]);

  useEffect(() => {
    if (!routeTaskId || !task || !isAgentTask(task)) return;
    const existing = sessionRef.current.turns.find((turn): turn is ChatTurn => turn.kind === 'analysis' && turn.taskId === task.task_id);
    if (existing && sessionRef.current.activeId === existing.id) return;
    commit(current => {
      const turn: ChatTurn = existing ?? { kind: 'analysis', id: crypto.randomUUID(), taskId: task.task_id,
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
      commit(current => ({ ...current, turns: current.turns.map(item => item.kind === 'analysis' && item.id === turn.id ? { ...item, taskId: result.task.task_id, retry: undefined } : item) }));
      return true;
    } catch (error) {
      setErrors(current => ({ ...current, [turn.id]: errorMessage(error) }));
      return false;
    } finally { postLock.current = false; setPosting(false); }
  }

  async function send(parameters: AnalysisParameters, question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || pending) return false;
    const previous = analysisSession(sessionRef.current).turns.at(-1);
    const turn: ChatTurn = previous?.kind === 'analysis' && !previous.taskId && previous.question === text
      && JSON.stringify(previous.parameters) === JSON.stringify(parameters) ? previous
      : { kind: 'analysis', id: crypto.randomUUID(), requestKey: crypto.randomUUID(), question: text, parameters };
    if (turn !== previous) commit(current => ({ turns: [...current.turns, turn], activeId: turn.id }));
    return submitTurn(turn);
  }

  async function sendBasic(question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || pending) return false;
    postLock.current = true;
    setPosting(true);
    const turn: BasicChatTurn = { kind: 'basic', id: crypto.randomUUID(), question: text, status: 'pending' };
    commit(current => ({ turns: [...current.turns, turn], activeId: turn.id }), false);
    try {
      const reply = await api.chat(text);
      if (!reply || typeof reply.answer !== 'string' || !reply.answer.trim()) throw new Error('模型未返回有效回答，请重新发送。');
      commit(current => ({ ...current, turns: current.turns.map(item => item.kind === 'basic' && item.id === turn.id
        ? { ...item, status: 'succeeded', answer: reply.answer } : item) }), false);
      return true;
    } catch (error) {
      commit(current => ({ ...current, turns: current.turns.map(item => item.kind === 'basic' && item.id === turn.id
        ? { ...item, status: 'failed', error: errorMessage(error) } : item) }), false);
      return false;
    } finally { postLock.current = false; setPosting(false); }
  }

  async function retry(turn: ChatTurn): Promise<void> {
    if (postLock.current || pending) return;
    const next = turn.taskId ? { ...turn, retry: turn.retry?.taskId === turn.taskId ? turn.retry : { taskId: turn.taskId, key: crypto.randomUUID() } } : turn;
    commit(current => ({ ...current, turns: current.turns.map(item => item.id === next.id ? next : item) }));
    await submitTurn(next, !!next.taskId);
  }

  return { session, activeTurn, conversation, activeMessage, activeState, pending, posting, errors, storageError, restoreError, send, sendBasic, retry, focusedTaskId: routeTaskId,
    release: () => commit(current => ({ ...current, activeId: undefined })) };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
