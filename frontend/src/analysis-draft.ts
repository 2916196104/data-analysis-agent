import dayjs from 'dayjs';
import type { AnalysisParameters, Options, Product, Quality } from './types';

export interface AnalysisDraft {
  dataset: string;
  productIds: string[];
  profileId?: number;
  current?: [string, string];
  baseline?: [string, string];
  note: string;
}
export const emptyDraft = (): AnalysisDraft => ({ dataset: '', productIds: [], note: '' });
export const datasetKey = (product: Product) => JSON.stringify([product.source, product.dataset_version]);
export function datasets(products: Product[]) {
  return [...new Map(products.map(p => [datasetKey(p), { key: datasetKey(p), source: p.source, version: p.dataset_version }])).values()];
}
export function analysisDates(draft: AnalysisDraft, quality?: Quality) {
  const latest = dayjs(quality?.published_at_end?.slice(0, 10));
  const end = latest.isValid() ? latest : dayjs();
  const current: [string, string] = draft.current ?? [end.startOf('month').format('YYYY-MM-DD'), end.format('YYYY-MM-DD')];
  const month = dayjs(current[0]).startOf('month');
  const baseline: [string, string] = draft.baseline ?? [month.subtract(1, 'month').format('YYYY-MM-DD'), month.subtract(1, 'day').format('YYYY-MM-DD')];
  return { current, baseline };
}
export function analysisParameters(draft: AnalysisDraft, options: Options, quality?: Quality): AnalysisParameters | undefined {
  const product = options.products.find(p => datasetKey(p) === draft.dataset);
  const profile = options.profiles.find(p => p.profile_id === draft.profileId);
  const { current, baseline } = analysisDates(draft, quality);
  if (!product || !profile || current[0] > current[1] || baseline[0] > baseline[1] || baseline[1] >= current[0]) return;
  return { model: profile.requested_model, base_url: profile.base_url, prompt_version: profile.prompt_version,
    source: product.source, dataset_version: product.dataset_version, product_ids: draft.productIds,
    date_from: current[0], date_to: current[1], baseline_from: baseline[0], baseline_to: baseline[1] };
}
