import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Badge, Button, Tooltip } from 'antd';
import {
  AppstoreOutlined, BarChartOutlined, BellOutlined, CheckOutlined,
  DatabaseOutlined, FolderOpenOutlined, HistoryOutlined, ImportOutlined,
  MenuOutlined, ReloadOutlined, RobotOutlined, TagsOutlined, UserOutlined,
} from '@ant-design/icons';
import DatasetSelector from '../features/datasets/components/DatasetSelector';

interface AppLayoutProps {
  children: ReactNode;
  connection: { status: 'error' | 'success' | 'warning'; text: string };
  loading: boolean;
  taskCount?: number;
  datasetSummary: string;
  hasProducts: boolean;
  section: 'analysis' | 'datasets' | 'annotation' | 'tasks' | 'assistant';
  fillContent?: boolean;
  onRefresh: () => void;
  onHistory: () => void;
  onImport: () => void;
  onPrepare: () => void;
  onAnnotation: () => void;
  onPreview: () => void;
  onNewAnalysis: () => void;
  onDatasets: () => void;
  onAgent: () => void;
  selectedDatasetId?: string;
  datasetOptions?: { value: string; label: string }[];
  datasetLoading?: boolean;
  onDatasetChange?: (value: string) => void;
}

/** 一级模块高亮由当前路由归属决定。 */
export default function AppLayout({
  children, connection, loading, taskCount, datasetSummary, fillContent = false,
  hasProducts, section, onRefresh, onHistory, onImport, onPrepare, onAnnotation,
  onPreview, onNewAnalysis, onDatasets, onAgent,
  selectedDatasetId, datasetOptions = [], datasetLoading = false, onDatasetChange,
}: AppLayoutProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { content.current?.scrollTo(0, 0); setMenuOpen(false); }, [section]);
  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        menuTrigger.current?.focus();
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [menuOpen]);
  const navigate = (action: () => void) => { action(); setMenuOpen(false); };
  const pageName = section === 'assistant' ? 'AI 助手' : section === 'tasks' ? '任务与报告' : section === 'annotation' ? '评论标注' : section === 'datasets' ? '数据管理' : '评论分析';
  return <div className="workbench">
    <div className="workspace-main">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="brand-mark"><BarChartOutlined /></div>
            <div className="brand-copy"><strong>观数</strong><span>/ {pageName}</span></div>
          </div>
          <div className="header-actions">
            {onDatasetChange && <DatasetSelector
              value={selectedDatasetId} options={datasetOptions} disabled={datasetLoading}
              onChange={onDatasetChange} ariaLabel="全局数据集"
            />}
            <Tooltip title="通知 · 未开放"><Button type="text" aria-label="通知（未开放）" aria-disabled="true" icon={<BellOutlined />} /></Tooltip>
            <Tooltip title="用户中心 · 未开放"><Button type="text" aria-label="用户中心（未开放）" aria-disabled="true" icon={<UserOutlined />} /></Tooltip>
            <Button ref={menuTrigger} className="mobile-menu-trigger" type="text" aria-label="工作空间导航" aria-controls="workspace-navigation" aria-expanded={menuOpen} icon={<MenuOutlined />} onClick={() => setMenuOpen(value => !value)} />
          </div>
        </div>
      </header>
      <div className="workspace-body">
        <aside className={`workspace-sidebar ${menuOpen ? 'is-open' : ''}`}>
          <div className="sidebar-caption">工作空间</div>
          <nav id="workspace-navigation" className="sidebar-nav" aria-label="工作空间">
            <button disabled title="数据概览 · 未开放"><AppstoreOutlined /><span>数据概览</span><small>未开放</small></button>
            <button aria-label="新建分析" aria-current={section === 'analysis' ? 'page' : undefined} className={section === 'analysis' ? 'active' : ''} onClick={() => navigate(onNewAnalysis)}><BarChartOutlined /><span>评论分析</span></button>
            <button aria-label="数据管理" className={section === 'datasets' ? 'active' : ''} aria-current={section === 'datasets' ? 'page' : undefined} onClick={() => navigate(onDatasets)}><DatabaseOutlined /><span>数据管理</span></button>
            <button aria-label="评论标注" className={section === 'annotation' ? 'active' : ''} aria-current={section === 'annotation' ? 'page' : undefined} onClick={() => navigate(onAnnotation)}><TagsOutlined /><span>评论标注</span></button>
            <button aria-label="AI 助手" className={section === 'assistant' ? 'active' : ''} aria-current={section === 'assistant' ? 'page' : undefined} onClick={() => navigate(onAgent)}><RobotOutlined /><span>AI 助手</span></button>
            <button aria-label="任务记录" className={section === 'tasks' ? 'active' : ''} aria-current={section === 'tasks' ? 'page' : undefined} onClick={() => navigate(onHistory)}><HistoryOutlined /><span>任务与报告</span>{taskCount ? <small>{taskCount}</small> : null}</button>
          </nav>
          <div className="sidebar-tools">
            <span className="connection-label"><Badge status={connection.status} text={connection.text} /></span>
            <div className="workspace-actions" role="group" aria-label="工作台操作">
              <Tooltip title="导入数据"><Button type="text" aria-label="导入数据文件" icon={<ImportOutlined />} onClick={() => navigate(onImport)} /></Tooltip>
              <Tooltip title="准备数据"><Button type="text" aria-label="准备数据" icon={<CheckOutlined />} onClick={() => navigate(onPrepare)} /></Tooltip>
              <Tooltip title="数据详情"><Button type="text" aria-label="数据详情" icon={<FolderOpenOutlined />} disabled={!hasProducts} onClick={() => navigate(onPreview)} /></Tooltip>
              <Tooltip title="刷新数据"><Button type="text" icon={<ReloadOutlined />} aria-label="刷新数据" loading={loading} onClick={() => navigate(onRefresh)} /></Tooltip>
            </div>
          </div>
        </aside>
        <div className={`workspace-content ${fillContent ? 'workspace-content-fill' : ''}`} ref={content}>
          <main className="main-content">{children}</main>
          {!fillContent && <footer className="workspace-footer"><span className="context-summary" title={datasetSummary}>{datasetSummary}</span><span>观数 · 数据有据</span></footer>}
        </div>
      </div>
    </div>
  </div>;
}
