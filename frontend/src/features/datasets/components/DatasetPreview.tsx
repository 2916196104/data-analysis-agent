import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Select, Table, Tabs } from 'antd';
import { reviewApi } from '../../../shared/api/review-api';
import { errorMessage } from '../../../shared/api/http-client';
import type { Options, Product, Review } from '../../../types';
import { useDatasetPreview } from '../hooks/useDatasetPreview';

interface DatasetPreviewProps { products: Product[]; options: Options; profileId?: number; onDetail: (value: unknown, title: string) => void }

export default function DatasetPreview({ products, options, profileId, onDetail }: DatasetPreviewProps) {
  const [selectedProductKey, setSelectedProductKey] = useState(products[0]?.key);
  const preview = useDatasetPreview(selectedProductKey, profileId);
  useEffect(() => { if (!products.some(product => product.key === selectedProductKey)) setSelectedProductKey(products[0]?.key); }, [products, selectedProductKey]);
  if (!products.length) return <Empty description="当前范围没有可预览的商品" />;
  const productOptions = products.map(product => ({ value: product.key, label: `${product.product_title || product.product_id} · ${product.review_count} 条` }));
  const fields = [
    ['product_id', '商品标识', '字符串'], ['text', '评论正文', '字符串，可为空'], ['rating', '用户评分', '数值，可为空'],
    ['published_at', '评论发布时间', 'UTC 时间'], ['processing_status', '模型处理状态', 'pending / success / failed / empty'], ['opinions', '当前有效观点', '维度、情感、引用及原文位置'],
  ].map(([field, label, type]) => ({ field, label, type }));
  const reviewColumns = [{ title: '评论', render: (_value: unknown, row: Review) => <div className="preview-review">
    <div className="muted">{row.published_at.slice(0, 10)} · 评分 {row.rating ?? '—'} · {options.processing_statuses[row.processing_status] ?? row.processing_status}</div>
    <p>{row.text || '（空正文）'}</p>
    <Button size="small" type="link" onClick={async () => { try { onDetail(await reviewApi.review(row.id, profileId), `评论 #${row.id}`); } catch (error) { /* row-level errors stay in the preview */ } }}>查看原文与字段</Button>
  </div> }];
  return <Tabs items={[{
    key: 'preview', label: '数据预览', children: <>
      <p className="muted">当前数据库中的评论。历史结果中的原文请在“原文证据”中查看。</p>
      <Select aria-label="预览商品" className="full-width" value={selectedProductKey} onChange={value => { setSelectedProductKey(value); preview.setPage(1); }} options={productOptions} />
      {preview.error && <Alert className="spaced" type="error" showIcon message={preview.error} action={<Button onClick={preview.retry}>重试</Button>} />}
      <Table className="spaced" loading={preview.loading} size="small" rowKey="id" dataSource={preview.data?.items} pagination={{ current: preview.data?.page ?? preview.page, total: preview.data?.total, pageSize: 8, showSizeChanger: false, onChange: preview.setPage }} columns={reviewColumns} locale={{ emptyText: '暂无评论' }} />
    </>,
  }, {
    key: 'fields', label: '字段信息', children: <><p className="muted">以下为后端评论接口的字段定义。</p><Table rowKey="field" size="small" pagination={false} dataSource={fields} columns={[{ title: '字段', dataIndex: 'field', render: (value: string) => <code>{value}</code> }, { title: '含义', dataIndex: 'label' }, { title: '类型', dataIndex: 'type' }]} /></>,
  }]} />;
}
