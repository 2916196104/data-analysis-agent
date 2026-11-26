import { useEffect, useMemo, useRef } from 'react';
import { api, errorMessage } from '../../api';
import { chatApi } from '../../shared/api/chat-api';
import { isAgentScope } from '../../analysis-draft';
import { isActive } from '../../task-state';
import { useTask } from '../../useTask';
import type { AnalysisParameters } from '../../types';
import {
  analysisSession,
  basicHistory,
  isAgentTask,
  type BasicChatTurn,
  type ChatTurn,
} from './chat-state';
import {
  buildTurnResolution,
  contextFromResult,
  fetchFollowupParents,
  planFollowupLookup,
  validResult,
} from './chat-followup-resolver';
import { newSession, useChatSessions } from './useChatSessions';
import { copyScope, sameScope, type QueryChoice } from './followup';

export function useAgentChat(
  routeTaskId?: string,
  scopeKey?: string,
  currentScope?: AnalysisParameters,
  datasetId?: string,
) {
  const sessions = useChatSessions();
  const {
    conversation,
    sessionRef,
    commit,
    storageError,
    posting,
    postLock,
    resolving,
    errors,
    setPosting,
    setErrors,
  } = sessions;

  const datasetIdentity = datasetId ?? (
    currentScope ? JSON.stringify([currentScope.source, currentScope.dataset_version]) : undefined
  );
  const previousDataset = useRef(datasetIdentity);
  useEffect(() => {
    if (previousDataset.current === datasetIdentity) return;
    const previous = previousDataset.current;
    previousDataset.current = datasetIdentity;
    commit(current => ({ ...current, context: undefined, intent: undefined }));
    if (previous && !routeTaskId) sessions.createSession();
  }, [datasetIdentity, commit, sessions.createSession, routeTaskId]);

  const session = useMemo(() => analysisSession(conversation), [conversation]);
  const previousRoute = useRef(routeTaskId);
  const conversationId = conversation.id;
  const dismissedRoute = useRef<string | undefined>(undefined);
  if (previousRoute.current !== routeTaskId) dismissedRoute.current = undefined;
  const focusedRoute = dismissedRoute.current === routeTaskId ? undefined : routeTaskId;

  const navigationEpoch = useRef(0);
  const routeRef = useRef([routeTaskId, scopeKey]);
  if (routeRef.current[0] !== routeTaskId || routeRef.current[1] !== scopeKey) {
    routeRef.current = [routeTaskId, scopeKey];
    navigationEpoch.current++;
  }

  const activeMessage = conversation.turns.find(turn => turn.id === conversation.activeId);
  const activeTurn = activeMessage?.kind === 'analysis' ? activeMessage : undefined;
  const taskId = activeMessage?.kind === 'basic' ? null : focusedRoute ?? activeTurn?.taskId ?? null;
  const activeState = useTask(taskId, { includeAudit: false });
  const task = activeState.id === taskId ? activeState.task : null;
  const restoreError = focusedRoute && task && !isAgentTask(task)
    ? '该任务不是 AI 助手查询，请从任务与报告查看。'
    : '';
  const pending = posting || (!!taskId && (!task || isActive(task.status)) && !restoreError);

  useEffect(() => {
    if (previousRoute.current === routeTaskId) return;
    previousRoute.current = routeTaskId;
    if (!routeTaskId) return;
    commit(current => (
      current.turns.some(turn => turn.id === current.activeId && turn.kind === 'basic')
        ? { ...current, activeId: undefined }
        : current
    ));
  }, [routeTaskId, commit]);

  useEffect(() => {
    if (!focusedRoute || !task || !isAgentTask(task)) return;
    const store = sessions.storeRef.current;
    const owner = store.sessions.find(item => (
      item.turns.some(turn => turn.kind === 'analysis' && turn.taskId === task.task_id)
    ));
    const taskOwner = typeof task.parameters.conversation_id === 'string'
      ? task.parameters.conversation_id
      : undefined;
    const target = owner || (
      taskOwner
        ? store.sessions.find(item => item.id === taskOwner) || newSession(taskOwner)
        : undefined
    );
    if (target && target.id !== conversation.id) {
      // 目标会话可能停留在基础问答；先聚焦任务，避免基础问答的 null 轮询阻断 URL 恢复。
      const focused = {
        ...target,
        activeId: target.turns.find(turn => turn.kind === 'analysis' && turn.taskId === task.task_id)?.id,
      };
      const exists = store.sessions.some(item => item.id === target.id);
      sessions.save({
        ...store,
        activeSessionId: target.id,
        sessions: exists
          ? store.sessions.map(item => (item.id === target.id ? focused : item))
          : [...store.sessions, focused],
      });
      return;
    }
    const existing = sessionRef.current.turns.find(
      (turn): turn is ChatTurn => turn.kind === 'analysis' && turn.taskId === task.task_id,
    );
    if (existing && sessionRef.current.activeId === existing.id) return;
    commit(current => {
      const turn: ChatTurn = existing ?? {
        kind: 'analysis',
        id: crypto.randomUUID(),
        taskId: task.task_id,
        requestKey: crypto.randomUUID(),
        question: String(task.parameters.question ?? task.result?.question ?? ''),
        selection: copyScope((task.parameters.selection_scope || task.parameters) as unknown as AnalysisParameters),
        parameters: {
          ...task.parameters,
          product_ids: Array.isArray(task.parameters.product_ids) ? [...task.parameters.product_ids] : [],
        } as unknown as AnalysisParameters,
      };
      return {
        ...current,
        turns: existing ? current.turns : [...current.turns, turn],
        activeId: turn.id,
      };
    });
  }, [focusedRoute, task, commit, conversation.id]);

  useEffect(() => {
    if (!activeTurn || task?.task_id !== activeTurn.taskId || !validResult(task)) return;
    const taskDatasetKey = JSON.stringify([task.result.scope.source, task.result.scope.dataset_version]);
    if (datasetIdentity !== undefined && datasetIdentity !== taskDatasetKey) return;
    const effective = { ...activeTurn.parameters, ...task.result.scope };
    const next = contextFromResult(conversationId, task.task_id, effective, task.result);
    if (
      JSON.stringify(sessionRef.current.context) === JSON.stringify(next)
      && sameScope(activeTurn.parameters, effective)
    ) {
      return;
    }
    commit(current => ({
      ...current,
      context: next,
      turns: current.turns.map(turn => (
        turn.id === activeTurn.id && turn.kind === 'analysis'
          ? { ...turn, parameters: effective }
          : turn
      )),
    }));
  }, [activeTurn, task, commit, datasetIdentity]);

  async function submitTurn(turn: ChatTurn, retry = false): Promise<boolean> {
    if (postLock.current || pending) return false;
    const turnDatasetKey = JSON.stringify([turn.parameters.source, turn.parameters.dataset_version]);
    if (
      !isAgentScope(turn.parameters)
      || !isAgentScope(turn.selection || turn.parameters)
      || (datasetIdentity !== undefined && datasetIdentity !== turnDatasetKey)
    ) {
      setErrors(current => ({
        ...current,
        [turn.id]: 'AI 助手仅支持 reviews_csv / reviews-csv；历史任务可查看，请切换数据集后重新提问。',
      }));
      return false;
    }
    const epoch = navigationEpoch.current;
    postLock.current = true;
    setPosting(true);
    setErrors(current => ({ ...current, [turn.id]: '' }));
    commit(current => ({ ...current, activeId: turn.id }));
    try {
      const result = retry && turn.taskId
        ? await api.retry(turn.taskId, turn.retry!.key)
        : await api.submit(
          {
            kind: 'analysis',
            parameters: {
              ...turn.parameters,
              question: turn.query?.effectiveQuestion ?? turn.question,
              selection_scope: copyScope(turn.selection || turn.parameters),
              conversation_id: conversationId,
              context_task_id: turn.query?.parentTaskId,
            },
          },
          turn.requestKey,
        );
      commit(current => ({
        ...current,
        ...(epoch === navigationEpoch.current
          ? {
            intent: {
              task_id: result.task.task_id,
              question: turn.question,
              product_ids: [...turn.parameters.product_ids],
              source: turn.parameters.source,
              dataset_version: turn.parameters.dataset_version,
            },
          }
          : {}),
        turns: current.turns.map(item => (
          item.kind === 'analysis' && item.id === turn.id
            ? { ...item, taskId: result.task.task_id, retry: undefined }
            : item
        )),
      }));
      return true;
    } catch (error) {
      setErrors(current => ({ ...current, [turn.id]: errorMessage(error) }));
      return false;
    } finally {
      postLock.current = false;
      setPosting(false);
    }
  }

  async function send(parameters: AnalysisParameters, question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || resolving.current || pending) return false;
    if (!isAgentScope(parameters)) return false;

    const previous = analysisSession(sessionRef.current).turns.at(-1);
    if (
      previous
      && !previous.taskId
      && !previous.clarification
      && previous.question === text
      && JSON.stringify(previous.parameters) === JSON.stringify(parameters)
    ) {
      return submitTurn(previous);
    }

    const frozen = structuredClone(parameters);
    const epoch = navigationEpoch.current;
    const plan = planFollowupLookup(
      text,
      frozen,
      analysisSession(sessionRef.current).turns,
      sessionRef.current.activeId,
    );
    let parent = undefined;
    let intentParent = plan.intentParent;
    let readError = '';
    if (plan.candidates.length) {
      resolving.current = true;
      setPosting(true);
      try {
        const fetched = await fetchFollowupParents({
          candidates: plan.candidates,
          frozen,
          conversationId,
          activeTask: task,
          isCurrentEpoch: () => epoch === navigationEpoch.current,
          onContextResolved: context => commit(current => ({ ...current, context })),
        });
        if (fetched.aborted) return false;
        parent = fetched.parent;
        intentParent = fetched.intentParent ?? intentParent;
        readError = fetched.readError;
      } finally {
        resolving.current = false;
        setPosting(false);
      }
    }
    if (epoch !== navigationEpoch.current) return false;
    const resolution = buildTurnResolution(
      text,
      frozen,
      conversationId,
      plan.reference,
      parent,
      intentParent,
      readError,
    );

    const turn: ChatTurn = {
      kind: 'analysis',
      id: crypto.randomUUID(),
      requestKey: crypto.randomUUID(),
      question: text,
      selection: frozen,
      ...resolution,
      parameters: resolution.parameters || frozen,
    };
    dismissedRoute.current = routeTaskId;
    commit(current => ({ ...current, turns: [...current.turns, turn], activeId: turn.id }));
    if (turn.clarification) return true;
    return submitTurn(turn);
  }

  async function clarify(turn: ChatTurn, choice: QueryChoice): Promise<void> {
    if (
      postLock.current
      || resolving.current
      || pending
      || turn.clarification?.resolved
      || !sessionRef.current.turns.some(item => item.id === turn.id)
      || sessionRef.current.activeId !== turn.id
      || !turn.clarification?.choices.includes(choice)
    ) {
      return;
    }
    if (scopeKey !== undefined && (!currentScope || !sameScope(currentScope, turn.selection || turn.parameters))) {
      commit(current => ({
        ...current,
        turns: current.turns.map(item => (
          item.id === turn.id && item.kind === 'analysis'
            ? {
              ...item,
              clarification: {
                message: '当前选择范围已改变，请按下方当前范围重新提出完整问题。',
                choices: [],
              },
            }
            : item
        )),
      }));
      return;
    }
    const next: ChatTurn = {
      kind: 'analysis',
      id: crypto.randomUUID(),
      requestKey: crypto.randomUUID(),
      question: `${turn.question}（${choice.label}）`,
      parameters: structuredClone(choice.parameters),
      query: { ...structuredClone(choice.query), createdAt: new Date().toISOString() },
    };
    commit(current => ({
      ...current,
      turns: [
        ...current.turns.map(item => (
          item.id === turn.id && item.kind === 'analysis'
            ? { ...item, clarification: { ...item.clarification!, resolved: true } }
            : item
        )),
        next,
      ],
      activeId: next.id,
    }));
    await submitTurn(next);
  }

  const basicStreamAborts = useRef(new Map<string, AbortController>());

  useEffect(() => () => {
    for (const controller of basicStreamAborts.current.values()) {
      controller.abort();
    }
    basicStreamAborts.current.clear();
  }, []);

  function stopBasicStream(targetSessionId?: string): void {
    const sid = typeof targetSessionId === 'string' ? targetSessionId : conversationId;
    basicStreamAborts.current.get(sid)?.abort(new DOMException('已停止生成', 'AbortError'));
  }

  async function sendBasic(question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || resolving.current || pending) return false;
    postLock.current = true;
    setPosting(true);
    const targetSessionId = conversationId;
    const history = basicHistory(sessionRef.current.turns);
    const turn: BasicChatTurn = {
      kind: 'basic',
      id: crypto.randomUUID(),
      question: text,
      status: 'pending',
    };
    dismissedRoute.current = routeTaskId;
    commit(current => ({ ...current, turns: [...current.turns, turn], activeId: turn.id }));
    const useStream = api.chat === chatApi.chat && typeof api.chatStream === 'function';
    const controller = useStream ? new AbortController() : null;
    if (controller) {
      basicStreamAborts.current.get(targetSessionId)?.abort(new DOMException('已替换为新请求', 'AbortError'));
      basicStreamAborts.current.set(targetSessionId, controller);
    }
    let lastStreamPersistAt = 0;
    try {
      const reply = useStream
        ? await api.chatStream(text, history, {
          signal: controller!.signal,
          onDelta: delta => {
            const now = Date.now();
            const shouldPersist = now - lastStreamPersistAt >= 400;
            if (shouldPersist) lastStreamPersistAt = now;
            commit(current => ({
              ...current,
              turns: current.turns.map(item => (
                item.kind === 'basic' && item.id === turn.id
                  ? { ...item, status: 'streaming', answer: (item.answer ?? '') + delta }
                  : item
              )),
            }), { persist: shouldPersist });
          },
        })
        : await api.chat(text, history);
      if (!reply || typeof reply.answer !== 'string' || !reply.answer.trim()) {
        throw new Error('模型未返回有效回答，请重新发送。');
      }
      commit(current => ({
        ...current,
        turns: current.turns.map(item => (
          item.kind === 'basic' && item.id === turn.id
            ? { ...item, status: 'succeeded', answer: reply.answer }
            : item
        )),
      }));
      return true;
    } catch (error) {
      const stopped = Boolean(controller?.signal.aborted);
      commit(current => ({
        ...current,
        turns: current.turns.map(item => (
          item.kind === 'basic' && item.id === turn.id
            ? {
              ...item,
              status: 'failed',
              error: stopped ? '已手动停止生成，可点击重新发送获取完整回答。' : errorMessage(error),
            }
            : item
        )),
      }));
      return false;
    } finally {
      if (controller && basicStreamAborts.current.get(targetSessionId) === controller) {
        basicStreamAborts.current.delete(targetSessionId);
      }
      postLock.current = false;
      setPosting(false);
    }
  }

  async function retry(turn: ChatTurn): Promise<void> {
    if (
      postLock.current
      || resolving.current
      || pending
      || turn.clarification
      || !sessionRef.current.turns.some(item => item.id === turn.id)
    ) {
      return;
    }
    const next = turn.taskId
      ? {
        ...turn,
        retry: turn.retry?.taskId === turn.taskId
          ? turn.retry
          : { taskId: turn.taskId, key: crypto.randomUUID() },
      }
      : turn;
    commit(current => ({
      ...current,
      turns: current.turns.map(item => (item.id === next.id ? next : item)),
    }));
    await submitTurn(next, !!next.taskId);
  }

  return {
    session,
    activeTurn,
    conversation,
    activeMessage,
    activeState,
    pending,
    posting,
    errors,
    storageError,
    restoreError,
    send,
    sendBasic,
    stopBasicStream,
    retry,
    clarify,
    focusedTaskId: focusedRoute,
    sessions: [...sessions.store.sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
    activeSessionId: conversation.id,
    recoveryRaw: sessions.recoveryRaw,
    createSession: () => {
      navigationEpoch.current++;
      dismissedRoute.current = routeTaskId;
      sessions.createSession();
    },
    selectSession: (id: string) => {
      navigationEpoch.current++;
      dismissedRoute.current = routeTaskId;
      sessions.selectSession(id);
    },
    renameSession: (id: string, title: string) => {
      sessions.renameSession(id, title);
    },
    deleteSession: (id: string) => {
      const activeAbort = basicStreamAborts.current.get(id);
      if (activeAbort) {
        activeAbort.abort(new DOMException('会话已删除', 'AbortError'));
        basicStreamAborts.current.delete(id);
      }
      if (id === conversation.id) {
        navigationEpoch.current++;
        dismissedRoute.current = routeTaskId;
      }
      sessions.deleteSession(id);
    },
    release: () => {
      navigationEpoch.current++;
      commit(current => ({ ...current, activeId: undefined }));
    },
  };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
