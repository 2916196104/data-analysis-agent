import { Select } from 'antd';

interface ProductSelectorProps {
  id?: string;
  value: string[];
  options: { value: string; label: string }[];
  disabled?: boolean;
  onChange: (value: string[]) => void;
  ariaLabel?: string;
  placeholder?: string;
}

export default function ProductSelector({ id, value, options, disabled, onChange, ariaLabel = '分析商品', placeholder = '全部商品' }: ProductSelectorProps) {
  return <Select id={id} aria-label={ariaLabel} mode="multiple" value={value} maxCount={100} maxTagCount="responsive" optionFilterProp="label" placeholder={placeholder} disabled={disabled} onChange={onChange} options={options} />;
}
