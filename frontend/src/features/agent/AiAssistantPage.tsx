import { useEffect, useRef } from 'react';
import { Alert, Button, Spin } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import type { AgentResult, AnalysisParameters } from '../../types';
import type { AgentChat } from './useAgentChat';
import ChatInput from './ChatInput';
import ChatMessage from './ChatMessage';
import './assistant.css';

const questions = ['按日查看评论趋势', '对比所选商品的负面评论占比', '查询包装负面评论的原文证据'];

export default function AiAssistantPage({ chat, parameters, context, input, onInput, onSend, onPrepare, onAnalysis, onNewQuestion }: {
  chat: AgentChat; parameters?: AnalysisParameters; context: string; input: string;
  onInput: (value: string) => void; onSend: () => void;
  onPrepare: (result: AgentResult, products: string[]) => void; onAnalysis: () => void; onNewQuestion: () => void;
}) {
  const messages = useRef<HTMLDivElement>(null);
  const suggestions = questions.map(question => <Button key={question} disabled={!parameters || chat.pending} onClick={() => onInput(question)}>{question}</Button>);
  useEffect(() => {
    const element = messages.current;
    if (!element) return;
    const focused = chat.focusedTaskId && chat.activeTurn
      ? [...element.querySelectorAll<HTMLElement>('.chat-turn')].find(turn => turn.dataset.turnId === chat.activeTurn?.id) : undefined;
    element.scrollTop = focused ? element.scrollTop + focused.getBoundingClientRect().top - element.getBoundingClientRect().top : element.scrollHeight;
  }, [chat.session, chat.focusedTaskId, chat.activeTurn, chat.activeState.task?.status, chat.activeState.task?.result, chat.errors]);
  return <section className="ai-assistant" aria-labelledby="ai-assistant-title">
    <header className="chat-heading"><h1 id="ai-assistant-title">AI 评论分析助手</h1>
      <p>查询已有评论标注，分析趋势、对比商品并查找原文证据。</p><p className="chat-context" title={context}>{context}</p>
    </header>
    {!parameters && <Alert className="chat-notice" type="info" showIcon message="请先选择可用标注版本与分析范围" action={<Button size="small" onClick={onAnalysis}>前往评论分析</Button>} />}
    {chat.storageError && <Alert className="chat-notice" type="warning" showIcon message="本机聊天记录无法保存或读取，刷新后可能丢失；后端任务仍可从任务与报告查看。" />}
    {chat.restoreError && <Alert className="chat-notice" type="error" showIcon message={chat.restoreError} action={<Button onClick={onNewQuestion}>继续新提问</Button>} />}
    {chat.activeState.error && <Button className="chat-recover" type="link" onClick={onNewQuestion}>继续新提问</Button>}
    <div className="chat-messages" ref={messages} role="log" aria-label="聊天消息">
      <div className="chat-message-stack">
        {!chat.session.turns.length && !chat.restoreError && <div className="chat-welcome"><RobotOutlined /><h2>从评论中找到有依据的答案</h2><p>每次提问独立使用当前范围，聊天记录仅在本机展示。</p>
          <div className="chat-suggestions">{suggestions}</div>
          {chat.pending && <p role="status"><Spin size="small" /> 正在恢复历史任务…</p>}
        </div>}
        {chat.session.turns.map(turn => <ChatMessage key={turn.id} turn={turn} chat={chat} onPrepare={onPrepare} />)}
      </div>
    </div>
    {!!chat.session.turns.length && <div className="chat-suggestions chat-followups" aria-label="预设问题">{suggestions}</div>}
    <ChatInput value={input} onChange={onInput} onSend={onSend} pending={chat.pending} disabled={!parameters || !!chat.restoreError} />
  </section>;
}
