import { API_BASE } from '../../api';
import type { AnalysisParameters, Task } from '../../types';
import type { Clarification, QuerySnapshot } from './followup';

export type ChatMode = 'basic' | 'analysis';

export interface ChatTurn {
  kind: 'analysis';
  id: string;
  question: string;
  parameters: AnalysisParameters;
  selection?: AnalysisParameters;
  requestKey: string;
  taskId?: string;
  retry?: { taskId: string; key: string };
  query?: QuerySnapshot;
  clarification?: Clarification;
}
export interface BasicChatTurn {
  kind: 'basic';
  id: string;
  question: string;
  status: 'pending' | 'succeeded' | 'failed';
  answer?: string;
  error?: string;
}
export interface ConversationAnalysisContext {
  conversation_id: string;
  last_successful_task_id?: string;
  dataset_id?: string;
  dataset_version?: string;
  product_ids: string[];
  current_time_range?: [string, string];
  baseline_time_range?: [string, string];
  annotation_identity?: { model?: string; prompt_version?: string };
  analysis_type?: string;
  sentiment?: string | null;
  aspect?: string | null;
  evidence_page?: number;
  evidence_page_size?: number;
  previous_result_refs: string[];
}
export type ConversationTurn = ChatTurn | BasicChatTurn;
export interface ChatSession { turns: ChatTurn[]; activeId?: string; context?: ConversationAnalysisContext }
export interface Conversation { turns: ConversationTurn[]; activeId?: string; context?: ConversationAnalysisContext }
export const CHAT_KEY = `review-agent-chat:${API_BASE}`;

/** 分析及澄清快照持久化；基础问答仍只保留在内存。 */
export function analysisSession(conversation: Conversation): ChatSession {
  const turns = conversation.turns.filter((turn): turn is ChatTurn => turn.kind === 'analysis');
  return { turns, activeId: turns.some(turn => turn.id === conversation.activeId) ? conversation.activeId : undefined, context: conversation.context };
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
      || typeof turn.parameters.date_from !== 'string' || typeof turn.parameters.date_to !== 'string'
      || (turn.query !== undefined && (!turn.query || typeof turn.query.effectiveQuestion !== 'string' || typeof turn.query.createdAt !== 'string'))
      || (turn.clarification !== undefined && (!turn.clarification || typeof turn.clarification.message !== 'string'
        || !Array.isArray(turn.clarification.choices) || turn.clarification.choices.some(choice => !choice
          || typeof choice.label !== 'string' || typeof choice.query?.effectiveQuestion !== 'string'
          || !Array.isArray(choice.parameters?.product_ids)))))) throw new Error('Invalid chat history');
    // 旧记录没有 kind；恢复时补齐类型，不重新提交模型任务。
    const context = saved.context;
    const validContext = !context || (typeof context === 'object' && typeof context.conversation_id === 'string'
      && Array.isArray(context.product_ids) && context.product_ids.every((id: unknown) => typeof id === 'string')
      && Array.isArray(context.previous_result_refs) && context.previous_result_refs.every((id: unknown) => typeof id === 'string'));
    if (!validContext) throw new Error('Invalid chat context');
    const turns: ChatTurn[] = saved.turns.map((turn: ChatTurn) => ({ ...turn, kind: 'analysis' }));
    return { session: { turns, activeId: turns.some(turn => turn.id === saved.activeId)
      ? saved.activeId : turns.at(-1)?.id, context }, error: false };
  } catch { return { session: { turns: [] }, error: true }; }
}
