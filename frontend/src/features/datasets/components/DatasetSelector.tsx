import { Select } from 'antd';

interface DatasetSelectorProps {
  id?: string;
  value?: string;
  options: { value: string; label: string }[];
  disabled?: boolean;
  onChange: (value: string) => void;
  ariaLabel?: string;
}

export default function DatasetSelector({ id, value, options, disabled, onChange, ariaLabel = '数据集' }: DatasetSelectorProps) {
  return <Select id={id} aria-label={ariaLabel} value={value || undefined} placeholder="请选择数据集" disabled={disabled} onChange={nextValue => onChange(nextValue)} options={options} />;
}
