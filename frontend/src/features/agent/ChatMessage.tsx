import { useState, type ReactNode } from 'react';
import { Alert, Button, Space, Spin } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import AgentResults from '../../AgentResults';
import { exportResult } from '../../local-history';
import { canRetry, isActive, stageLabels } from '../../task-state';
import { useTask } from '../../useTask';
import type { AgentResult } from '../../types';
import type { BasicChatTurn, ChatTurn, ConversationTurn } from './chat-state';
import type { AgentChat } from './useAgentChat';
import Markdown from './Markdown';
import { exportAgentReport } from './report-export';

export default function ChatMessage({ turn, chat, onPrepare }: {
  turn: ConversationTurn; chat: AgentChat; onPrepare: (result: AgentResult, products: string[]) => void;
}) {
  return turn.kind === 'basic' ? <BasicMessage turn={turn} chat={chat} />
    : <AnalysisMessage turn={turn} chat={chat} onPrepare={onPrepare} />;
}

function MessageFrame({ turn, children }: { turn: ConversationTurn; children: ReactNode }) {
  return <article className="chat-turn" data-turn-id={turn.id} data-message-kind={turn.kind}>
    <div className="chat-user" aria-label="用户提问"><p>{turn.question}</p></div>
    <div className="chat-assistant" aria-label="AI 回复">
      <span className="chat-avatar"><RobotOutlined /></span>
      <div className="chat-reply">{children}</div>
    </div>
  </article>;
}

function BasicMessage({ turn, chat }: { turn: BasicChatTurn; chat: AgentChat }) {
  return <MessageFrame turn={turn}>
    {turn.status === 'pending' && <div className="chat-status" role="status"><Spin size="small" /> 正在等待回答…</div>}
    {turn.status === 'succeeded' && turn.answer && <Markdown text={turn.answer} />}
    {turn.status === 'failed' && <Alert type="error" showIcon message="回答未完成" description={turn.error}
      action={<Button disabled={chat.pending} onClick={() => void chat.sendBasic(turn.question)}>重新发送</Button>} />}
  </MessageFrame>;
}

function AnalysisMessage({ turn, chat, onPrepare }: {
  turn: ChatTurn; chat: AgentChat; onPrepare: (result: AgentResult, products: string[]) => void;
}) {
  const active = chat.session.activeId === turn.id;
  const archivedState = useTask(active ? null : turn.taskId ?? null);
  const state = active ? chat.activeState : archivedState;
  const task = state.id === turn.taskId ? state.task : null;
  const result = task?.result?.agent === true ? task.result as unknown as AgentResult : undefined;
  const [detail, setDetail] = useState<{ value: unknown; title: string }>();
  return <MessageFrame turn={turn}>
        <section className="chat-turn-scope" aria-label="本次问题的固定范围"><strong>本次查询范围</strong>
          <dl className="assistant-scope-values">
            <div><dt>数据集</dt><dd>{turn.parameters.source} / {turn.parameters.dataset_version}</dd></div>
            <div><dt>标注版本</dt><dd>{turn.parameters.model} · {turn.parameters.prompt_version}<br />{turn.parameters.base_url}</dd></div>
            <div><dt>商品</dt><dd>{turn.parameters.product_ids.length ? turn.parameters.product_ids.join('、') : '全部商品'}</dd></div>
            <div><dt>当前窗口（UTC）</dt><dd>{turn.parameters.date_from} — {turn.parameters.date_to}</dd></div>
            <div><dt>基线窗口（UTC）</dt><dd>{turn.parameters.baseline_from} — {turn.parameters.baseline_to}</dd></div>
            {turn.parameters.as_of && <div><dt>评论截止时间</dt><dd>{turn.parameters.as_of}</dd></div>}
          </dl>
        </section>
        {!turn.taskId && (chat.posting && active ? <div className="chat-status" role="status"><Spin size="small" /> 正在提交问题…</div>
          : <Alert type="error" showIcon message="问题尚未确认提交" description={chat.errors[turn.id] || '提交结果待确认，请重试确认同一任务。'}
            action={<Button disabled={chat.pending} onClick={() => void chat.retry(turn)}>重试提交</Button>} />)}
        {turn.taskId && !task && !state.error && <div className="chat-status" role="status"><Spin size="small" /> 正在读取任务…</div>}
        {state.error && <Alert type="error" showIcon message="任务连接异常" description={state.error} action={<Button onClick={state.refresh}>重新连接</Button>} />}
        {task && isActive(task.status) && <div className="chat-status" role="status"><Spin size="small" /> {stageLabels[task.progress.stage] || '正在分析…'}</div>}
        {task?.error && <Alert type="error" showIcon message={task.error.message} description={<details><summary>错误详情</summary>{task.error.code}</details>} />}
        {chat.errors[turn.id] && turn.taskId && <Alert type="error" showIcon message="重试未完成" description={chat.errors[turn.id]} />}
        {task && canRetry(task.status) && <Button className="spaced" loading={active && chat.posting} disabled={chat.pending} onClick={() => void chat.retry(turn)}>重试任务</Button>}
        {result && <>
          <Markdown text={result.answer || (result.status === 'ok' ? '查询结果已保存，可展开统计依据与原文证据查看结果。' : '查询未完成，请查看任务错误与执行记录。')} />
          <Button size="small" onClick={() => exportAgentReport(result, turn.taskId)}>导出分析报告</Button>
          <details className="chat-report"><summary>查看统计、证据与执行记录</summary>
            <AgentResults result={result} idPrefix={`agent-${turn.id}`} onDetail={(value, title) => setDetail({ value, title })} onPrepare={products => onPrepare(result, products)} />
          </details>
        </>}
        {detail && <section className="chat-inline-detail" aria-label="数据详情">
          <h3>{detail.title}</h3><Space wrap><Button aria-label="关闭数据详情" onClick={() => setDetail(undefined)}>关闭详情</Button><Button onClick={() => exportResult(detail.value, 'agent-detail')}>下载 JSON</Button></Space>
          <pre className="json-view">{JSON.stringify(detail.value, null, 2)}</pre>
        </section>}
  </MessageFrame>;
}
