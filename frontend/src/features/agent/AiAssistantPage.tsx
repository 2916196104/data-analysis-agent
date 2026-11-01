import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Spin } from 'antd';
import { HistoryOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons';
import type { AgentResult, AnalysisParameters, Options, Quality } from '../../types';
import type { AnalysisDraft } from '../../analysis-draft';
import type { AgentChat } from './useAgentChat';
import type { ChatMode } from './chat-state';
import ChatInput from './ChatInput';
import ChatMessage from './ChatMessage';
import AssistantScopeBar, { type AssistantScope } from './AssistantScopeBar';
import './assistant.css';

const questions = ['查看缺失正文、失败标注和覆盖率', '按月查看包装和物流问题变化', '比较当前窗口与基线窗口'];
const basicQuestions = ['你能做什么？', '如何理解评论标注覆盖率？'];

export default function AiAssistantPage({ chat, parameters, input, onInput, onSend, onPrepare, onNewQuestion, mode, onMode, draft, options, quality, onApplyScope, loading, refreshKey }: {
  chat: AgentChat; parameters?: AnalysisParameters; input: string; draft: AnalysisDraft; options: Options; quality?: Quality;
  loading: boolean; refreshKey: number;
  mode: ChatMode; onMode: (mode: ChatMode) => void;
  onInput: (value: string) => void; onSend: () => void;
  onPrepare: (result: AgentResult, products: string[]) => void; onNewQuestion: (preserveSession?: boolean) => void;
  onApplyScope: (scope: AssistantScope) => void;
}) {
  const messages = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const [scopeEditing, setScopeEditing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => { stickToBottom.current = true; setShowLatest(false); setScopeEditing(false); }, [chat.activeSessionId]);
  const analysis = mode === 'analysis';
  const suggestions = (analysis ? questions : basicQuestions).map(question => <Button key={question}
    disabled={chat.pending || (analysis && !parameters)} onClick={() => onInput(question)}>{question}</Button>);
  useEffect(() => {
    const element = messages.current;
    if (!element) return;
    if (!stickToBottom.current) return;
    element.scrollTop = element.scrollHeight;
    setShowLatest(false);
  }, [chat.conversation, chat.focusedTaskId, chat.activeTurn, chat.activeState.task?.status, chat.activeState.task?.result, chat.errors]);
  function onMessagesScroll() {
    const element = messages.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
    stickToBottom.current = atBottom;
    setShowLatest(!atBottom && element.scrollHeight > element.clientHeight);
  }
  function showLatestMessage() {
    const element = messages.current;
    if (!element) return;
    stickToBottom.current = true;
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
    setShowLatest(false);
  }
  return <section className="ai-assistant" aria-labelledby="ai-assistant-title">
    <header className="chat-heading"><h1 id="ai-assistant-title">AI 评论分析助手</h1>
      <div className="chat-session-actions">
        <Button aria-label="历史会话" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>历史会话</Button>
        <Button aria-label="新建会话" type="primary" icon={<PlusOutlined />} onClick={() => { onNewQuestion(true); chat.createSession(); onInput(''); }}>新建会话</Button>
      </div>
    </header>
    <Drawer title="历史会话" open={historyOpen} onClose={() => setHistoryOpen(false)} width="min(360px, 100vw)">
      <p className="chat-history-note">会话仅保存在当前浏览器。继续提问使用当前选择范围。</p>
      <nav className="chat-session-list" aria-label="历史会话列表">
        {chat.sessions.map(item => <button key={item.id} type="button" className="chat-session-item"
          aria-current={item.id === chat.activeSessionId ? 'true' : undefined}
          onClick={() => { onNewQuestion(true); chat.selectSession(item.id); onInput(''); setHistoryOpen(false); }}>
          <strong>{item.title}</strong><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN')}</time>
          {item.id === chat.activeSessionId && <span>当前会话</span>}
        </button>)}
      </nav>
    </Drawer>
    {chat.storageError && <Alert className="chat-notice" type="warning" showIcon message="本机会话无法完整读取或保存，原始记录会保留；新消息刷新后可能丢失。后端任务仍可从任务与报告查看。"
      action={chat.recoveryRaw && <Button onClick={() => {
        const url = URL.createObjectURL(new Blob([chat.recoveryRaw!], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url; link.download = 'chat-history-recovery.json'; link.click(); URL.revokeObjectURL(url);
      }}>导出原始记录</Button>} />}
    {chat.restoreError && <Alert className="chat-notice" type="error" showIcon message={chat.restoreError} action={<Button onClick={() => onNewQuestion()}>继续新提问</Button>} />}
    {chat.activeState.error && <Button className="chat-recover" type="link" onClick={() => onNewQuestion()}>继续新提问</Button>}
    <div className="chat-messages" ref={messages} onScroll={onMessagesScroll} role="log" aria-label="聊天消息">
      <div className="chat-message-stack">
        {!chat.conversation.turns.length && !chat.restoreError && <div className="chat-welcome"><RobotOutlined /><h2>{analysis ? '从评论中找到有依据的答案' : '有什么可以帮你？'}</h2>
          <p>{analysis ? '检查数据质量、分析趋势与星级分布，对比商品和时间窗口，导出有来源的报告。' : '基础问答保存在当前浏览器，不读取当前商品数据。'}</p>
          <div className="chat-suggestions">{suggestions}</div>
          {chat.pending && <div className="chat-status" role="status"><Spin size="small" /> 正在恢复历史任务…</div>}
        </div>}
        {chat.conversation.turns.map(turn => <ChatMessage key={turn.id} turn={turn} chat={chat} onPrepare={onPrepare} />)}
      </div>
      {showLatest && <Button className="chat-jump-to-latest" size="small" onClick={showLatestMessage}>回到最新消息</Button>}
    </div>
    {analysis && <AssistantScopeBar draft={draft} options={options} quality={quality} disabled={chat.pending || loading}
      refreshKey={refreshKey} editing={scopeEditing} onEditing={setScopeEditing} onApply={onApplyScope} />}
    <ChatInput value={input} onChange={onInput} onSend={onSend} pending={chat.pending}
      disabled={analysis && (!parameters || loading || scopeEditing || !!chat.restoreError)} mode={mode} onMode={onMode} />
  </section>;
}
