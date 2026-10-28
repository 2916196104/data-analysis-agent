import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AppLayout from './AppLayout';

const props = () => ({
  children: <h1>当前内容</h1>, connection: { status: 'success' as const, text: '服务已连接' },
  loading: false, datasetSummary: '当前数据集', hasProducts: true, section: 'analysis' as const,
  onRefresh: vi.fn(), onHistory: vi.fn(), onImport: vi.fn(), onPrepare: vi.fn(),
  onAnnotation: vi.fn(), onPreview: vi.fn(), onNewAnalysis: vi.fn(), onDatasets: vi.fn(), onAgent: vi.fn(),
});

beforeEach(() => { Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() }); });
afterEach(cleanup);

describe('工作空间折叠导航', () => {
  it('Escape 收起导航并把焦点交回菜单按钮', () => {
    render(<AppLayout {...props()} />);
    const trigger = screen.getByRole('button', { name: '工作空间导航' });
    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    screen.getByRole('button', { name: '数据管理' }).focus();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger);
  });

  it.each(['导入数据文件', '准备数据', '数据详情', '刷新数据'])(
    '%s 保留原回调并收起导航，为后续页面或弹窗留出空间', name => {
      const callbacks = props();
      render(<AppLayout {...callbacks} />);
      const trigger = screen.getByRole('button', { name: '工作空间导航' });
      fireEvent.click(trigger);
      fireEvent.click(screen.getByRole('button', { name }));
      const actions: Record<string, () => void> = { 导入数据文件: callbacks.onImport, 准备数据: callbacks.onPrepare,
        数据详情: callbacks.onPreview, 刷新数据: callbacks.onRefresh };
      expect(actions[name]).toHaveBeenCalledOnce();
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
    },
  );

  it('外部路由切换时收起导航，保留模块高亮', () => {
    const callbacks = props();
    const view = render(<AppLayout {...callbacks} />);
    fireEvent.click(screen.getByRole('button', { name: '工作空间导航' }));
    view.rerender(<AppLayout {...callbacks} section="datasets" />);
    expect(screen.getByRole('button', { name: '工作空间导航' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByRole('button', { name: '数据管理' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('button', { name: '新建分析' }).getAttribute('aria-current')).toBeNull();
  });
});
