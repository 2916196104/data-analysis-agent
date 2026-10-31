import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from '../../api';
import { isActive } from '../../task-state';
import { useTask } from '../../useTask';
import type { AgentResult, AnalysisParameters, Task } from '../../types';
import { analysisSession, CHAT_KEY, isAgentTask, readChatSession, type BasicChatTurn, type Conversation, type ChatTurn, type ConversationAnalysisContext } from './chat-state';
import { historyReference, isFollowup, resolveQuery, sameScope, type QueryChoice, type QueryParent } from './followup';

function contextFromResult(conversationId: string, taskId: string, parameters: AnalysisParameters, result: AgentResult): ConversationAnalysisContext {
  const selected = (result.result_ids || []).map(id => result.results[id]).filter(Boolean);
  const evidence = selected.find(item => item.kind === 'evidence');
  const aspects = [...new Set(selected.flatMap(item => {
    if (item.parameters.aspect) return [String(item.parameters.aspect)];
    return [];
  }))];
  return { conversation_id: conversationId, last_successful_task_id: taskId,
    dataset_id: parameters.source || undefined, dataset_version: parameters.dataset_version || undefined, product_ids: [...parameters.product_ids],
    current_time_range: [parameters.date_from, parameters.date_to],
    baseline_time_range: [parameters.baseline_from, parameters.baseline_to],
    annotation_identity: { model: parameters.model || undefined, prompt_version: parameters.prompt_version || undefined },
    analysis_type: result.analysis_intent, sentiment: result.sentiment_intent, aspect: aspects.length === 1 ? aspects[0] : null,
    evidence_page: evidence?.data.page, evidence_page_size: evidence?.parameters.page_size as number | undefined,
    previous_result_refs: [...(result.result_ids || [])] };
}

function validResult(task: Task | null): task is Task & { result: AgentResult } {
  const result = task?.result as AgentResult | null | undefined;
  return !!task && task.status === 'succeeded' && !task.error && result?.agent === true
    && result.status === 'ok' && result.validation?.status === 'passed';
}

