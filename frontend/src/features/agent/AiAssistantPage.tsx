import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import type { AgentResult, AnalysisParameters, Options, Quality } from '../../types';
import type { AnalysisDraft } from '../../analysis-draft';
import type { AgentChat } from './useAgentChat';
import type { ChatMode } from './chat-state';
import ChatInput from './ChatInput';
import ChatMessage from './ChatMessage';
import AssistantScopeBar, { type AssistantScope } from './AssistantScopeBar';
import './assistant.css';

const questions = ['查看缺失正文、失败标注和覆盖率', '按月查看包装和物流问题变化', '对比所选商品的星级分布', '比较当前窗口与基线窗口', '查询包装负面评论的原文证据'];
const basicQuestions = ['你能做什么？', '如何理解评论标注覆盖率？'];

export default function AiAssistantPage({ chat, parameters, input, onInput, onSend, onPrepare, onNewQuestion, mode, onMode, draft, options, quality, onApplyScope, loading, refreshKey }: {
  chat: AgentChat; parameters?: AnalysisParameters; input: string; draft: AnalysisDraft; options: Options; quality?: Quality;
  loading: boolean; refreshKey: number;
  mode: ChatMode; onMode: (mode: ChatMode) => void;
  onInput: (value: string) => void; onSend: () => void;
  onPrepare: (result: AgentResult, products: string[]) => void; onNewQuestion: () => void;
  onApplyScope: (scope: AssistantScope) => void;
}) {
  const messages = useRef<HTMLDivElement>(null);
  const [scopeEditing, setScopeEditing] = useState(false);
  const analysis = mode === 'analysis';
  const suggestions = (analysis ? questions : basicQuestions).map(question => <Button key={question}
    disabled={chat.pending || (analysis && !parameters)} onClick={() => onInput(question)}>{question}</Button>);
  useEffect(() => {
    const element = messages.current;
    if (!element) return;
    const focused = chat.focusedTaskId && chat.activeTurn
      ? [...element.querySelectorAll<HTMLElement>('.chat-turn')].find(turn => turn.dataset.turnId === chat.activeTurn?.id) : undefined;
    element.scrollTop = focused ? element.scrollTop + focused.getBoundingClientRect().top - element.getBoundingClientRect().top : element.scrollHeight;
  }, [chat.conversation, chat.focusedTaskId, chat.activeTurn, chat.activeState.task?.status, chat.activeState.task?.result, chat.errors]);
  return <section className="ai-assistant" aria-labelledby="ai-assistant-title">
    <header className="chat-heading"><h1 id="ai-assistant-title">AI 评论分析助手</h1></header>
    {chat.storageError && <Alert className="chat-notice" type="warning" showIcon message="本机分析记录无法保存或读取，刷新后可能丢失；后端任务仍可从任务与报告查看。" />}
    {chat.restoreError && <Alert className="chat-notice" type="error" showIcon message={chat.restoreError} action={<Button onClick={onNewQuestion}>继续新提问</Button>} />}
    {chat.activeState.error && <Button className="chat-recover" type="link" onClick={onNewQuestion}>继续新提问</Button>}
    <div className="chat-messages" ref={messages} role="log" aria-label="聊天消息">
      <div className="chat-message-stack">
        {!chat.conversation.turns.length && !chat.restoreError && <div className="chat-welcome"><RobotOutlined /><h2>{analysis ? '从评论中找到有依据的答案' : '有什么可以帮你？'}</h2>
          <p>{analysis ? '检查数据质量、分析趋势与星级分布，对比商品和时间窗口，导出有来源的报告。' : '基础问答仅在本次打开期间保留，不读取当前商品数据。'}</p>
          <div className="chat-suggestions">{suggestions}</div>
          {chat.pending && <div className="chat-status" role="status"><Spin size="small" /> 正在恢复历史任务…</div>}
        </div>}
        {chat.conversation.turns.map(turn => <ChatMessage key={turn.id} turn={turn} chat={chat} onPrepare={onPrepare} />)}
        {!!chat.conversation.turns.length && <div className="chat-suggestions chat-followups" aria-label="预设问题">{suggestions}</div>}
      </div>
    </div>
    {analysis && <AssistantScopeBar draft={draft} options={options} quality={quality} disabled={chat.pending || loading}
      refreshKey={refreshKey} editing={scopeEditing} onEditing={setScopeEditing} onApply={onApplyScope} />}
    <ChatInput value={input} onChange={onInput} onSend={onSend} pending={chat.pending}
      disabled={analysis && (!parameters || loading || scopeEditing || !!chat.restoreError)} mode={mode} onMode={onMode} />
  </section>;
}
