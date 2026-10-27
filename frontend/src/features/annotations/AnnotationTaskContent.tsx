import { Button, Card, Space } from 'antd';
import { isActive } from '../../task-state';
import type { Task } from '../../types';

export function AnnotationProgress({ task }: { task: Task }) {
  return <>
    <Space wrap aria-label="标注进度">
      <span>标注进度 {task.progress.completed}/{task.progress.total ?? '待确定'}</span>
      <span>成功 {Number(task.progress.successful_reviews ?? 0)}</span>
      <span>失败 {Number(task.progress.failed_reviews ?? 0)}</span>
      <span>模型调用 {Number(task.progress.model_calls ?? 0)}</span>
    </Space>
    {task.status === 'running' && task.progress.completed === 0 && <p className="muted">正在准备或等待首条模型响应，收到结果后会自动更新进度。</p>}
    {task.status === 'succeeded' && task.progress.total === 0 && <p>所选范围内没有需要标注的评论。可更换商品，或勾选重试失败评论。</p>}
  </>;
}

export function AnnotationNextSteps({ task, dates, loading, busy, profileId, connectionError, onAnalyze, onContinue }: {
  task: Task;
  dates: { current: [string, string]; baseline: [string, string] };
  loading: boolean; busy: boolean; profileId?: number; connectionError: boolean;
  onAnalyze: (start: boolean) => void; onContinue: () => void;
}) {
  if (isActive(task.status)) return null;
  return <Card className="spaced" title="继续分析">
    <p>分析 {dates.current[0]} 至 {dates.current[1]}，对比 {dates.baseline[0]} 至 {dates.baseline[1]}。</p>
    <p className="muted">沿用本次商品和标注版本。分析使用已有标注；未处理的评论会显示为覆盖率不足。</p>
    <Space wrap><Button type="primary" loading={busy || loading} disabled={loading || !profileId || connectionError} onClick={() => onAnalyze(true)}>分析这些评论</Button><Button type="link" disabled={loading || !profileId} onClick={() => onAnalyze(false)}>调整分析范围</Button><Button onClick={onContinue}>继续标注下一批</Button></Space>
    {!profileId && !loading && <p className="muted">暂无可用标注版本，请先完成一批数据准备。</p>}
  </Card>;
}
