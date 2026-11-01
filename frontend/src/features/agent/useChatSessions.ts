import { useCallback, useMemo, useRef, useState } from 'react';
import { CHAT_KEY, readChatSession, type ChatSession, type ChatStore, type Conversation } from './chat-state';

export function newSession(id: string = crypto.randomUUID()): ChatSession {
  const now = new Date().toISOString();
  return { id, title: '新会话', createdAt: now, updatedAt: now, turns: [] };
}

export function readChatStore(): { store: ChatStore; error: boolean; backup?: string } {
  const empty = newSession();
  const fallback: ChatStore = { version: 2, sessions: [empty], activeSessionId: empty.id };
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(CHAT_KEY);
    if (!raw) return { store: fallback, error: false };
    const saved = JSON.parse(raw);
    if (saved.version === undefined) {
      const legacy = readChatSession(raw);
      if (legacy.error) throw new Error('Invalid legacy history');
      const id = legacy.session.context?.conversation_id || legacy.session.turns.find(turn => turn.query?.conversationId)?.query?.conversationId || empty.id;
      const migrated = { ...newSession(id), ...legacy.session, title: legacy.session.turns[0]?.question.slice(0, 40) || '历史会话' };
      return { store: { version: 2, sessions: [migrated], activeSessionId: id }, error: false, backup: raw };
    }
    if (saved.version !== 2 || !Array.isArray(saved.sessions) || !saved.sessions.length) throw new Error('Invalid sessions');
    const ids = new Set<string>();
    for (const session of saved.sessions as ChatSession[]) {
      if (!session || typeof session.id !== 'string' || !session.id || ids.has(session.id)
        || typeof session.title !== 'string' || !Number.isFinite(Date.parse(session.createdAt))
        || !Number.isFinite(Date.parse(session.updatedAt)) || !Array.isArray(session.turns)) throw new Error('Invalid session');
      ids.add(session.id);
      const analysis = session.turns.filter(turn => turn?.kind !== 'basic');
      if (readChatSession(JSON.stringify({ ...session, turns: analysis })).error) throw new Error('Invalid analysis');
      if (session.context && session.context.conversation_id !== session.id) throw new Error('Invalid context owner');
      const turnIds = new Set<string>();
      session.turns = session.turns.map(turn => {
        if (!turn || turnIds.has(turn.id)) throw new Error('Invalid turn');
        turnIds.add(turn.id);
        if (turn.kind !== 'basic') return { ...turn, kind: 'analysis' };
        if (typeof turn.id !== 'string' || typeof turn.question !== 'string' || !['pending', 'succeeded', 'failed'].includes(turn.status)
          || (turn.answer !== undefined && typeof turn.answer !== 'string') || (turn.error !== undefined && typeof turn.error !== 'string')) throw new Error('Invalid basic message');
        // 基础问答没有服务端任务引用，刷新后不能自动重发模型请求。
        return turn.status === 'pending' ? { ...turn, status: 'failed', error: '页面已刷新，回答未确认，请重新发送。' } : turn;
      });
      if (session.activeId && !turnIds.has(session.activeId)) throw new Error('Invalid active turn');
    }
    if (!ids.has(saved.activeSessionId)) throw new Error('Invalid active session');
    return { store: saved, error: false };
  } catch { return { store: fallback, error: true, backup: raw || undefined }; }
}

interface RequestState { postLock: { current: boolean }; resolving: { current: boolean }; posting: boolean; errors: Record<string, string> }

export function useChatSessions() {
  const [initial] = useState(readChatStore);
  const [store, setStore] = useState(initial.store);
  const storeRef = useRef(store);
  const backup = useRef(initial.backup);
  const [writeError, setWriteError] = useState(false);
  const [, render] = useState(0);
  const requests = useRef(new Map<string, RequestState>());
  const id = store.activeSessionId;
  if (!requests.current.has(id)) requests.current.set(id, { postLock: { current: false }, resolving: { current: false }, posting: false, errors: {} });
  const request = requests.current.get(id)!;
  const conversation = store.sessions.find(item => item.id === id)!;
  // 回调绑定发起请求的会话，切换后的渲染不会改变旧回调的写入目标。
  const sessionRef = useMemo(() => ({ get current() { return storeRef.current.sessions.find(item => item.id === id)!; } }), [id]);
  const save = useCallback((next: ChatStore) => {
    storeRef.current = next; setStore(next);
    try {
      if (initial.error && initial.backup === undefined) throw new Error('History could not be read');
      if (backup.current !== undefined) {
        localStorage.setItem(`${CHAT_KEY}:backup:${crypto.randomUUID()}`, backup.current);
        backup.current = undefined;
      }
      localStorage.setItem(CHAT_KEY, JSON.stringify(next)); setWriteError(false);
    } catch { setWriteError(true); }
  }, []);
  const commit = useCallback((update: (current: Conversation) => Conversation) => {
    const current = storeRef.current.sessions.find(item => item.id === id)!;
    const next = update(current);
    if (next === current) return;
    const session = { ...current, ...next, title: next.turns[0]?.question.slice(0, 40) || current.title,
      updatedAt: next.turns !== current.turns ? new Date().toISOString() : current.updatedAt };
    save({ ...storeRef.current, sessions: storeRef.current.sessions.map(item => item.id === id ? session : item) });
  }, [id, save]);
  const selectSession = useCallback((target: string) => {
    if (storeRef.current.sessions.some(item => item.id === target)) save({ ...storeRef.current, activeSessionId: target });
  }, [save]);
  const createSession = useCallback(() => {
    const current = storeRef.current.sessions.find(item => item.id === storeRef.current.activeSessionId)!;
    if (!current.turns.length) return;
    const session = storeRef.current.sessions.find(item => !item.turns.length) || newSession();
    save({ ...storeRef.current, sessions: storeRef.current.sessions.includes(session) ? storeRef.current.sessions : [...storeRef.current.sessions, session], activeSessionId: session.id });
  }, [save]);
  return { store, storeRef, save, conversation, sessionRef, commit, selectSession, createSession,
    storageError: initial.error || writeError, recoveryRaw: initial.error ? initial.backup : undefined,
    postLock: request.postLock, resolving: request.resolving, posting: request.posting, errors: request.errors,
    setPosting: (value: boolean) => { request.posting = value; render(value => value + 1); },
    setErrors: (update: (current: Record<string, string>) => Record<string, string>) => { request.errors = update(request.errors); render(value => value + 1); } };
}
