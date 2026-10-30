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
  const savedResult = task?.result?.agent === true ? task.result as unknown as AgentResult : undefined;
  const result = task?.status === 'succeeded' && !task.error && savedResult
    && ['ok', 'unsupported'].includes(savedResult.status) && savedResult.validation?.status !== 'failed' ? savedResult : undefined;
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
        {turn.query && <details className="chat-query-snapshot"><summary>查看本轮查询快照</summary>
          <p>记录时间：{turn.query.createdAt}{turn.query.parentTaskId ? ` · 承接任务：${turn.query.parentTaskId}` : ' · 独立查询'}</p>
          <pre>{turn.query.effectiveQuestion}</pre>
          {result && <p>执行快照：{result.snapshot_at}</p>}
          <Button size="small" onClick={() => exportResult({ question: turn.question, parameters: turn.parameters,
            query: turn.query, clarification: turn.clarification, taskId: turn.taskId,
            snapshot_at: result?.snapshot_at, results: result?.results }, 'query-snapshot')}>下载查询快照</Button>
        </details>}
        {turn.clarification && <Alert type="info" showIcon message={turn.clarification.resolved ? '已确认查询目标' : '需要澄清'}
          description={<><p>{turn.clarification.message}</p><div className="chat-clarification-choices">
            {turn.clarification.choices.map((choice, index) => <Button key={index} disabled={chat.pending || !active || turn.clarification?.resolved}
              onClick={() => void chat.clarify(turn, choice)}>{choice.label}</Button>)}
          </div>{!active && !turn.clarification.resolved && <p>该轮已保留，请在输入框重新提出问题。</p>}</>} />}
        {!turn.taskId && !turn.clarification && (chat.posting && active ? <div className="chat-status" role="status"><Spin size="small" /> 正在提交问题…</div>
          : <Alert type="error" showIcon message="问题尚未确认提交" description={chat.errors[turn.id] || '提交结果待确认，请重试确认同一任务。'}
            action={<Button disabled={chat.pending} onClick={() => void chat.retry(turn)}>重试提交</Button>} />)}
        {turn.taskId && !task && !state.error && <div className="chat-status" role="status"><Spin size="small" /> 正在读取任务…</div>}
        {state.error && <Alert type="error" showIcon message="任务连接异常" description={state.error} action={<Button onClick={state.refresh}>重新连接</Button>} />}
        {task && isActive(task.status) && <div className="chat-status" role="status"><Spin size="small" /> {stageLabels[task.progress.stage] || '正在分析…'}</div>}
        {task?.error && <Alert type="error" showIcon message={task.error.message} description={<details><summary>错误详情</summary>{task.error.code}</details>} />}
        {task?.status === 'partial' && <Alert type="warning" showIcon message="任务部分完成" description="完整回答尚未确认，统计与原文暂不展示。可以重试任务。" />}
        {task?.status === 'failed' && <p role="status">分析未完成，未发布统计与原文。请重试任务。</p>}
        {task?.status === 'succeeded' && savedResult && !result && <Alert type="error" message="分析结果未通过校验，未发布回答。" />}
        {chat.errors[turn.id] && turn.taskId && <Alert type="error" showIcon message="重试未完成" description={chat.errors[turn.id]} />}
        {task && canRetry(task.status) && <Button className="spaced" loading={active && chat.posting} disabled={chat.pending} onClick={() => void chat.retry(turn)}>重试任务</Button>}
        {result && <>
          <Markdown text={result.answer || (result.status === 'ok' ? '查询结果已保存，可展开统计依据与原文证据查看结果。' : '查询未完成，请查看任务错误与执行记录。')} />
          <Button size="small" onClick={() => exportAgentReport(result, turn.taskId)}>导出分析报告</Button>
          <details className="chat-report"><summary>查看统计、证据与执行记录</summary>
            <AgentResults result={result} idPrefix={`agent-${turn.id}`} onDetail={(value, title) => setDetail({ value, title })} onPrepare={products => onPrepare(result, products)} />
          </details>
        </>}
        {result && detail && <section className="chat-inline-detail" aria-label="数据详情">
          <h3>{detail.title}</h3><Space wrap><Button aria-label="关闭数据详情" onClick={() => setDetail(undefined)}>关闭详情</Button><Button onClick={() => exportResult(detail.value, 'agent-detail')}>下载 JSON</Button></Space>
          <pre className="json-view">{JSON.stringify(detail.value, null, 2)}</pre>
        </section>}
  </MessageFrame>;
}
