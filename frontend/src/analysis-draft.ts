import dayjs from 'dayjs';
import type { AnalysisParameters, Options, Product, Quality } from './types';

export interface AnalysisDraft {
  dataset: string;
  productIds: string[];
  profileId?: number;
  current?: [string, string];
  baseline?: [string, string];
  rangeConfirmed?: boolean;
  note: string;
  agentQuestion?: string;
}

export const DEFAULT_ANALYSIS_MODEL = 'Pro/deepseek-ai/DeepSeek-V3.2';

/** 优先使用产品默认的 V3.2；旧数据库没有该模型时回退到服务端默认版本。 */
export function defaultProfileId(options: Options): number | undefined {
  return options.profiles.find(profile => profile.requested_model === DEFAULT_ANALYSIS_MODEL)?.profile_id
    ?? options.selected_profile_id
    ?? options.profiles[0]?.profile_id;
}

export const emptyDraft = (): AnalysisDraft => ({ dataset: '', productIds: [], note: '' });

export const datasetKey = (product: Pick<Product, 'source' | 'dataset_version'>): string => (
  JSON.stringify([product.source, product.dataset_version])
);

export function datasets(products: Product[]): Array<{
  key: string;
  source: string;
  version: string;
}> {
  const keyedDatasets = products.map(product => {
    const key = datasetKey(product);
    return [
      key,
      { key, source: product.source, version: product.dataset_version },
    ] as const;
  });
  return [...new Map(keyedDatasets).values()];
}

export function analysisDates(
  draft: AnalysisDraft,
  quality?: Quality,
  options?: Options,
): { current: [string, string]; baseline: [string, string] } {
  const dataset = options?.datasets?.find(item => datasetKey(item) === draft.dataset);
  // 有数据集目录时只使用当前数据集的日期，避免继承其他数据集的全库时间。
  const latest = dayjs((options?.datasets ? dataset?.published_at_end : quality?.published_at_end)?.slice(0, 10));
  const currentEnd = latest.isValid() ? latest : dayjs();
  let current: [string, string] = draft.current ?? [
    dataset?.published_at_start?.slice(0, 10) ?? currentEnd.startOf('month').format('YYYY-MM-DD'),
    currentEnd.format('YYYY-MM-DD'),
  ];
  const start = dataset?.published_at_start?.slice(0, 10);
  const end = dataset?.published_at_end?.slice(0, 10);
  // 旧版曾把全库跨度冻结到单数据集草稿；仅收窄覆盖整个数据集的旧窗口。
  const staleFullRange = !draft.rangeConfirmed && !!(start && end && current[0] <= start && current[1] >= end
    && (current[0] < start || current[1] > end));
  if (staleFullRange) current = [start!, end!];
  const currentMonth = dayjs(current[0]).startOf('month');
  const baseline: [string, string] = (!staleFullRange && draft.baseline) || [
    currentMonth.subtract(1, 'month').format('YYYY-MM-DD'),
    currentMonth.subtract(1, 'day').format('YYYY-MM-DD'),
  ];
  return { current, baseline };
}

export function analysisParameters(
  draft: AnalysisDraft,
  options: Options,
  quality?: Quality,
): AnalysisParameters | undefined {
  const product = options.products.find(item => datasetKey(item) === draft.dataset);
  const profile = options.profiles.find(item => item.profile_id === draft.profileId);
  const { current, baseline } = analysisDates(draft, quality, options);

  const invalidDates = current[0] > current[1]
    || baseline[0] > baseline[1]
    || baseline[1] >= current[0];
  if (!product || !profile || invalidDates) return undefined;

  return {
    model: profile.requested_model,
    base_url: profile.base_url,
    prompt_version: profile.prompt_version,
    source: product.source,
    dataset_version: product.dataset_version,
    product_ids: draft.productIds,
    date_from: current[0],
    date_to: current[1],
    baseline_from: baseline[0],
    baseline_to: baseline[1],
  };
}
