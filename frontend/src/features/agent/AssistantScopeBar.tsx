import { useEffect, useState } from 'react';
import { Alert, Button, DatePicker, Select, Spin, Tag } from 'antd';
import dayjs from 'dayjs';
import { analysisDates, analysisParameters, type AnalysisDraft } from '../../analysis-draft';
import { sampleState } from '../../agent-report';
import type { Options, Quality } from '../../types';
import { DatasetSelector, ProductSelector } from '../datasets';
import { datasetSelectOptions, productSelectOptions, productsForDataset } from '../datasets/model/dataset-options';
import { useProfileSummaries } from '../analysis/use-profile-summaries';
import DatasetSummary from '../datasets/components/DatasetSummary';

export type AssistantScope = Pick<AnalysisDraft, 'dataset' | 'productIds' | 'profileId' | 'current' | 'baseline'>;

export default function AssistantScopeBar({ draft, options, quality, disabled, refreshKey, editing, onEditing, onApply }: {
  draft: AnalysisDraft; options: Options; quality?: Quality; disabled: boolean; refreshKey: number;
  editing: boolean; onEditing: (value: boolean) => void; onApply: (scope: AssistantScope) => void;
}) {
  const dates = analysisDates(draft, quality, options);
  const applied = { dataset: draft.dataset, productIds: draft.productIds, profileId: draft.profileId,
    current: dates.current, baseline: dates.baseline };
  const appliedKey = JSON.stringify(applied);
  const [pending, setPending] = useState<AssistantScope>(applied);
  const [revision, setRevision] = useState(0);
  const parameters = analysisParameters(draft, options, quality);
  const profile = options.profiles.find(item => item.profile_id === draft.profileId);
  const selectedProducts = productsForDataset(options, draft.dataset).filter(item =>
    !draft.productIds.length || draft.productIds.includes(item.product_id));
  const summaryState = useProfileSummaries(parameters && profile ? [profile] : [], draft.dataset,
    draft.productIds, dates.current[0], dates.current[1], refreshKey + revision);
  const summary = profile ? summaryState.summaries[profile.profile_id] : undefined;
  const error = profile ? summaryState.errors[profile.profile_id] : undefined;
  const availability = summary ? sampleState(summary) : undefined;
  const pendingProducts = productsForDataset(options, pending.dataset);
  const pendingDates = analysisDates({ ...pending, note: '' }, quality, options);
  const pendingValid = !!analysisParameters({ ...pending, note: '' }, options, quality)
    && pending.productIds.every(id => pendingProducts.some(item => item.product_id === id));
  const displayDataset = (value: string) => {
    const match = datasetSelectOptions(options.products).find(item => item.value === value);
    return match?.label || value || '未选择';
  };
  const displayProducts = (value: AssistantScope) => {
    const products = productsForDataset(options, value.dataset).filter(item => !value.productIds.length || value.productIds.includes(item.product_id));
    return value.productIds.length
      ? products.map(item => item.product_title || item.product_id).join('、') || value.productIds.join('、')
      : `全部商品（${products.length} 款）`;
  };

  // 顶部全局范围或目录改变后，丢弃尚未应用的编辑，避免覆盖外部选择。
  useEffect(() => { setPending(applied); onEditing(false); }, [appliedKey, options]);

  function update(update: Partial<AssistantScope>) { setPending(current => ({ ...current, ...update })); }
  function apply() {
    if (!pendingValid) return;
    onApply({ ...pending, productIds: [...pending.productIds], current: [...pendingDates.current], baseline: [...pendingDates.baseline] });
    onEditing(false);
  }

  return <section className="assistant-scope-bar" aria-label="助手分析范围">
    <div className="assistant-scope-heading">
      <strong>下次提问的分析范围</strong>
      <div className="assistant-scope-actions">
        {editing && <Button size="small" aria-label="取消" disabled={disabled} onClick={() => { setPending(applied); onEditing(false); }}>取消</Button>}
        <Button size="small" type={editing ? 'primary' : 'default'} disabled={disabled || (editing && !pendingValid)}
          onClick={() => editing ? apply() : onEditing(true)}>{editing ? '应用范围' : '切换范围'}</Button>
      </div>
    </div>
    <div className="assistant-scope-body">
      <p className="assistant-scope-summary-line">当前数据集：{displayDataset(editing ? pending.dataset : draft.dataset)} · 商品：{displayProducts(editing ? pending : applied)}</p>
      {!editing && <DatasetSummary dataset={draft.dataset} options={options} compact />}
      {editing ? <div className="assistant-scope-controls">
        <div><label htmlFor="assistant-dataset">数据集</label><DatasetSelector id="assistant-dataset" value={pending.dataset}
          options={datasetSelectOptions(options.products)} disabled={disabled} ariaLabel="助手数据集"
          onChange={dataset => update({ dataset, productIds: [], current: undefined, baseline: undefined })} /></div>
        <div><label htmlFor="assistant-profile">标注版本</label><Select id="assistant-profile" aria-label="助手标注版本"
          value={pending.profileId} disabled={disabled || !options.profiles.length} placeholder="准备数据后可选择"
          onChange={profileId => update({ profileId })} options={options.profiles.map(item => ({
            value: item.profile_id, label: `${item.requested_model} · ${item.prompt_version}`,
          }))} /></div>
        <div className="assistant-scope-products"><label htmlFor="assistant-products">商品（留空表示全部）</label><ProductSelector
          id="assistant-products" ariaLabel="助手分析商品" value={pending.productIds} options={productSelectOptions(pendingProducts)}
          disabled={disabled || !pending.dataset} onChange={productIds => update({ productIds })} /></div>
        <div><label>当前窗口（UTC）</label><DatePicker.RangePicker aria-label="助手当前窗口"
          classNames={{ popup: { root: 'assistant-scope-calendar' } }}
          value={[dayjs(pendingDates.current[0]), dayjs(pendingDates.current[1])]} allowClear={false} disabled={disabled}
          onChange={value => { if (value?.[0] && value[1]) update({ current: [value[0].format('YYYY-MM-DD'), value[1].format('YYYY-MM-DD')] }); }} /></div>
        <div><label>基线窗口（UTC）</label><DatePicker.RangePicker aria-label="助手基线窗口"
          classNames={{ popup: { root: 'assistant-scope-calendar' } }}
          value={[dayjs(pendingDates.baseline[0]), dayjs(pendingDates.baseline[1])]} allowClear={false} disabled={disabled}
          onChange={value => { if (value?.[0] && value[1]) update({ baseline: [value[0].format('YYYY-MM-DD'), value[1].format('YYYY-MM-DD')] }); }} /></div>
        {!pendingValid && <Alert className="assistant-scope-edit-warning" type="warning" showIcon
          message="请选择可用数据集与标注版本；基线结束日期必须早于当前窗口开始日期。" />}
      </div> : <details className="assistant-scope-details">
        <summary>查看完整查询范围</summary>
        <dl className="assistant-scope-values">
          <div><dt>数据集</dt><dd>{selectedProducts[0] ? `${selectedProducts[0].dataset_version} · ${selectedProducts[0].source}` : '未选择可用数据集'}</dd></div>
          <div><dt>标注版本</dt><dd>{profile ? `${profile.requested_model} · ${profile.prompt_version}` : '未选择可用标注版本'}</dd></div>
          <div><dt>商品</dt><dd>{draft.productIds.length ? selectedProducts.map(item => item.product_title || item.product_id).join('、') : `全部商品（${selectedProducts.length} 款）`}</dd></div>
          <div><dt>当前窗口（UTC）</dt><dd>{dates.current.join(' — ')}</dd></div>
          <div><dt>基线窗口（UTC）</dt><dd>{dates.baseline.join(' — ')}</dd></div>
        </dl>
      </details>}
      {editing && <DatasetSummary dataset={pending.dataset} options={options} compact />}
    </div>
    <div className="assistant-scope-status" role="status">
      {editing ? '应用范围后再发送问题；历史问题保持原查询范围。'
        : !parameters ? '请先选择可用标注版本与分析范围'
        : summaryState.loading ? <><Spin size="small" /> 正在读取当前范围覆盖率…</>
        : error ? <><span>覆盖率读取失败：{error}</span><Button size="small" onClick={() => setRevision(value => value + 1)}>重试覆盖率</Button></>
        : summary && availability ? <><Tag color={availability.color}>{availability.label}</Tag>
          <span>覆盖率 {summary.labeling_coverage == null ? '—' : `${(summary.labeling_coverage * 100).toFixed(1)}%`}（成功 {summary.successful_reviews.toLocaleString()} / 有效正文 {summary.eligible_text_reviews.toLocaleString()}）</span>
          <span>{availability.reason}</span></> : '当前范围暂无统计'}
    </div>
  </section>;
}

