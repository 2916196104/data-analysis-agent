import dayjs from 'dayjs';
import type { AnalysisParameters, Options, Product, Quality } from './types';

export interface AnalysisDraft {
  dataset: string;
  productIds: string[];
  profileId?: number;
  current?: [string, string];
  baseline?: [string, string];
  note: string;
  agentQuestion?: string;
}

export const emptyDraft = (): AnalysisDraft => ({ dataset: '', productIds: [], note: '' });

export const datasetKey = (product: Product): string => (
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
): { current: [string, string]; baseline: [string, string] } {
  const latest = dayjs(quality?.published_at_end?.slice(0, 10));
  const currentEnd = latest.isValid() ? latest : dayjs();
  const current: [string, string] = draft.current ?? [
    currentEnd.startOf('month').format('YYYY-MM-DD'),
    currentEnd.format('YYYY-MM-DD'),
  ];
  const currentMonth = dayjs(current[0]).startOf('month');
  const baseline: [string, string] = draft.baseline ?? [
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
  const { current, baseline } = analysisDates(draft, quality);

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
