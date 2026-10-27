import { API_BASE } from '../../api';
import type { AnalysisParameters, Task } from '../../types';

export interface ChatTurn {
  id: string;
  question: string;
  parameters: AnalysisParameters;
  requestKey: string;
  taskId?: string;
  retry?: { taskId: string; key: string };
}
export interface ChatSession { turns: ChatTurn[]; activeId?: string }
export const CHAT_KEY = `review-agent-chat:${API_BASE}`;

export function isAgentTask(task: Task): boolean {
  return task.kind === 'analysis' && (typeof task.parameters.question === 'string' || task.result?.agent === true);
}

export function readChatSession(): { session: ChatSession; error: boolean } {
  try {
    const raw = localStorage.getItem(CHAT_KEY);
    if (!raw) return { session: { turns: [] }, error: false };
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved.turns) || saved.turns.some((turn: ChatTurn) => !turn || typeof turn.id !== 'string'
      || typeof turn.question !== 'string' || typeof turn.requestKey !== 'string'
      || (turn.taskId !== undefined && typeof turn.taskId !== 'string')
      || !turn.parameters || !Array.isArray(turn.parameters.product_ids)
      || !turn.parameters.product_ids.every(id => typeof id === 'string')
      || typeof turn.parameters.date_from !== 'string' || typeof turn.parameters.date_to !== 'string')) throw new Error('Invalid chat history');
    return { session: { turns: saved.turns, activeId: typeof saved.activeId === 'string' ? saved.activeId : saved.turns.at(-1)?.id }, error: false };
  } catch { return { session: { turns: [] }, error: true }; }
}
