import { useState, type ReactNode } from 'react';
import { Badge, Button, Tooltip } from 'antd';
import {
  AppstoreOutlined, BarChartOutlined, BellOutlined, CheckOutlined,
  DatabaseOutlined, FolderOpenOutlined, HistoryOutlined, ImportOutlined,
  MenuOutlined, ReloadOutlined, RobotOutlined, TagsOutlined, UserOutlined,
} from '@ant-design/icons';

interface AppLayoutProps {
  children: ReactNode;
  connection: { status: 'error' | 'success' | 'warning'; text: string };
  loading: boolean;
  historyOpen: boolean;
  taskCount?: number;
  datasetSummary: string;
  hasProducts: boolean;
  section: 'analysis' | 'datasets';
  onRefresh: () => void;
  onHistory: () => void;
  onImport: () => void;
  onPrepare: () => void;
  onPreview: () => void;
  onNewAnalysis: () => void;
  onDatasets: () => void;
  onAgent: () => void;
}

/** 导航调用现有工作流；当前项目没有 URL 路由，不伪造页面地址。 */
export default function AppLayout({
  children, connection, loading, historyOpen, taskCount, datasetSummary,
  hasProducts, section, onRefresh, onHistory, onImport, onPrepare,
  onPreview, onNewAnalysis, onDatasets, onAgent,
}: AppLayoutProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = (action: () => void) => { action(); setMenuOpen(false); };
  const pageName = section === 'datasets' ? '数据管理' : '评论分析';
  return <div className="workbench">
    <div className="workspace-main">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="brand-mark"><BarChartOutlined /></div>
            <div className="brand-copy"><strong>观数</strong><span>/ {pageName}</span></div>
          </div>
          <div className="header-actions">
            <Tooltip title="通知 · 未开放"><Button type="text" aria-label="通知（未开放）" aria-disabled="true" icon={<BellOutlined />} /></Tooltip>
            <Tooltip title="用户中心 · 未开放"><Button type="text" aria-label="用户中心（未开放）" aria-disabled="true" icon={<UserOutlined />} /></Tooltip>
            <Button className="mobile-menu-trigger" type="text" aria-label="工作空间导航" aria-controls="workspace-navigation" aria-expanded={menuOpen} icon={<MenuOutlined />} onClick={() => setMenuOpen(value => !value)} />
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
            <button aria-label="评论标注" onClick={() => navigate(onPrepare)}><TagsOutlined /><span>评论标注</span></button>
            <button aria-label="AI 助手" onClick={() => navigate(onAgent)}><RobotOutlined /><span>AI 助手</span></button>
            <button aria-label="任务记录" aria-expanded={historyOpen} onClick={() => navigate(onHistory)}><HistoryOutlined /><span>任务与报告</span>{taskCount ? <small>{taskCount}</small> : null}</button>
          </nav>
          <div className="sidebar-tools">
            <span className="connection-label"><Badge status={connection.status} text={connection.text} /></span>
            <div className="workspace-actions" role="group" aria-label="工作台操作">
              <Tooltip title="导入数据"><Button type="text" aria-label="导入数据文件" icon={<ImportOutlined />} onClick={onImport} /></Tooltip>
              <Tooltip title="准备数据"><Button type="text" aria-label="准备数据" icon={<CheckOutlined />} onClick={onPrepare} /></Tooltip>
              <Tooltip title="数据详情"><Button type="text" aria-label="数据详情" icon={<FolderOpenOutlined />} disabled={!hasProducts} onClick={onPreview} /></Tooltip>
              <Tooltip title="刷新数据"><Button type="text" icon={<ReloadOutlined />} aria-label="刷新数据" loading={loading} onClick={onRefresh} /></Tooltip>
            </div>
          </div>
        </aside>
        <div className="workspace-content">
          <main className="main-content">{children}</main>
          <footer className="workspace-footer"><span className="context-summary" title={datasetSummary}>{datasetSummary}</span><span>观数 · 数据有据</span></footer>
        </div>
      </div>
    </div>
  </div>;
}
