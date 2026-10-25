import { Button, Table } from 'antd';
import { ImportOutlined } from '@ant-design/icons';
import type { Options } from '../../../types';
import DatasetSelector from './DatasetSelector';
import { datasetSelectOptions, productsForDataset } from '../model/dataset-options';

export default function DataManagement({ options, dataset, onDataset, onImport, onPreview }: {
  options: Options; dataset: string; onDataset: (value: string) => void;
  onImport: () => void; onPreview: () => void;
}) {
  const products = productsForDataset(options, dataset);
  return <section className="data-management">
    <div className="review-page-heading"><div><h1>数据管理</h1><p>管理已有数据集，查看商品与评论原文。</p></div><Button type="primary" icon={<ImportOutlined />} onClick={onImport}>上传评论文件</Button></div>
    <div className="data-management-controls"><DatasetSelector value={dataset} options={datasetSelectOptions(options.products)} onChange={onDataset} /><Button disabled={!products.length} onClick={onPreview}>查看评论原文</Button></div>
    <Table rowKey="key" dataSource={products} scroll={{ x: 540 }} pagination={{ pageSize: 10, hideOnSinglePage: true }} locale={{ emptyText: '暂无商品，请先上传评论文件' }} columns={[
      { title: '商品', render: (_, product) => <><strong>{product.product_title || product.product_id}</strong><div className="muted">{product.product_id}</div></> },
      { title: '数据集', dataIndex: 'dataset_version' },
      { title: '评论数', dataIndex: 'review_count', align: 'right' },
      { title: '成功标注', dataIndex: 'success_count', align: 'right' },
    ]} />
    <p className="muted spaced">目录计数涵盖全部日期。人工确认与纠错请使用 <a href={import.meta.env.VITE_REVIEW_WORKBENCH_URL || 'http://127.0.0.1:8765'} target="_blank" rel="noreferrer">独立人工复核工作台</a>（需先启动原有服务）。</p>
  </section>;
}
