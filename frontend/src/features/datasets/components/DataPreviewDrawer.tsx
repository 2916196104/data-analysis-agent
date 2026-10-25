import { Drawer } from 'antd';
import type { Options, Product } from '../../../types';
import DatasetPreview from './DatasetPreview';

export default function DataPreviewDrawer({ open, onClose, products, options, profileId, onDetail }: { open: boolean; onClose: () => void; products: Product[]; options: Options; profileId?: number; onDetail: (value: unknown, title: string) => void }) {
  return <Drawer title="数据详情" width={620} open={open} onClose={onClose} destroyOnHidden><DatasetPreview products={products} options={options} profileId={profileId} onDetail={onDetail} /></Drawer>;
}
