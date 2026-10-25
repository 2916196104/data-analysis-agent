import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { Alert, Button, DatePicker, Form, Input, Modal, Select, Space } from 'antd';
import { DatabaseOutlined, ThunderboltOutlined } from '@ant-design/icons';
import ReviewDashboard from './features/analysis/ReviewDashboard';
import dayjs, { type Dayjs } from 'dayjs';
import {
  analysisDates, analysisParameters, type AnalysisDraft,
} from './analysis-draft';
import type { AnalysisParameters, Options, Quality } from './types';
import { DatasetSelector, ProductSelector } from './features/datasets';
import { datasetSelectOptions, productSelectOptions, productsForDataset } from './features/datasets/model/dataset-options';

interface Values {
  note: string;
  current: [Dayjs, Dayjs];
  baseline: [Dayjs, Dayjs];
}

interface ComposerProps {
  options: Options;
  quality?: Quality;
  draft: AnalysisDraft;
  setDraft: Dispatch<SetStateAction<AnalysisDraft>>;
  busy: boolean;
  refreshKey: number;
  agentOpen: boolean;
  setAgentOpen: (open: boolean) => void;
  onSubmit: (parameters: AnalysisParameters, note: string) => void;
  onPreview: () => void;
  onLabel: () => void;
}

export default function Composer({
  options, quality, draft, setDraft, busy, refreshKey, agentOpen, setAgentOpen, onSubmit, onPreview, onLabel,
}: ComposerProps) {
  const [form] = Form.useForm<Values>();
  const advanced = useRef<HTMLDetailsElement>(null);
  const availableProducts = productsForDataset(options, draft.dataset);
  const selectedProducts = availableProducts.filter(
    product => !draft.productIds.length || draft.productIds.includes(product.product_id),
  );
  const profile = options.profiles.find(item => item.profile_id === draft.profileId);
  const dates = analysisDates(draft, quality);
  const datasetOptions = datasetSelectOptions(options.products);
  const productOptions = productSelectOptions(availableProducts);
  const profileOptions = options.profiles.map(item => ({
    value: item.profile_id,
    label: `${item.requested_model} · ${item.prompt_version}`,
  }));

  useEffect(() => {
    form.setFieldsValue({
      current: dates.current.map(date => dayjs(date)) as [Dayjs, Dayjs],
      baseline: dates.baseline.map(date => dayjs(date)) as [Dayjs, Dayjs],
    });
  }, [form, dates.current[0], dates.current[1], dates.baseline[0], dates.baseline[1]]);

  const parameters = analysisParameters(draft, options, quality);
  const selectedDefaultProfile = draft.profileId === options.selected_profile_id;
  const selectedProductsUnlabeled = selectedProducts.length > 0
    && selectedProducts.every(product => product.success_count === 0);
  const needsPreparation = !profile || (selectedDefaultProfile && selectedProductsUnlabeled);

  function handleValuesChange(changed: Partial<Values>): void {
    const next = { ...draft };
    if (changed.current?.every(Boolean)) {
      next.current = changed.current.map(date => date.format('YYYY-MM-DD')) as [string, string];
      if (!draft.baseline) {
        const automaticBaseline = analysisDates(next, quality).baseline.map(date => dayjs(date));
        form.setFieldValue('baseline', automaticBaseline);
      }
    }
    if (changed.baseline?.every(Boolean)) {
      next.baseline = changed.baseline.map(date => date.format('YYYY-MM-DD')) as [string, string];
    }
    if (changed.note !== undefined) next.note = changed.note;
    setDraft(next);
  }

  function handleSubmit(): void {
    if (parameters) onSubmit(parameters, draft.note.trim() || '评论趋势与负面问题分析');
  }

  function resetBaseline(): void {
    const next = { ...draft, baseline: undefined };
    setDraft(next);
    form.setFieldValue('baseline', analysisDates(next, quality).baseline.map(date => dayjs(date)));
  }

  function submitAgent(): void {
    const question = draft.agentQuestion?.trim();
    if (!parameters || !question) return;
    setAgentOpen(false);
    onSubmit({ ...parameters, question }, question);
  }

  return <div className="composer-wrap">
    <div className="review-page-heading"><div><h1>评论分析</h1><p>{availableProducts[0]?.dataset_version || '尚未选择数据集'} · {availableProducts.length} 款商品</p></div>
      {!availableProducts.length ? <Button type="primary" disabled>开始分析</Button> : needsPreparation ? <Button type="primary" onClick={onLabel}>准备数据</Button> : <Button aria-label="开始分析" type="primary" htmlType="submit" form="review-analysis-form" loading={busy}>开始分析</Button>}
    </div>
    <Form<Values>
      id="review-analysis-form"
      form={form}
      layout="vertical"
      requiredMark={false}
      initialValues={{
        note: draft.note,
        current: dates.current.map(date => dayjs(date)),
        baseline: dates.baseline.map(date => dayjs(date)),
      }}
      onKeyDown={event => {
      // 日期选择器中的 Enter 用于确认日期，不能提交外层表单。
      const insideDatePicker = event.target instanceof HTMLInputElement
        && event.target.closest('.ant-picker');
      if (event.key === 'Enter' && insideDatePicker) event.preventDefault();
      }}
      onValuesChange={handleValuesChange}
      onFinishFailed={() => { if (advanced.current) advanced.current.open = true; }}
      onFinish={handleSubmit}
    >
      <div className="scope-card review-filter">
        <div className="review-filter-caption">筛选条件</div>
        <div className="review-filter-grid">
          <Form.Item>
            <ProductSelector value={draft.productIds} options={productOptions} disabled={busy || !draft.dataset} onChange={productIds => setDraft(previous => ({ ...previous, productIds }))} />
          </Form.Item>
          <Form.Item name="current" rules={[{ required: true, message: '请选择分析时间' }]}><DatePicker.RangePicker aria-label="当前窗口" title="时间范围（UTC）" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
        </div>
      </div>
      <ReviewDashboard parameters={parameters} productCount={selectedProducts.length} hasProducts={!!availableProducts.length} refreshKey={refreshKey} />
      <section className="review-insight">
        <Button type="text" aria-label="Agent 问答" icon={<ThunderboltOutlined />} disabled={busy || !parameters} onClick={() => setAgentOpen(true)}>AI 洞察</Button>
        <p>解释变化、查找负面问题并关联原始评论。</p>
        {!parameters && <span className="muted">选择可用标注版本与分析范围后启用</span>}
      </section>
        <details className="advanced-settings" ref={advanced}>
          <summary>高级设置 <span>数据集、对比时间、标注版本、备注</span></summary>
          <div className="advanced-content">
            <Form.Item label="数据集">
              <DatasetSelector value={draft.dataset} options={datasetOptions} disabled={busy} onChange={dataset => setDraft(previous => ({ ...previous, dataset, productIds: [] }))} />
            </Form.Item>
            <p className="scope-summary">{selectedProducts.length} 款商品 · 共 {selectedProducts.reduce((sum, product) => sum + product.review_count, 0).toLocaleString()} 条评论（目录全部日期）<br />对比 {dates.baseline[0]} 至 {dates.baseline[1]} · 日期按 UTC</p>
            <Form.Item label="对比时间" name="baseline" dependencies={['current']} rules={[{ required: true, message: '请选择对比时间' }, ({ getFieldValue }) => ({ validator(_, value) {
              const current = getFieldValue('current');
              return value && current && !value[1].isBefore(current[0], 'day') ? Promise.reject(new Error('对比结束日期必须早于分析开始日期')) : Promise.resolve();
            } })]}><DatePicker.RangePicker aria-label="基线窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
            {draft.baseline && <Button className="reset-baseline" type="link" size="small" disabled={busy} onClick={resetBaseline}>恢复自动对比上一个月</Button>}
            <Form.Item label="标注版本">
              <Select
                aria-label="标注版本"
                value={draft.profileId}
                disabled={busy}
                placeholder="准备数据后自动选择"
                onChange={profileId => setDraft(previous => ({ ...previous, profileId }))}
                options={profileOptions}
              />
            </Form.Item>
            <Form.Item label="分析备注（可选）" name="note" extra="用于识别本次分析，不改变统计内容。"><Input aria-label="分析问题备注" placeholder="例如：十月包装问题复盘" maxLength={1000} disabled={busy} /></Form.Item>
            <div className="submit-row"><Button aria-label="预览数据" icon={<DatabaseOutlined />} disabled={!availableProducts.length} onClick={onPreview}>预览数据</Button></div>
          </div>
        </details>
        {!options.products.length && <Alert className="spaced" type="info" showIcon message="从数据管理或侧栏导入评论后开始分析" />}
        {!!availableProducts.length && needsPreparation && <Alert className="spaced" type="info" showIcon message="先准备数据" description="所选范围尚无可用的成功标注。确认本批处理数量后，模型会提取评论观点。" />}
    </Form>
    <Modal title="只读评论 Agent" open={agentOpen} onCancel={() => setAgentOpen(false)} onOk={submitAgent}
      okText="提交问题" cancelText="取消" okButtonProps={{ disabled: !parameters || !draft.agentQuestion?.trim(), loading: busy }}>
      <p>模型将查询当前选择范围的已有标注，返回统计和原文证据。</p>
      <p className="muted">{selectedProducts.length} 款商品 · {dates.current[0]} 至 {dates.current[1]}（UTC）</p>
      <Input.TextArea aria-label="评论分析问题" value={draft.agentQuestion || ''} rows={4} maxLength={4000} showCount
        placeholder="例如：按日查看评论趋势，对比所选商品，并给出包装负面评论原文。"
        onChange={event => setDraft(previous => ({ ...previous, agentQuestion: event.target.value }))} />
      <Space className="spaced" wrap>
        {['按日查看评论趋势', '对比所选商品的负面评论占比', '查询包装负面评论的原文证据'].map(question =>
          <Button key={question} size="small" onClick={() => setDraft(previous => ({ ...previous, agentQuestion: question }))}>{question}</Button>)}
      </Space>
    </Modal>
  </div>;
}
