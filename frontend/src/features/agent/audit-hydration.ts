import type { AgentResult, AgentToolResult } from '../../types';
import type { TaskAuditRecord } from '../../shared/api/task-api';

export interface AuditStorageRef {
  version: string;
  storage: string;
  audit_sha256: string;
  audit_bytes: number;
  fields: string[];
}

export interface AuditSplitPayload {
  version: string;
  top_level?: Record<string, unknown>;
  tool_data?: Record<string, Record<string, unknown>>;
}

export function needsAuditHydration(result: AgentResult | undefined): boolean {
  const ref = result?._audit_ref;
  return Boolean(ref && ref.storage === 'task_audit_blobs' && typeof ref.audit_sha256 === 'string');
}

export function auditSizeLabel(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/**
 * 将 `/api/v1/tasks/{id}/audit` 返回的分离审计载荷无损合并回轻量 AgentResult，
 * 供「查看统计、证据与执行记录」与「导出分析报告」按需使用。
 */
export function rehydrateAgentResult(result: AgentResult, record: TaskAuditRecord): AgentResult {
  if (!needsAuditHydration(result) || !record.stored_separately || !record.audit) {
    return result;
  }
  const payload = record.audit as unknown as AuditSplitPayload;
  if (result._audit_ref && record.audit_sha256 && record.audit_sha256 !== result._audit_ref.audit_sha256) {
    throw new Error('审计证据校验摘要不一致，请刷新任务后重试。');
  }

  const nextResults: Record<string, AgentToolResult> = { ...result.results };
  const toolData = payload.tool_data;
  if (toolData && typeof toolData === 'object') {
    for (const [rid, fields] of Object.entries(toolData)) {
      const existing = nextResults[rid];
      if (!existing || !fields || typeof fields !== 'object') continue;
      nextResults[rid] = {
        ...existing,
        data: {
          ...(existing.data as Record<string, unknown>),
          ...fields,
        },
      } as AgentToolResult;
    }
  }

  const restored: AgentResult = {
    ...result,
    ...(payload.top_level ?? {}),
    results: nextResults,
  };
  delete restored._audit_ref;
  return restored;
}
