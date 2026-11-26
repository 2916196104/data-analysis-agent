import { api, errorMessage } from '../../api';
import type { AgentResult, AnalysisParameters, Task } from '../../types';
import {
  isAgentTask,
  type ChatTurn,
  type ConversationAnalysisContext,
} from './chat-state';
import {
  historyReference,
  isFollowup,
  resolveQuery,
  sameScope,
  sameScopeIdentity,
  type QueryParent,
} from './followup';

export function contextFromResult(
  conversationId: string,
  taskId: string,
  parameters: AnalysisParameters,
  result: AgentResult,
): ConversationAnalysisContext {
  const selected = (result.result_ids || []).map(id => result.results[id]).filter(Boolean);
  const evidence = selected.find(item => item.kind === 'evidence');
  const aspects = [...new Set(selected.flatMap(item => (
    item.parameters.aspect ? [String(item.parameters.aspect)] : []
  )))];
  return {
    conversation_id: conversationId,
    last_successful_task_id: taskId,
    dataset_id: parameters.source || undefined,
    dataset_version: parameters.dataset_version || undefined,
    product_ids: [...parameters.product_ids],
    current_time_range: [parameters.date_from, parameters.date_to],
    baseline_time_range: [parameters.baseline_from, parameters.baseline_to],
    annotation_identity: {
      model: parameters.model || undefined,
      prompt_version: parameters.prompt_version || undefined,
    },
    analysis_type: result.analysis_intent,
    sentiment: result.sentiment_intent,
    aspect: aspects.length === 1 ? aspects[0] : null,
    evidence_page: evidence?.data.page,
    evidence_page_size: evidence?.parameters.page_size as number | undefined,
    previous_result_refs: [...(result.result_ids || [])],
  };
}

export function validResult(task: Task | null): task is Task & { result: AgentResult } {
  const result = task?.result as AgentResult | null | undefined;
  return !!task
    && task.status === 'succeeded'
    && !task.error
    && result?.agent === true
    && result.status === 'ok'
    && result.validation?.status === 'passed'
    && !!result.scope
    && sameScopeIdentity(task.parameters as unknown as AnalysisParameters, result.scope);
}

export interface FollowupPlan {
  reference: ReturnType<typeof historyReference>;
  candidates: ChatTurn[];
  intentParent?: QueryParent;
}

export function planFollowupLookup(
  text: string,
  frozen: AnalysisParameters,
  turns: ChatTurn[],
  activeId?: string,
): FollowupPlan {
  const reference = historyReference(text);
  const active = turns.find(turn => turn.id === activeId);
  if (isFollowup(text)) {
    // 优先当前轮；当前轮失败时回退到最近一轮已校验成功的任务，不把失败结果写入上下文。
    const referenced = reference ? turns.filter(turn => turn.taskId)[reference.index - 1] : undefined;
    const rawCandidates = reference
      ? [referenced]
      : [active, ...[...turns].reverse().filter(turn => sameScope(turn.parameters, frozen))];
    const candidates = rawCandidates.filter((turn, index, values): turn is ChatTurn => (
      !!turn
      && !turn.clarification
      && !!turn.taskId
      && values.findIndex(item => item?.id === turn.id) === index
    ));
    return { reference, candidates };
  }
  // 规则未命中的口语也携带有限上下文引用；服务端重新读取并校验，不发送本地结果。
  const candidate = [active, ...[...turns].reverse()].find(turn => (
    turn?.taskId
    && !turn.clarification
    && sameScopeIdentity(frozen, turn.selection || turn.parameters)
  ));
  const intentParent = candidate
    ? {
      id: candidate.id,
      taskId: candidate.taskId,
      question: candidate.question,
      parameters: candidate.parameters,
      selection: candidate.selection,
    }
    : undefined;
  return { reference, candidates: [], intentParent };
}

interface FetchParentsOptions {
  candidates: ChatTurn[];
  frozen: AnalysisParameters;
  conversationId: string;
  activeTask: Task | null;
  isCurrentEpoch: () => boolean;
  onContextResolved: (context: ConversationAnalysisContext) => void;
}

