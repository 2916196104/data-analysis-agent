import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { Alert, Button, DatePicker, Form, Input, Select } from 'antd';
import { ArrowRightOutlined, DatabaseOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import { analysisDates, analysisParameters, datasetKey, datasets, type AnalysisDraft } from './analysis-draft';
import type { AnalysisParameters, Options, Quality } from './types';

interface Values { note: string; current: [Dayjs, Dayjs]; baseline: [Dayjs, Dayjs] }
export default function Composer({ options, quality, draft, setDraft, busy, onSubmit, onPreview, onImport, onLabel }: {
  options: Options; quality?: Quality; draft: AnalysisDraft; setDraft: Dispatch<SetStateAction<AnalysisDraft>>;
  busy: boolean; onSubmit: (parameters: AnalysisParameters, note: string) => void; onPreview: () => void; onImport: () => void; onLabel: () => void;
}) {
  const [form] = Form.useForm<Values>();
  const advanced = useRef<HTMLDetailsElement>(null);
  const available = options.products.filter(p => datasetKey(p) === draft.dataset);
  const selected = available.filter(p => !draft.productIds.length || draft.productIds.includes(p.product_id));
  const profile = options.profiles.find(p => p.profile_id === draft.profileId);
  const dates = analysisDates(draft, quality);
  useEffect(() => {
    form.setFieldsValue({ current: dates.current.map(d => dayjs(d)) as [Dayjs, Dayjs], baseline: dates.baseline.map(d => dayjs(d)) as [Dayjs, Dayjs] });
  }, [form, dates.current[0], dates.current[1], dates.baseline[0], dates.baseline[1]]);
  const parameters = analysisParameters(draft, options, quality);
  const needsPreparation = !profile || (draft.profileId === options.selected_profile_id && selected.length > 0 && selected.every(p => p.success_count === 0));
  return <div className="composer-wrap">
    <div className="welcome"><h1>评论分析</h1><p>选好数据与时间，即可查看口碑变化、商品差异和负面问题。</p></div>
    <Form<Values> form={form} layout="vertical" requiredMark={false} initialValues={{ note: draft.note,
      current: dates.current.map(d => dayjs(d)), baseline: dates.baseline.map(d => dayjs(d)),
    }} onKeyDown={event => {
      // Date-picker Enter confirms the date; it must not submit the surrounding form.
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement && event.target.closest('.ant-picker')) event.preventDefault();
    }} onValuesChange={(changed: Partial<Values>) => {
      const next = { ...draft };
      if (changed.current?.every(Boolean)) {
        next.current = changed.current.map(d => d.format('YYYY-MM-DD')) as [string, string];
        if (!draft.baseline) form.setFieldValue('baseline', analysisDates(next, quality).baseline.map(d => dayjs(d)));
      }
      if (changed.baseline?.every(Boolean)) next.baseline = changed.baseline.map(d => d.format('YYYY-MM-DD')) as [string, string];
      if (changed.note !== undefined) next.note = changed.note;
      setDraft(next);
    }} onFinishFailed={() => { if (advanced.current) advanced.current.open = true; }} onFinish={() => {
      if (parameters) onSubmit(parameters, draft.note.trim() || '评论趋势与负面问题分析');
    }}>
      <div className="scope-card">
        <div className="scope-title"><strong>分析范围</strong><Button type="link" size="small" onClick={onImport}>导入新数据</Button></div>
        <div className="form-grid">
          <Form.Item label="数据集"><Select aria-label="数据集" value={draft.dataset || undefined} placeholder="请选择数据集" disabled={busy} onChange={dataset => setDraft(previous => ({ ...previous, dataset, productIds: [] }))} options={datasets(options.products).map(d => ({ value: d.key, label: datasets(options.products).filter(other => other.version === d.version).length > 1 ? `${d.version} · ${d.source}` : d.version }))} /></Form.Item>
          <Form.Item label="商品"><Select aria-label="分析商品" mode="multiple" value={draft.productIds} maxCount={100} maxTagCount="responsive" optionFilterProp="label" placeholder="全部商品" disabled={busy || !draft.dataset} onChange={productIds => setDraft(previous => ({ ...previous, productIds }))} options={available.map(p => ({ value: p.product_id, label: `${p.product_title || p.product_id}（${p.review_count} 条）` }))} /></Form.Item>
          <Form.Item label="分析时间" name="current" className="span-two" rules={[{ required: true, message: '请选择分析时间' }]}><DatePicker.RangePicker aria-label="当前窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
        </div>
        <p className="scope-summary">{selected.length} 款商品 · 共 {selected.reduce((sum, p) => sum + p.review_count, 0).toLocaleString()} 条评论（全部日期）<br />对比 {dates.baseline[0]} 至 {dates.baseline[1]} · 日期按 UTC</p>
        <details className="advanced-settings" ref={advanced}>
          <summary>高级设置 <span>对比时间、标注版本、备注</span></summary>
          <div className="advanced-content">
            <Form.Item label="对比时间" name="baseline" dependencies={['current']} rules={[{ required: true, message: '请选择对比时间' }, ({ getFieldValue }) => ({ validator(_, value) {
              const current = getFieldValue('current');
              return value && current && !value[1].isBefore(current[0], 'day') ? Promise.reject(new Error('对比结束日期必须早于分析开始日期')) : Promise.resolve();
            } })]}><DatePicker.RangePicker aria-label="基线窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
            {draft.baseline && <Button className="reset-baseline" type="link" size="small" disabled={busy} onClick={() => {
              const next = { ...draft, baseline: undefined }; setDraft(next);
              form.setFieldValue('baseline', analysisDates(next, quality).baseline.map(d => dayjs(d)));
            }}>恢复自动对比上一个月</Button>}
            <Form.Item label="标注版本"><Select aria-label="标注版本" value={draft.profileId} disabled={busy} placeholder="准备数据后自动选择" onChange={profileId => setDraft(previous => ({ ...previous, profileId }))} options={options.profiles.map(p => ({ value: p.profile_id, label: `${p.requested_model} · ${p.prompt_version}` }))} /></Form.Item>
            <Form.Item label="分析备注（可选）" name="note" extra="用于识别本次分析，不改变统计内容。"><Input aria-label="分析问题备注" placeholder="例如：十月包装问题复盘" maxLength={1000} disabled={busy} /></Form.Item>
          </div>
        </details>
        {!options.products.length && <Alert className="spaced" type="info" showIcon message="先导入评论数据，再开始分析" />}
        {!!available.length && needsPreparation && <Alert className="spaced" type="info" showIcon message="先准备数据" description="所选范围尚无可用的成功标注。确认本批处理数量后，模型会提取评论观点。" />}
        <div className="submit-row">
          <Button aria-label="预览数据" icon={<DatabaseOutlined />} disabled={!available.length} onClick={onPreview}>预览数据</Button>
          {!available.length ? <Button type="primary" onClick={onImport}>导入数据</Button> : needsPreparation ? <Button type="primary" onClick={onLabel}>准备数据</Button> : <Button aria-label="开始分析" type="primary" htmlType="submit" loading={busy} icon={<ArrowRightOutlined />} iconPosition="end">开始分析</Button>}
        </div>
      </div>
    </Form>
  </div>;
}
