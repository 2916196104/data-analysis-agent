import type { ReactNode } from 'react';
import { Badge, Button } from 'antd';
import {
  BarChartOutlined, CheckOutlined, DatabaseOutlined, FolderOpenOutlined,
  HistoryOutlined, ImportOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons';

interface AppLayoutProps {
  children: ReactNode;
  connection: { status: 'error' | 'success' | 'warning'; text: string };
  loading: boolean;
  historyOpen: boolean;
  taskCount?: number;
  datasetSummary: string;
  hasProducts: boolean;
  hasSelectedTask: boolean;
  onRefresh: () => void;
  onHistory: () => void;
  onImport: () => void;
  onPrepare: () => void;
  onPreview: () => void;
  onNewAnalysis: () => void;
}

/** 页面壳只接收展示状态和操作回调，数据加载与任务编排由工作台负责。 */
export default function AppLayout({
  children, connection, loading, historyOpen, taskCount, datasetSummary,
  hasProducts, hasSelectedTask, onRefresh, onHistory, onImport, onPrepare,
  onPreview, onNewAnalysis,
}: AppLayoutProps) {
  return <div className="workbench">
    <div className="workspace-main">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="brand-mark"><BarChartOutlined /></div>
            <div className="brand-copy"><strong>观数</strong><span>评论分析工作台</span></div>
          </div>
          <div className="header-actions">
            <span className="connection-label"><Badge status={connection.status} text={connection.text} /></span>
            <Button type="text" icon={<ReloadOutlined />} aria-label="刷新数据" title="刷新数据" loading={loading} onClick={onRefresh} />
            <Button className="history-trigger" icon={<HistoryOutlined />} aria-label="任务记录" aria-expanded={historyOpen} onClick={onHistory}>
              任务记录{taskCount ? <span className="history-count">{taskCount}</span> : null}
            </Button>
          </div>
        </div>
      </header>
      <div className="workspace-toolbar">
        <div className="workspace-context">
          <span className="context-icon"><DatabaseOutlined /></span>
          <div><span className="context-caption">当前分析范围</span><span className="context-summary" title={datasetSummary}>{datasetSummary}</span></div>
        </div>
        <nav className="workspace-actions" aria-label="工作台操作">
          <Button aria-label="导入数据文件" icon={<ImportOutlined />} onClick={onImport}>导入数据</Button>
          <Button aria-label="准备数据" icon={<CheckOutlined />} onClick={onPrepare}>准备数据</Button>
          <Button aria-label="数据详情" icon={<FolderOpenOutlined />} disabled={!hasProducts} onClick={onPreview}>数据详情</Button>
          <Button aria-label="新建分析" type={hasSelectedTask ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={onNewAnalysis}>新建分析</Button>
        </nav>
      </div>
      <main className="main-content">{children}</main>
      <footer className="workspace-footer">观数 · 数据有据，分析可溯<span>评论分析工作台</span></footer>
    </div>
  </div>;
}