export async function fetchFollowupParents({
  candidates,
  frozen,
  conversationId,
  activeTask,
  isCurrentEpoch,
  onContextResolved,
}: FetchParentsOptions): Promise<{
  aborted: boolean;
  parent?: QueryParent;
  intentParent?: QueryParent;
  readError: string;
}> {
  let parent: QueryParent | undefined;
  let intentParent: QueryParent | undefined;
  let readError = '';
  try {
    for (const candidate of candidates.slice(0, 4)) {
      const saved = activeTask?.task_id === candidate.taskId
        ? activeTask
        : await api.task(candidate.taskId!);
      if (!isCurrentEpoch()) return { aborted: true, readError: '' };
      if (saved?.parameters.conversation_id !== conversationId) continue;
      const candidateScope = candidate.selection || candidate.parameters;
      if (
        !sameScopeIdentity(frozen, candidateScope)
        || !sameScopeIdentity(frozen, saved.parameters as unknown as AnalysisParameters)
      ) {
        continue;
      }
      if (!intentParent && saved && isAgentTask(saved)) {
        intentParent = {
          id: candidate.id,
          taskId: candidate.taskId,
          question: candidate.question,
          parameters: candidate.parameters,
          selection: candidate.selection,
        };
      }
      if (validResult(saved)) {
        parent = {
          id: candidate.id,
          taskId: candidate.taskId,
          parameters: { ...candidate.parameters, ...saved.result.scope },
          selection: candidate.selection,
          result: saved.result,
        };
        onContextResolved(
          contextFromResult(conversationId, candidate.taskId!, parent.parameters, saved.result),
        );
        break;
      }
    }
  } catch (error) {
    readError = `无法读取上轮查询：${errorMessage(error)}。请恢复连接后重新发送追问。`;
  }
  return { aborted: false, parent, intentParent, readError };
}

export function buildTurnResolution(
  text: string,
  frozen: AnalysisParameters,
  conversationId: string,
  reference: ReturnType<typeof historyReference>,
  parent: QueryParent | undefined,
  intentParent: QueryParent | undefined,
  readError: string,
) {
  const resolution = resolveQuery(reference?.text || text, frozen, parent, new Date(), intentParent);
  if (!resolution.clarification && !resolution.query.parentTaskId && intentParent?.taskId) {
    resolution.query.parentTurnId = intentParent.id;
    resolution.query.parentTaskId = intentParent.taskId;
  }
  if (reference && parent && sameScope(frozen, parent.selection || parent.parameters)) {
    resolution.query.parentTurnId = parent.id;
    resolution.query.parentTaskId = parent.taskId;
    resolution.parameters = resolution.parameters || structuredClone(parent.parameters);
  }
  if (reference && !parent) {
    resolution.clarification = {
      message: '所引用轮次不存在或未通过校验，请选择一个已完成的历史分析。',
      choices: [],
    };
  }
  if (parent && !sameScope(frozen, parent.selection || parent.parameters)) {
    const standalone = resolveQuery(reference?.text || text, frozen);
    resolution.parameters = standalone.parameters || frozen;
    resolution.query = standalone.query;
    resolution.clarification = !reference && !standalone.clarification
      ? undefined
      : {
        message: '当前选择范围与历史分析不同，不能继承旧分析上下文。请按下方当前范围提出完整问题。',
        choices: [],
      };
    if (!resolution.clarification && sameScopeIdentity(frozen, parent.parameters)) {
      resolution.query.parentTurnId = parent.id;
      resolution.query.parentTaskId = parent.taskId;
    }
  }
  // 独立可解析的问题不依赖历史读取成功；只在必须承接上文时保留读取错误。
  if (readError && resolution.clarification) {
    resolution.clarification = { message: readError, choices: [] };
  }
  resolution.query.conversationId = conversationId;
  return resolution;
}
