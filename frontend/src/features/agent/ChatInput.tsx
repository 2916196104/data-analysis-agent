import { Button, Input } from 'antd';
import { SendOutlined } from '@ant-design/icons';

export default function ChatInput({ value, onChange, onSend, pending, disabled }: {
  value: string; onChange: (value: string) => void; onSend: () => void; pending: boolean; disabled: boolean;
}) {
  return <div className="chat-input-area">
    <div className="chat-input-box">
      <Input.TextArea aria-label="评论分析问题" value={value} onChange={event => onChange(event.target.value)}
        autoSize={{ minRows: 2, maxRows: 5 }} maxLength={4000} disabled={disabled}
        placeholder="询问评论趋势、商品对比或负面问题…"
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); if (!pending && !disabled && value.trim()) onSend();
          }
        }} />
      <Button type="primary" shape="circle" aria-label="提交问题" icon={<SendOutlined />} loading={pending}
        disabled={pending || disabled || !value.trim()} onClick={onSend} />
    </div>
    <p className="chat-input-hint">Enter 发送 · Shift + Enter 换行 · 每次独立查询，不向模型发送聊天历史</p>
  </div>;
}
