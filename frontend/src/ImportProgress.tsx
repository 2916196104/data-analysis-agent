import { Progress } from 'antd';
import { progressPercent, stageLabels } from './task-state';
import type { Task } from './types';

export const formatBytes = (value: number) => value < 1024 ? `${value} B`
  : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`;

interface UploadState { loaded: number; total: number; confirmed: boolean; busy: boolean; failed: boolean; detail: string }

/** 上传完成以服务端确认为准；处理条只表示当前步骤，事务提交后才完成。 */
export default function ImportProgress({ upload, task }: { upload?: UploadState; task?: Pick<Task, 'status' | 'progress'> }) {
  const uploaded = !!task || !!upload?.confirmed;
  const uploadPercent = upload?.total ? Math.floor(upload.loaded / upload.total * 100) : 0;
  const done = task?.progress.stage === 'imported' && task.status !== 'failed';
  const failed = task?.status === 'failed';
  const processingPercent = task ? progressPercent(task.progress) : 0;
  const reading = task?.progress.stage.startsWith('importing_');
  return <div className="import-progress" aria-label="导入进度">
    <section aria-label="文件上传阶段">
      <div className="import-progress-heading"><strong>1 · 文件上传</strong><span>{uploaded ? (task ? '文件已就绪' : '上传完成') : upload?.failed ? '上传未完成，可重试' : '上传中'}</span></div>
      <Progress aria-label="文件上传进度" percent={uploaded ? 100 : uploadPercent} size="small" status={uploaded ? 'success' : upload?.failed ? 'exception' : 'active'} format={value => `${value}%`} />
      {upload && <p className="muted">{formatBytes(upload.loaded)} / {formatBytes(upload.total)}{!uploaded && upload.loaded === upload.total ? ' · 已发送，等待服务器确认' : ''}</p>}
      {upload?.busy && <p className="muted">{upload.detail} 请保持页面打开。</p>}
    </section>
    <section aria-label="数据处理阶段">
      <div className="import-progress-heading"><strong>2 · 数据处理</strong><span>{done ? (task?.status === 'partial' ? '部分记录未导入' : '处理完成') : failed ? '处理失败' : task ? (stageLabels[task.progress.stage] || task.progress.stage) : '等待上传并提交任务'}</span></div>
      {processingPercent !== undefined && <Progress aria-label="数据处理当前步骤进度" percent={processingPercent} size="small" status={failed ? 'exception' : done ? (task?.status === 'partial' ? 'normal' : 'success') : 'active'} format={value => `${value}%`} />}
      {task && !done && <p className="muted">{task.progress.unit === 'bytes'
        ? `${reading ? '文件读取进度' : '当前校验进度'}：${formatBytes(task.progress.completed)} / ${formatBytes(task.progress.total ?? 0)}。后续步骤与入库完成后才结束。`
        : task.status === 'queued' ? '等待后台执行器领取任务。' : task.progress.stage === 'committing_import' ? '正在提交数据库事务…' : '正在准备处理，总量待确定。'}</p>}
      {task && typeof task.progress.total_lines === 'number' && <p className="import-counts">已处理 {task.progress.total_lines.toLocaleString()} 行 · {done ? '已新增' : failed ? '失败前尝试新增' : '新增待提交'} {Number(task.progress.inserted_records ?? 0).toLocaleString()} 条 · 无效 {Number(task.progress.invalid_records ?? 0).toLocaleString()} 条</p>}
      {reading && task?.progress.filename?.toString().toLowerCase().endsWith('.gz') && <p className="muted">压缩文件读取量包含解压缓冲，处理行数会继续更新。</p>}
      {task?.progress.limit_reached === true && !done && <p className="muted">已达到新增评论上限，正在完成校验与入库。</p>}
    </section>
  </div>;
}
