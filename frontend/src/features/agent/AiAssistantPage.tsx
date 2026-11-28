import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Input, Popconfirm, Spin, Tag } from 'antd';
import { CheckOutlined, CloseOutlined, CloudOutlined, DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons';
import { api } from '../../api';
import type { AgentResult, AnalysisParameters, Options, Quality, ServerConversationSummary } from '../../types';
import { AGENT_DATASET, datasetKey, type AnalysisDraft } from '../../analysis-draft';
import type { AgentChat } from './useAgentChat';
import type { ChatMode } from './chat-state';
import { MAX_SESSIONS } from './useChatSessions';
import ChatInput from './ChatInput';
import ChatMessage from './ChatMessage';
import AssistantScopeBar, { type AssistantScope } from './AssistantScopeBar';
import './assistant.css';

const questions = ['查看缺失正文、失败标注和覆盖率', '按月分析物流好评占比趋势', '对比当前窗口与基线的物流好评占比'];
const basicQuestions = ['你能做什么？', '如何理解评论标注覆盖率？'];

export default function AiAssistantPage({ chat, parameters, input, onInput, onSend, onPrepare, onNewQuestion, mode, onMode, onTransferMode, draft, options, quality, onApplyScope, loading, refreshKey }: {
  chat: AgentChat; parameters?: AnalysisParameters; input: string; draft: AnalysisDraft; options: Options; quality?: Quality;
  loading: boolean; refreshKey: number;
  mode: ChatMode; onMode: (mode: ChatMode) => void; onTransferMode?: (targetMode: ChatMode) => void;
  onInput: (value: string) => void; onSend: () => void;
  onPrepare: (result: AgentResult, products: string[]) => void; onNewQuestion: (preserveSession?: boolean) => void;
  onApplyScope: (scope: AssistantScope) => void;
}) {
  const messages = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const [scopeEditing, setScopeEditing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [serverSessions, setServerSessions] = useState<ServerConversationSummary[]>([]);
  const [serverLoading, setServerLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  useEffect(() => { stickToBottom.current = true; setShowLatest(false); setScopeEditing(false); }, [chat.activeSessionId]);
  useEffect(() => { if (!historyOpen) { setEditingSessionId(null); setEditingTitle(''); } }, [historyOpen]);
  useEffect(() => {
    if (!historyOpen || typeof api.conversations !== 'function') return;
    const controller = new AbortController();
    setServerLoading(true);
    api.conversations(30, controller.signal)
      .then(res => {
        if (!controller.signal.aborted) setServerSessions(res.items || []);
      })
      .catch(() => {
        if (!controller.signal.aborted) setServerSessions([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setServerLoading(false);
      });
    return () => controller.abort();
  }, [historyOpen]);
  const analysis = mode === 'analysis';
  const datasetWarning = !options.products.some(product => datasetKey(product) === AGENT_DATASET)
    ? 'AI 助手数据集 reviews_csv / reviews-csv 当前不可用，请先确认数据集；不会自动切换到其他来源。'
    : draft.dataset !== AGENT_DATASET ? 'AI 助手目前仅支持 reviews_csv / reviews-csv，请切换到该数据集；历史任务仍可按原范围查看。' : '';
  const suggestions = (analysis ? questions : basicQuestions).map(question => <Button key={question}
    disabled={chat.pending || (analysis && !parameters)} onClick={() => onInput(question)}>{question}</Button>);
  useEffect(() => {
    const element = messages.current;
    if (!element) return;
    if (!stickToBottom.current) return;
    element.scrollTop = element.scrollHeight;
    setShowLatest(false);
  }, [mode, chat.conversation, chat.focusedTaskId, chat.activeTurn, chat.activeState.task?.status, chat.activeState.task?.progress, chat.activeState.task?.result, chat.errors]);
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
  function startRename(id: string, title: string) {
    setEditingSessionId(id);
    setEditingTitle(title);
  }
  function saveRename(id: string) {
    const trimmed = editingTitle.trim();
    if (trimmed) chat.renameSession(id, trimmed);
    setEditingSessionId(null);
    setEditingTitle('');
  }
  return <section className="ai-assistant" aria-labelledby="ai-assistant-title">
    <header className="chat-heading"><h1 id="ai-assistant-title">AI 评论分析助手</h1>
      <div className="chat-session-actions">
        <Button aria-label="历史会话" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>历史会话</Button>
        <Button aria-label="新建会话" type="primary" icon={<PlusOutlined />} onClick={() => { onNewQuestion(true); chat.createSession(); onInput(''); }}>新建会话</Button>
      </div>
    </header>
    {analysis && <details className="chat-notice"><summary>查看支持的分析能力</summary>
      <p>支持数据质量、星级分布、正负向维度汇总、日/月趋势、商品对比、时间窗口对比及评论原文。正向对比支持整体或一个维度；整体好评以成功标注评论为分母，维度好评默认以含正向观点评论为分母，也可明确指定成功标注评论。</p>
      <p>负向对比按整体统计。细分主题统计仅覆盖已有规则，具体内容归纳取决于有效标注和证据。评论数据不能证明故障原因，也不能用于销量、销售额预测。</p>
    </details>}
    <Drawer title="历史会话" open={historyOpen} onClose={() => setHistoryOpen(false)} width="min(380px, 100vw)">
      <p className="chat-history-note">本地会话保存在当前浏览器（最多保留 {MAX_SESSIONS} 个），分析任务同时保存在服务端，可跨会话恢复。</p>
      <nav className="chat-session-list" aria-label="历史会话列表">
        {chat.sessions.map(item => {
          const isCurrent = item.id === chat.activeSessionId;
          const isEditing = editingSessionId === item.id;
          return <div key={item.id} className={isCurrent ? 'chat-session-row chat-session-row-active' : 'chat-session-row'}>
            {isEditing ? <div className="chat-session-rename">
              <Input size="small" aria-label="会话名称" maxLength={60} value={editingTitle} autoFocus
                onChange={event => setEditingTitle(event.target.value)}
                onPressEnter={() => saveRename(item.id)}
                onKeyDown={event => { if (event.key === 'Escape') { setEditingSessionId(null); setEditingTitle(''); } }} />
              <div className="chat-session-rename-actions">
                <Button size="small" type="primary" icon={<CheckOutlined />} aria-label="保存会话名称"
                  disabled={!editingTitle.trim()} onClick={() => saveRename(item.id)}>保存</Button>
                <Button size="small" icon={<CloseOutlined />} aria-label="取消重命名"
                  onClick={() => { setEditingSessionId(null); setEditingTitle(''); }}>取消</Button>
              </div>
            </div> : <>
              <button type="button" className="chat-session-item" aria-current={isCurrent ? 'true' : undefined}
                onClick={() => { onNewQuestion(true); chat.selectSession(item.id); onInput(''); setHistoryOpen(false); }}>
                <strong>{item.title}</strong>
                <div className="chat-session-meta">
                  <time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString('zh-CN')}</time>
                  <span>{item.turns.length} 轮</span>
                  {isCurrent && <span className="chat-session-current">当前会话</span>}
                </div>
              </button>
              <div className="chat-session-tools">
                <Button type="text" size="small" icon={<EditOutlined />} aria-label={`重命名会话：${item.title}`}
                  onClick={() => startRename(item.id, item.title)} />
                <Popconfirm title="删除该会话？" description="仅删除当前浏览器中的会话记录，不影响后端已保存的任务。"
                  okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                  onConfirm={() => {
                    if (isCurrent) onNewQuestion(true);
                    chat.deleteSession(item.id);
                    if (isCurrent) onInput('');
                  }}>
                  <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除会话：${item.title}`} />
                </Popconfirm>
              </div>
            </>}
          </div>;
        })}
      </nav>
      {(() => {
        const localIds = new Set(chat.sessions.map(item => item.id));
        const remoteOnly = serverSessions.filter(item => !localIds.has(item.conversation_id));
        if (!serverLoading && !remoteOnly.length) return null;
        return <>
          <p className="chat-session-section-title"><CloudOutlined /> 服务端已保存会话</p>
          {serverLoading && !remoteOnly.length && <div className="chat-status"><Spin size="small" /> 正在读取服务端会话…</div>}
          {!!remoteOnly.length && <nav className="chat-session-list" aria-label="服务端历史会话列表">
            {remoteOnly.map(item => <div key={item.conversation_id} className="chat-session-row">
              <button type="button" className="chat-session-item" disabled={restoringId === item.conversation_id}
                onClick={async () => {
                  if (typeof chat.restoreServerSession !== 'function') return;
                  setRestoringId(item.conversation_id);
                  try {
                    const ok = await chat.restoreServerSession(item.conversation_id);
                    if (ok) {
                      onNewQuestion(true);
                      onMode('analysis');
                      onInput('');
                      setHistoryOpen(false);
                    }
                  } finally {
                    setRestoringId(null);
                  }
                }}>
                <strong>{item.title}</strong>
                <div className="chat-session-meta">
                  <Tag color="green">服务端</Tag>
                  <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time>
                  <span>{item.turn_count} 轮</span>
                  {restoringId === item.conversation_id && <span>恢复中…</span>}
                </div>
              </button>
            </div>)}
          </nav>}
        </>;
      })()}
    </Drawer>
    {chat.storageError && <Alert className="chat-notice" type="warning" showIcon message="本机会话无法完整读取或保存，原始记录会保留；新消息刷新后可能丢失。后端任务仍可从任务与报告查看。"
      action={chat.recoveryRaw && <Button onClick={() => {
        const url = URL.createObjectURL(new Blob([chat.recoveryRaw!], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url; link.download = 'chat-history-recovery.json'; link.click(); URL.revokeObjectURL(url);
      }}>导出原始记录</Button>} />}
    {chat.restoreError && <Alert className="chat-notice" type="error" showIcon message={chat.restoreError} action={<Button onClick={() => onNewQuestion()}>继续新提问</Button>} />}
    {analysis && datasetWarning && <Alert className="chat-notice" type="warning" showIcon message={datasetWarning} />}
    {chat.activeState.error && <Button className="chat-recover" type="link" onClick={() => onNewQuestion()}>继续新提问</Button>}
    <div className="chat-messages" ref={messages} onScroll={onMessagesScroll} role="log" aria-label="聊天消息">
      <div className="chat-message-stack">
        {!chat.conversation.turns.length && !chat.restoreError && <div className="chat-welcome"><RobotOutlined /><h2>{analysis ? '从评论中找到有依据的答案' : '有什么可以帮你？'}</h2>
          <p>{analysis ? '检查数据质量、分析趋势与星级分布，对比商品和时间窗口，导出有来源的报告。' : '基础问答保存在当前浏览器，不读取当前商品数据。'}</p>
          <div className="chat-suggestions">{suggestions}</div>
          {chat.pending && <div className="chat-status" role="status"><Spin size="small" /> 正在恢复历史任务…</div>}
        </div>}
        {chat.conversation.turns.map(turn => <ChatMessage key={turn.id} turn={turn} chat={chat} onPrepare={onPrepare}
          onSuggestQuestion={analysis ? onInput : undefined} />)}
      </div>
    </div>
    {showLatest && <div className="chat-jump-bar">
      <Button className="chat-jump-to-latest" size="small" onClick={showLatestMessage}>回到最新消息</Button>
    </div>}
    {analysis && <AssistantScopeBar draft={draft} options={options} quality={quality} disabled={chat.pending || loading}
      refreshKey={refreshKey} editing={scopeEditing} onEditing={setScopeEditing} onApply={onApplyScope} />}
    <ChatInput value={input} onChange={onInput} onSend={onSend} pending={chat.pending}
      disabled={analysis && (!parameters || loading || scopeEditing || !!chat.restoreError)} mode={mode} onMode={onMode}
      onTransferMode={onTransferMode} />
  </section>;
}
