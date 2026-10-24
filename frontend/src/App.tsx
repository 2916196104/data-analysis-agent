import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Drawer, Form, Input, InputNumber, Modal, Pagination,
  Progress, Skeleton, Space, Spin, Tag,
} from 'antd';
import {
  ArrowRightOutlined, BarChartOutlined, CheckOutlined, DatabaseOutlined, DeleteOutlined,
  DownloadOutlined, FileTextOutlined, FolderOpenOutlined, HistoryOutlined, ImportOutlined,
  PlusOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { api, API_BASE, errorMessage } from './api';
import Composer from './Composer';
import { analysisDates, analysisParameters, datasetKey } from './analysis-draft';
import DataPreview from './DataPreview';
import LabelForm from './LabelForm';
import ImportProgress from './ImportProgress';
import ResultCards from './ResultCards';
import AgentResults from './AgentResults';
import { exportResult, readHistory, writeHistory } from './local-history';
import { canRetry, isActive, kindLabels, progressPercent, stageLabels, statusLabels } from './task-state';
import { useTask } from './useTask';
import type {
  AgentResult, AnalysisParameters, AnalysisScope, Health, Identity, ImportParameters, Options, Page, Quality,
  TaskInput, TaskSummary, UploadedImportFile,
} from './types';

const emptyOptions: Options = {
  profiles: [], selected_profile_id: null, products: [], aspects: {}, sentiments: {},
  processing_statuses: {}, human_statuses: {},
};
const dateLabel = (value: string) => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
});

function taskStepLabel(kind: TaskSummary['kind']): string {
  if (kind === 'import') return '第 1 步 · 导入数据';
  if (kind === 'label') return '第 2 步 · 准备数据';
  return '分析结果';
}

function productSummary(products: Options['products'], fallbackDataset?: string): string {
  const datasetCount = new Set(products.map(datasetKey)).size;
  const dataset = datasetCount > 1
    ? '多个数据集'
    : products[0]?.dataset_version || fallbackDataset || '尚未选择';
  const reviewCount = products.reduce((sum, product) => sum + product.review_count, 0);
  return `${dataset} · ${products.length} 款商品 · ${reviewCount.toLocaleString()} 条评论`;
}

function progressStatus(task: TaskSummary['status']): 'exception' | 'success' | 'active' | 'normal' {
  if (task === 'failed') return 'exception';
  if (task === 'succeeded') return 'success';
  return isActive(task) ? 'active' : 'normal';
}

