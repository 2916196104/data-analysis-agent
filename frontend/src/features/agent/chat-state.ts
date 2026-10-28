import { API_BASE } from '../../api';
import type { AnalysisParameters, Task } from '../../types';

export type ChatMode = 'basic' | 'analysis';

export interface ChatTurn {
  kind: 'analysis';
  id: string;
  question: string;
  parameters: AnalysisParameters;
  requestKey: string;
  taskId?: string;
  retry?: { taskId: string; key: string };
}
export interface BasicChatTurn {
  kind: 'basic';
  id: string;
  question: string;
  status: 'pending' | 'succeeded' | 'failed';
  answer?: string;
  error?: string;
}
export type ConversationTurn = ChatTurn | BasicChatTurn;
export interface ChatSession { turns: ChatTurn[]; activeId?: string }
export interface Conversation { turns: ConversationTurn[]; activeId?: string }
export const CHAT_KEY = `review-agent-chat:${API_BASE}`;

/** 保留旧页面的分析消息视图，也作为唯一允许持久化的历史内容。 */
export function analysisSession(conversation: Conversation): ChatSession {
  const turns = conversation.turns.filter((turn): turn is ChatTurn => turn.kind === 'analysis');
  return { turns, activeId: turns.some(turn => turn.id === conversation.activeId) ? conversation.activeId : undefined };
}

export function isAgentTask(task: Task): boolean {
  return task.kind === 'analysis' && (typeof task.parameters.question === 'string' || task.result?.agent === true);
}

export function readChatSession(): { session: ChatSession; error: boolean } {
  try {
    const raw = localStorage.getItem(CHAT_KEY);
    if (!raw) return { session: { turns: [] }, error: false };
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved.turns) || saved.turns.some((turn: ChatTurn) => !turn
      || (turn.kind !== undefined && turn.kind !== 'analysis') || typeof turn.id !== 'string'
      || typeof turn.question !== 'string' || typeof turn.requestKey !== 'string'
      || (turn.taskId !== undefined && typeof turn.taskId !== 'string')
      || !turn.parameters || !Array.isArray(turn.parameters.product_ids)
      || !turn.parameters.product_ids.every(id => typeof id === 'string')
      || typeof turn.parameters.date_from !== 'string' || typeof turn.parameters.date_to !== 'string')) throw new Error('Invalid chat history');
    // 旧记录没有 kind；恢复时补齐类型，不重新提交模型任务。
    const turns: ChatTurn[] = saved.turns.map((turn: ChatTurn) => ({ ...turn, kind: 'analysis' }));
    return { session: { turns, activeId: turns.some(turn => turn.id === saved.activeId)
      ? saved.activeId : turns.at(-1)?.id }, error: false };
  } catch { return { session: { turns: [] }, error: true }; }
}
