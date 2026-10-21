import { API_BASE } from './api';

const KEY = `review-workbench:${API_BASE}`;
export interface LocalHistory { selected: string | null; notes: Record<string, string> }
export function readHistory(): LocalHistory {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    return {
      selected: typeof saved.selected === 'string' ? saved.selected : null,
      notes: Object.fromEntries(Object.entries(saved.notes ?? {}).filter(([, value]) => typeof value === 'string')) as Record<string, string>,
    };
  } catch { return { selected: null, notes: {} }; }
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
