import { useEffect, useState } from 'react';
import type { AnnotationDefaults, TaskKind } from '../types';

export type AppRoute =
  | { page: 'analysis' }
  | { page: 'assistant'; taskId?: string }
  | { page: 'datasets' }
  | { page: 'annotation'; defaults?: AnnotationDefaults }
  | { page: 'annotation-review' }
  | { page: 'tasks' }
  | { page: 'task'; taskId: string; module: 'analysis' | 'annotation' };

export function taskRoute(taskId: string, kind?: TaskKind, agent = false): AppRoute {
  if (agent) return { page: 'assistant', taskId };
  return { page: 'task', taskId, module: kind === 'label' ? 'annotation' : 'analysis' };
}

export function routeUrl(route: AppRoute): string {
  switch (route.page) {
    case 'datasets': return '/data-management';
    case 'annotation': return '/annotation';
    case 'annotation-review': return '/annotation/review';
    case 'assistant': return route.taskId ? `/assistant/tasks/${encodeURIComponent(route.taskId)}` : '/assistant';
    case 'tasks': return '/tasks';
    case 'task': return `${route.module === 'annotation' ? '/annotation/tasks' : '/tasks'}/${encodeURIComponent(route.taskId)}`;
    default: return '/analysis/new';
  }
}

export function parseRoute(path: string, search = '', legacyTask: string | null = null, defaults?: AnnotationDefaults): AppRoute {
  const pathname = path.replace(/\/+$/, '') || '/';
  if (pathname === '/' && legacyTask) return taskRoute(legacyTask);
  if (pathname === '/data-management') return { page: 'datasets' };
  if (pathname === '/annotation' || pathname === '/annotation/prepare') return { page: 'annotation', defaults };
  if (pathname === '/annotation/review') return { page: 'annotation-review' };
  if (pathname === '/tasks') return { page: 'tasks' };
  if (pathname === '/assistant' || (pathname === '/analysis/new' && new URLSearchParams(search).get('mode') === 'agent')) return { page: 'assistant' };
  const assistantTask = pathname.match(/^\/assistant\/tasks\/([^/]+)$/);
  if (assistantTask) {
    try { return { page: 'assistant', taskId: decodeURIComponent(assistantTask[1]) }; }
    catch { return { page: 'assistant' }; }
  }
  const match = pathname.match(/^\/(annotation\/tasks|tasks)\/([^/]+)$/);
  if (match) {
    try { return { page: 'task', taskId: decodeURIComponent(match[2]), module: match[1] === 'annotation/tasks' ? 'annotation' : 'analysis' }; }
    catch { /* 无效 URL 回到分析页，不提交任何任务。 */ }
  }
  return { page: 'analysis' };
}

function readRoute(legacyTask: string | null): AppRoute {
  return parseRoute(window.location.pathname, window.location.search, legacyTask, window.history.state?.annotationDefaults);
}

function routeState(route: AppRoute) {
  return route.page === 'annotation' && route.defaults ? { annotationDefaults: route.defaults } : null;
}

export function navigate(route: AppRoute, replace = false): void {
  const target = routeUrl(route);
  const sameUrl = `${window.location.pathname}${window.location.search}` === target;
  window.history[replace || sameUrl ? 'replaceState' : 'pushState'](routeState(route), '', target);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function useAppRoute(legacyTask: string | null): AppRoute {
  const [route, setRoute] = useState(() => readRoute(legacyTask));
  useEffect(() => {
    const onPopState = () => setRoute(readRoute(legacyTask));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [legacyTask]);
  useEffect(() => {
    const target = routeUrl(route);
    if (`${window.location.pathname}${window.location.search}` !== target) {
      window.history.replaceState(routeState(route), '', target);
    }
  }, [route]);
  return route;
}
