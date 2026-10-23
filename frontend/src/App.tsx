import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Card, Drawer, Form, Input, InputNumber, Modal, Pagination, Progress, Skeleton, Space, Spin, Tag } from 'antd';
import { ArrowLeftOutlined, BarChartOutlined, CheckOutlined, DatabaseOutlined, DownloadOutlined, FileTextOutlined, FolderOpenOutlined, HistoryOutlined, ImportOutlined, MenuOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, API_BASE, errorMessage } from './api';
import Composer from './Composer';
import { analysisDates, analysisParameters, datasetKey } from './analysis-draft';
import DataPreview from './DataPreview';
import LabelForm from './LabelForm';
import ResultCards from './ResultCards';
import { exportResult, readHistory, writeHistory } from './local-history';
import { canRetry, isActive, kindLabels, progressPercent, stageLabels, statusLabels } from './task-state';
import { useTask } from './useTask';
import type { AnalysisParameters, Health, Identity, ImportParameters, Options, Page, Quality, TaskInput, TaskSummary } from './types';

const emptyOptions: Options = { profiles: [], selected_profile_id: null, products: [], aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };
const dateLabel = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

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
  const [navOpen, setNavOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [detail, setDetail] = useState<{ title: string; value: unknown }>();
  const [importOpen, setImportOpen] = useState(false);
  const [labelDefaults, setLabelDefaults] = useState<{ dataset: string; products: string[]; limit?: number; workers?: number }>();
  const [importForm] = Form.useForm<ImportParameters>();
  const importAdvanced = useRef<HTMLDetailsElement>(null);
  const submissionKey = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const retryKey = useRef<{ id: string; key: string } | undefined>(undefined);
  const historyRequest = useRef(0);
  const loadRequest = useRef(0);
  const taskState = useTask(local.selected);
  const task = taskState.id === local.selected ? taskState.task : null;
  const run = taskState.id === local.selected ? taskState.run : null;
  const fetchHistory = useCallback(async () => {
    const requestId = ++historyRequest.current;
    try {
      const next = await api.tasks(page);
      if (requestId === historyRequest.current) { setHistory(next); setHistoryError(''); }
    } catch (error) { if (requestId === historyRequest.current) setHistoryError(errorMessage(error)); }
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
        const dataset = next.products.some(p => datasetKey(p) === previous.dataset) ? previous.dataset : next.products[0] ? datasetKey(next.products[0]) : '';
        return { ...previous, dataset,
          current: previous.current ?? (qualityResult.status === 'fulfilled' ? analysisDates(previous, qualityResult.value).current : undefined),
          productIds: previous.productIds.filter(id => next.products.some(p => datasetKey(p) === dataset && p.product_id === id)),
          profileId: next.profiles.some(p => p.profile_id === previous.profileId) ? previous.profileId : next.selected_profile_id ?? undefined };
      });
    } else failures.push(errorMessage(optionResult.reason));
    if (qualityResult.status === 'fulfilled') setQuality(qualityResult.value);
    else failures.push(errorMessage(qualityResult.reason));
    if (healthResult.status === 'fulfilled') setHealth(healthResult.value);
    else { setHealth(undefined); failures.push(errorMessage(healthResult.reason)); }
    setConnectionError([...new Set(failures)].join(' '));
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void fetchHistory();
    const timer = setInterval(() => { void fetchHistory(); }, 8000);
    return () => clearInterval(timer);
  }, [fetchHistory, task?.status]);
  useEffect(() => {
    const timer = setInterval(() => {
      api.health().then(setHealth).catch(() => setHealth(undefined));
    }, 10_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => { setStorageError(!writeHistory({ ...local, draft })); }, [local, draft]);
  useEffect(() => {
    if ((task?.kind === 'import' || task?.kind === 'label') && !isActive(task.status)) void load();
  }, [task?.task_id, task?.status, task?.kind, load]);

  const selectTask = (id: string | null) => {
    setLocal(previous => ({ ...previous, selected: id }));
    setNavOpen(false); setSubmitError('');
  };
  async function submit(input: TaskInput, note: string) {
    const fingerprint = JSON.stringify(input);
    if (submissionKey.current?.fingerprint !== fingerprint) submissionKey.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true); setSubmitError('');
    try {
      const result = await api.submit(input, submissionKey.current.key);
      setLocal(previous => ({ ...previous, selected: result.task.task_id, notes: { ...previous.notes, [result.task.task_id]: note } }));
      submissionKey.current = undefined;
      setImportOpen(false); setLabelDefaults(undefined); setPage(1); void fetchHistory();
    } catch (error) { setSubmitError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  async function retry() {
    if (!task || !canRetry(task.status)) return;
    if (retryKey.current?.id !== task.task_id) retryKey.current = { id: task.task_id, key: crypto.randomUUID() };
    setBusy(true); setSubmitError('');
    try {
      const result = await api.retry(task.task_id, retryKey.current.key);
      setLocal(previous => ({ ...previous, selected: result.task.task_id, notes: { ...previous.notes, [result.task.task_id]: previous.notes[task.task_id] || kindLabels[task.kind] } }));
      retryKey.current = undefined; void fetchHistory();
    } catch (error) { setSubmitError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  const currentProducts = options.products.filter(product => {
    if (task) {
      const ids = task.parameters.product_ids as string[] | undefined;
      return (!task.parameters.source || product.source === task.parameters.source)
        && (!task.parameters.dataset_version || product.dataset_version === task.parameters.dataset_version)
        && (!ids?.length || ids.includes(product.product_id));
    }
    return datasetKey(product) === dataset && (!productIds.length || productIds.includes(product.product_id));
  });
  const title = task ? local.notes[task.task_id] || `${kindLabels[task.kind]} · ${dateLabel(task.created_at)}` : '';
  const openLabel = () => {
    const scoped = task?.kind === 'import' || task?.kind === 'label';
    setSubmitError(''); setNavOpen(false);
    setLabelDefaults({
      dataset: scoped ? JSON.stringify([task.parameters.source || 'amazon_reviews_2023', task.parameters.dataset_version]) : dataset,
      products: scoped ? (task.parameters.product_ids as string[] || []) : productIds,
      limit: task?.kind === 'label' ? task.parameters.limit as number : undefined,
      workers: task?.kind === 'label' ? task.parameters.workers as number : undefined,
    });
  };
  const taskIdentity = task?.kind === 'label' ? task.parameters.model_identity as Identity | undefined : task?.parameters;
  const previewProfile = task ? options.profiles.find(p => p.requested_model === taskIdentity?.model && p.base_url === taskIdentity?.base_url && p.prompt_version === taskIdentity?.prompt_version)?.profile_id : profileId;
  const preparedDraft = { ...draft, dataset: task?.kind === 'label' ? JSON.stringify([task.parameters.source, task.parameters.dataset_version]) : dataset,
    productIds: task?.kind === 'label' ? (task.parameters.product_ids as string[] || []) : productIds, profileId: previewProfile };
  const preparedDates = analysisDates(preparedDraft, quality);
  const analyzeLabeled = (start: boolean) => {
    if (!task) return;
    setDraft(preparedDraft);
    const parameters = analysisParameters(preparedDraft, options, quality);
    if (start && parameters) void submit({ kind: 'analysis', parameters }, draft.note.trim() || '评论趋势与负面问题分析');
    else selectTask(null);
  };
  const showDetail = (value: unknown, title: string) => setDetail({ title, value });
  const side = <>
    <div className="brand"><div className="brand-mark"><BarChartOutlined /></div><div><strong>观数</strong><span>评论分析工作台</span></div></div>
    <Button aria-label="新建分析" className="new-button" type="primary" block icon={<PlusOutlined />} onClick={() => selectTask(null)}>分析工作台</Button>
    <div className="nav-caption"><HistoryOutlined /> 最近任务</div>
    <div className="history-list">
      {historyError && <Alert type="warning" message="历史暂不可用" description={historyError} action={<Button size="small" onClick={() => void fetchHistory()}>重试</Button>} />}
      {!history?.items.length && !historyError && <div className="history-empty">还没有分析记录<br /><span>开始一次分析，结果将在这里保留</span></div>}
      {history?.items.map(item => <button key={item.task_id} className={`history-item ${local.selected === item.task_id ? 'selected' : ''}`} onClick={() => selectTask(item.task_id)}>
        <div className="history-title"><FileTextOutlined /><span>{local.notes[item.task_id] || kindLabels[item.kind]}</span></div>
        <div className="history-meta"><span>{dateLabel(item.created_at)}</span><span className={`task-state ${item.status}`}>{statusLabels[item.status]}</span></div>
      </button>)}
    </div>
    {!!history?.total && history.total > 15 && <Pagination simple size="small" current={page} total={history.total} pageSize={15} onChange={setPage} />}
    <div className="sidebar-bottom"><div className="nav-caption"><DatabaseOutlined /> 当前数据集</div>
      <div className="dataset-tile"><div className="dataset-icon"><DatabaseOutlined /></div><div><strong>{new Set(currentProducts.map(datasetKey)).size > 1 ? '多个数据集' : currentProducts[0]?.dataset_version || (task?.parameters.dataset_version as string) || '尚未选择'}</strong><span>{currentProducts.length} 款商品 · {currentProducts.reduce((sum, p) => sum + p.review_count, 0).toLocaleString()} 条评论</span></div></div>
      <Button aria-label="从服务端目录导入" block icon={<ImportOutlined />} onClick={() => { setSubmitError(''); setImportOpen(true); }}>从服务端目录导入</Button>
      <Button className="spaced" block icon={<CheckOutlined />} onClick={openLabel}>准备数据</Button>
      <p className="local-note"><span className="status-dot" /> 本机工作空间</p>
    </div>
  </>;
  return <div className="workbench">
    <aside className="desktop-sidebar">{side}</aside>
    <Drawer title="工作空间" placement="left" width={280} open={navOpen} onClose={() => setNavOpen(false)} className="mobile-nav">{side}</Drawer>
    <div className="workspace-main">
      <header className="topbar"><div className="breadcrumb"><Button className="mobile-menu" type="text" aria-label="展开侧栏" icon={<MenuOutlined />} onClick={() => setNavOpen(true)} /><span>工作空间</span><span className="breadcrumb-slash">/</span><strong>分析工作台</strong></div>
        <Space size={16}><span className="connection-label"><Badge status={!health ? 'error' : health.worker_available ? 'success' : 'warning'} text={!health ? '连接待确认' : health.worker_available ? '服务已连接' : '执行器离线'} /></span><Button type="text" icon={<ReloadOutlined />} aria-label="刷新数据" loading={loading} onClick={() => { void load(); void fetchHistory(); taskState.refresh(); }} /><Button icon={<FolderOpenOutlined />} disabled={!currentProducts.length} onClick={() => setPreviewOpen(true)}>数据详情</Button></Space>
      </header>
      <main className="main-content">
        {connectionError && <Alert className="spaced-bottom" type="error" showIcon message="无法完整加载工作空间" description={<>{connectionError}<div className="api-address">API：{API_BASE}</div></>} action={<Button onClick={() => void load()}>重新连接</Button>} />}
        {storageError && <Alert className="spaced-bottom" type="warning" showIcon message="浏览器存储不可用，问题备注和当前选择在刷新后可能丢失；后端任务仍会保留。" />}
        {health && !health.worker_available && <Alert className="spaced-bottom" type="warning" showIcon message="后台执行器离线" description="可以提交任务，但任务会留在队列中，直到后端 worker 启动。" />}
        {submitError && !importOpen && !labelDefaults && <Alert className="spaced-bottom" type="error" showIcon message="请求未完成" description={submitError} />}
        {!local.selected ? loading && !options.products.length ? <Skeleton active paragraph={{ rows: 6 }} /> : <Composer key={dataset} options={options} quality={quality} draft={draft} setDraft={setDraft} busy={busy} onSubmit={(parameters: AnalysisParameters, question: string) => void submit({ kind: 'analysis', parameters }, question)} onPreview={() => setPreviewOpen(true)} onImport={() => setImportOpen(true)} onLabel={openLabel} /> : <>
          <Button className="back-button" type="text" icon={<ArrowLeftOutlined />} onClick={() => selectTask(null)}>新建另一项分析</Button>
          {taskState.loading && !task && <Skeleton active paragraph={{ rows: 5 }} />}
          {taskState.error && <Alert className="spaced-bottom" type="error" showIcon message="任务连接异常" description={<>{taskState.error}<div>保留最后一次已知状态，正在尝试重新连接。</div></>} action={<Button onClick={taskState.refresh}>立即重连</Button>} />}
          {task && <>
            <div className="task-heading"><span className="eyebrow">{task.kind === 'import' ? '第 1 步 · 导入数据' : task.kind === 'label' ? '第 2 步 · 准备数据' : '分析结果'}</span><h1>{title}</h1><p className="muted">创建于 {new Date(task.created_at).toLocaleString('zh-CN')}</p></div>
            <div className="scope-tags"><Tag icon={<DatabaseOutlined />}>{String(task.parameters.dataset_version || '全部版本')}</Tag><Tag>{(task.parameters.product_ids as string[] | undefined)?.join('、') || '全部商品'}</Tag>{!!task.parameters.date_from && <Tag>{String(task.parameters.date_from)} — {String(task.parameters.date_to)} · UTC</Tag>}<Button type="link" size="small" onClick={() => showDetail(task.parameters, '本次任务参数')}>查看参数</Button></div>
            <Card className="progress-card" aria-live="polite">
              <div className="progress-heading"><span>{isActive(task.status) ? <Spin size="small" /> : task.status === 'succeeded' ? <CheckOutlined className="success-icon" /> : <Badge status={task.status === 'failed' ? 'error' : 'warning'} />}<strong>{statusLabels[task.status]}</strong></span><span className="muted">{stageLabels[task.progress.stage] || task.progress.stage}</span></div>
              {progressPercent(task.progress) !== undefined ? <Progress percent={progressPercent(task.progress)} status={task.status === 'failed' ? 'exception' : task.status === 'succeeded' ? 'success' : isActive(task.status) ? 'active' : 'normal'} size="small" /> : <p className="muted">{task.status === 'queued' ? '等待后台执行器领取任务。' : `已处理 ${task.progress.completed}，当前阶段总量未知。`}</p>}
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
              <div className="task-actions">{canRetry(task.status) && <Button aria-label="重试任务" loading={busy} icon={<ReloadOutlined />} onClick={() => void retry()}>重试任务</Button>}<Button size="small" type="text" onClick={() => showDetail(task, '任务详情')}>执行详情</Button>{task.parent_task_id && <Button type="link" size="small" onClick={() => selectTask(task.parent_task_id)}>查看原任务</Button>}</div>
            </Card>
            {task.kind === 'import' && (task.status === 'succeeded' || task.status === 'partial') && <Card className="spaced" title="导入完成，继续准备数据">
              {Array.isArray(task.result?.imports) && task.result.imports.map((item, index) => <p key={index}>{item.kind === 'reviews' ? '评论' : '商品信息'}：新增 <strong>{item.inserted_records}</strong> 条，重复 {item.duplicate_records} 条{item.limit_reached ? '；已达到本次导入上限' : ''}。</p>)}
              <p className="muted">已带入本次数据集。下一步确认商品和处理数量，提取评论观点。</p><Button type="primary" disabled={loading || !currentProducts.length} onClick={openLabel}>标注已导入的数据</Button></Card>}
            {task.kind === 'label' && !isActive(task.status) && <Card className="spaced" title="继续分析">
              <p>分析 {preparedDates.current[0]} 至 {preparedDates.current[1]}，对比 {preparedDates.baseline[0]} 至 {preparedDates.baseline[1]}。</p>
              <p className="muted">沿用本次商品和标注版本。分析使用已有标注；未处理的评论会显示为覆盖率不足。</p>
              <Space wrap><Button type="primary" loading={busy || loading} disabled={loading || !previewProfile || !!connectionError} onClick={() => analyzeLabeled(true)}>分析这些评论</Button><Button type="link" disabled={loading || !previewProfile} onClick={() => analyzeLabeled(false)}>调整分析范围</Button><Button onClick={openLabel}>继续标注下一批</Button></Space>
              {!previewProfile && !loading && <p className="muted">暂无可用标注版本，请先完成一批数据准备。</p>}
            </Card>}
            {run && <ResultCards run={run} question={title} onDetail={showDetail} />}
            {task.result && task.kind !== 'analysis' && <details className="advanced-settings spaced"><summary>处理明细 <span>原始结果与下载</span></summary><div className="advanced-content"><Button icon={<DownloadOutlined />} onClick={() => exportResult(task.result, `task-${task.task_id}`)}>下载 JSON</Button><pre className="json-view">{JSON.stringify(task.result, null, 2)}</pre></div></details>}
            {task.kind === 'analysis' && task.status === 'succeeded' && !run && !taskState.error && <div className="loading-result"><Spin /> 正在读取分析快照…</div>}
          </>}
        </>}
      </main>
      <footer className="workspace-footer">观数 · 数据有据，分析可溯<span>评论分析工作台</span></footer>
    </div>
    <Drawer title="数据详情" width={620} open={previewOpen} onClose={() => setPreviewOpen(false)} destroyOnHidden><DataPreview products={currentProducts} options={options} profileId={previewProfile} onDetail={showDetail} /></Drawer>
    <Drawer title={detail?.title} width={650} open={!!detail} onClose={() => setDetail(undefined)} extra={<Button icon={<DownloadOutlined />} onClick={() => exportResult(detail?.value, 'analysis-detail')}>下载 JSON</Button>}><pre className="json-view">{JSON.stringify(detail?.value, null, 2)}</pre></Drawer>
    <Modal title="准备数据" open={!!labelDefaults} onCancel={() => { if (!busy) setLabelDefaults(undefined); }} footer={null} destroyOnHidden>
      {submitError && <Alert className="spaced-bottom" type="error" showIcon message="标注任务未提交" description={submitError} />}
      {labelDefaults && <LabelForm options={options} initialDataset={labelDefaults.dataset} initialProducts={labelDefaults.products}
        initialLimit={labelDefaults.limit} initialWorkers={labelDefaults.workers} busy={busy}
        onSubmit={parameters => {
          setDraft(previous => {
            const dataset = JSON.stringify([parameters.source, parameters.dataset_version]);
            return { ...previous, dataset, productIds: parameters.product_ids,
              note: previous.dataset === dataset && JSON.stringify(previous.productIds) === JSON.stringify(parameters.product_ids) ? previous.note : '' };
          });
          void submit({ kind: 'label', parameters }, `准备数据 · 最多 ${parameters.limit} 条`);
        }} />}
    </Modal>
    <Modal title="从服务端目录导入" open={importOpen} onCancel={() => { if (!busy) setImportOpen(false); }} footer={null} destroyOnHidden>
      <p className="muted">填写已放入导入目录的文件路径。支持 Amazon 格式的 JSONL / JSONL.gz，评论时间为 1996—2023 年。</p>
      {submitError && <Alert className="spaced-bottom" type="error" showIcon message={submitError} />}
      <Form form={importForm} layout="vertical" initialValues={{ dataset_version: 'amazon-reviews-2023' }} onFinishFailed={({ errorFields }) => { if (importAdvanced.current && errorFields.some(field => field.name[0] === 'dataset_version')) importAdvanced.current.open = true; }} onFinish={values => void submit({ kind: 'import', parameters: {
        reviews_path: values.reviews_path?.trim() || undefined, meta_path: values.meta_path?.trim() || undefined,
        dataset_version: values.dataset_version.trim(), product_ids: [],
        max_reviews: values.max_reviews ?? undefined,
      } }, `导入 ${values.reviews_path || values.meta_path}`)}>
        <Form.Item label="评论文件相对路径" name="reviews_path" dependencies={['meta_path']} rules={[({ getFieldValue }) => ({ validator(_, value) {
          return value?.trim() || getFieldValue('meta_path')?.trim() ? Promise.resolve() : Promise.reject(new Error('请至少填写一个文件路径'));
        } })]}><Input placeholder="reviews.jsonl.gz" disabled={busy} /></Form.Item>
        <Form.Item label="本次最多新增评论数（可选）" name="max_reviews" dependencies={['reviews_path']} extra="留空则全部导入。重复记录不占额度；达到数量后停止，后续评论不在本次检查范围内。" rules={[({ getFieldValue }) => ({ validator(_, value) {
          if (value == null) return Promise.resolve();
          if (!Number.isSafeInteger(value) || value < 1) return Promise.reject(new Error('请输入正整数'));
          return getFieldValue('reviews_path')?.trim() ? Promise.resolve() : Promise.reject(new Error('限制评论数量时请填写评论文件路径'));
        } })]}><InputNumber min={1} step={1} placeholder="例如 1000" disabled={busy} style={{ width: '100%' }} /></Form.Item>
        <details className="advanced-settings" ref={importAdvanced}><summary>导入设置 <span>商品信息、数据集版本</span></summary><div className="advanced-content">
          <Form.Item label="商品元数据相对路径（可选）" name="meta_path"><Input placeholder="meta.jsonl.gz" disabled={busy} /></Form.Item>
          <Form.Item label="数据集版本" name="dataset_version" rules={[{ required: true, whitespace: true, message: '请输入数据集版本' }]}><Input disabled={busy} /></Form.Item>
        </div></details>
        <Button className="spaced" aria-label="提交导入任务" block type="primary" htmlType="submit" icon={<ImportOutlined />} loading={busy}>导入数据</Button>
      </Form>
    </Modal>
  </div>;
}
