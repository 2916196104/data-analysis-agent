"""任务轻量结果与大体积审计证据的分离与无损复原。

设计原则：
1. `analysis_result_integrity` 始终对拆分前的完整结果计算 SHA-256，不改变完整性校验语义。
2. 仅当结果超过阈值且存在可拆分的重型审计字段（如明细 items、trace、answer_sections、retrieval、
   rank_examples 等）时，将其分离到 `task_audit_blobs` 表，并在轻量结果中写入 `_audit_ref`。
3. 复原时严格校验 `audit_sha256`；若审计记录缺失或被篡改，不静默放行，交由上游完整性校验失败关闭。
"""

from copy import deepcopy
from typing import Any

from data_analysis_agent.core.serialization import canonical_json, content_sha256

AUDIT_STORAGE_VERSION = 'task-audit-split-v1'
AUDIT_SPLIT_THRESHOLD_BYTES = 64 * 1024
TOP_LEVEL_AUDIT_FIELDS = ('trace', 'answer_sections')
TOOL_DATA_AUDIT_FIELDS = (
    'items',
    'unclassified_items',
    'rank_examples',
    'retrieval',
    'opinion_groups',
    'batches',
    'candidate_judgments',
)


def audit_ref_from_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    ref = result.get('_audit_ref')
    if (isinstance(ref, dict)
            and ref.get('version') == AUDIT_STORAGE_VERSION
            and ref.get('storage') == 'task_audit_blobs'
            and isinstance(ref.get('audit_sha256'), str)
            and len(ref['audit_sha256']) == 64):
        return ref
    return None


def split_task_result(
    result: Any,
    *,
    threshold_bytes: int = AUDIT_SPLIT_THRESHOLD_BYTES,
) -> tuple[Any, dict[str, Any] | None, str | None]:
    """将超阈值的 Agent 分析结果拆分为 (lightweight_result, audit_payload, audit_sha256)。

    未超阈值或无可拆分审计字段时原样返回 `(result, None, None)`。
    """
    if not isinstance(result, dict) or result.get('agent') is not True:
        return result, None, None
    encoded = canonical_json(result).encode('utf-8')
    if len(encoded) < threshold_bytes:
        return result, None, None

    light = deepcopy(result)
    top_level: dict[str, Any] = {}
    tool_data: dict[str, dict[str, Any]] = {}
    extracted_fields: list[str] = []

    for field in TOP_LEVEL_AUDIT_FIELDS:
        if field in light:
            top_level[field] = light.pop(field)
            extracted_fields.append(field)

    results_map = light.get('results')
    if isinstance(results_map, dict):
        for rid, entry in results_map.items():
            if not isinstance(entry, dict):
                continue
            data = entry.get('data')
            if not isinstance(data, dict):
                continue
            extracted_for_tool: dict[str, Any] = {}
            for field in TOOL_DATA_AUDIT_FIELDS:
                if field in data:
                    extracted_for_tool[field] = data.pop(field)
                    extracted_fields.append(f'results.{rid}.data.{field}')
            if extracted_for_tool:
                tool_data[rid] = extracted_for_tool

    if not extracted_fields:
        return result, None, None

    audit_payload = {
        'version': AUDIT_STORAGE_VERSION,
        'top_level': top_level,
        'tool_data': tool_data,
    }
    audit_bytes = len(canonical_json(audit_payload).encode('utf-8'))
    audit_sha256 = content_sha256(audit_payload)
    light['_audit_ref'] = {
        'version': AUDIT_STORAGE_VERSION,
        'storage': 'task_audit_blobs',
        'audit_sha256': audit_sha256,
        'audit_bytes': audit_bytes,
        'fields': extracted_fields,
    }
    return light, audit_payload, audit_sha256


def rehydrate_task_result(light_result: Any, audit_payload: Any) -> Any:
    """将轻量结果与审计载荷无损合成为原始完整结果；校验失败时返回原轻量对象以触发上游 fail-closed。"""
    ref = audit_ref_from_result(light_result)
    if ref is None:
        return light_result
    if not isinstance(audit_payload, dict) or audit_payload.get('version') != AUDIT_STORAGE_VERSION:
        return light_result
    if content_sha256(audit_payload) != ref['audit_sha256']:
        return light_result

    restored = deepcopy(light_result)
    restored.pop('_audit_ref', None)

    top_level = audit_payload.get('top_level')
    if isinstance(top_level, dict):
        for key, value in top_level.items():
            restored[key] = value

    tool_data = audit_payload.get('tool_data')
    results_map = restored.get('results')
    if isinstance(tool_data, dict) and isinstance(results_map, dict):
        for rid, fields in tool_data.items():
            entry = results_map.get(rid)
            if isinstance(entry, dict) and isinstance(entry.get('data'), dict) and isinstance(fields, dict):
                for key, value in fields.items():
                    entry['data'][key] = value

    return restored