/** 工作台页面：加载目录和任务历史，提交任务后轮询并展示冻结结果。 */
export default function App() {
  const [local, setLocal] = useState(readHistory);
  const [options, setOptions] = useState<Options>(emptyOptions);
  const [quality, setQuality] = useState<Quality>();
  const [health, setHealth] = useState<Health>();
  const [draft, setDraft] = useState(local.draft);
  const { dataset, productIds, profileId } = draft;
  const [history, setHistory] = useState<Page<TaskSummary>>();
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{
    kind: 'task' | 'data'; taskId: string; label: string;
  }>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [detail, setDetail] = useState<{ title: string; value: unknown }>();
  const [importOpen, setImportOpen] = useState(false);
  const [reviewFile, setReviewFile] = useState<File>();
  const [metaFile, setMetaFile] = useState<File>();
  const [importPhase, setImportPhase] = useState('');
  const [uploadAttempted, setUploadAttempted] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Partial<Record<'reviews' | 'meta', { file: File; loaded: number }>>>({});
  const [labelDefaults, setLabelDefaults] = useState<{
    dataset: string;
    products: string[];
    limit?: number;
    workers?: number;
    scope?: Pick<AnalysisScope, 'date_from' | 'date_to' | 'as_of'>;
    retryFailed?: boolean;
  }>();
  const [importForm] = Form.useForm<ImportParameters>();
  const importAdvanced = useRef<HTMLDetailsElement>(null);
  const reviewFileInput = useRef<HTMLInputElement>(null);
  const metaFileInput = useRef<HTMLInputElement>(null);
  const uploadedFiles = useRef<Partial<Record<'reviews' | 'meta', { file: File; uploaded: UploadedImportFile }>>>({});
  const submissionKey = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const retryKey = useRef<{ id: string; key: string } | undefined>(undefined);
  const historyRequest = useRef(0);
  const loadRequest = useRef(0);
  const taskState = useTask(local.selected);
  const task = taskState.id === local.selected ? taskState.task : null;
  const run = taskState.id === local.selected ? taskState.run : null;
  const agentResult = task?.result?.agent === true ? task.result as unknown as AgentResult : null;
  const fetchHistory = useCallback(async () => {
    const requestId = ++historyRequest.current;
    try {
      const next = await api.tasks(page);
      if (requestId === historyRequest.current) {
        setHistory(next);
        setHistoryError('');
      }
    } catch (error) {
      if (requestId === historyRequest.current) setHistoryError(errorMessage(error));
    }
  }, [page]);

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    const results = await Promise.allSettled([api.options(), api.quality(), api.health()] as const);
    if (requestId !== loadRequest.current) return;
    const failures: string[] = [];
    const [optionResult, qualityResult, healthResult] = results;
    if (optionResult.status === 'fulfilled') {
      const next = optionResult.value;
      setOptions(next);
      setDraft(previous => {
        const currentDatasetExists = next.products.some(
          product => datasetKey(product) === previous.dataset,
        );
        const dataset = currentDatasetExists
          ? previous.dataset
          : next.products[0] ? datasetKey(next.products[0]) : '';
        const current = qualityResult.status === 'fulfilled'
          ? analysisDates(previous, qualityResult.value).current
          : undefined;
        const productIds = previous.productIds.filter(id => next.products.some(
          product => datasetKey(product) === dataset && product.product_id === id,
        ));
        const profileExists = next.profiles.some(
          profile => profile.profile_id === previous.profileId,
        );
        return {
          ...previous,
          dataset,
          current: previous.current ?? current,
          productIds,
          profileId: profileExists
            ? previous.profileId
            : next.selected_profile_id ?? undefined,
        };
      });
    } else {
      failures.push(errorMessage(optionResult.reason));
    }
    if (qualityResult.status === 'fulfilled') {
      setQuality(qualityResult.value);
    } else {
      failures.push(errorMessage(qualityResult.reason));
    }
    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value);
    } else {
      setHealth(undefined);
      failures.push(errorMessage(healthResult.reason));
    }
    setConnectionError([...new Set(failures)].join(' '));
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    void fetchHistory();
    const timer = setInterval(() => {
      void fetchHistory();
    }, 8000);
    return () => clearInterval(timer);
  }, [fetchHistory, task?.status]);
  useEffect(() => {
    const timer = setInterval(() => {
      api.health().then(setHealth).catch(() => setHealth(undefined));
    }, 10_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    setStorageError(!writeHistory({ ...local, draft }));
  }, [local, draft]);
  useEffect(() => {
    const dataPreparationFinished = task
      && (task.kind === 'import' || task.kind === 'label')
      && !isActive(task.status);
    if (dataPreparationFinished) void load();
  }, [task?.task_id, task?.status, task?.kind, load]);

  const selectTask = (id: string | null) => {
    setLocal(previous => ({ ...previous, selected: id }));
    setHistoryOpen(false);
    setSubmitError('');
  };

  async function submit(input: TaskInput, note: string) {
    const fingerprint = JSON.stringify(input);
    if (submissionKey.current?.fingerprint !== fingerprint) {
      submissionKey.current = { fingerprint, key: crypto.randomUUID() };
    }
    setBusy(true);
    setSubmitError('');
    try {
      const result = await api.submit(input, submissionKey.current.key);
      setLocal(previous => ({
        ...previous,
        selected: result.task.task_id,
        notes: { ...previous.notes, [result.task.task_id]: note },
      }));
      submissionKey.current = undefined;
      setImportOpen(false);
      setLabelDefaults(undefined);
      setPage(1);
      void fetchHistory();
      return true;
    } catch (error) {
      setSubmitError(errorMessage(error));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!task || !canRetry(task.status)) return;
    if (retryKey.current?.id !== task.task_id) {
      retryKey.current = { id: task.task_id, key: crypto.randomUUID() };
    }
    setBusy(true);
    setSubmitError('');
    try {
      const result = await api.retry(task.task_id, retryKey.current.key);
      setLocal(previous => ({
        ...previous,
        selected: result.task.task_id,
        notes: {
          ...previous.notes,
          [result.task.task_id]: previous.notes[task.task_id] || kindLabels[task.kind],
        },
      }));
      retryKey.current = undefined;
      void fetchHistory();
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function confirmDeleteTask(taskId: string, label: string): void {
    setPendingDelete({ kind: 'task', taskId, label });
  }

  function confirmDeleteImportedData(taskId: string): void {
    setPendingDelete({ kind: 'data', taskId, label: '这次导入的数据' });
  }

  async function executeDelete(): Promise<void> {
    if (!pendingDelete) return;
    const current = pendingDelete;
    setDeleteBusy(true);
    setSubmitError('');
    try {
      if (current.kind === 'task') {
        await api.deleteTask(current.taskId);
        if (local.selected === current.taskId) selectTask(null);
        else void fetchHistory();
      } else {
        await api.deleteImportedData(current.taskId);
        await Promise.all([fetchHistory(), load()]);
        taskState.refresh();
      }
      setPendingDelete(undefined);
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setDeleteBusy(false);
    }
  }

  async function uploadAndSubmitImport(values: ImportParameters): Promise<void> {
    if (!reviewFile && !metaFile) {
      setSubmitError('请先选择评论文件或商品元数据文件。');
      return;
    }
    if (values.max_reviews != null && !reviewFile) {
      setSubmitError('设置新增评论上限时必须选择评论文件。');
      return;
    }
    setSubmitError('');
    setBusy(true);
    setUploadAttempted(true);
    setUploadProgress({});
    try {
      const uploadedReviews = await uploadSelectedFile(reviewFile, 'reviews');
      const uploadedMeta = await uploadSelectedFile(metaFile, 'meta');
      setImportPhase('正在提交导入任务…');
      const submitted = await submit({ kind: 'import', parameters: {
        reviews_path: uploadedReviews?.path,
        meta_path: uploadedMeta?.path,
        dataset_version: values.dataset_version.trim(), product_ids: [], max_reviews: values.max_reviews ?? undefined,
      } }, `导入 ${reviewFile?.name || metaFile?.name}`);
      if (submitted) {
        setReviewFile(undefined);
        setMetaFile(undefined);
        uploadedFiles.current = {};
        setUploadAttempted(false);
        setUploadProgress({});
      }
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setBusy(false);
      setImportPhase('');
    }
  }

  async function uploadSelectedFile(file: File | undefined, kind: 'reviews' | 'meta') {
    if (!file) return undefined;
    const saved = uploadedFiles.current[kind];
    if (saved?.file === file) return saved.uploaded;
    setImportPhase(`正在上传${kind === 'reviews' ? '评论' : '元数据'}文件…`);
    const uploaded = await api.uploadImportFile(file, kind, loaded => {
      setUploadProgress(current => ({ ...current, [kind]: { file, loaded } }));
    });
    uploadedFiles.current[kind] = { file, uploaded };
    setUploadProgress(current => ({ ...current, [kind]: { file, loaded: file.size } }));
    return uploaded;
  }

  function selectImportFile(file: File | undefined, kind: 'reviews' | 'meta') {
    if (!file) return;
    const supported = kind === 'reviews' ? /\.(?:csv|jsonl(?:\.gz)?)$/i : /\.jsonl(?:\.gz)?$/i;
    if (!supported.test(file.name) || file.size === 0 || file.size > 512 * 1024 * 1024) {
      setSubmitError(`请选择非空的 ${kind === 'reviews' ? '.csv、.jsonl 或 .jsonl.gz' : '.jsonl 或 .jsonl.gz'} 文件，单个文件最大 512 MiB。`);
      return;
    }
    setSubmitError('');
    setUploadAttempted(false);
    if (kind === 'reviews') {
      setReviewFile(file);
      const csv = /\.csv$/i.test(file.name);
      if (csv) setMetaFile(undefined);
      if (['amazon-reviews-2023', 'reviews-csv'].includes(importForm.getFieldValue('dataset_version'))) {
        importForm.setFieldValue('dataset_version', csv ? 'reviews-csv' : 'amazon-reviews-2023');
      }
    }
    else setMetaFile(file);
  }

  const importResults = Array.isArray(task?.result?.imports) ? task.result.imports : [];
  const taskSource = task?.parameters.source || importResults.find(item => item.kind === 'reviews')?.source || importResults[0]?.source;
  const currentProducts = options.products.filter(product => {
    if (task) {
      const ids = task.parameters.product_ids as string[] | undefined;
      return (!taskSource || product.source === taskSource)
        && (!task.parameters.dataset_version || product.dataset_version === task.parameters.dataset_version)
        && (!ids?.length || ids.includes(product.product_id));
    }
    return datasetKey(product) === dataset && (!productIds.length || productIds.includes(product.product_id));
  });
  const title = task
    ? local.notes[task.task_id] || `${kindLabels[task.kind]} · ${dateLabel(task.created_at)}`
    : '';

  const openLabel = () => {
    const scoped = task?.kind === 'import' || task?.kind === 'label';
    setSubmitError('');
    setLabelDefaults({
      dataset: scoped
        ? JSON.stringify([
          taskSource || 'amazon_reviews_2023',
          task.parameters.dataset_version,
        ])
        : dataset,
      products: scoped ? (task.parameters.product_ids as string[] || []) : productIds,
      limit: task?.kind === 'label' ? task.parameters.limit as number : undefined,
      workers: task?.kind === 'label' ? task.parameters.workers as number : undefined,
    });
  };

  const taskIdentity = task?.kind === 'label'
    ? task.parameters.model_identity as Identity | undefined
    : task?.parameters;
  const previewProfile = task
    ? options.profiles.find(profile => (
      profile.requested_model === taskIdentity?.model
      && profile.base_url === taskIdentity?.base_url
      && profile.prompt_version === taskIdentity?.prompt_version
    ))?.profile_id
    : profileId;
  const preparedDraft = {
    ...draft,
    ...(task?.kind === 'label' && task.parameters.date_from && task.parameters.date_to
      ? { current: [task.parameters.date_from, task.parameters.date_to] as [string, string], baseline: undefined }
      : {}),
    dataset: task?.kind === 'label'
      ? JSON.stringify([task.parameters.source, task.parameters.dataset_version])
      : dataset,
    productIds: task?.kind === 'label'
      ? (task.parameters.product_ids as string[] || [])
      : productIds,
    profileId: previewProfile,
  };
  const preparedDates = analysisDates(preparedDraft, quality);
  const analyzeLabeled = (start: boolean) => {
    if (!task) return;
    setDraft(preparedDraft);
    const parameters = analysisParameters(preparedDraft, options, quality);
    if (start && parameters) {
      void submit(
        { kind: 'analysis', parameters },
        draft.note.trim() || '评论趋势与负面问题分析',
      );
    } else {
      selectTask(null);
    }
  };
  const showDetail = (value: unknown, title: string) => setDetail({ title, value });
  const datasetSummary = productSummary(currentProducts, task?.parameters.dataset_version as string);
  const importFiles = (['reviews', 'meta'] as const).map(kind => ({ kind, file: kind === 'reviews' ? reviewFile : metaFile }));
  const importUpload = {
    total: importFiles.reduce((sum, { file }) => sum + (file?.size ?? 0), 0),
    loaded: importFiles.reduce((sum, { kind, file }) => {
      if (!file) return sum;
      const sent = uploadProgress[kind];
      return sum + (uploadedFiles.current[kind]?.file === file ? file.size : sent?.file === file ? sent.loaded : 0);
    }, 0),
    confirmed: importFiles.every(({ kind, file }) => !file || uploadedFiles.current[kind]?.file === file),
    busy, failed: !!submitError && !busy, detail: importPhase,
  };
  return <div className="workbench">
    <Drawer title="任务记录" placement="right" width="min(640px, 100vw)" open={historyOpen} onClose={() => setHistoryOpen(false)} closable={{ 'aria-label': '关闭任务记录' }} className="task-history"
      footer={!!history?.total && <Pagination size="small" current={page} total={history.total} pageSize={15} showSizeChanger={false} showTotal={total => `共 ${total} 项任务`} onChange={setPage} />}>
      <div className="history-intro"><div><h2>最近任务</h2><p>查看处理进度，或打开任务继续查看结果。</p></div><Button type="text" aria-label="刷新任务记录" icon={<ReloadOutlined />} onClick={() => void fetchHistory()} /></div>
      {historyError && <Alert className="spaced-bottom" type="warning" showIcon message="任务记录暂不可用" description={historyError} action={<Button size="small" aria-label="重试任务记录" onClick={() => void fetchHistory()}>重试</Button>} />}
      {!history && !historyError && <Skeleton active paragraph={{ rows: 5 }} />}
      {history && !history.items.length && !historyError && <div className="history-empty"><HistoryOutlined /><h3>还没有任务记录</h3><p>导入、准备数据和分析的任务会自动保存在这里。</p><Button onClick={() => selectTask(null)}>开始一次分析</Button></div>}
      <div className="history-list">
        {history?.items.map(item => <div key={item.task_id} role="button" tabIndex={0} aria-current={local.selected === item.task_id ? 'true' : undefined} className={`history-item ${local.selected === item.task_id ? 'selected' : ''}`} onClick={() => selectTask(item.task_id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectTask(item.task_id); } }}>
          <span className={`history-icon ${item.kind}`}>{item.kind === 'import' ? <ImportOutlined /> : item.kind === 'label' ? <CheckOutlined /> : <FileTextOutlined />}</span>
          <span className="history-copy"><span className="history-title">{local.notes[item.task_id] || kindLabels[item.kind]}</span><span className="history-meta"><span>{kindLabels[item.kind]}</span><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time>{local.selected === item.task_id && <span className="history-current">当前查看</span>}</span></span>
          <span className={`task-state ${item.status}`}><span className="status-dot" />{statusLabels[item.status]}</span><Button className="history-delete" type="text" danger aria-label={`删除${local.notes[item.task_id] || kindLabels[item.kind]}`} icon={<DeleteOutlined />} loading={deleteBusy} onClick={event => { event.stopPropagation(); confirmDeleteTask(item.task_id, local.notes[item.task_id] || kindLabels[item.kind]); }} /><ArrowRightOutlined className="history-arrow" />
        </div>)}
      </div>
    </Drawer>
    <div className="workspace-main">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand"><div className="brand-mark"><BarChartOutlined /></div><div className="brand-copy"><strong>观数</strong><span>评论分析工作台</span></div></div>
          <div className="header-actions"><span className="connection-label"><Badge status={!health ? 'error' : health.worker_available ? 'success' : 'warning'} text={!health ? '连接待确认' : health.worker_available ? '服务已连接' : '执行器离线'} /></span><Button type="text" icon={<ReloadOutlined />} aria-label="刷新数据" title="刷新数据" loading={loading} onClick={() => { void load(); void fetchHistory(); taskState.refresh(); }} /><Button className="history-trigger" icon={<HistoryOutlined />} aria-label="任务记录" aria-expanded={historyOpen} onClick={() => { setHistoryOpen(true); void fetchHistory(); }}>任务记录{history?.total ? <span className="history-count">{history.total}</span> : null}</Button></div>
        </div>
      </header>
      <div className="workspace-toolbar">
        <div className="workspace-context"><span className="context-icon"><DatabaseOutlined /></span><div><span className="context-caption">当前分析范围</span><span className="context-summary" title={datasetSummary}>{datasetSummary}</span></div></div>
        <nav className="workspace-actions" aria-label="工作台操作">
          <Button aria-label="导入数据文件" icon={<ImportOutlined />} onClick={() => { setSubmitError(''); setImportOpen(true); }}>导入数据</Button>
          <Button aria-label="准备数据" icon={<CheckOutlined />} onClick={openLabel}>准备数据</Button>
          <Button aria-label="数据详情" icon={<FolderOpenOutlined />} disabled={!currentProducts.length} onClick={() => setPreviewOpen(true)}>数据详情</Button>
          <Button aria-label="新建分析" type={local.selected ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={() => selectTask(null)}>新建分析</Button>
        </nav>
      </div>
      <main className="main-content">
        {connectionError && <Alert className="spaced-bottom" type="error" showIcon message="无法完整加载工作空间" description={<>{connectionError}<div className="api-address">API：{API_BASE}</div></>} action={<Button onClick={() => void load()}>重新连接</Button>} />}
        {storageError && <Alert className="spaced-bottom" type="warning" showIcon message="浏览器存储不可用，问题备注和当前选择在刷新后可能丢失；后端任务仍会保留。" />}
        {health && !health.worker_available && <Alert className="spaced-bottom" type="warning" showIcon message="后台执行器离线" description="可以提交任务，但任务会留在队列中，直到后端 worker 启动。" />}
        {submitError && !importOpen && !labelDefaults && <Alert className="spaced-bottom" type="error" showIcon message="请求未完成" description={submitError} />}
        {!local.selected ? loading && !options.products.length ? <Skeleton active paragraph={{ rows: 6 }} /> : <Composer key={dataset} options={options} quality={quality} draft={draft} setDraft={setDraft} busy={busy} onSubmit={(parameters: AnalysisParameters, question: string) => void submit({ kind: 'analysis', parameters }, question)} onPreview={() => setPreviewOpen(true)} onLabel={openLabel} /> : <>
          {taskState.loading && !task && <Skeleton active paragraph={{ rows: 5 }} />}
          {taskState.error && <Alert className="spaced-bottom" type="error" showIcon message="任务连接异常" description={<>{taskState.error}<div>保留最后一次已知状态，正在尝试重新连接。</div></>} action={<Button onClick={taskState.refresh}>立即重连</Button>} />}
          {task && <>
            <div className="task-heading"><span className="eyebrow">{taskStepLabel(task.kind)}</span><h1>{title}</h1><p className="muted">创建于 {new Date(task.created_at).toLocaleString('zh-CN')}</p></div>
            <div className="scope-tags"><Tag icon={<DatabaseOutlined />}>{String(task.parameters.dataset_version || '全部版本')}</Tag><Tag>{(task.parameters.product_ids as string[] | undefined)?.join('、') || '全部商品'}</Tag>{!!task.parameters.date_from && <Tag>{String(task.parameters.date_from)} — {String(task.parameters.date_to)} · UTC</Tag>}<Button type="link" size="small" onClick={() => showDetail(task.parameters, '本次任务参数')}>查看参数</Button></div>
            <Card className="progress-card" aria-live="polite">
              <div className="progress-heading"><span>{isActive(task.status) ? <Spin size="small" /> : task.status === 'succeeded' ? <CheckOutlined className="success-icon" /> : <Badge status={task.status === 'failed' ? 'error' : 'warning'} />}<strong>{statusLabels[task.status]}</strong></span><span className="muted">{stageLabels[task.progress.stage] || task.progress.stage}</span></div>
              {task.kind === 'import' ? <ImportProgress task={task} /> : progressPercent(task.progress) !== undefined ? <Progress percent={progressPercent(task.progress)} status={progressStatus(task.status)} size="small" /> : <p className="muted">{task.status === 'queued' ? '等待后台执行器领取任务。' : `已处理 ${task.progress.completed}，当前阶段总量未知。`}</p>}
              {task.kind === 'label' && <>
                <Space wrap aria-label="标注进度">
                  <span>标注进度 {task.progress.completed}/{task.progress.total ?? '待确定'}</span>
                  <span>成功 {Number(task.progress.successful_reviews ?? 0)}</span>
                  <span>失败 {Number(task.progress.failed_reviews ?? 0)}</span>
                  <span>模型调用 {Number(task.progress.model_calls ?? 0)}</span>
                </Space>
                {task.status === 'running' && task.progress.completed === 0 && <p className="muted">正在准备或等待首条模型响应，收到结果后会自动更新进度。</p>}
                {task.status === 'succeeded' && task.progress.total === 0 && <p>所选范围内没有需要标注的评论。可更换商品，或勾选重试失败评论。</p>}
              </>}
              {task.error && <Alert className="spaced" type="error" showIcon message={task.error.message} description={`错误码：${task.error.code}`} />}
              {task.status === 'partial' && <Alert className="spaced" type="warning" showIcon message="任务部分完成" description="请先查看返回结果中的错误与已完成记录，再决定是否重试。" />}
              <div className="task-actions">{canRetry(task.status) && <Button aria-label="重试任务" loading={busy} icon={<ReloadOutlined />} onClick={() => void retry()}>重试任务</Button>}<Button size="small" type="text" onClick={() => showDetail(task, '任务详情')}>执行详情</Button>{task.parent_task_id && <Button type="link" size="small" onClick={() => selectTask(task.parent_task_id)}>查看原任务</Button>}{!isActive(task.status) && <Button danger loading={deleteBusy} icon={<DeleteOutlined />} onClick={() => confirmDeleteTask(task.task_id, title)}>删除任务记录</Button>}</div>
            </Card>
            {task.kind === 'import' && (task.status === 'succeeded' || task.status === 'partial') && <Card className="spaced" title="导入完成，继续准备数据">
              {Array.isArray(task.result?.imports) && task.result.imports.map((item, index) => <p key={index}>{item.kind === 'reviews' ? '评论' : '商品信息'}：新增 <strong>{item.inserted_records}</strong> 条，重复 {item.duplicate_records} 条{item.limit_reached ? '；已达到本次导入上限' : ''}。</p>)}
              <p className="muted">已带入本次数据集。下一步确认商品和处理数量，提取评论观点。</p><Space wrap><Button type="primary" disabled={loading || !currentProducts.length} onClick={openLabel}>标注已导入的数据</Button>{!Boolean(task.result?.import_data_deleted) && <Button danger loading={deleteBusy} icon={<DeleteOutlined />} onClick={() => confirmDeleteImportedData(task.task_id)}>删除导入数据</Button>}{Boolean(task.result?.import_data_deleted) && <Tag color="green">导入数据已删除</Tag>}</Space></Card>}
            {task.kind === 'label' && !isActive(task.status) && <Card className="spaced" title="继续分析">
              <p>分析 {preparedDates.current[0]} 至 {preparedDates.current[1]}，对比 {preparedDates.baseline[0]} 至 {preparedDates.baseline[1]}。</p>
              <p className="muted">沿用本次商品和标注版本。分析使用已有标注；未处理的评论会显示为覆盖率不足。</p>
              <Space wrap><Button type="primary" loading={busy || loading} disabled={loading || !previewProfile || !!connectionError} onClick={() => analyzeLabeled(true)}>分析这些评论</Button><Button type="link" disabled={loading || !previewProfile} onClick={() => analyzeLabeled(false)}>调整分析范围</Button><Button onClick={openLabel}>继续标注下一批</Button></Space>
              {!previewProfile && !loading && <p className="muted">暂无可用标注版本，请先完成一批数据准备。</p>}
            </Card>}
            {run && <ResultCards run={run} question={title} onDetail={showDetail} />}
            {agentResult && <AgentResults key={task.task_id} result={agentResult} onDetail={showDetail} onPrepare={products => {
              setSubmitError('');
              setLabelDefaults({ dataset: JSON.stringify([agentResult.scope.source, agentResult.scope.dataset_version]), products,
                scope: { date_from: agentResult.scope.date_from, date_to: agentResult.scope.date_to, as_of: agentResult.scope.as_of }, retryFailed: true });
            }} />}
            {task.result && task.kind !== 'analysis' && <details className="advanced-settings spaced"><summary>处理明细 <span>原始结果与下载</span></summary><div className="advanced-content"><Button icon={<DownloadOutlined />} onClick={() => exportResult(task.result, `task-${task.task_id}`)}>下载 JSON</Button><pre className="json-view">{JSON.stringify(task.result, null, 2)}</pre></div></details>}
            {task.kind === 'analysis' && task.status === 'succeeded' && !run && !agentResult && !taskState.error && <div className="loading-result"><Spin /> 正在读取分析快照…</div>}
          </>}
        </>}
      </main>
      <footer className="workspace-footer">观数 · 数据有据，分析可溯<span>评论分析工作台</span></footer>
    </div>
    <Drawer title="数据详情" width={620} open={previewOpen} onClose={() => setPreviewOpen(false)} destroyOnHidden><DataPreview products={currentProducts} options={options} profileId={previewProfile} onDetail={showDetail} /></Drawer>
    <Drawer title={detail?.title} width={650} open={!!detail} onClose={() => setDetail(undefined)} closable={{ 'aria-label': '关闭数据详情' }} extra={<Button icon={<DownloadOutlined />} onClick={() => exportResult(detail?.value, 'analysis-detail')}>下载 JSON</Button>}><pre className="json-view">{JSON.stringify(detail?.value, null, 2)}</pre></Drawer>
    <Modal title={pendingDelete?.kind === 'data' ? '删除这次导入的数据？' : '删除任务记录？'} open={!!pendingDelete} onCancel={() => { if (!deleteBusy) setPendingDelete(undefined); }} onOk={() => void executeDelete()} okText={pendingDelete?.kind === 'data' ? '删除导入数据' : '删除记录'} cancelText="取消" okButtonProps={{ danger: true, loading: deleteBusy }} destroyOnHidden>
      {pendingDelete?.kind === 'data'
        ? '会删除本次导入产生的评论、错误记录和关联标注；其他导入批次共用的商品信息会保留。此操作不可恢复。'
        : `将删除“${pendingDelete?.label}”及其重试记录。已导入的评论数据不会被删除。`}
    </Modal>
    <Modal title="准备数据" open={!!labelDefaults} onCancel={() => { if (!busy) setLabelDefaults(undefined); }} footer={null} destroyOnHidden>
      {submitError && <Alert className="spaced-bottom" type="error" showIcon message="标注任务未提交" description={submitError} />}
      {labelDefaults && <LabelForm options={options} initialDataset={labelDefaults.dataset} initialProducts={labelDefaults.products}
        initialLimit={labelDefaults.limit} initialWorkers={labelDefaults.workers} initialScope={labelDefaults.scope} initialRetryFailed={labelDefaults.retryFailed} busy={busy}
        onSubmit={parameters => {
          setDraft(previous => {
            const dataset = JSON.stringify([parameters.source, parameters.dataset_version]);
            return { ...previous, dataset, productIds: parameters.product_ids,
              note: previous.dataset === dataset && JSON.stringify(previous.productIds) === JSON.stringify(parameters.product_ids) ? previous.note : '' };
          });
          void submit({ kind: 'label', parameters }, `准备数据 · 最多 ${parameters.limit} 条`);
        }} />}
    </Modal>
    <Modal title="导入数据" open={importOpen} onCancel={() => { if (!busy) setImportOpen(false); }} footer={null} destroyOnHidden styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}>
      <p className="muted">从电脑选择评论 CSV 或 Amazon JSONL / JSONL.gz，单个文件最大 512 MiB。CSV 支持 UTF-8、GB18030 编码，需包含商品ID、评论内容、评论时间（也支持英文列名）；交易和用户行为数据不适用于评论分析。</p>
      {submitError && <Alert className="spaced-bottom" type="error" showIcon message={submitError} />}
      {uploadAttempted && <ImportProgress upload={importUpload} />}
      <Form name="import-data" form={importForm} layout="vertical" style={{ display: busy && uploadAttempted ? 'none' : undefined }} initialValues={{ dataset_version: 'amazon-reviews-2023' }} onFinishFailed={({ errorFields }) => { if (importAdvanced.current && errorFields.some(field => field.name[0] === 'dataset_version')) importAdvanced.current.open = true; }} onFinish={values => void uploadAndSubmitImport(values)}>
        <Form.Item label="评论文件（可选）"><div className="file-picker-row"><input ref={reviewFileInput} type="file" aria-label="评论文件" accept=".csv,.jsonl,.jsonl.gz" hidden style={{ display: 'none' }} disabled={busy} onChange={event => { selectImportFile(event.target.files?.[0], 'reviews'); event.target.value = ''; }} /><Button onClick={() => reviewFileInput.current?.click()} disabled={busy}>选择评论文件</Button><span title={reviewFile?.name}>{reviewFile?.name || '未选择'}</span>{reviewFile && <Button type="text" size="small" aria-label="移除评论文件" disabled={busy} onClick={() => setReviewFile(undefined)}>移除</Button>}</div></Form.Item>
        <Form.Item label="本次最多新增评论数（可选）" name="max_reviews" extra="留空则全部导入。重复记录不占额度；达到数量后停止，后续评论不在本次检查范围内。" rules={[() => ({ validator(_, value) {
          if (value == null) return Promise.resolve();
          if (!Number.isSafeInteger(value) || value < 1) return Promise.reject(new Error('请输入正整数'));
          return Promise.resolve();
        } })]}><InputNumber min={1} step={1} placeholder="例如 1000" disabled={busy} style={{ width: '100%' }} /></Form.Item>
        <details className="advanced-settings" ref={importAdvanced}><summary>导入设置 <span>商品信息、数据集版本</span></summary><div className="advanced-content">
          <Form.Item label="商品元数据文件（可选）" extra="仅用于 Amazon JSONL；CSV 可直接包含商品名称、品牌。"><div className="file-picker-row"><input ref={metaFileInput} type="file" aria-label="商品元数据文件" accept=".jsonl,.jsonl.gz" hidden style={{ display: 'none' }} disabled={busy || /\.csv$/i.test(reviewFile?.name || '')} onChange={event => { selectImportFile(event.target.files?.[0], 'meta'); event.target.value = ''; }} /><Button onClick={() => metaFileInput.current?.click()} disabled={busy || /\.csv$/i.test(reviewFile?.name || '')}>选择元数据文件</Button><span title={metaFile?.name}>{metaFile?.name || '未选择'}</span>{metaFile && <Button type="text" size="small" aria-label="移除元数据文件" disabled={busy} onClick={() => setMetaFile(undefined)}>移除</Button>}</div></Form.Item>
          <Form.Item label="数据集版本" name="dataset_version" rules={[{ required: true, whitespace: true, message: '请输入数据集版本' }]}><Input aria-label="数据集版本" disabled={busy} /></Form.Item>
        </div></details>
        <Button className="spaced" aria-label="提交导入任务" block type="primary" htmlType="submit" icon={<ImportOutlined />} loading={busy}>{importPhase || '导入数据'}</Button>
      </Form>
    </Modal>
  </div>;
}
