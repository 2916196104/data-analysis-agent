import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Select, Table, Tabs } from 'antd';
import { api, errorMessage } from './api';
import type { Options, Page, Product, Review } from './types';

export default function DataPreview({ products, options, profileId, onDetail }: {
  products: Product[]; options: Options; profileId?: number; onDetail: (value: unknown, title: string) => void;
}) {
  const [key, setKey] = useState(products[0]?.key);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Review>>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setData(undefined);
    api.reviews(key, profileId, page, controller.signal).then(setData).catch(error => {
      if (!controller.signal.aborted) setError(errorMessage(error));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [key, profileId, page, revision]);
  const fields = [
    ['product_id', '商品标识', '字符串'], ['text', '评论正文', '字符串，可为空'],
    ['rating', '用户评分', '数值，可为空'], ['published_at', '评论发布时间', 'UTC 时间'],
    ['processing_status', '模型处理状态', 'pending / success / failed / empty'],
    ['opinions', '当前有效观点', '维度、情感、引用及原文位置'],
  ].map(([field, label, type]) => ({ field, label, type }));
  if (!products.length) return <Empty description="当前范围没有可预览的商品" />;
  return <Tabs items={[
    { key: 'preview', label: '数据预览', children: <>
      <p className="muted">当前数据库中的评论。历史结果中的原文请在“原文证据”中查看。</p>
      <Select aria-label="预览商品" className="full-width" value={key} onChange={value => { setKey(value); setPage(1); }} options={products.map(p => ({ value: p.key, label: `${p.product_title || p.product_id} · ${p.review_count} 条` }))} />
      {error && <Alert className="spaced" type="error" showIcon message={error} action={<Button onClick={() => setRevision(v => v + 1)}>重试</Button>} />}
      <Table className="spaced" loading={loading} size="small" rowKey="id" dataSource={data?.items} pagination={{ current: data?.page ?? page, total: data?.total, pageSize: 8, showSizeChanger: false, onChange: setPage }} columns={[
        { title: '评论', render: (_, row) => <div className="preview-review"><div className="muted">{row.published_at.slice(0, 10)} · 评分 {row.rating ?? '—'} · {options.processing_statuses[row.processing_status] ?? row.processing_status}</div><p>{row.text || '（空正文）'}</p><Button size="small" type="link" onClick={async () => {
          try { onDetail(await api.review(row.id, profileId), `评论 #${row.id}`); }
          catch (error) { setError(errorMessage(error)); }
        }}>查看原文与字段</Button></div> },
      ]} locale={{ emptyText: '暂无评论' }} />
    </> },
    { key: 'fields', label: '字段信息', children: <><p className="muted">以下为后端评论接口的字段定义。</p><Table rowKey="field" size="small" pagination={false} dataSource={fields} columns={[
      { title: '字段', dataIndex: 'field', render: (value: string) => <code>{value}</code> },
      { title: '含义', dataIndex: 'label' }, { title: '类型', dataIndex: 'type' },
    ]} /></> },
  ]} />;
}
