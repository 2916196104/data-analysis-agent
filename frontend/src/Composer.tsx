import { Alert, Button, DatePicker, Form, Input, Select, Tag } from 'antd';
import { ArrowRightOutlined, DatabaseOutlined, SlidersOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import type { AnalysisParameters, Options, Product, Quality } from './types';

export const datasetKey = (product: Product) => JSON.stringify([product.source, product.dataset_version]);
export function datasets(products: Product[]) {
  return [...new Map(products.map(p => [datasetKey(p), { key: datasetKey(p), source: p.source, version: p.dataset_version }])).values()];
}

interface Values { question: string; current: [Dayjs, Dayjs]; baseline: [Dayjs, Dayjs] }
export default function Composer({ options, quality, dataset, setDataset, productIds, setProductIds, profileId, setProfileId, busy, onSubmit, onPreview, onImport, onLabel }: {
  options: Options; quality?: Quality; dataset: string; setDataset: (key: string) => void;
  productIds: string[]; setProductIds: (ids: string[]) => void; profileId?: number; setProfileId: (id: number) => void;
  busy: boolean; onSubmit: (parameters: AnalysisParameters, question: string) => void; onPreview: () => void; onImport: () => void; onLabel: () => void;
}) {
  const [form] = Form.useForm<Values>();
  const available = options.products.filter(p => datasetKey(p) === dataset);
  const profile = options.profiles.find(p => p.profile_id === profileId);
  const end = dayjs(quality?.published_at_end?.slice(0, 10));
  const currentEnd = end.isValid() ? end : dayjs();
  const start = currentEnd.startOf('month');
  const templates = ['评论口碑有什么变化？', '哪些商品需要重点关注？', '负面问题集中在哪些方面？'];
  return <div className="composer-wrap">
    <div className="welcome"><span className="eyebrow">从数据出发 · 让判断有据</span><h1>让每一条评论，<br />成为更清晰的判断。</h1><p>选择数据与时间范围，查看趋势、商品差异和原文证据。</p></div>
    <Form<Values> form={form} layout="vertical" requiredMark={false} initialValues={{
      question: '', current: [start, currentEnd], baseline: [start.subtract(1, 'month'), start.subtract(1, 'day')],
    }} onFinish={values => {
      if (!profile || !available.length) return;
      onSubmit({ model: profile.requested_model, base_url: profile.base_url, prompt_version: profile.prompt_version,
        source: available[0].source, dataset_version: available[0].dataset_version, product_ids: productIds,
        date_from: values.current[0].format('YYYY-MM-DD'), date_to: values.current[1].format('YYYY-MM-DD'),
        baseline_from: values.baseline[0].format('YYYY-MM-DD'), baseline_to: values.baseline[1].format('YYYY-MM-DD'),
      }, values.question.trim() || '分析评论趋势、商品差异与负面问题');
    }}>
      <div className="question-box">
        <div className="question-label">这次想了解什么？ <Tag bordered={false}>分析备注</Tag></div>
        <Form.Item name="question" className="question-item"><Input.TextArea aria-label="分析问题备注" placeholder="例如：最近的负面反馈主要集中在哪些商品和维度？" autoSize={{ minRows: 3, maxRows: 6 }} maxLength={1000} /></Form.Item>
        <div className="question-footer"><span>当前执行固定的评论统计；此备注保存在本机，用于识别任务。</span></div>
      </div>
      <div className="suggestions">{templates.map(text => <button type="button" key={text} onClick={() => form.setFieldValue('question', text)}>{text}<ArrowRightOutlined /></button>)}</div>
      <div className="scope-card">
        <div className="scope-title"><SlidersOutlined /><strong>分析范围</strong><span className="muted">日期统一按 UTC</span></div>
        <div className="form-grid">
          <Form.Item label="数据集（来源 / 版本）"><Select aria-label="数据集" value={dataset || undefined} placeholder="请选择数据集" disabled={busy} onChange={key => { setDataset(key); setProductIds([]); }} options={datasets(options.products).map(d => ({ value: d.key, label: `${d.source} / ${d.version}` }))} /></Form.Item>
          <Form.Item label="商品"><Select aria-label="分析商品" mode="multiple" value={productIds} maxCount={100} maxTagCount="responsive" placeholder="全部商品" disabled={busy || !dataset} onChange={setProductIds} options={available.map(p => ({ value: p.product_id, label: `${p.product_title || p.product_id}（${p.review_count} 条）` }))} /></Form.Item>
          <Form.Item label="当前窗口" name="current" rules={[{ required: true, message: '请选择当前分析窗口' }]}><DatePicker.RangePicker aria-label="当前窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
          <Form.Item label="基线窗口" name="baseline" dependencies={['current']} rules={[{ required: true, message: '请选择基线窗口' }, ({ getFieldValue }) => ({ validator(_, value) {
            const current = getFieldValue('current');
            return value && current && !value[1].isBefore(current[0], 'day') ? Promise.reject(new Error('基线结束日期必须早于当前窗口开始日期')) : Promise.resolve();
          } })]}><DatePicker.RangePicker aria-label="基线窗口" className="full-width" allowClear={false} disabled={busy} /></Form.Item>
          <Form.Item label="标注版本" className="span-two"><Select aria-label="标注版本" value={profileId} disabled={busy} placeholder="暂无标注版本" onChange={setProfileId} options={options.profiles.map(p => ({ value: p.profile_id, label: `${p.requested_model} · ${p.prompt_version}` }))} /></Form.Item>
        </div>
        {!options.products.length && <Alert type="info" showIcon message="还没有评论数据" description="可从后端已配置的导入目录导入 JSONL 或 JSONL.gz 文件。" action={<Button onClick={onImport}>导入数据</Button>} />}
        {!!options.products.length && !profile && <Alert type="warning" showIcon message="暂无可用的标注版本" description="请先完成模型标注，再进行分析。标注结束后会自动刷新可用版本。" action={<Button onClick={onLabel}>开始模型标注</Button>} />}
        <div className="submit-row"><Button aria-label="预览数据" icon={<DatabaseOutlined />} disabled={!available.length} onClick={onPreview}>预览数据</Button><Button aria-label="开始分析" type="primary" htmlType="submit" loading={busy} disabled={!profile || !available.length} icon={<ArrowRightOutlined />} iconPosition="end">开始分析</Button></div>
      </div>
    </Form>
    <div className="capability-note"><span className="status-dot" />支持评论趋势、商品对比、六维度问题与证据回查。当前 HTTP 服务尚未开放自然语言问答和浏览器文件上传。</div>
  </div>;
}
