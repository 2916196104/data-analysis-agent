import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Drawer, Modal,
  Progress, Skeleton, Space, Spin, Tag,
} from 'antd';
import {
  CheckOutlined, DatabaseOutlined, DeleteOutlined,
  DownloadOutlined, ReloadOutlined,
} from '@ant-design/icons';
import AppLayout from './app/AppLayout';
import { navigate, routeUrl, taskRoute, useAppRoute } from './app/router';
import DataManagement from './features/datasets/components/DataManagement';
import { api, API_BASE, errorMessage } from './api';
import Composer from './Composer';
import { analysisDates, analysisParameters, datasetKey } from './analysis-draft';
import AnnotationPage from './features/annotations/AnnotationPage';
import { AnnotationNextSteps, AnnotationProgress } from './features/annotations/AnnotationTaskContent';
import ReviewWorkbenchPage from './features/annotations/review-workbench/ReviewWorkbenchPage';
import TaskReportsPage from './features/tasks/TaskReportsPage';
import ResultCards from './ResultCards';
import AiAssistantPage from './features/agent/AiAssistantPage';
import { useAgentChat } from './features/agent/useAgentChat';
import { isAgentTask, type ChatMode } from './features/agent/chat-state';
import { DataPreviewDrawer, ImportDialog, ImportProgress, useImportTask } from './features/datasets';
import { exportResult, readHistory, writeHistory } from './local-history';
import { canRetry, isActive, kindLabels, progressPercent, stageLabels, statusLabels } from './task-state';
import { useTask } from './useTask';
import type {
  AgentResult, AnalysisParameters, Health, Identity, Options, Page, Quality,
  TaskInput, TaskSummary,
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
  const route = useAppRoute(local.selected);
  const section = route.page === 'task' ? route.module : route.page === 'annotation-review' ? 'annotation' : route.page;
  const selectedTaskId = route.page === 'task' ? route.taskId : null;
  const [datasetPage, setDatasetPage] = useState(1);
  const [options, setOptions] = useState<Options>(emptyOptions);
  const [quality, setQuality] = useState<Quality>();
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [health, setHealth] = useState<Health>();
  const [draft, setDraft] = useState(local.draft);
  const [chatMode, setChatMode] = useState<ChatMode>('basic');
  const [basicInput, setBasicInput] = useState('');
  const { dataset, productIds, profileId } = draft;
  const [history, setHistory] = useState<Page<TaskSummary>>();
  const [historyLoading, setHistoryLoading] = useState(false);
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
  const [previewOpen, setPreviewOpen] = useState(false);
  const [detail, setDetail] = useState<{ title: string; value: unknown }>();
  const [importOpen, setImportOpen] = useState(false);
  const labelDefaults = route.page === 'annotation' ? route.defaults : undefined;
  const submissionKey = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const retryKey = useRef<{ id: string; key: string } | undefined>(undefined);
  const historyRequest = useRef(0);
  const loadRequest = useRef(0);
  const taskState = useTask(selectedTaskId);
  const importTask = useImportTask({ onSubmit: submit, onBusyChange: setBusy, onError: setSubmitError });
  const task = taskState.id === selectedTaskId ? taskState.task : null;
  const run = taskState.id === selectedTaskId ? taskState.run : null;
  const chat = useAgentChat(route.page === 'assistant' ? route.taskId : undefined);
  const fetchHistory = useCallback(async () => {
    const requestId = ++historyRequest.current;
    setHistoryLoading(true);
    try {
      const next = await api.tasks(page);
      if (requestId === historyRequest.current) {
        setHistory(next);
        setHistoryError('');
      }
    } catch (error) {
      if (requestId === historyRequest.current) setHistoryError(errorMessage(error));
    } finally {
      if (requestId === historyRequest.current) setHistoryLoading(false);
    }
  }, [page]);
  useEffect(() => {
    const saved = chat.session.turns.filter(turn => turn.taskId);
    if (!saved.length) return;
    setLocal(previous => ({ ...previous,
      selected: route.page === 'assistant' ? chat.activeTurn?.taskId ?? null : previous.selected,
      notes: { ...previous.notes, ...Object.fromEntries(saved.map(turn => [turn.taskId!, turn.question])) },
    }));
    void fetchHistory();
  }, [chat.session, route.page, fetchHistory]);

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
    setCatalogRevision(previous => previous + 1);
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
    if (route.page === 'tasks') return;
    setLocal(previous => previous.selected === selectedTaskId ? previous : { ...previous, selected: selectedTaskId });
  }, [route.page, selectedTaskId]);
  useEffect(() => {
    setSubmitError('');
    setPendingDelete(undefined);
  }, [route]);
  useEffect(() => {
    if (route.page === 'annotation' && !route.defaults && !loading) {
      navigate({ page: 'annotation', defaults: { dataset, products: productIds } }, true);
    }
  }, [route, loading, dataset, productIds]);
  useEffect(() => {
    if (route.page !== 'task' || !task) return;
    const canonical = taskRoute(task.task_id, task.kind, isAgentTask(task));
    if (routeUrl(canonical) !== routeUrl(route)) navigate(canonical, true);
  }, [route, task]);
  useEffect(() => {
    const dataPreparationFinished = task
      && (task.kind === 'import' || task.kind === 'label')
      && !isActive(task.status);
    if (dataPreparationFinished) void load();
  }, [task?.task_id, task?.status, task?.kind, load]);

  const selectTask = (id: string | null) => {
    const kind = history?.items.find(item => item.task_id === id)?.kind ?? (task?.task_id === id ? task.kind : undefined);
    navigate(id ? taskRoute(id, kind, chat.session.turns.some(turn => turn.taskId === id)) : { page: 'analysis' });
    setLocal(previous => ({ ...previous, selected: id }));
    setPendingDelete(undefined);
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
      navigate(taskRoute(result.task.task_id, result.task.kind));
      setLocal(previous => ({
        ...previous,
        selected: result.task.task_id,
        notes: { ...previous.notes, [result.task.task_id]: note },
      }));
      submissionKey.current = undefined;
      setImportOpen(false);
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
      navigate(taskRoute(result.task.task_id, result.task.kind));
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
        if (local.selected === current.taskId) {
          if (section === 'tasks') setLocal(previous => ({ ...previous, selected: null }));
          else if (section === 'annotation') navigate({ page: 'annotation' });
          else selectTask(null);
        }
        void fetchHistory();
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


  const importResults = Array.isArray(task?.result?.imports) ? task.result.imports : [];
  const taskSource = task?.parameters.source || importResults.find(item => item.kind === 'reviews')?.source || importResults[0]?.source;
  const currentProducts = options.products.filter(product => {
    if (task) {
      const ids = task.parameters.product_ids as string[] | undefined;
      return (!taskSource || product.source === taskSource)
        && (!task.parameters.dataset_version || product.dataset_version === task.parameters.dataset_version)
        && (!ids?.length || ids.includes(product.product_id));
    }
    const activeDataset = labelDefaults?.dataset ?? dataset;
    const activeProducts = labelDefaults?.products ?? productIds;
    return datasetKey(product) === activeDataset && (!activeProducts.length || activeProducts.includes(product.product_id));
  });
  const title = task
    ? local.notes[task.task_id] || `${kindLabels[task.kind]} · ${dateLabel(task.created_at)}`
    : '';

  const openLabel = () => {
    if (route.page === 'annotation') return;
    if (loading) { navigate({ page: 'annotation' }); return; }
    const scoped = task?.kind === 'import' || task?.kind === 'label';
    setSubmitError('');
    setPendingDelete(undefined);
    navigate({ page: 'annotation', defaults: {
      dataset: scoped
        ? JSON.stringify([
          taskSource || 'amazon_reviews_2023',
          task.parameters.dataset_version,
        ])
        : dataset,
      products: scoped ? (task.parameters.product_ids as string[] || []) : productIds,
      limit: task?.kind === 'label' ? task.parameters.limit as number : undefined,
      workers: task?.kind === 'label' ? task.parameters.workers as number : undefined,
    } });
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
  const prepareAgent = (result: AgentResult, products: string[]) => {
    setSubmitError('');
    navigate({ page: 'annotation', defaults: { dataset: JSON.stringify([result.scope.source, result.scope.dataset_version]), products,
      scope: { date_from: result.scope.date_from, date_to: result.scope.date_to, as_of: result.scope.as_of }, retryFailed: true } });
  };
  const chatParameters = analysisParameters(draft, options, quality);
  const chatDates = analysisDates(draft, quality);
  const chatContext = `${currentProducts[0]?.dataset_version || '尚未选择数据集'} · ${productIds.length ? currentProducts.map(product => product.product_title || product.product_id).join('、') : `全部商品（${currentProducts.length} 款）`} · ${chatDates.current[0]} — ${chatDates.current[1]}（UTC）`;
  const sendChat = async () => {
    const question = (chatMode === 'basic' ? basicInput : draft.agentQuestion)?.trim();
    if (!question || chat.pending || (chatMode === 'analysis' && !chatParameters)) return;
    if (route.page === 'assistant' && route.taskId) navigate({ page: 'assistant' });
    const sent = chatMode === 'basic' ? await chat.sendBasic(question) : await chat.send(chatParameters!, question);
    if (!sent) return;
    if (chatMode === 'basic') setBasicInput(previous => previous.trim() === question ? '' : previous);
    else setDraft(previous => previous.agentQuestion?.trim() === question ? { ...previous, agentQuestion: '' } : previous);
  };
  const datasetSummary = productSummary(currentProducts, task?.parameters.dataset_version as string);
  return <>
    <AppLayout
      connection={{ status: !health ? 'error' : health.worker_available ? 'success' : 'warning', text: !health ? '连接待确认' : health.worker_available ? '服务已连接' : '执行器离线' }}
      loading={loading} taskCount={history?.total} fillContent={route.page === 'assistant'}
      datasetSummary={datasetSummary} hasProducts={!!currentProducts.length} section={section}
      onDatasets={() => navigate({ page: 'datasets' })}
      onAnnotation={() => { if (route.page !== 'annotation') navigate({ page: 'annotation' }); }}
      onAgent={() => navigate({ page: 'assistant' })}
      onRefresh={() => { void load(); void fetchHistory(); taskState.refresh(); }}
      onHistory={() => { navigate({ page: 'tasks' }); void fetchHistory(); }}
      onImport={() => { setSubmitError(''); setImportOpen(true); }}
      onPrepare={openLabel} onPreview={() => setPreviewOpen(true)} onNewAnalysis={() => selectTask(null)}>
        {connectionError && <Alert className="spaced-bottom" type="error" showIcon message="无法完整加载工作空间" description={<>{connectionError}<div className="api-address">API：{API_BASE}</div></>} action={<Button onClick={() => void load()}>重新连接</Button>} />}
        {storageError && <Alert className="spaced-bottom" type="warning" showIcon message="浏览器存储不可用，问题备注和当前选择在刷新后可能丢失；后端任务仍会保留。" />}
        {submitError && !importOpen && route.page !== 'annotation' && <Alert className="spaced-bottom" type="error" showIcon message="请求未完成" description={submitError} />}
        {route.page === 'assistant' ? <AiAssistantPage chat={chat} parameters={chatParameters} context={chatContext}
          mode={chatMode} onMode={setChatMode} input={chatMode === 'basic' ? basicInput : draft.agentQuestion || ''}
          onInput={value => chatMode === 'basic' ? setBasicInput(value) : setDraft(previous => ({ ...previous, agentQuestion: value }))}
          onSend={() => void sendChat()} onPrepare={prepareAgent} onAnalysis={() => selectTask(null)}
          onNewQuestion={() => { chat.release(); navigate({ page: 'assistant' }); }}
        /> : section === 'tasks' ? <TaskReportsPage tasks={history} page={page} loading={historyLoading} error={historyError}
          notes={local.notes} selectedId={local.selected} deleteBusy={deleteBusy} formatCreatedAt={dateLabel}
          pendingDelete={pendingDelete?.kind === 'task' ? pendingDelete : undefined}
          onRefresh={() => void fetchHistory()} onPage={value => { setPendingDelete(undefined); setPage(value); }}
          onSelect={selectTask} onDelete={confirmDeleteTask} onConfirmDelete={() => void executeDelete()}
          onCancelDelete={() => { if (!deleteBusy) setPendingDelete(undefined); }}
        /> : route.page === 'annotation-review' ? <ReviewWorkbenchPage onRefresh={() => void load()} /> : route.page === 'annotation' ? !labelDefaults || (loading && !options.products.length) ? <Skeleton active paragraph={{ rows: 6 }} /> : <AnnotationPage
          options={options} initialDataset={labelDefaults.dataset} initialProducts={labelDefaults.products}
          initialLimit={labelDefaults.limit} initialWorkers={labelDefaults.workers} initialScope={labelDefaults.scope}
          initialRetryFailed={labelDefaults.retryFailed} busy={busy} error={submitError} onReview={() => navigate({ page: 'annotation-review' })}
          onSubmit={parameters => {
            setDraft(previous => {
              const dataset = JSON.stringify([parameters.source, parameters.dataset_version]);
              return { ...previous, dataset, productIds: parameters.product_ids,
                note: previous.dataset === dataset && JSON.stringify(previous.productIds) === JSON.stringify(parameters.product_ids) ? previous.note : '' };
            });
            navigate({ page: 'annotation', defaults: { ...labelDefaults,
              dataset: JSON.stringify([parameters.source, parameters.dataset_version]), products: parameters.product_ids,
              limit: parameters.limit, workers: parameters.workers, retryFailed: parameters.retry_failed } }, true);
            void submit({ kind: 'label', parameters }, `准备数据 · 最多 ${parameters.limit} 条`);
          }}
        /> : section === 'datasets' ? <DataManagement options={options} dataset={dataset} page={datasetPage} onPage={setDatasetPage} onDataset={value => { setDatasetPage(1); setDraft(previous => ({ ...previous, dataset: value, productIds: [] })); }} onImport={() => { setSubmitError(''); setImportOpen(true); }} onPreview={() => setPreviewOpen(true)} /> : !selectedTaskId ? loading && !options.products.length ? <Skeleton active paragraph={{ rows: 6 }} /> : <Composer key={dataset} options={options} quality={quality} draft={draft} setDraft={setDraft} busy={busy} refreshKey={catalogRevision} onAgent={() => navigate({ page: 'assistant' })} onSubmit={(parameters: AnalysisParameters, question: string) => void submit({ kind: 'analysis', parameters }, question)} onPreview={() => setPreviewOpen(true)} onLabel={openLabel} /> : <>
          {taskState.loading && !task && <Skeleton active paragraph={{ rows: 5 }} />}
          {taskState.error && <Alert className="spaced-bottom" type="error" showIcon message="任务连接异常" description={<>{taskState.error}<div>保留最后一次已知状态，正在尝试重新连接。</div></>} action={<Button onClick={taskState.refresh}>立即重连</Button>} />}
          {task && <>
            <div className="task-heading"><span className="eyebrow">{taskStepLabel(task.kind)}</span><h1>{title}</h1><p className="muted">创建于 {new Date(task.created_at).toLocaleString('zh-CN')}</p></div>
            <div className="scope-tags"><Tag icon={<DatabaseOutlined />}>{String(task.parameters.dataset_version || '全部版本')}</Tag><Tag>{(task.parameters.product_ids as string[] | undefined)?.join('、') || '全部商品'}</Tag>{!!task.parameters.date_from && <Tag>{String(task.parameters.date_from)} — {String(task.parameters.date_to)} · UTC</Tag>}<Button type="link" size="small" onClick={() => showDetail(task.parameters, '本次任务参数')}>查看参数</Button></div>
            <Card className="progress-card" aria-live="polite">
              <div className="progress-heading"><span>{isActive(task.status) ? <Spin size="small" /> : task.status === 'succeeded' ? <CheckOutlined className="success-icon" /> : <Badge status={task.status === 'failed' ? 'error' : 'warning'} />}<strong>{statusLabels[task.status]}</strong></span><span className="muted">{stageLabels[task.progress.stage] || task.progress.stage}</span></div>
              {task.kind === 'import' ? <ImportProgress task={task} /> : progressPercent(task.progress) !== undefined ? <Progress percent={progressPercent(task.progress)} status={progressStatus(task.status)} size="small" /> : <p className="muted">{task.status === 'queued' ? '等待后台执行器领取任务。' : `已处理 ${task.progress.completed}，当前阶段总量未知。`}</p>}
              {task.kind === 'label' && <AnnotationProgress task={task} />}
              {task.error && <Alert className="spaced" type="error" showIcon message={task.error.message} description={`错误码：${task.error.code}`} />}
              {task.status === 'partial' && <Alert className="spaced" type="warning" showIcon message="任务部分完成" description="请先查看返回结果中的错误与已完成记录，再决定是否重试。" />}
              <div className="task-actions">{canRetry(task.status) && <Button aria-label="重试任务" loading={busy} icon={<ReloadOutlined />} onClick={() => void retry()}>重试任务</Button>}<Button size="small" type="text" onClick={() => showDetail(task, '任务详情')}>执行详情</Button>{task.parent_task_id && <Button type="link" size="small" onClick={() => selectTask(task.parent_task_id)}>查看原任务</Button>}{!isActive(task.status) && <Button danger loading={deleteBusy} icon={<DeleteOutlined />} onClick={() => confirmDeleteTask(task.task_id, title)}>删除任务记录</Button>}</div>
            </Card>
            {task.kind === 'import' && (task.status === 'succeeded' || task.status === 'partial') && <Card className="spaced" title="导入完成，继续准备数据">
              {Array.isArray(task.result?.imports) && task.result.imports.map((item, index) => <p key={index}>{item.kind === 'reviews' ? '评论' : '商品信息'}：新增 <strong>{item.inserted_records}</strong> 条，重复 {item.duplicate_records} 条{item.limit_reached ? '；已达到本次导入上限' : ''}。</p>)}
              <p className="muted">已带入本次数据集。下一步确认商品和处理数量，提取评论观点。</p><Space wrap><Button type="primary" disabled={loading || !currentProducts.length} onClick={openLabel}>标注已导入的数据</Button>{!Boolean(task.result?.import_data_deleted) && <Button danger loading={deleteBusy} icon={<DeleteOutlined />} onClick={() => confirmDeleteImportedData(task.task_id)}>删除导入数据</Button>}{Boolean(task.result?.import_data_deleted) && <Tag color="green">导入数据已删除</Tag>}</Space></Card>}
            {task.kind === 'label' && <AnnotationNextSteps task={task} dates={preparedDates} loading={loading} busy={busy} profileId={previewProfile} connectionError={!!connectionError} onAnalyze={analyzeLabeled} onContinue={openLabel} />}
            {run && <ResultCards run={run} question={title} onDetail={showDetail} />}
            {task.result && task.kind !== 'analysis' && <details className="advanced-settings spaced"><summary>处理明细 <span>原始结果与下载</span></summary><div className="advanced-content"><Button icon={<DownloadOutlined />} onClick={() => exportResult(task.result, `task-${task.task_id}`)}>下载 JSON</Button><pre className="json-view">{JSON.stringify(task.result, null, 2)}</pre></div></details>}
            {task.kind === 'analysis' && task.status === 'succeeded' && !run && !isAgentTask(task) && !taskState.error && <div className="loading-result"><Spin /> 正在读取分析快照…</div>}
          </>}
        </>}
        {route.page !== 'assistant' && health && !health.worker_available && <p className="executor-note" role="status"><Badge status="warning" /><span>后台执行器离线</span> · 提交的任务将排队等待执行器启动。</p>}
    </AppLayout>
    <DataPreviewDrawer open={previewOpen} onClose={() => setPreviewOpen(false)} products={currentProducts} options={options} profileId={previewProfile} onDetail={showDetail} />
    <Drawer title={detail?.title} width={650} open={!!detail} onClose={() => setDetail(undefined)} closable={{ 'aria-label': '关闭数据详情' }} extra={<Button icon={<DownloadOutlined />} onClick={() => exportResult(detail?.value, 'analysis-detail')}>下载 JSON</Button>}><pre className="json-view">{JSON.stringify(detail?.value, null, 2)}</pre></Drawer>
    <Modal title={pendingDelete?.kind === 'data' ? '删除这次导入的数据？' : '删除任务记录？'} open={!!pendingDelete && section !== 'tasks'} onCancel={() => { if (!deleteBusy) setPendingDelete(undefined); }} onOk={() => void executeDelete()} okText={pendingDelete?.kind === 'data' ? '删除导入数据' : '删除记录'} cancelText="取消" okButtonProps={{ danger: true, loading: deleteBusy }} destroyOnHidden>
      {pendingDelete?.kind === 'data'
        ? '会删除本次导入产生的评论、错误记录和关联标注；其他导入批次共用的商品信息会保留。此操作不可恢复。'
        : `将删除“${pendingDelete?.label}”及其重试记录。已导入的评论数据不会被删除。`}
    </Modal>
    <ImportDialog
      open={importOpen}
      busy={busy}
      submitError={submitError}
      reviewFile={importTask.reviewFile}
      metaFile={importTask.metaFile}
      uploadAttempted={importTask.uploadAttempted}
      uploadState={importTask.uploadState}
      phase={importTask.phase}
      onError={setSubmitError}
      onSelectFile={importTask.selectFile}
      onRemoveFile={importTask.removeFile}
      onSubmit={importTask.submitImport}
      onCancelUpload={importTask.cancelUpload}
      onClose={() => setImportOpen(false)}
    />
  </>;
}