export function useAgentChat(routeTaskId?: string, scopeKey?: string) {
  const [initial] = useState(readChatSession);
  const [conversation, setConversation] = useState<Conversation>(initial.session);
  const sessionRef = useRef(conversation);
  const session = useMemo(() => analysisSession(conversation), [conversation]);
  const previousRoute = useRef(routeTaskId);
  const conversationId = useRef(initial.session.context?.conversation_id || crypto.randomUUID());
  const navigationEpoch = useRef(0);
  const routeRef = useRef([routeTaskId, scopeKey]);
  if (routeRef.current[0] !== routeTaskId || routeRef.current[1] !== scopeKey) {
    routeRef.current = [routeTaskId, scopeKey]; navigationEpoch.current++;
  }
  const [storageError, setStorageError] = useState(initial.error);
  const [posting, setPosting] = useState(false);
  const postLock = useRef(false);
  const resolving = useRef(false);
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
        parameters: { ...task.parameters, product_ids: Array.isArray(task.parameters.product_ids) ? [...task.parameters.product_ids] : [] } as unknown as AnalysisParameters };
      return { ...current, turns: existing ? current.turns : [...current.turns, turn], activeId: turn.id };
    });
  }, [routeTaskId, task, commit]);

  useEffect(() => {
    if (!activeTurn || task?.task_id !== activeTurn.taskId || !validResult(task)) return;
    const effective = { ...activeTurn.parameters, ...task.result.scope };
    const next = contextFromResult(conversationId.current, task.task_id, effective, task.result);
    if (JSON.stringify(sessionRef.current.context) === JSON.stringify(next) && sameScope(activeTurn.parameters, effective)) return;
    commit(current => ({ ...current, context: next, turns: current.turns.map(turn => turn.id === activeTurn.id && turn.kind === 'analysis'
      ? { ...turn, parameters: effective } : turn) }));
  }, [activeTurn, task, commit]);

  async function submitTurn(turn: ChatTurn, retry = false): Promise<boolean> {
    if (postLock.current || pending) return false;
    postLock.current = true;
    setPosting(true);
    setErrors(current => ({ ...current, [turn.id]: '' }));
    commit(current => ({ ...current, activeId: turn.id }));
    try {
      const result = retry && turn.taskId
        ? await api.retry(turn.taskId, turn.retry!.key)
        : await api.submit({ kind: 'analysis', parameters: { ...turn.parameters, question: turn.query?.effectiveQuestion ?? turn.question,
          conversation_id: conversationId.current, ...(turn.query?.parentTaskId ? { context_task_id: turn.query.parentTaskId } : {}) } }, turn.requestKey);
      commit(current => ({ ...current, intent: { task_id: result.task.task_id, question: turn.question,
        product_ids: [...turn.parameters.product_ids], source: turn.parameters.source, dataset_version: turn.parameters.dataset_version },
        turns: current.turns.map(item => item.kind === 'analysis' && item.id === turn.id ? { ...item, taskId: result.task.task_id, retry: undefined } : item) }));
      return true;
    } catch (error) {
      setErrors(current => ({ ...current, [turn.id]: errorMessage(error) }));
      return false;
    } finally { postLock.current = false; setPosting(false); }
  }

  async function send(parameters: AnalysisParameters, question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || resolving.current || pending) return false;
    const previous = analysisSession(sessionRef.current).turns.at(-1);
    if (previous && !previous.taskId && !previous.clarification && previous.question === text
      && JSON.stringify(previous.parameters) === JSON.stringify(parameters)) return submitTurn(previous);
    const frozen = structuredClone(parameters);
    const epoch = navigationEpoch.current;
    const reference = historyReference(text);
    let parent: QueryParent | undefined;
    let intentParent: QueryParent | undefined;
    let readError = '';
    if (isFollowup(text)) {
      // 优先当前轮；当前轮失败时回退到最近一轮已校验成功的任务，不把失败结果写入上下文。
      const turns = analysisSession(sessionRef.current).turns;
      const active = turns.find(turn => turn.id === sessionRef.current.activeId);
      const referenced = reference ? turns.filter(turn => turn.taskId)[reference.index - 1] : undefined;
      const candidates = (reference ? [referenced] : [active, ...[...turns].reverse().filter(turn => sameScope(turn.parameters, frozen))]).filter((turn, index, values): turn is ChatTurn =>
        !!turn && !turn.clarification && !!turn.taskId && values.findIndex(item => item?.id === turn.id) === index);
      if (candidates.length) {
        resolving.current = true;
        setPosting(true);
        try {
          for (const candidate of candidates.slice(0, 4)) {
            const saved = task?.task_id === candidate.taskId ? task : await api.task(candidate.taskId!);
            if (epoch !== navigationEpoch.current) return false;
            if (!intentParent && saved && isAgentTask(saved) && sameScope(frozen, candidate.selection || candidate.parameters)) {
              intentParent = { id: candidate.id, taskId: candidate.taskId, question: candidate.question,
                parameters: candidate.parameters, selection: candidate.selection };
            }
            if (validResult(saved)) {
              parent = { id: candidate.id, taskId: candidate.taskId, parameters: { ...candidate.parameters, ...saved.result.scope }, selection: candidate.selection,
                result: saved.result };
              const context = contextFromResult(conversationId.current, candidate.taskId!, parent.parameters, saved.result);
              commit(current => ({ ...current, context }));
              break;
            }
          }
        } catch (error) { readError = `无法读取上轮查询：${errorMessage(error)}。请恢复连接后重新发送追问。`; }
        finally { resolving.current = false; setPosting(false); }
      }
    } else {
      // 规则未命中的口语也携带有限上下文引用；服务端重新读取并校验，不发送本地结果。
      const turns = analysisSession(sessionRef.current).turns;
      const active = turns.find(turn => turn.id === sessionRef.current.activeId);
      const candidate = [active, ...[...turns].reverse()].find(turn => turn?.taskId
        && !turn.clarification && sameScope(frozen, turn.selection || turn.parameters));
      if (candidate) intentParent = { id: candidate.id, taskId: candidate.taskId, question: candidate.question,
        parameters: candidate.parameters, selection: candidate.selection };
    }
    const resolution = resolveQuery(reference?.text || text, reference && parent ? parent.parameters : frozen, parent, new Date(), intentParent);
    if (!resolution.clarification && !resolution.query.parentTaskId && intentParent?.taskId) {
      resolution.query.parentTurnId = intentParent.id; resolution.query.parentTaskId = intentParent.taskId;
    }
    if (reference && parent) {
      resolution.query.parentTurnId = parent.id; resolution.query.parentTaskId = parent.taskId;
      resolution.parameters = resolution.parameters || structuredClone(parent.parameters);
    }
    if (reference && !parent) resolution.clarification = { message: '所引用轮次不存在或未通过校验，请选择一个已完成的历史分析。', choices: [] };
    // 独立可解析的问题不依赖历史读取成功；只在必须承接上文时保留读取错误。
    if (readError && resolution.clarification) resolution.clarification = { message: readError, choices: [] };
    resolution.query.conversationId = conversationId.current;
    const turn: ChatTurn = { kind: 'analysis', id: crypto.randomUUID(), requestKey: crypto.randomUUID(), question: text,
      selection: frozen, ...resolution, parameters: resolution.parameters || frozen };
    commit(current => ({ ...current, turns: [...current.turns, turn], activeId: turn.id }));
    if (turn.clarification) return true;
    return submitTurn(turn);
  }

  async function clarify(turn: ChatTurn, choice: QueryChoice): Promise<void> {
    if (postLock.current || resolving.current || pending || turn.clarification?.resolved
      || sessionRef.current.activeId !== turn.id || !turn.clarification?.choices.includes(choice)) return;
    const next: ChatTurn = { kind: 'analysis', id: crypto.randomUUID(), requestKey: crypto.randomUUID(),
      question: `${turn.question}（${choice.label}）`, parameters: structuredClone(choice.parameters),
      query: { ...structuredClone(choice.query), createdAt: new Date().toISOString() } };
    commit(current => ({ ...current, turns: [...current.turns.map(item => item.id === turn.id && item.kind === 'analysis'
      ? { ...item, clarification: { ...item.clarification!, resolved: true } } : item), next], activeId: next.id }));
    await submitTurn(next);
  }

  async function sendBasic(question: string): Promise<boolean> {
    const text = question.trim();
    if (!text || text.length > 4000 || postLock.current || resolving.current || pending) return false;
    postLock.current = true;
    setPosting(true);
    const turn: BasicChatTurn = { kind: 'basic', id: crypto.randomUUID(), question: text, status: 'pending' };
    commit(current => ({ ...current, turns: [...current.turns, turn], activeId: turn.id }), false);
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
    if (postLock.current || resolving.current || pending || turn.clarification) return;
    const next = turn.taskId ? { ...turn, retry: turn.retry?.taskId === turn.taskId ? turn.retry : { taskId: turn.taskId, key: crypto.randomUUID() } } : turn;
    commit(current => ({ ...current, turns: current.turns.map(item => item.id === next.id ? next : item) }));
    await submitTurn(next, !!next.taskId);
  }

  return { session, activeTurn, conversation, activeMessage, activeState, pending, posting, errors, storageError, restoreError, send, sendBasic, retry, clarify, focusedTaskId: routeTaskId,
    release: () => { navigationEpoch.current++; commit(current => ({ ...current, activeId: undefined })); } };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
