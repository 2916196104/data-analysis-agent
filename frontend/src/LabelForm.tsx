import { Alert, Button, Checkbox, Form, InputNumber, Select } from 'antd';
import { datasetKey, datasets } from './Composer';
import type { LabelParameters, Options } from './types';

interface Values { dataset: string; product_ids: string[]; limit: number; workers: number; retry_failed: boolean }

export default function LabelForm({ options, initialDataset, initialProducts, initialLimit = 100, initialWorkers = 2, busy, onSubmit }: {
  options: Options; initialDataset: string; initialProducts: string[]; initialLimit?: number; initialWorkers?: number;
  busy: boolean; onSubmit: (parameters: LabelParameters) => void;
}) {
  const [form] = Form.useForm<Values>();
  const dataset = Form.useWatch('dataset', form) ?? initialDataset;
  const available = options.products.filter(p => datasetKey(p) === dataset);
  return <Form<Values> form={form} layout="vertical" initialValues={{ dataset: initialDataset || undefined,
    product_ids: initialProducts, limit: initialLimit, workers: initialWorkers, retry_failed: false,
  }} onFinish={values => {
    const selected = datasets(options.products).find(d => d.key === values.dataset);
    if (!selected || !available.length) return;
    onSubmit({ source: selected.source, dataset_version: selected.version, product_ids: values.product_ids,
      limit: values.limit, workers: values.workers, retry_failed: values.retry_failed });
  }}>
    <p className="muted">使用后端配置的模型处理所选商品的全部日期评论，自动跳过同一标注版本下已成功的记录。范围也可能包含之前导入但尚未标注的评论。</p>
    <Form.Item label="标注数据集" name="dataset" rules={[{ required: true, message: '请选择数据集' }]}>
      <Select aria-label="标注数据集" disabled={busy} placeholder="请选择数据集" onChange={() => form.setFieldValue('product_ids', [])}
        options={datasets(options.products).map(d => ({ value: d.key, label: `${d.source} / ${d.version}` }))} />
    </Form.Item>
    <Form.Item label="标注商品" name="product_ids" extra="留空表示所选数据集的全部商品。">
      <Select aria-label="标注商品" mode="multiple" maxCount={100} maxTagCount="responsive" optionFilterProp="label"
        placeholder="全部商品" disabled={busy || !available.length}
        options={available.map(p => ({ value: p.product_id, label: `${p.product_title || p.product_id} · ${p.product_id}（${p.review_count} 条）` }))} />
    </Form.Item>
    <div className="form-grid">
      <Form.Item label="每批标注数量" name="limit" extra="本次任务最多处理的评论数，1–1000 条；所有商品合计。" rules={[{ required: true, type: 'integer', min: 1, max: 1000, message: '请输入 1–1000 的整数' }]}>
        <InputNumber aria-label="每批标注数量" min={1} max={1000} step={1} disabled={busy} className="full-width" />
      </Form.Item>
      <Form.Item label="并发数" name="workers" extra="同时请求模型的数量，1–4；建议从 2 开始。" rules={[{ required: true, type: 'integer', min: 1, max: 4, message: '请输入 1–4 的整数' }]}>
        <InputNumber aria-label="并发数" min={1} max={4} step={1} disabled={busy} className="full-width" />
      </Form.Item>
    </div>
    <Form.Item name="retry_failed" valuePropName="checked"><Checkbox disabled={busy}>同时重试此前标注失败的评论</Checkbox></Form.Item>
    {!options.products.length && <Alert className="spaced-bottom" type="info" showIcon message="请先导入评论数据，导入完成后即可选择商品。" />}
    <Button block type="primary" htmlType="submit" loading={busy} disabled={!available.length}>提交标注任务</Button>
  </Form>;
}
