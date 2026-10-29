import { useEffect, useState } from 'react';
import { errorMessage } from '../../api';
import { analysisApi } from '../../shared/api/analysis-api';
import type { Profile, Summary } from '../../types';

export interface ProfileSummaryState {
  loading: boolean;
  summaries: Record<number, Summary>;
  errors: Record<number, string>;
}

/** 读取当前分析范围内每个标注版本的真实覆盖率和负面数量。 */
export function useProfileSummaries(
  profiles: Profile[],
  dataset: string,
  productIds: string[],
  dateFrom: string,
  dateTo: string,
  refreshKey = 0,
): ProfileSummaryState {
  const [state, setState] = useState<ProfileSummaryState & { key: string }>({ key: '', loading: false, summaries: {}, errors: {} });
  const key = JSON.stringify({ profiles, dataset, productIds, dateFrom, dateTo, refreshKey });
  const enabled = !!(profiles.length && dataset && dateFrom && dateTo);

  useEffect(() => {
    if (!profiles.length || !dataset || !dateFrom || !dateTo) {
      setState({ key, loading: false, summaries: {}, errors: {} });
      return;
    }
    let parsedDataset: [string, string];
    try {
      const value: unknown = JSON.parse(dataset);
      if (!Array.isArray(value) || value.length !== 2 || value.some(item => typeof item !== 'string')) throw new Error('数据集选择无效');
      parsedDataset = value as [string, string];
    } catch {
      setState({ key, loading: false, summaries: {}, errors: {} });
      return;
    }
    const controller = new AbortController();
    setState({ key, loading: true, summaries: {}, errors: {} });
    const requests = profiles.map(async profile => {
      try {
        const result = await analysisApi.trend({
          model: profile.requested_model,
          base_url: profile.base_url,
          prompt_version: profile.prompt_version,
          source: parsedDataset[0],
          dataset_version: parsedDataset[1],
          product_ids: productIds,
          date_from: dateFrom,
          date_to: dateTo,
        }, controller.signal);
        return { profileId: profile.profile_id, summary: result.summary };
      } catch (error) {
        return { profileId: profile.profile_id, error: errorMessage(error) };
      }
    });
    void Promise.all(requests).then(results => {
      if (controller.signal.aborted) return;
      const summaries: Record<number, Summary> = {};
      const errors: Record<number, string> = {};
      results.forEach(result => {
        if ('summary' in result && result.summary) summaries[result.profileId] = result.summary;
        else errors[result.profileId] = result.error;
      });
      setState({ key, loading: false, summaries, errors });
    });
    return () => controller.abort();
  }, [key]);

  return state.key === key ? state : { loading: enabled, summaries: {}, errors: {} };
}
