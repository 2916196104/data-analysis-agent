import { describe, expect, it } from 'vitest';
import { analysisDates, analysisParameters, datasetKey, emptyDraft } from './analysis-draft';
import { readHistory, writeHistory } from './local-history';
import type { Options, Quality } from './types';

const quality: Quality = { review_count: 48, product_count: 1, published_at_start: '2023-01-01', published_at_end: '2023-02-24T00:00:00Z' };
const options: Options = { profiles: [{ profile_id: 2, requested_model: 'model', base_url: 'http://local', prompt_version: 'v2' }],
  selected_profile_id: 2, products: [{ key: 'A', source: 'source', dataset_version: 'v1', product_id: 'A', product_title: null,
    review_count: 48, pending_count: 0, success_count: 48 }], aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };

describe('分析范围与衔接', () => {
  it('按数据最新月份给出分析时间和上月对比，跨年仍有效', () => {
    expect(analysisDates(emptyDraft(), quality)).toEqual({ current: ['2023-02-01', '2023-02-24'], baseline: ['2023-01-01', '2023-01-31'] });
    expect(analysisDates({ ...emptyDraft(), current: ['2023-01-01', '2023-01-20'] }, quality).baseline).toEqual(['2022-12-01', '2022-12-31']);
  });
  it('保留人工选择的时间和对比范围', () => {
    const draft = { ...emptyDraft(), current: ['2023-02-05', '2023-02-20'] as [string, string], baseline: ['2022-12-01', '2022-12-31'] as [string, string] };
    expect(analysisDates(draft, quality)).toEqual({ current: draft.current, baseline: draft.baseline });
  });
  it('快捷分析使用继承的商品与标注版本，拒绝重叠时间和失效版本', () => {
    const draft = { ...emptyDraft(), dataset: datasetKey(options.products[0]), productIds: ['A'], profileId: 2 };
    expect(analysisParameters(draft, options, quality)).toMatchObject({ product_ids: ['A'], model: 'model', prompt_version: 'v2',
      source: 'source', dataset_version: 'v1', date_from: '2023-02-01', baseline_to: '2023-01-31' });
    expect(analysisParameters({ ...draft, baseline: ['2023-02-01', '2023-02-24'] }, options, quality)).toBeUndefined();
    expect(analysisParameters({ ...draft, profileId: 99 }, options, quality)).toBeUndefined();
  });
  it('刷新后恢复分析选择和备注', () => {
    const saved = { selected: 'task-1', notes: { 'task-1': '复盘' }, draft: { ...emptyDraft(), dataset: 'dataset', productIds: ['A'],
      profileId: 2, current: ['2023-02-01', '2023-02-24'] as [string, string], note: '复盘' } };
    expect(writeHistory(saved)).toBe(true);
    expect(readHistory()).toEqual(saved);
  });
  it('保留尚未提交的 Agent 问题', () => {
    const saved = { selected: null, notes: {}, draft: { ...emptyDraft(), agentQuestion: '查询包装原文证据' } };
    writeHistory(saved);
    expect(readHistory().draft.agentQuestion).toBe('查询包装原文证据');
  });
  it('读取旧版记录和损坏日期时保留历史并恢复可用默认值', () => {
    localStorage.clear();
    writeHistory({ selected: 'legacy', notes: {}, draft: emptyDraft() });
    const key = localStorage.key(0)!;
    localStorage.setItem(key, JSON.stringify({ selected: 'legacy', notes: { legacy: '旧分析' } }));
    expect(readHistory()).toEqual({ selected: 'legacy', notes: { legacy: '旧分析' }, draft: emptyDraft() });
    localStorage.setItem(key, JSON.stringify({ draft: { current: ['2023-02-30', '2023-03-01'], baseline: ['2023-02-01', '2023-01-01'] } }));
    expect(readHistory().draft.current).toBeUndefined();
    expect(readHistory().draft.baseline).toBeUndefined();
  });
});
