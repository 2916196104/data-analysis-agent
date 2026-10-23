import { API_BASE } from './api';
import dayjs from 'dayjs';
import { emptyDraft, type AnalysisDraft } from './analysis-draft';

const KEY = `review-workbench:${API_BASE}`;
export interface LocalHistory { selected: string | null; notes: Record<string, string>; draft: AnalysisDraft }
function dateRange(value: unknown): [string, string] | undefined {
  return Array.isArray(value) && value.length === 2 && value.every(d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) && dayjs(d).format('YYYY-MM-DD') === d) && value[0] <= value[1]
    ? value as [string, string] : undefined;
}
export function readHistory(): LocalHistory {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    return {
      selected: typeof saved.selected === 'string' ? saved.selected : null,
      notes: Object.fromEntries(Object.entries(saved.notes ?? {}).filter(([, value]) => typeof value === 'string')) as Record<string, string>,
      draft: { dataset: typeof saved.draft?.dataset === 'string' ? saved.draft.dataset : '',
        productIds: Array.isArray(saved.draft?.productIds) ? saved.draft.productIds.filter((id: unknown) => typeof id === 'string') : [],
        profileId: Number.isInteger(saved.draft?.profileId) && saved.draft.profileId > 0 ? saved.draft.profileId : undefined,
        current: dateRange(saved.draft?.current), baseline: dateRange(saved.draft?.baseline),
        note: typeof saved.draft?.note === 'string' ? saved.draft.note : '' },
    };
  } catch { return { selected: null, notes: {}, draft: emptyDraft() }; }
}
export function writeHistory(history: LocalHistory): boolean {
  try { localStorage.setItem(KEY, JSON.stringify(history)); return true; }
  catch { return false; }
}
export function exportResult(value: unknown, name: string) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${name}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
