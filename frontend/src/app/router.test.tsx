import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { navigate, parseRoute, routeUrl, taskRoute, useAppRoute } from './router';

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });

describe('模块路由与浏览器历史', () => {
  it.each([
    ['/data-management', 'datasets'], ['/annotation', 'annotation'],
    ['/annotation/prepare', 'annotation'], ['/annotation/review', 'annotation-review'],
    ['/tasks', 'tasks'], ['/analysis/new', 'analysis'],
  ])('%s 解析为独立模块 %s', (path, page) => {
    expect(parseRoute(path).page).toBe(page);
  });

  it('标注任务由任务类型和 URL 归属，不依赖上次菜单', () => {
    expect(taskRoute('label/id', 'label')).toEqual({ page: 'task', taskId: 'label/id', module: 'annotation' });
    expect(routeUrl(taskRoute('label/id', 'label'))).toBe('/annotation/tasks/label%2Fid');
    expect(parseRoute('/annotation/tasks/label%2Fid')).toEqual(taskRoute('label/id', 'label'));
    expect(parseRoute('/annotation', '', 'old-analysis').page).toBe('annotation');
  });

  it('replay 任务当前沿用分析任务路径，记录未区分模块的边界行为', () => {
    const route = taskRoute('replay/id', 'replay');
    expect(route).toEqual({ page: 'task', taskId: 'replay/id', module: 'analysis' });
    expect(routeUrl(route)).toBe('/tasks/replay%2Fid');
    expect(parseRoute('/tasks/replay%2Fid')).toEqual(route);
  });

  it('人工复核位于评论标注模块内，并使用独立页面路径', () => {
    expect(routeUrl({ page: 'annotation-review' })).toBe('/annotation/review');
    expect(parseRoute('/annotation/review')).toEqual({ page: 'annotation-review' });
  });

  it('仅旧根入口兼容本机选中任务，显式 URL 优先', () => {
    expect(parseRoute('/', '', 'legacy')).toEqual(taskRoute('legacy'));
    expect(parseRoute('/data-management', '', 'legacy')).toEqual({ page: 'datasets' });
    expect(parseRoute('/tasks/%broken').page).toBe('analysis');
  });

  it('AI 助手和历史问答使用独立路由，旧弹窗 URL 兼容到聊天页', () => {
    expect(parseRoute('/assistant')).toEqual({ page: 'assistant' });
    expect(parseRoute('/analysis/new', '?mode=agent')).toEqual({ page: 'assistant' });
    expect(routeUrl(taskRoute('agent-task', 'analysis', true))).toBe('/assistant/tasks/agent-task');
    expect(parseRoute('/assistant/tasks/agent-task')).toEqual({ page: 'assistant', taskId: 'agent-task' });
  });

  it('直接进入标注页，并能在浏览器后退和前进时同步模块', async () => {
    window.history.replaceState(null, '', '/annotation');
    const { result } = renderHook(() => useAppRoute('old-task'));
    expect(result.current.page).toBe('annotation');
    act(() => navigate({ page: 'datasets' }));
    expect(result.current.page).toBe('datasets');
    act(() => window.history.back());
    await waitFor(() => expect(result.current.page).toBe('annotation'));
    expect(window.location.pathname).toBe('/annotation');
    act(() => window.history.forward());
    await waitFor(() => expect(result.current.page).toBe('datasets'));
  });

  it('刷新和返回保留标注范围及批次参数，不把它们写入数据管理路由', async () => {
    window.history.replaceState(null, '', '/annotation');
    const defaults = { dataset: '["source","version"]', products: ['A'], limit: 2, workers: 1,
      retryFailed: true, scope: { date_from: '2023-01-01', date_to: '2023-01-31', as_of: '2023-02-01T00:00:00Z' } };
    let hook = renderHook(() => useAppRoute(null));
    act(() => navigate({ page: 'annotation', defaults }, true));
    hook.unmount();
    hook = renderHook(() => useAppRoute(null));
    expect(hook.result.current).toEqual({ page: 'annotation', defaults });
    act(() => navigate(taskRoute('batch', 'label')));
    act(() => window.history.back());
    await waitFor(() => expect(hook.result.current).toEqual({ page: 'annotation', defaults }));
    act(() => navigate({ page: 'datasets' }));
    expect(window.history.state).toBeNull();
  });
});
