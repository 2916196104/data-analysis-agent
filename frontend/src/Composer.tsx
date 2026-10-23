import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { Alert, Button, DatePicker, Form, Input, Select } from 'antd';
import { ArrowRightOutlined, DatabaseOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  analysisDates, analysisParameters, datasetKey, datasets, type AnalysisDraft,
} from './analysis-draft';
import type { AnalysisParameters, Options, Quality } from './types';

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
  onSubmit: (parameters: AnalysisParameters, note: string) => void;
  onPreview: () => void;
  onLabel: () => void;
}

export default function Composer({
  options, quality, draft, setDraft, busy, onSubmit, onPreview, onLabel,
}: ComposerProps) {
  const [form] = Form.useForm<Values>();
  const advanced = useRef<HTMLDetailsElement>(null);
  const availableProducts = options.products.filter(
    product => datasetKey(product) === draft.dataset,
  );
  const selectedProducts = availableProducts.filter(
    product => !draft.productIds.length || draft.productIds.includes(product.product_id),
  );
  const profile = options.profiles.find(item => item.profile_id === draft.profileId);
  const dates = analysisDates(draft, quality);
  const availableDatasets = datasets(options.products);
  const datasetOptions = availableDatasets.map(item => ({
    value: item.key,
    label: availableDatasets.filter(other => other.version === item.version).length > 1
      ? `${item.version} · ${item.source}`
      : item.version,
  }));
  const productOptions = availableProducts.map(product => ({
    value: product.product_id,
    label: `${product.product_title || product.product_id}（${product.review_count} 条）`,
  }));
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

  return <div className="composer-wrap">
    <div className="welcome"><h1>评论分析</h1><p>选好数据与时间，即可查看口碑变化、商品差异和负面问题。</p></div>
    <Form<Values>
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
      <div className="scope-card">
        <div className="scope-title"><strong>分析范围</strong></div>
        <div className="form-grid">
          <Form.Item label="数据集">
            <Select
              aria-label="数据集"
              value={draft.dataset || undefined}
              placeholder="请选择数据集"
              disabled={busy}
              onChange={dataset => setDraft(previous => ({ ...previous, dataset, productIds: [] }))}
              options={datasetOptions}
            />
          </Form.Item>
          <Form.Item label="商品">
            <Select
              aria-label="分析商品"
              mode="multiple"
              value={draft.productIds}
              maxCount={100}
              maxTagCount="responsive"
              optionFilterProp="label"
              placeholder="全部商品"
              disabled={busy || !draft.dataset}
              onChange={productIds => setDraft(previous => ({ ...previous, productIds }))}
              options={productOptions}
            />
          </Form.Item>
          <Form.Item label="分析时间" name="current" className="span-two" rules={[{ required: true, message: '请选择分析时间' }]}><DatePicker.RangePicker aria-label="当前窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
        </div>
        <p className="scope-summary">{selectedProducts.length} 款商品 · 共 {selectedProducts.reduce((sum, product) => sum + product.review_count, 0).toLocaleString()} 条评论（全部日期）<br />对比 {dates.baseline[0]} 至 {dates.baseline[1]} · 日期按 UTC</p>
        <details className="advanced-settings" ref={advanced}>
          <summary>高级设置 <span>对比时间、标注版本、备注</span></summary>
          <div className="advanced-content">
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
          </div>
        </details>
        {!options.products.length && <Alert className="spaced" type="info" showIcon message="点击顶部“导入数据”，添加评论后开始分析" />}
        {!!availableProducts.length && needsPreparation && <Alert className="spaced" type="info" showIcon message="先准备数据" description="所选范围尚无可用的成功标注。确认本批处理数量后，模型会提取评论观点。" />}
        <div className="submit-row">
          <Button aria-label="预览数据" icon={<DatabaseOutlined />} disabled={!availableProducts.length} onClick={onPreview}>预览数据</Button>
          {!availableProducts.length ? <Button type="primary" disabled>开始分析</Button> : needsPreparation ? <Button type="primary" onClick={onLabel}>准备数据</Button> : <Button aria-label="开始分析" type="primary" htmlType="submit" loading={busy} icon={<ArrowRightOutlined />} iconPosition="end">开始分析</Button>}
        </div>
      </div>
    </Form>
  </div>;
}
