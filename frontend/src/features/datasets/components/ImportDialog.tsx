import { useRef } from 'react';
import { Alert, Button, Form, Input, InputNumber, Modal, Steps, Tag } from 'antd';
import { CloseCircleOutlined, ImportOutlined, StopOutlined } from '@ant-design/icons';
import ImportProgress, { type UploadState } from './ImportProgress';
import type { ImportParameters } from '../../../types';

interface ImportDialogProps {
  open: boolean;
  busy: boolean;
  submitError: string;
  reviewFile?: File;
  metaFile?: File;
  uploadAttempted: boolean;
  uploadState: UploadState;
  phase: string;
  onError: (message: string) => void;
  onSelectFile: (file: File | undefined, kind: 'reviews' | 'meta') => void;
  onRemoveFile: (kind: 'reviews' | 'meta') => void;
  onSubmit: (values: ImportParameters) => void;
  onCancelUpload: () => void;
  onClose: () => void;
}

export default function ImportDialog({
  open, busy, submitError, reviewFile, metaFile, uploadAttempted, uploadState, phase,
  onError, onSelectFile, onRemoveFile, onSubmit, onCancelUpload, onClose,
}: ImportDialogProps) {
  const [form] = Form.useForm<ImportParameters>();
  const advanced = useRef<HTMLDetailsElement>(null);
  const reviewInput = useRef<HTMLInputElement>(null);
  const metaInput = useRef<HTMLInputElement>(null);
  return <Modal
    title="导入数据"
    open={open}
    onCancel={() => { if (!busy) onClose(); }}
    footer={null}
    destroyOnHidden
    className="import-dialog"
    styles={{ body: { maxHeight: 'min(720px, 70vh)', overflowY: 'auto' } }}
  >
    <div className="import-dialog-intro">
      <p>添加评论文件后，系统会先上传并等待服务端确认，再读取、校验并写入数据库。</p>
      <div className="import-dialog-rules"><Tag color="green">CSV</Tag><Tag color="green">JSONL</Tag><Tag color="green">JSONL.GZ</Tag><span>单文件最大 512 MiB</span></div>
    </div>
    <Steps className="import-steps" size="small" current={uploadAttempted ? 1 : 0} items={[{ title: '选择文件' }, { title: '上传确认' }, { title: '读取入库' }]} />
    {submitError && <Alert className="spaced-bottom" type="error" showIcon closable={!busy} onClose={() => onError('')} message={submitError} />}
    {uploadAttempted && <ImportProgress upload={uploadState} />}
    <Form
      name="import-data" form={form} layout="vertical" className="import-form"
      style={{ display: busy && uploadAttempted ? 'none' : undefined }}
      initialValues={{ dataset_version: 'amazon-reviews-2023' }}
      onFinishFailed={({ errorFields }) => { if (advanced.current && errorFields.some(field => field.name[0] === 'dataset_version')) advanced.current.open = true; }}
      onFinish={onSubmit}
    >
      <section className="import-file-section">
        <div className="import-section-heading"><div><strong>评论文件</strong><span>必选，支持 CSV、JSONL、JSONL.GZ</span></div><span className="import-section-status">{reviewFile ? '已选择' : '待选择'}</span></div>
        <div className="file-picker-row">
          <input ref={reviewInput} type="file" aria-label="评论文件" accept=".csv,.jsonl,.jsonl.gz" hidden style={{ display: 'none' }} disabled={busy} onChange={event => { onSelectFile(event.target.files?.[0], 'reviews'); event.target.value = ''; }} />
          <Button onClick={() => reviewInput.current?.click()} disabled={busy}>选择评论文件</Button>
          <span title={reviewFile?.name}>{reviewFile?.name || '尚未选择文件'}</span>
          {reviewFile && <Button type="text" size="small" aria-label="移除评论文件" disabled={busy} onClick={() => onRemoveFile('reviews')} icon={<CloseCircleOutlined />}>移除</Button>}
        </div>
      </section>
      <Form.Item label="本次最多新增评论数（可选）" name="max_reviews" extra="留空表示全部导入；重复记录不占额度。">
        <InputNumber min={1} step={1} placeholder="例如 1000" disabled={busy} style={{ width: '100%' }} />
      </Form.Item>
      <details className="advanced-settings import-advanced" ref={advanced}>
        <summary>高级设置 <span>商品元数据、数据集版本</span></summary>
        <div className="advanced-content">
          <section className="import-file-section import-meta-section">
            <div className="import-section-heading"><div><strong>商品元数据文件</strong><span>可选，仅 JSONL/JSONL.GZ</span></div><span className="import-section-status">{metaFile ? '已选择' : '可选'}</span></div>
            <div className="file-picker-row">
              <input ref={metaInput} type="file" aria-label="商品元数据文件" accept=".jsonl,.jsonl.gz" hidden style={{ display: 'none' }} disabled={busy || /\.csv$/i.test(reviewFile?.name || '')} onChange={event => { onSelectFile(event.target.files?.[0], 'meta'); event.target.value = ''; }} />
              <Button onClick={() => metaInput.current?.click()} disabled={busy || /\.csv$/i.test(reviewFile?.name || '')}>选择元数据文件</Button>
              <span title={metaFile?.name}>{metaFile?.name || '未选择（可选）'}</span>
              {metaFile && <Button type="text" size="small" aria-label="移除元数据文件" disabled={busy} onClick={() => onRemoveFile('meta')} icon={<CloseCircleOutlined />}>移除</Button>}
            </div>
          </section>
          <Form.Item label="数据集版本" name="dataset_version" rules={[{ required: true, whitespace: true, message: '请输入数据集版本' }]}>
            <Input aria-label="数据集版本" disabled={busy} />
          </Form.Item>
        </div>
      </details>
      <Button className="spaced import-submit" aria-label="提交导入任务" block type="primary" htmlType="submit" icon={<ImportOutlined />} loading={busy}>{phase || '上传并导入数据'}</Button>
    </Form>
    {busy && <Button className="import-cancel" type="text" icon={<StopOutlined />} onClick={onCancelUpload}>取消上传并保留文件</Button>}
  </Modal>;
}
