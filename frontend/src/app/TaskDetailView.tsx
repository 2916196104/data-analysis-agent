import { Alert, Badge, Button, Card, Progress, Skeleton, Space, Spin, Tag } from 'antd';
import {
  CheckOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { AnnotationNextSteps, AnnotationProgress } from '../features/annotations/AnnotationTaskContent';
import { isAgentTask } from '../features/agent/chat-state';
import { ImportProgress } from '../features/datasets';
import { exportResult } from '../local-history';
import ResultCards from '../ResultCards';
import { canRetry, isActive, progressPercent, stageLabels, statusLabels } from '../task-state';
import type { Run, Task, TaskSummary } from '../types';

function taskStepLabel(kind: TaskSummary['kind']): string {
  if (kind === 'import') return '第 1 步 · 导入数据';
  if (kind === 'label') return '第 2 步 · 准备数据';
  return '分析结果';
}

function progressStatus(task: TaskSummary['status']): 'exception' | 'success' | 'active' | 'normal' {
  if (task === 'failed') return 'exception';
  if (task === 'succeeded') return 'success';
  return isActive(task) ? 'active' : 'normal';
}

export interface TaskDetailViewProps {
  task: Task | null;
  run: Run | null;
  loading: boolean;
  taskLoading: boolean;
  taskError: string | null;
  title: string;
  busy: boolean;
  deleteBusy: boolean;
  hasProducts: boolean;
  preparedDates: { current: [string, string]; baseline: [string, string] };
  previewProfile?: number;
  connectionError: boolean;
  onRefresh: () => void;
  onRetry: () => void;
  onShowDetail: (value: unknown, title: string) => void;
  onSelectTask: (id: string | null) => void;
  onConfirmDeleteTask: (taskId: string, label: string) => void;
  onConfirmDeleteImportedData: (taskId: string) => void;
  onOpenLabel: () => void;
  onAnalyzeLabeled: (start: boolean) => void;
}

export default function TaskDetailView({
  task,
  run,
  loading,
  taskLoading,
  taskError,
  title,
  busy,
  deleteBusy,
  hasProducts,
  preparedDates,
  previewProfile,
  connectionError,
  onRefresh,
  onRetry,
  onShowDetail,
  onSelectTask,
  onConfirmDeleteTask,
  onConfirmDeleteImportedData,
  onOpenLabel,
  onAnalyzeLabeled,
}: TaskDetailViewProps) {
  return <>
    {taskLoading && !task && <Skeleton active paragraph={{ rows: 5 }} />}
    {taskError && (
      <Alert
        className="spaced-bottom"
        type="error"
        showIcon
        message="任务连接异常"
        description={<>{taskError}<div>保留最后一次已知状态，正在尝试重新连接。</div></>}
        action={<Button onClick={onRefresh}>立即重连</Button>}
      />
    )}
    {task && <>
      <div className="task-heading">
        <span className="eyebrow">{taskStepLabel(task.kind)}</span>
        <h1>{title}</h1>
        <p className="muted">创建于 {new Date(task.created_at).toLocaleString('zh-CN')}</p>
      </div>

      <div className="scope-tags">
        <Tag icon={<DatabaseOutlined />}>{String(task.parameters.dataset_version || '全部版本')}</Tag>
        <Tag>{(task.parameters.product_ids as string[] | undefined)?.join('、') || '全部商品'}</Tag>
        {!!task.parameters.date_from && (
          <Tag>{String(task.parameters.date_from)} — {String(task.parameters.date_to)} · UTC</Tag>
        )}
        <Button type="link" size="small" onClick={() => onShowDetail(task.parameters, '本次任务参数')}>
          查看参数
        </Button>
      </div>

      <Card className="progress-card" aria-live="polite">
        <div className="progress-heading">
          <span>
            {isActive(task.status) ? (
              <Spin size="small" />
            ) : task.status === 'succeeded' ? (
              <CheckOutlined className="success-icon" />
            ) : (
              <Badge status={task.status === 'failed' ? 'error' : 'warning'} />
            )}
            <strong>{statusLabels[task.status]}</strong>
          </span>
          <span className="muted">{stageLabels[task.progress.stage] || task.progress.stage}</span>
        </div>

        {task.kind === 'import' ? (
          <ImportProgress task={task} />
        ) : progressPercent(task.progress) !== undefined ? (
          <Progress
            percent={progressPercent(task.progress)}
            status={progressStatus(task.status)}
            size="small"
          />
        ) : (
          <p className="muted">
            {task.status === 'queued'
              ? '等待后台执行器领取任务。'
              : `已处理 ${task.progress.completed}，当前阶段总量未知。`}
          </p>
        )}

        {task.kind === 'label' && <AnnotationProgress task={task} />}
        {task.error && (
          <Alert
            className="spaced"
            type="error"
            showIcon
            message={task.error.message}
            description={`错误码：${task.error.code}`}
          />
        )}
        {task.status === 'partial' && (
          <Alert
            className="spaced"
            type="warning"
            showIcon
            message="任务部分完成"
            description="请先查看返回结果中的错误与已完成记录，再决定是否重试。"
          />
        )}

        <div className="task-actions">
          {canRetry(task.status) && (
            <Button
              aria-label="重试任务"
              loading={busy}
              icon={<ReloadOutlined />}
              onClick={onRetry}
            >
              重试任务
            </Button>
          )}
          <Button size="small" type="text" onClick={() => onShowDetail(task, '任务详情')}>
            执行详情
          </Button>
          {task.parent_task_id && (
            <Button type="link" size="small" onClick={() => onSelectTask(task.parent_task_id)}>
              查看原任务
            </Button>
          )}
          {!isActive(task.status) && (
            <Button
              danger
              loading={deleteBusy}
              icon={<DeleteOutlined />}
              onClick={() => onConfirmDeleteTask(task.task_id, title)}
            >
              删除任务记录
            </Button>
          )}
        </div>
      </Card>

      {task.kind === 'import' && (task.status === 'succeeded' || task.status === 'partial') && (
        <Card className="spaced" title="导入完成，继续准备数据">
          {Array.isArray(task.result?.imports) && task.result.imports.map((item, index) => (
            <p key={index}>
              {item.kind === 'reviews' ? '评论' : '商品信息'}：新增{' '}
              <strong>{item.inserted_records}</strong> 条，重复 {item.duplicate_records} 条
              {item.limit_reached ? '；已达到本次导入上限' : ''}。
            </p>
          ))}
          <p className="muted">已带入本次数据集。下一步确认商品和处理数量，提取评论观点。</p>
          <Space wrap>
            <Button type="primary" disabled={loading || !hasProducts} onClick={onOpenLabel}>
              标注已导入的数据
            </Button>
            {!Boolean(task.result?.import_data_deleted) && (
              <Button
                danger
                loading={deleteBusy}
                icon={<DeleteOutlined />}
                onClick={() => onConfirmDeleteImportedData(task.task_id)}
              >
                删除导入数据
              </Button>
            )}
            {Boolean(task.result?.import_data_deleted) && <Tag color="green">导入数据已删除</Tag>}
          </Space>
        </Card>
      )}

      {task.kind === 'label' && (
        <AnnotationNextSteps
          task={task}
          dates={preparedDates}
          loading={loading}
          busy={busy}
          profileId={previewProfile}
          connectionError={connectionError}
          onAnalyze={onAnalyzeLabeled}
          onContinue={onOpenLabel}
        />
      )}

      {run && <ResultCards run={run} question={title} onDetail={onShowDetail} />}

      {task.result && task.kind !== 'analysis' && (
        <details className="advanced-settings spaced">
          <summary>处理明细 <span>原始结果与下载</span></summary>
          <div className="advanced-content">
            <Button
              icon={<DownloadOutlined />}
              onClick={() => exportResult(task.result, `task-${task.task_id}`)}
            >
              下载 JSON
            </Button>
            <pre className="json-view">{JSON.stringify(task.result, null, 2)}</pre>
          </div>
        </details>
      )}

      {task.kind === 'analysis' && task.status === 'succeeded' && !run && !isAgentTask(task) && !taskError && (
        <div className="loading-result"><Spin /> 正在读取分析快照…</div>
      )}
    </>}
  </>;
}
