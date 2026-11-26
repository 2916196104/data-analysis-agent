import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api, errorMessage } from '../api';
import {
  AGENT_DATASET,
  analysisDates,
  datasetKey,
  defaultProfileId,
  type AnalysisDraft,
} from '../analysis-draft';
import type { Health, Options, Page, Quality, TaskSummary } from '../types';

const emptyOptions: Options = {
  profiles: [],
  selected_profile_id: null,
  products: [],
  aspects: {},
  sentiments: {},
  processing_statuses: {},
  human_statuses: {},
};

interface UseWorkspaceCatalogOptions {
  dataset: string;
  taskStatus?: TaskSummary['status'];
  setDraft: Dispatch<SetStateAction<AnalysisDraft>>;
  onDatasetInvalidated: () => void;
}

export function useWorkspaceCatalog({
  dataset,
  taskStatus,
  setDraft,
  onDatasetInvalidated,
}: UseWorkspaceCatalogOptions) {
  const [options, setOptions] = useState<Options>(emptyOptions);
  const [quality, setQuality] = useState<Quality>();
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [health, setHealth] = useState<Health>();
  const [history, setHistory] = useState<Page<TaskSummary>>();
  const [historyLoading, setHistoryLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState('');
  const [datasetNotice, setDatasetNotice] = useState('');
  const [historyError, setHistoryError] = useState('');

  const datasetRef = useRef(dataset);
  const profileInitialized = useRef(false);
  const historyRequest = useRef(0);
  const loadRequest = useRef(0);
  const invalidatedRef = useRef(onDatasetInvalidated);
  datasetRef.current = dataset;
  invalidatedRef.current = onDatasetInvalidated;

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

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    const results = await Promise.allSettled([api.options(), api.quality(), api.health()] as const);
    if (requestId !== loadRequest.current) return;

    const failures: string[] = [];
    const [optionResult, qualityResult, healthResult] = results;
    if (optionResult.status === 'fulfilled') {
      const next = optionResult.value;
      const useDefaultProfile = !profileInitialized.current;
      profileInitialized.current = true;
      setOptions(next);
      if (datasetRef.current && !next.products.some(product => datasetKey(product) === datasetRef.current)) {
        setDatasetNotice(
          next.products.length
            ? '原数据集已不可用，请重新确认数据集；AI 助手仅支持 reviews_csv / reviews-csv，不会切换其他来源。'
            : '暂无可用数据集，请导入数据或确认访问权限。',
        );
        invalidatedRef.current();
      }
      setDraft(previous => {
        const currentDatasetExists = next.products.some(
          product => datasetKey(product) === previous.dataset,
        );
        const resolvedDataset = currentDatasetExists
          ? previous.dataset
          : !previous.dataset && next.products.some(product => datasetKey(product) === AGENT_DATASET)
            ? AGENT_DATASET
            : '';
        const currentDates = analysisDates(
          { ...previous, dataset: resolvedDataset },
          qualityResult.status === 'fulfilled' ? qualityResult.value : undefined,
          next,
        ).current;
        const profileExists = next.profiles.some(
          profile => profile.profile_id === previous.profileId,
        );
        return {
          ...previous,
          dataset: resolvedDataset,
          current: currentDatasetExists ? previous.current ?? currentDates : undefined,
          baseline: currentDatasetExists ? previous.baseline : undefined,
          rangeConfirmed: currentDatasetExists ? previous.rangeConfirmed : false,
          agentQuestion: currentDatasetExists ? previous.agentQuestion : undefined,
          // 失效的单商品选择必须重新确认，不能过滤成“全部商品”。
          productIds: currentDatasetExists ? previous.productIds : [],
          profileId: useDefaultProfile || !profileExists
            ? defaultProfileId(next)
            : previous.profileId,
        };
      });
    } else {
      setOptions(emptyOptions);
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
  }, [setDraft]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void fetchHistory();
    const timer = setInterval(() => {
      void fetchHistory();
    }, 8000);
    return () => clearInterval(timer);
  }, [fetchHistory, taskStatus]);

  useEffect(() => {
    const timer = setInterval(() => {
      api.health().then(setHealth).catch(() => setHealth(undefined));
    }, 10_000);
    return () => clearInterval(timer);
  }, []);

  return {
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
  };
}
