import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import ImportProgress from './ImportProgress';
import type { Task } from './types';

afterEach(cleanup);
describe('两阶段导入进度', () => {
  it('发送完成但没有服务器确认时，仍等待上传确认', () => {
    render(<ImportProgress upload={{ loaded: 1024, total: 1024, confirmed: false, busy: true, failed: false, detail: '正在上传评论文件' }} />);
    expect(screen.getByText(/已发送，等待服务器确认/)).toBeTruthy();
    expect(screen.getByText('等待上传并提交任务')).toBeTruthy();
    expect(screen.queryByText('上传完成')).toBeNull();
  });
  it('读取 100% 仍显示待提交计数，提交后才显示处理完成', () => {
    const task: Pick<Task, 'status' | 'progress'> = { status: 'running', progress: {
      stage: 'importing_reviews', completed: 1000, total: 1000, unit: 'bytes', total_lines: 12,
      inserted_records: 10, invalid_records: 2, filename: 'reviews.jsonl.gz',
    } };
    const view = render(<ImportProgress task={task} />);
    expect(screen.getByText(/新增待提交 10 条/)).toBeTruthy();
    expect(screen.getByText(/压缩文件读取量包含解压缓冲/)).toBeTruthy();
    expect(screen.queryByText('处理完成')).toBeNull();
    view.rerender(<ImportProgress task={{ ...task, status: 'partial', progress: { ...task.progress, stage: 'imported', completed: 1, total: 1, unit: 'files' } }} />);
    expect(screen.getByText('部分记录未导入')).toBeTruthy();
    expect(screen.getByText(/已新增 10 条/)).toBeTruthy();
  });
  it('失败时保留最后真实计数，并标记处理失败', () => {
    render(<ImportProgress task={{ status: 'failed', progress: {
      stage: 'importing_reviews', completed: 100, total: 1000, unit: 'bytes', total_lines: 3, inserted_records: 2,
    } }} />);
    expect(screen.getByText('处理失败')).toBeTruthy();
    expect(screen.getByText(/失败前尝试新增 2 条/)).toBeTruthy();
    expect(screen.queryByText('处理完成')).toBeNull();
  });
});
