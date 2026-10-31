import { useEffect, useState } from 'react';
import { Alert, Button, DatePicker, Popover, Select, Spin, Tag, Tooltip } from 'antd';
import dayjs from 'dayjs';
import { DatabaseOutlined, SlidersOutlined } from '@ant-design/icons';
import { analysisDates, analysisParameters, type AnalysisDraft } from '../../analysis-draft';
import { sampleState } from '../../agent-report';
import type { Options, Quality } from '../../types';
import { DatasetSelector, ProductSelector } from '../datasets';
import { datasetSelectOptions, productSelectOptions, productsForDataset } from '../datasets/model/dataset-options';
import { useProfileSummaries } from '../analysis/use-profile-summaries';
import DatasetSummary from '../datasets/components/DatasetSummary';

export type AssistantScope = Pick<AnalysisDraft, 'dataset' | 'productIds' | 'profileId' | 'current' | 'baseline' | 'rangeConfirmed'>;

export default function AssistantScopeBar({ draft, options, quality, disabled, refreshKey, editing, onEditing, onApply }: {
  draft: AnalysisDraft; options: Options; quality?: Quality; disabled: boolean; refreshKey: number;
  editing: boolean; onEditing: (value: boolean) => void; onApply: (scope: AssistantScope) => void;
}) {
  const dates = analysisDates(draft, quality, options);
  const applied = { dataset: draft.dataset, productIds: draft.productIds, profileId: draft.profileId,
    current: dates.current, baseline: dates.baseline, ...(draft.rangeConfirmed ? { rangeConfirmed: true } : {}) };
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
  const pendingProfile = options.profiles.find(item => item.profile_id === pending.profileId);
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
  const compactProduct = draft.productIds.length
    ? selectedProducts.length > 1 ? `${selectedProducts[0]?.product_title || selectedProducts[0]?.product_id || draft.productIds[0]} 等 ${selectedProducts.length} 款`
      : selectedProducts[0]?.product_title || selectedProducts[0]?.product_id || draft.productIds[0]
    : `全部商品（${selectedProducts.length} 款）`;
  const compactDataset = displayDataset(draft.dataset);
  function update(update: Partial<AssistantScope>) { setPending(current => ({ ...current, ...update })); }
  function apply() {
    if (!pendingValid) return;
    onApply({ ...pending, productIds: [...pending.productIds], current: [...pendingDates.current], baseline: [...pendingDates.baseline] });
    onEditing(false);
  }
  const scopeEditor = <div className="assistant-scope-editor">
    <div className="assistant-scope-editor-heading">
      <div><strong>修改分析范围</strong><span>只影响后续新问题，历史消息保留原范围</span></div>
      <Button size="small" aria-label="取消" onClick={() => onEditing(false)}>取消</Button>
    </div>
    <div className="assistant-scope-editor-controls">
      <div><label htmlFor="assistant-dataset">数据集</label><DatasetSelector id="assistant-dataset" value={pending.dataset}
        options={datasetSelectOptions(options.products)} disabled={disabled} ariaLabel="助手数据集"
        onChange={dataset => update({ dataset, productIds: [], current: undefined, baseline: undefined, rangeConfirmed: false })} /></div>
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
        onChange={value => { if (value?.[0] && value[1]) update({ current: [value[0].format('YYYY-MM-DD'), value[1].format('YYYY-MM-DD')], rangeConfirmed: true }); }} /></div>
      <div><label>基线窗口（UTC）</label><DatePicker.RangePicker aria-label="助手基线窗口"
        classNames={{ popup: { root: 'assistant-scope-calendar' } }}
        value={[dayjs(pendingDates.baseline[0]), dayjs(pendingDates.baseline[1])]} allowClear={false} disabled={disabled}
        onChange={value => { if (value?.[0] && value[1]) update({ baseline: [value[0].format('YYYY-MM-DD'), value[1].format('YYYY-MM-DD')] }); }} /></div>
    </div>
    <div className="assistant-scope-popover-summary">
      <div className="assistant-scope-metrics" aria-label="当前范围统计">
        <span>当前评论 <strong>{summary?.review_count.toLocaleString() ?? '—'}</strong></span>
        <span>有效正文 <strong>{summary?.eligible_text_reviews.toLocaleString() ?? '—'}</strong></span>
        {availability && <Tag color={availability.color}>{availability.label}</Tag>}
        <span>标注覆盖率 <strong>{summary?.labeling_coverage == null ? '—' : `${(summary.labeling_coverage * 100).toFixed(1)}%`}</strong></span>
      </div>
      {availability && <span className="assistant-scope-availability">{availability.reason}</span>}
      {summaryState.loading && <div className="assistant-scope-popover-status"><Spin size="small" /> 正在读取当前范围覆盖率…</div>}
      {error && <Alert type="warning" showIcon message={`覆盖率读取失败：${error}`} action={<Button size="small" aria-label="重试覆盖率" onClick={() => setRevision(value => value + 1)}>重试</Button>} />}
    </div>
    <details className="assistant-scope-advanced">
      <summary>高级设置</summary>
      <dl className="assistant-scope-values">
        <div><dt>标注版本</dt><dd>{pendingProfile ? `${pendingProfile.requested_model} · ${pendingProfile.prompt_version}` : '未选择'}</dd></div>
        <div><dt>完整数据日期</dt><dd>{pendingDates.current.join(' — ')}</dd></div>
        <div><dt>基线窗口</dt><dd>{pendingDates.baseline.join(' — ')}</dd></div>
      </dl>
      <DatasetSummary dataset={pending.dataset} options={options} compact />
    </details>
    {options.datasets?.some(item => JSON.stringify([item.source, item.dataset_version]) === pending.dataset
      && !!item.published_at_start && pendingDates.baseline[1] < item.published_at_start.slice(0, 10))
      && <p className="muted">基线早于数据集最早评论，对比可能无可用数据；普通问题仅查询当前窗口。</p>}
    {!pendingValid && <Alert className="assistant-scope-edit-warning" type="warning" showIcon
      message="请选择可用数据集与标注版本；基线结束日期必须早于当前窗口开始日期。" />}
    <div className="assistant-scope-editor-footer">
      <Button type="primary" disabled={!pendingValid || disabled} onClick={apply}>应用范围</Button>
    </div>
  </div>;

  // 外部数据集或目录改变后，丢弃尚未应用的编辑，避免覆盖外部选择。
  useEffect(() => { setPending(applied); onEditing(false); }, [appliedKey, options]);

  return <Popover open={editing} onOpenChange={open => { if (!disabled) onEditing(open); }} trigger="click" placement="topRight"
    overlayClassName="assistant-scope-popover" content={scopeEditor} destroyOnHidden>
    <section className="assistant-scope-bar" aria-label="助手分析范围">
      <div className="assistant-scope-identity">
        <DatabaseOutlined aria-hidden="true" />
        <span className="assistant-scope-dataset" title={compactDataset}>{compactDataset}</span>
        <span className="assistant-scope-separator" aria-hidden="true">/</span>
        <Tooltip title={displayProducts(applied)}>
          <span className="assistant-scope-product">{compactProduct}</span>
        </Tooltip>
      </div>
      <Button size="small" aria-label="修改范围" icon={<SlidersOutlined />} disabled={disabled} onClick={() => onEditing(true)}>修改范围</Button>
    </section>
  </Popover>;
}

