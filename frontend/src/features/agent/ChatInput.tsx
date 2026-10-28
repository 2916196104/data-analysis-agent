import { Button, Input, Radio } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import type { ChatMode } from './chat-state';

export default function ChatInput({ value, onChange, onSend, pending, disabled, mode, onMode }: {
  value: string; onChange: (value: string) => void; onSend: () => void; pending: boolean; disabled: boolean;
  mode: ChatMode; onMode: (mode: ChatMode) => void;
}) {
  return <div className="chat-input-area">
    <div className="chat-input-toolbar">
      <Radio.Group aria-label="问答模式" size="small" optionType="button" buttonStyle="solid" value={mode}
        disabled={pending} onChange={event => onMode(event.target.value)} options={[
          { label: '基础问答', value: 'basic' }, { label: '评论分析', value: 'analysis' },
        ]} />
      <span className="chat-mode-hint">{mode === 'basic' ? '普通问答，不查询当前商品数据' : '按当前范围查询已标注评论'}</span>
    </div>
    <div className="chat-input-box">
      <Input.TextArea aria-label={mode === 'basic' ? '基础问答问题' : '评论分析问题'} value={value} onChange={event => onChange(event.target.value)}
        autoSize={{ minRows: 2, maxRows: 5 }} maxLength={4000} disabled={disabled}
        placeholder={mode === 'basic' ? '输入问题，例如：你能做什么？' : '询问评论趋势、商品对比或负面问题…'}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); if (!pending && !disabled && value.trim()) onSend();
          }
        }} />
      <Button type="primary" shape="circle" aria-label="提交问题" icon={<SendOutlined />} loading={pending}
        disabled={pending || disabled || !value.trim()} onClick={onSend} />
    </div>
  </div>;
}
