import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Drawer, Modal, Skeleton } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import AppLayout from './app/AppLayout';
import { navigate, routeUrl, taskRoute, useAppRoute } from './app/router';
import TaskDetailView from './app/TaskDetailView';
import { useWorkspaceCatalog } from './app/useWorkspaceCatalog';
import DataManagement from './features/datasets/components/DataManagement';
import { api, API_BASE, errorMessage } from './api';
import Composer from './Composer';
import {
  AGENT_DATASET,
  agentAnalysisParameters,
  analysisDates,
  analysisParameters,
  datasetKey,
  taskDataset,
} from './analysis-draft';
import AnnotationPage from './features/annotations/AnnotationPage';
import ReviewWorkbenchPage from './features/annotations/review-workbench/ReviewWorkbenchPage';
import TaskReportsPage from './features/tasks/TaskReportsPage';
import AiAssistantPage from './features/agent/AiAssistantPage';
import { useAgentChat } from './features/agent/useAgentChat';
import { isAgentTask, type ChatMode } from './features/agent/chat-state';
import { DataPreviewDrawer, ImportDialog, useImportTask } from './features/datasets';
import { datasetSelectOptions } from './features/datasets/model/dataset-options';
import { exportResult, readHistory, writeHistory } from './local-history';
import { canRetry, isActive, kindLabels } from './task-state';
import { useTask } from './useTask';
import type { AgentResult, AnalysisParameters, Identity, Options, TaskInput } from './types';

const dateLabel = (value: string) => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function productSummary(products: Options['products'], fallbackDataset?: string): string {
  const datasetCount = new Set(products.map(datasetKey)).size;
  const dataset = datasetCount > 1
    ? '多个数据集'
    : products[0]?.dataset_version || fallbackDataset || '尚未选择';
  const reviewCount = products.reduce((sum, product) => sum + product.review_count, 0);
  return `${dataset} · ${products.length} 款商品 · ${reviewCount.toLocaleString()} 条评论`;
}

