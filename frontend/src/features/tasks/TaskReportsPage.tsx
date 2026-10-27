import { Alert, Button, Pagination, Skeleton, Space } from 'antd';
import { ArrowRightOutlined, CheckOutlined, DeleteOutlined, FileTextOutlined, HistoryOutlined, ImportOutlined, ReloadOutlined } from '@ant-design/icons';
import { kindLabels, statusLabels } from '../../task-state';
import type { Page, TaskSummary } from '../../types';
import './task-reports.css';

interface TaskReportsPageProps {
  tasks?: Page<TaskSummary>;
  page: number;
  loading: boolean;
  error: string;
  notes: Record<string, string>;
  selectedId: string | null;
  deleteBusy: boolean;
  pendingDelete?: { taskId: string; label: string };
  formatCreatedAt: (value: string) => string;
  onRefresh: () => void;
  onPage: (page: number) => void;
  onSelect: (id: string | null) => void;
  onDelete: (id: string, label: string) => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}

export default function TaskReportsPage({
  tasks, page, loading, error, notes, selectedId, deleteBusy, pendingDelete,
  formatCreatedAt, onRefresh, onPage, onSelect, onDelete, onConfirmDelete, onCancelDelete,
}: TaskReportsPageProps) {
  return <section className="task-reports" aria-labelledby="task-reports-title">
    <div className="review-page-heading">
      <div><h1 id="task-reports-title">任务与报告</h1><p>查看历史任务、执行状态和分析结果</p></div>
      <Button aria-label="刷新任务记录" icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新</Button>
    </div>
    {error && <Alert className="spaced-bottom" type="warning" showIcon message="任务记录暂不可用" description={error} action={<Button size="small" aria-label="重试任务记录" onClick={onRefresh}>重试</Button>} />}
    {!tasks && !error && <Skeleton active paragraph={{ rows: 5 }} />}
    {tasks && !tasks.items.length && !error && <div className="history-empty"><HistoryOutlined /><h3>还没有任务记录</h3><p>导入、准备数据和分析的任务会自动保存在这里。</p><Button onClick={() => onSelect(null)}>开始一次分析</Button></div>}
    {!!tasks?.items.length && <h2 className="task-reports-list-title">最近任务</h2>}
    <div className="history-list" aria-busy={loading}>
      {tasks?.items.map(item => {
        const title = notes[item.task_id] || kindLabels[item.kind];
        return <div className="history-entry" key={item.task_id}>
          <div role="button" tabIndex={0} data-task-id={item.task_id} aria-current={selectedId === item.task_id ? 'true' : undefined}
            className={`history-item ${selectedId === item.task_id ? 'selected' : ''}`}
            onClick={() => onSelect(item.task_id)} onKeyDown={event => {
              if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault(); onSelect(item.task_id);
              }
            }}>
            <span className={`history-icon ${item.kind}`}>{item.kind === 'import' ? <ImportOutlined /> : item.kind === 'label' ? <CheckOutlined /> : <FileTextOutlined />}</span>
            <span className="history-copy"><span className="history-title">{title}</span><span className="history-meta"><span>{kindLabels[item.kind]}</span><time dateTime={item.created_at}>{formatCreatedAt(item.created_at)}</time>{selectedId === item.task_id && <span className="history-current">当前查看</span>}</span></span>
            <span className={`task-state ${item.status}`}><span className="status-dot" />{statusLabels[item.status]}</span>
            <Button className="history-delete" type="text" danger aria-label={`删除${title}`} icon={<DeleteOutlined />} loading={deleteBusy}
              onClick={event => { event.stopPropagation(); onDelete(item.task_id, title); }} />
            <ArrowRightOutlined className="history-arrow" />
          </div>
          {pendingDelete?.taskId === item.task_id && <Alert className="history-delete-confirm" type="warning" showIcon message="删除任务记录？" description={<>
            <p>将删除“{pendingDelete.label}”及其重试记录。已导入的评论数据不会被删除。</p>
            <Space wrap><Button danger aria-label="确认删除任务" loading={deleteBusy} onClick={onConfirmDelete}>删除记录</Button><Button aria-label="取消删除任务" disabled={deleteBusy} onClick={onCancelDelete}>取消</Button></Space>
          </>} />}
        </div>;
      })}
    </div>
    {!!tasks?.total && <Pagination className="task-reports-pagination" size="small" current={page} total={tasks.total} pageSize={15} showSizeChanger={false} showTotal={total => `共 ${total} 项任务`} onChange={onPage} />}
  </section>;
}
