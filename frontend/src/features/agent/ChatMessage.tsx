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
import PublicAnswerView from './PublicAnswerView';

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
    && ['ok', 'unsupported'].includes(savedResult.status) && savedResult.validation?.status !== 'failed'
    && (!savedResult.public_answer || ['ok', 'unsupported'].includes(savedResult.public_answer.status)) ? savedResult : undefined;
  const [detail, setDetail] = useState<{ value: unknown; title: string }>();
  // 执行范围是任务元数据；失败时仍展示，正文继续受成功校验约束。
  const scope = savedResult?.scope || turn.parameters;
  const topicFailures = Object.values(savedResult?.results || {}).flatMap(item => {
    if (item.kind !== 'aspect_drilldown') return [];
    const aspect = ({ quality: '商品质量', effectiveness: '使用效果', price: '价格', packaging: '包装', logistics: '物流', service: '客服' } as Record<string, string>)[item.data.aspect || ''] || '评论';
    return (item.data.failures || []).map(failure => {
      const stage = failure.batch === 0 ? '主题发现' : `主题归属复核第 ${failure.batch} 批`;
      const reason = ({ timeout: '请求超时', budget_exhausted: '总耗时预算已用完',
        connection_error: '无法连接模型服务', http_error: '模型服务请求失败', invalid_response: '模型响应未通过校验',
        invalid_or_failed_batch: '模型调用失败或响应未通过校验', invalid_or_failed_discovery: '模型调用失败或响应未通过校验',
      } as Record<string, string>)[failure.code] || '未完成';
      return { code: failure.code, message: aspect + (failure.message || stage + reason) + '。' };
    });
  });
  const products = scope.product_ids.length > 2 ? `${scope.product_ids.length} 款商品`
    : scope.product_ids.length ? scope.product_ids.join('、') : '全部商品';
  const dataset = scope.dataset_version || scope.source || '当前数据集';
  const failureDetails = <>{topicFailures.map((failure, index) => <p key={index}>{failure.message}</p>)}
    <details><summary>错误详情</summary>{[task?.error?.code, ...topicFailures.map(failure => failure.code)].filter(Boolean).join(' · ')}</details></>;
  return <MessageFrame turn={turn}>
        <div className="chat-scope-summary" aria-label="本次问题的简要范围">
          <span>当前数据集：{dataset}</span><span>商品：{products}</span>
        </div>
        <details className="chat-turn-scope" aria-label="本次问题的固定范围"><summary>查看完整查询范围</summary>
          <dl className="assistant-scope-values">
            <div><dt>范围依据</dt><dd>{savedResult?.scope ? '本轮实际执行范围' : '提交范围，执行后以实际范围为准'}</dd></div>
            <div><dt>数据集</dt><dd>{scope.source} / {scope.dataset_version}</dd></div>
            <div><dt>标注版本</dt><dd>{scope.model} · {scope.prompt_version}</dd></div>
            <div><dt>商品</dt><dd>{scope.product_ids.length ? scope.product_ids.join('、') : '全部商品'}</dd></div>
            <div><dt>当前窗口（UTC）</dt><dd>{scope.date_from} — {scope.date_to}</dd></div>
            <div><dt>基线窗口（UTC）</dt><dd>{scope.baseline_from} — {scope.baseline_to}</dd></div>
            {scope.as_of && <div><dt>评论截止时间</dt><dd>{scope.as_of}</dd></div>}
          </dl>
          <details className="chat-developer-scope"><summary>开发调试字段</summary>
            <dl className="assistant-scope-values">
              <div><dt>标注服务</dt><dd>{scope.base_url}</dd></div>
              <div><dt>提示词版本</dt><dd>{scope.prompt_version}</dd></div>
            </dl>
          </details>
        </details>
        {turn.query && <details className="chat-query-snapshot"><summary>查看本轮查询快照</summary>
          <p>记录时间：{turn.query.createdAt}{turn.query.parentTaskId ? ` · 承接任务：${turn.query.parentTaskId}` : ' · 独立查询'}</p>
          <pre>{turn.query.effectiveQuestion}</pre>
          {savedResult?.snapshot_at && <p>执行快照：{savedResult.snapshot_at}</p>}
          <Button size="small" onClick={() => exportResult({ question: turn.question, parameters: turn.parameters, selection: turn.selection,
            query: turn.query, clarification: turn.clarification, taskId: turn.taskId,
            snapshot_at: savedResult?.snapshot_at, resolved_scope: savedResult?.scope,
            failure_reasons: topicFailures, results: result?.results }, 'query-snapshot')}>下载查询快照</Button>
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
        {task && isActive(task.status) && <div className="chat-status" role="status"><Spin size="small" /> {task.progress.stage === 'starting' ? '正在准备分析，首次本地检索可能需要加载模型，请稍候…' : stageLabels[task.progress.stage] || '正在分析…'}</div>}
        {task?.error && task.status !== 'partial' && <Alert type="error" showIcon message={topicFailures.length ? '主题分析未完成' : task.error.message === 'embedding_failed' ? '本地语义检索暂不可用，请检查模型运行配置后重试。' : task.error.message}
          description={failureDetails} />}
        {task?.status === 'partial' && <Alert type="warning" showIcon message="任务部分完成"
          description={<>完整回答尚未确认，统计与原文暂不展示。可以重试任务。{topicFailures.length > 0 && failureDetails}</>} />}
        {task?.status === 'failed' && <p role="status">分析未完成，未发布统计与原文。请重试任务。</p>}
        {task?.status === 'succeeded' && savedResult && !result && <Alert type="error" message="分析结果未通过校验，未发布回答。" />}
        {chat.errors[turn.id] && turn.taskId && <Alert type="error" showIcon message="重试未完成" description={chat.errors[turn.id]} />}
        {task && canRetry(task.status) && <Button className="spaced" loading={active && chat.posting} disabled={chat.pending} onClick={() => void chat.retry(turn)}>重试任务</Button>}
        {result && <>
          {result.public_answer && ['ok', 'unsupported'].includes(result.public_answer.status)
            ? <PublicAnswerView answer={result.public_answer} evidence={(result.result_ids || []).flatMap(id => {
              const item = result.results[id];
              return item?.kind === 'semantic_evidence' ? item.data.retrieval.evidence : [];
            })} />
            : <p>{result.status === 'unsupported' ? '当前问题超出已有分析能力，请调整查询目标。'
              : '历史分析结果已保存，可展开统计详情查看已核验的指标与原文。'}</p>}
          <Button size="small" onClick={() => exportAgentReport(result, turn.taskId)}>导出分析报告</Button>
          <details className="chat-report"><summary>查看统计、证据与执行记录</summary>
            <AgentResults result={result} idPrefix={`agent-${turn.id}`} onDetail={(value, title) => setDetail({ value, title })}
              onPrepare={products => onPrepare(result, products)}
              onEvidenceNext={active && !chat.pending ? () => void chat.send(turn.parameters, '继续查原文') : undefined} />
          </details>
        </>}
        {result && detail && <section className="chat-inline-detail" aria-label="数据详情">
          <h3>{detail.title}</h3><Space wrap><Button aria-label="关闭数据详情" onClick={() => setDetail(undefined)}>关闭详情</Button><Button onClick={() => exportResult(detail.value, 'agent-detail')}>下载 JSON</Button></Space>
          <pre className="json-view">{JSON.stringify(detail.value, null, 2)}</pre>
        </section>}
  </MessageFrame>;
}