/** 工作台页面：加载目录和任务历史，提交任务后轮询并展示冻结结果。 */
export default function App() {
  const [local, setLocal] = useState(readHistory);
  const route = useAppRoute(local.selected);
  const section = route.page === 'task'
    ? route.module
    : route.page === 'annotation-review'
      ? 'annotation'
      : route.page;
  const selectedTaskId = route.page === 'task' ? route.taskId : null;

  const [datasetPage, setDatasetPage] = useState(1);
  const [draft, setDraft] = useState(local.draft);
  const [chatMode, setChatMode] = useState<ChatMode>('basic');
  const [basicInput, setBasicInput] = useState('');
  const { dataset, productIds, profileId } = draft;
  // 数据集 ID 沿用现有的 source + dataset_version 复合键，并由 App 作为全局唯一状态持有。
  const selectedDatasetId = dataset;

  const [submitError, setSubmitError] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{
    kind: 'task' | 'data';
    taskId: string;
    label: string;
  }>();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [detail, setDetail] = useState<{ title: string; value: unknown }>();
  const [importOpen, setImportOpen] = useState(false);

  const labelDefaults = route.page === 'annotation' ? route.defaults : undefined;
  const submissionKey = useRef<{ fingerprint: string; key: string } | undefined>(undefined);
  const retryKey = useRef<{ id: string; key: string } | undefined>(undefined);

  const taskState = useTask(selectedTaskId);
  const task = taskState.id === selectedTaskId ? taskState.task : null;
  const run = taskState.id === selectedTaskId ? taskState.run : null;

  const handleDatasetInvalidated = useCallback(() => {
    setDatasetPage(1);
    setPreviewOpen(false);
  }, []);

  const {
    options,
    quality,
    catalogRevision,
    health,
    history,
    historyLoading,
    page,
    setPage,
    loading,
    connectionError,
    datasetNotice,
    setDatasetNotice,
    historyError,
    load,
    fetchHistory,
  } = useWorkspaceCatalog({
    dataset,
    taskStatus: task?.status,
    setDraft,
    onDatasetInvalidated: handleDatasetInvalidated,
  });

  const importTask = useImportTask({
    onSubmit: submit,
    onBusyChange: setBusy,
    onError: setSubmitError,
  });

  const chat = useAgentChat(
    route.page === 'assistant' ? route.taskId : undefined,
    JSON.stringify([draft.dataset, [...draft.productIds].sort(), draft.profileId, draft.current, draft.baseline]),
    agentAnalysisParameters(draft, options, quality),
    draft.dataset,
  );
  const activeChatId = useRef(chat.activeSessionId);
  const inputSessionId = useRef(chat.activeSessionId);
  activeChatId.current = chat.activeSessionId;

  useEffect(() => {
    // Restore the active message's mode without undoing a subsequent manual mode switch.
    if (chat.activeMessage) {
      setChatMode(chat.activeMessage.kind === 'analysis' ? 'analysis' : 'basic');
    }
  }, [chat.activeSessionId, chat.activeMessage?.id]);

  useEffect(() => {
    if (inputSessionId.current === chat.activeSessionId) return;
    inputSessionId.current = chat.activeSessionId;
    setBasicInput('');
    setDraft(previous => ({ ...previous, agentQuestion: '' }));
  }, [chat.activeSessionId]);

  useEffect(() => {
    const saved = chat.session.turns.filter(turn => turn.taskId);
    if (!saved.length) return;
    setLocal(previous => ({
      ...previous,
      selected: route.page === 'assistant' ? chat.activeTurn?.taskId ?? null : previous.selected,
      notes: {
        ...previous.notes,
        ...Object.fromEntries(saved.map(turn => [turn.taskId!, turn.question])),
      },
    }));
    void fetchHistory();
  }, [chat.session, route.page, fetchHistory]);

  useEffect(() => {
    if (route.page !== 'assistant' || route.taskId || loading) return;
    setDraft(previous => (
      previous.dataset === AGENT_DATASET || !previous.dataset
        ? previous
        : {
          ...previous,
          dataset: options.products.some(product => datasetKey(product) === AGENT_DATASET)
            ? AGENT_DATASET
            : '',
          productIds: [],
          current: undefined,
          baseline: undefined,
          rangeConfirmed: false,
          agentQuestion: undefined,
        }
    ));
  }, [route.page, route.page === 'assistant' ? route.taskId : undefined, loading]);

  useEffect(() => {
    setStorageError(!writeHistory({ ...local, draft }));
  }, [local, draft]);

  useEffect(() => {
    if (route.page === 'tasks') return;
    setLocal(previous => (
      previous.selected === selectedTaskId ? previous : { ...previous, selected: selectedTaskId }
    ));
  }, [route.page, selectedTaskId]);

  useEffect(() => {
    setSubmitError('');
    setPendingDelete(undefined);
  }, [route]);

  useEffect(() => {
    if (route.page === 'annotation' && (!route.defaults || route.defaults.dataset !== dataset) && !loading) {
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
    const kind = history?.items.find(item => item.task_id === id)?.kind
      ?? (task?.task_id === id ? task.kind : undefined);
    navigate(
      id ? taskRoute(id, kind, chat.session.turns.some(turn => turn.taskId === id)) : { page: 'analysis' },
    );
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

  const savedDataset = task ? taskDataset(task) : undefined;
  const currentProducts = options.products.filter(product => {
    if (task) {
      const ids = task.parameters.product_ids as string[] | undefined;
      return !!savedDataset
        && product.source === savedDataset.source
        && product.dataset_version === savedDataset.dataset_version
        && (!ids?.length || ids.includes(product.product_id));
    }
    const activeDataset = labelDefaults?.dataset ?? dataset;
    const activeProducts = labelDefaults?.products ?? productIds;
    return (
      datasetKey(product) === activeDataset
      && (!activeProducts.length || activeProducts.includes(product.product_id))
    );
  });
  const title = task
    ? local.notes[task.task_id] || `${kindLabels[task.kind]} · ${dateLabel(task.created_at)}`
    : '';

  const openLabel = () => {
    if (route.page === 'annotation') return;
    if (loading) {
      navigate({ page: 'annotation' });
      return;
    }
    const scoped = task?.kind === 'import' || task?.kind === 'label';
    if (scoped && !savedDataset) {
      setSubmitError('该历史任务缺少可确认的数据来源或版本，请先明确选择数据集。');
      return;
    }
    setSubmitError('');
    setPendingDelete(undefined);
    if (scoped) {
      setDraft(previous => ({
        ...previous,
        dataset: datasetKey(savedDataset!),
        productIds: (task.parameters.product_ids as string[]) || [],
      }));
    }
    navigate({
      page: 'annotation',
      defaults: {
        dataset: scoped ? datasetKey(savedDataset!) : dataset,
        products: scoped ? ((task.parameters.product_ids as string[]) || []) : productIds,
        limit: task?.kind === 'label' ? (task.parameters.limit as number) : undefined,
        workers: task?.kind === 'label' ? (task.parameters.workers as number) : undefined,
      },
    });
  };

  const taskIdentity = task?.kind === 'label'
    ? (task.parameters.model_identity as Identity | undefined)
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
      ? {
        current: [task.parameters.date_from, task.parameters.date_to] as [string, string],
        baseline: undefined,
      }
      : {}),
    dataset: task?.kind === 'label'
      ? (savedDataset ? datasetKey(savedDataset) : '')
      : dataset,
    productIds: task?.kind === 'label'
      ? ((task.parameters.product_ids as string[]) || [])
      : productIds,
    profileId: previewProfile,
  };
  const preparedDates = analysisDates(preparedDraft, quality, options);

  const analyzeLabeled = (start: boolean) => {
    if (!task) return;
    if (!savedDataset) {
      setSubmitError('该历史任务缺少可确认的数据来源或版本，请先明确选择数据集。');
      return;
    }
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

  const showDetail = (value: unknown, detailTitle: string) => setDetail({ title: detailTitle, value });

  const setSelectedDatasetId = (value: string) => {
    if (value === dataset || !options.products.some(product => datasetKey(product) === value)) return;
    setDatasetNotice('');
    setDatasetPage(1);
    setPreviewOpen(false);
    setSubmitError('');
    setDraft(previous => ({
      ...previous,
      dataset: value,
      productIds: [],
      current: undefined,
      baseline: undefined,
      rangeConfirmed: false,
      agentQuestion: undefined,
    }));
    if (route.page === 'assistant' && route.taskId) navigate({ page: 'assistant' });
    if (route.page === 'annotation') {
      navigate({ page: 'annotation', defaults: { dataset: value, products: [] } }, true);
    }
  };

  const prepareAgent = (result: AgentResult, products: string[]) => {
    setSubmitError('');
    const scopeDataset = JSON.stringify([result.scope.source, result.scope.dataset_version]);
    setDraft(previous => ({ ...previous, dataset: scopeDataset, productIds: products }));
    navigate({
      page: 'annotation',
      defaults: {
        dataset: scopeDataset,
        products,
        scope: {
          date_from: result.scope.date_from,
          date_to: result.scope.date_to,
          as_of: result.scope.as_of,
        },
        retryFailed: true,
      },
    });
  };

  const chatParameters = agentAnalysisParameters(draft, options, quality);
  const sendChat = async () => {
    const question = (chatMode === 'basic' ? basicInput : draft.agentQuestion)?.trim();
    if (!question || chat.pending || (chatMode === 'analysis' && !chatParameters)) return;
    const hadRouteTask = route.page === 'assistant' && Boolean(route.taskId);
    if (chatMode === 'basic' && hadRouteTask) navigate({ page: 'assistant' });
    const sent = chatMode === 'basic'
      ? await chat.sendBasic(question)
      : await chat.send(chatParameters!, question);
    if (!sent) return;
    if (chatMode === 'analysis' && hadRouteTask) navigate({ page: 'assistant' });
    if (activeChatId.current !== chat.activeSessionId) return;
    if (chatMode === 'basic') {
      setBasicInput(previous => (previous.trim() === question ? '' : previous));
    } else {
      setDraft(previous => (
        previous.agentQuestion?.trim() === question ? { ...previous, agentQuestion: '' } : previous
      ));
    }
  };

  const datasetSummary = productSummary(currentProducts, task?.parameters.dataset_version as string);

  function renderMainContent() {
    if (route.page === 'assistant') {
      return (
        <AiAssistantPage
          chat={chat}
          parameters={chatParameters}
          draft={draft}
          options={options}
          quality={quality}
          loading={loading}
          refreshKey={catalogRevision}
          onApplyScope={scope => {
            setPreviewOpen(false);
            setDatasetPage(1);
            setDatasetNotice('');
            setSubmitError('');
            if (scope.dataset !== draft.dataset && route.taskId) navigate({ page: 'assistant' });
            setDraft(previous => ({ ...previous, ...scope, rangeConfirmed: true }));
          }}
          mode={chatMode}
          onMode={setChatMode}
          input={chatMode === 'basic' ? basicInput : draft.agentQuestion || ''}
          onInput={value => (
            chatMode === 'basic'
              ? setBasicInput(value)
              : setDraft(previous => ({ ...previous, agentQuestion: value }))
          )}
          onSend={() => void sendChat()}
          onPrepare={prepareAgent}
          onNewQuestion={preserveSession => {
            if (!preserveSession) chat.release();
            navigate({ page: 'assistant' });
          }}
        />
      );
    }
    if (section === 'tasks') {
      return (
        <TaskReportsPage
          tasks={history}
          page={page}
          loading={historyLoading}
          error={historyError}
          notes={local.notes}
          selectedId={local.selected}
          deleteBusy={deleteBusy}
          formatCreatedAt={dateLabel}
          pendingDelete={pendingDelete?.kind === 'task' ? pendingDelete : undefined}
          onRefresh={() => void fetchHistory()}
          onPage={value => {
            setPendingDelete(undefined);
            setPage(value);
          }}
          onSelect={selectTask}
          onDelete={confirmDeleteTask}
          onConfirmDelete={() => void executeDelete()}
          onCancelDelete={() => {
            if (!deleteBusy) setPendingDelete(undefined);
          }}
        />
      );
    }
    if (route.page === 'annotation-review') {
      return <ReviewWorkbenchPage datasetId={dataset} onRefresh={() => void load()} />;
    }
    if (route.page === 'annotation') {
      if (!labelDefaults || (loading && !options.products.length)) {
        return <Skeleton active paragraph={{ rows: 6 }} />;
      }
      return (
        <AnnotationPage
          key={labelDefaults.dataset}
          options={options}
          onDatasetChange={setSelectedDatasetId}
          initialDataset={labelDefaults.dataset}
          initialProducts={labelDefaults.products}
          initialLimit={labelDefaults.limit}
          initialWorkers={labelDefaults.workers}
          initialScope={labelDefaults.scope}
          initialRetryFailed={labelDefaults.retryFailed}
          busy={busy}
          error={submitError}
          onReview={() => navigate({ page: 'annotation-review' })}
          onSubmit={parameters => {
            const nextDataset = JSON.stringify([parameters.source, parameters.dataset_version]);
            setDraft(previous => ({
              ...previous,
              dataset: nextDataset,
              productIds: parameters.product_ids,
              note: previous.dataset === nextDataset
                && JSON.stringify(previous.productIds) === JSON.stringify(parameters.product_ids)
                ? previous.note
                : '',
            }));
            navigate(
              {
                page: 'annotation',
                defaults: {
                  ...labelDefaults,
                  dataset: nextDataset,
                  products: parameters.product_ids,
                  limit: parameters.limit,
                  workers: parameters.workers,
                  retryFailed: parameters.retry_failed,
                },
              },
              true,
            );
            void submit({ kind: 'label', parameters }, `准备数据 · 最多 ${parameters.limit} 条`);
          }}
        />
      );
    }
    if (section === 'datasets') {
      return (
        <DataManagement
          options={options}
          dataset={dataset}
          page={datasetPage}
          onPage={setDatasetPage}
          onDataset={setSelectedDatasetId}
          onImport={() => {
            setSubmitError('');
            setImportOpen(true);
          }}
          onPreview={() => setPreviewOpen(true)}
        />
      );
    }
    if (!selectedTaskId) {
      if (loading && !options.products.length) return <Skeleton active paragraph={{ rows: 6 }} />;
      return (
        <Composer
          key={dataset}
          onDatasetChange={setSelectedDatasetId}
          options={options}
          quality={quality}
          draft={draft}
          setDraft={setDraft}
          busy={busy}
          refreshKey={catalogRevision}
          onAgent={() => {
            setChatMode('analysis');
            navigate({ page: 'assistant' });
          }}
          onSubmit={(parameters: AnalysisParameters, question: string) => (
            void submit({ kind: 'analysis', parameters }, question)
          )}
          onPreview={() => setPreviewOpen(true)}
          onLabel={openLabel}
        />
      );
    }
    return (
      <TaskDetailView
        task={task}
        run={run}
        loading={loading}
        taskLoading={taskState.loading}
        taskError={taskState.error}
        title={title}
        busy={busy}
        deleteBusy={deleteBusy}
        hasProducts={!!currentProducts.length}
        preparedDates={preparedDates}
        previewProfile={previewProfile}
        connectionError={!!connectionError}
        onRefresh={taskState.refresh}
        onRetry={() => void retry()}
        onShowDetail={showDetail}
        onSelectTask={selectTask}
        onConfirmDeleteTask={confirmDeleteTask}
        onConfirmDeleteImportedData={confirmDeleteImportedData}
        onOpenLabel={openLabel}
        onAnalyzeLabeled={analyzeLabeled}
      />
    );
  }

  return <>
    <AppLayout
      connection={{
        status: !health ? 'error' : health.worker_available ? 'success' : 'warning',
        text: !health ? '连接待确认' : health.worker_available ? '服务已连接' : '执行器离线',
      }}
      loading={loading}
      taskCount={history?.total}
      fillContent={route.page === 'assistant'}
      datasetSummary={datasetSummary}
      hasProducts={!!currentProducts.length}
      section={section}
      selectedDatasetId={selectedDatasetId}
      datasetOptions={datasetSelectOptions(options.products)}
      datasetLoading={loading || busy}
      onDatasetChange={setSelectedDatasetId}
      onDatasets={() => navigate({ page: 'datasets' })}
      onAnnotation={() => {
        if (route.page !== 'annotation') navigate({ page: 'annotation' });
      }}
      onAgent={() => navigate({ page: 'assistant' })}
      onRefresh={() => {
        void load();
        void fetchHistory();
        taskState.refresh();
      }}
      onHistory={() => {
        navigate({ page: 'tasks' });
        void fetchHistory();
      }}
      onImport={() => {
        setSubmitError('');
        setImportOpen(true);
      }}
      onPrepare={openLabel}
      onPreview={() => setPreviewOpen(true)}
      onNewAnalysis={() => selectTask(null)}
    >
      {connectionError && (
        <Alert
          className="spaced-bottom"
          type="error"
          showIcon
          message="无法完整加载工作空间"
          description={<>{connectionError}<div className="api-address">API：{API_BASE}</div></>}
          action={<Button onClick={() => void load()}>重新连接</Button>}
        />
      )}
      {datasetNotice && (
        <Alert
          className="spaced-bottom"
          type="info"
          showIcon
          message={datasetNotice}
          closable
          onClose={() => setDatasetNotice('')}
        />
      )}
      {storageError && (
        <Alert
          className="spaced-bottom"
          type="warning"
          showIcon
          message="浏览器存储不可用，问题备注和当前选择在刷新后可能丢失；后端任务仍会保留。"
        />
      )}
      {submitError && !importOpen && route.page !== 'annotation' && (
        <Alert
          className="spaced-bottom"
          type="error"
          showIcon
          message="请求未完成"
          description={submitError}
        />
      )}
      {renderMainContent()}
      {route.page !== 'assistant' && health && !health.worker_available && (
        <p className="executor-note" role="status">
          <Badge status="warning" />
          <span>后台执行器离线</span> · 提交的任务将排队等待执行器启动。
        </p>
      )}
    </AppLayout>

    <DataPreviewDrawer
      open={previewOpen}
      onClose={() => setPreviewOpen(false)}
      products={currentProducts}
      options={options}
      profileId={previewProfile}
      onDetail={showDetail}
    />
    <Drawer
      title={detail?.title}
      width={650}
      open={!!detail}
      onClose={() => setDetail(undefined)}
      closable={{ 'aria-label': '关闭数据详情' }}
      extra={(
        <Button
          icon={<DownloadOutlined />}
          onClick={() => exportResult(detail?.value, 'analysis-detail')}
        >
          下载 JSON
        </Button>
      )}
    >
      <pre className="json-view">{JSON.stringify(detail?.value, null, 2)}</pre>
    </Drawer>
    <Modal
      title={pendingDelete?.kind === 'data' ? '删除这次导入的数据？' : '删除任务记录？'}
      open={!!pendingDelete && section !== 'tasks'}
      onCancel={() => {
        if (!deleteBusy) setPendingDelete(undefined);
      }}
      onOk={() => void executeDelete()}
      okText={pendingDelete?.kind === 'data' ? '删除导入数据' : '删除记录'}
      cancelText="取消"
      okButtonProps={{ danger: true, loading: deleteBusy }}
      destroyOnHidden
    >
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
