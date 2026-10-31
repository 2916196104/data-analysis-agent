import { useState } from 'react';
import { Alert, Button, Card, Input, Select, Space, Table, Tag, Tooltip } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { exportResult } from './local-history';
import { rate } from './ResultCards';
import { comparisonRows, sampleState, sectionSummary } from './agent-report';
import type { AgentResult, AgentToolResult, Summary } from './types';
import ExtendedResults from './features/agent/ExtendedResults';
import { exportAgentReport } from './features/agent/report-export';

const toolNames: Record<string, string> = {
  get_analysis_context: '读取分析范围', get_trend: '查询评论趋势', compare_products: '比较商品',
  get_top_issues: '比较维度问题', get_evidence: '查询原文证据',
  get_data_quality: '查看数据质量', get_aspect_trend: '查询维度趋势', get_positive_aspects: '查询正向维度',
  get_rating_distribution: '查询星级分布', compare_time_windows: '比较时间窗口',
};
const sectionNames = { opinion_breakdown: '观点文本分析', context: '分析范围', trend: '评论趋势', comparison: '商品对比', issues: '六维度问题', evidence: '原文证据',
  quality: '数据质量', aspect_trend: '维度趋势', ratings: '星级分布', window_comparison: '时间窗口对比', positive_aspects: '正向维度' };
const countColumns = [
  { title: '评论数', dataIndex: 'review_count', width: 85 },
  { title: '成功标注（分母）', dataIndex: 'successful_reviews', width: 135 },
  { title: '负面数', dataIndex: 'negative_review_count', width: 80 },
  { title: '负面占比', key: 'negative_rate', width: 140, render: (_: unknown, row: Summary) =>
    row.successful_reviews > 0 ? <><strong>{rate(row.negative_rate)}</strong><div className="muted">{row.negative_review_count} / {row.successful_reviews}</div></>
      : <Tooltip title={sampleState(row).reason}><span>{sampleState(row).label}</span></Tooltip> },
  { title: '标注覆盖率', key: 'labeling_coverage', width: 140, render: (_: unknown, row: Summary) => <>
    {rate(row.labeling_coverage)}<div className="muted">{row.successful_reviews} / {row.eligible_text_reviews} 条有效正文</div></> },
  { title: '数据状态', key: 'state', width: 130, render: (_: unknown, row: Summary) => {
    const state = sampleState(row);
    return <Tooltip title={state.reason}><Tag color={state.color}>{state.label}</Tag></Tooltip>;
  } },
];

function ProductComparison({ item, titles, onPrepare }: {
  item: Extract<AgentToolResult, { kind: 'comparison' }>;
  titles: Map<string, string | null>; onPrepare?: (productIds: string[]) => void;
}) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const rows = comparisonRows(item);
  const needsLabels = rows.filter(row => row.unprocessed_reviews + row.failed_reviews > 0);
  const filtered = rows.filter(row => `${row.product_id} ${titles.get(row.product_id) || ''}`.toLowerCase().includes(search.trim().toLowerCase())
    && (filter === 'all' || (filter === 'ready' ? row.successful_reviews > 0 : row.successful_reviews === 0)));
  return <>
    <p>{sectionSummary(item)}</p>
    <Space wrap className="spaced-bottom agent-table-controls">
      <Input aria-label="搜索对比商品" placeholder="搜索商品名称或 ID" allowClear value={search} onChange={event => setSearch(event.target.value)} />
      <Select aria-label="筛选商品数据状态" value={filter} onChange={setFilter} options={[
        { value: 'all', label: `全部商品（${rows.length}）` },
        { value: 'ready', label: `可计算占比（${rows.filter(row => row.successful_reviews > 0).length}）` },
        { value: 'missing', label: `暂无占比（${rows.filter(row => !row.successful_reviews).length}）` },
      ]} />
      {onPrepare && needsLabels.length > 0 && <Button onClick={() => onPrepare(needsLabels.map(row => row.product_id))}>准备待处理商品（{needsLabels.length}）</Button>}
    </Space>
    <Table size="small" rowKey="product_id" dataSource={filtered} pagination={{ pageSize: 8, hideOnSinglePage: true, showSizeChanger: false }} scroll={{ x: 930 }} columns={[
      { title: '商品', dataIndex: 'product_id', width: 220, render: (id: string) => <><strong>{titles.get(id) || id}</strong>{titles.get(id) && <div className="muted">{id}</div>}</> },
      ...countColumns,
    ]} />
    {needsLabels.length > 0 && <p className="muted">待标注 {needsLabels.reduce((total, row) => total + row.unprocessed_reviews, 0)} 条，标注失败 {needsLabels.reduce((total, row) => total + row.failed_reviews, 0)} 条。状态按本次选用的标注版本统计；其他版本的标注不计入分母。</p>}
  </>;
}

export default function AgentResults({ result, onDetail, onPrepare, onEvidenceNext, idPrefix = 'agent' }: {
  result: AgentResult; onDetail: (value: unknown, title: string) => void;
  onPrepare?: (productIds: string[]) => void;
  onEvidenceNext?: () => void;
  idPrefix?: string;
}) {
  const selected = (result.result_ids || []).map(id => result.results[id]).filter(Boolean);
  if (result.status === 'error' || result.validation?.status === 'failed') {
    return <Alert type="error" message="分析未完成，统计与原文暂不展示。" />;
  }
  if (result.status === 'unsupported') return <Alert type="info" message={result.answer || '当前查询能力不足，请调整问题。'} />;
  const overview = Object.values(result.results).find(item => item.kind === 'context');
  const titles = new Map(overview?.data.products?.map(product => [product.product_id, product.title]));
  const summary = overview?.data.summary;
  return <div className="results-stack spaced agent-results" aria-label="Agent 分析结果">
    <div className="section-heading"><h2>Agent 分析结果</h2><Tag>只读查询</Tag></div>
    {!result.public_answer && <Card title="分析摘要">
      {result.status === 'ok' ? <ul className="agent-summary">{selected.map(item => <li key={item.result_id}>
        <a href={`#${idPrefix}-${item.result_id}`}>{sectionNames[item.kind]} [{item.result_id}]</a><span>{sectionSummary(item)}</span>
      </li>)}</ul> : <p className="agent-answer">{result.answer || '分析未完成，请查看任务错误和执行记录。'}</p>}
      {summary && <div className="agent-overview">
        <span>评论总数 <strong>{summary.review_count}</strong></span>
        <span>成功标注 <strong>{summary.successful_reviews}</strong></span>
        <span>待标注 <strong>{summary.unprocessed_reviews}</strong></span>
        <span>标注失败 <strong>{summary.failed_reviews}</strong></span>
      </div>}
      {summary && !summary.successful_reviews && <Alert className="spaced" type="warning" showIcon message={sampleState(summary).reason}
        description="暂无可用于负面占比的成功标注样本。可检查选用的标注版本，或准备数据后重新分析；这不表示负面占比为 0%。" />}
      {onPrepare && summary && summary.failed_reviews + summary.unprocessed_reviews > 0 && <Button className="spaced" onClick={() => onPrepare(result.scope.product_ids)}>补齐当前范围标注</Button>}
      <p className="muted spaced">负面占比 = 负面评论数 / 成功标注数；覆盖率 = 成功标注数 / 有效正文数。未标注评论不用于判断正负面。成功标注不足 20 条或覆盖率低于 80% 时标记为“样本有限”。</p>
    </Card>}
    {selected.map(item => <Card id={`${idPrefix}-${item.result_id}`} key={item.result_id} title={`${sectionNames[item.kind]} [${item.result_id}]`}
      extra={<Button size="small" onClick={() => onDetail(item, `统计依据 [${item.result_id}]`)}>统计依据</Button>}>
      {item.kind === 'trend' ? <>
        <p className="muted">按 UTC 统计；首末时间段可能不完整，比较前请核对日期和标注覆盖率。</p>
        <Table size="small" rowKey="bucket" dataSource={item.data.buckets} pagination={{ pageSize: 8, hideOnSinglePage: true, showSizeChanger: false }} scroll={{ x: 850 }} columns={[
          { title: item.data.period === 'day' ? '日期（UTC）' : '月份（UTC）', dataIndex: 'bucket', width: 140 }, ...countColumns,
        ]} />
      </> : item.kind === 'comparison' ? <ProductComparison item={item} titles={titles} onPrepare={onPrepare} />
        : item.kind === 'evidence' ? <>
          <p className="muted">匹配 {item.data.total} 条 · 第 {item.data.page} 页 · 展示 {item.data.items.length} 条。原文仅是示例证据，不能单独确认问题根因；没有匹配证据不等于没有问题。</p>
          {item.data.items.length ? <div className="agent-evidence-list">{item.data.items.map(row => <article key={row.review_id}>
            <div><strong>{titles.get(row.product_id) || row.product_id}</strong><span className="muted"> · {row.published_at.slice(0, 10)}</span></div>
            {Array.from(new Set(row.opinions.map(opinion => opinion.quote))).map((quote, index) => <blockquote key={index}>{quote}</blockquote>)}
            <Button type="link" onClick={() => onDetail(row, `原文证据 #${row.review_id}`)}>查看原文 #{row.review_id}</Button>
          </article>)}</div> : <Alert type="info" message="当前条件没有匹配证据，可检查标注覆盖率、维度、情感和时间范围。" />}
          {onEvidenceNext && item.data.items.length > 0 && item.data.total > item.data.page * item.data.items.length &&
            <Button className="spaced" onClick={onEvidenceNext}>继续查看下一页</Button>}
        </> : item.kind === 'issues' ? <>
          <p className="muted">成功标注分母：当前 {item.data.current?.successful_reviews ?? '未知'} 条{item.data.baseline ? `，基线 ${item.data.baseline.successful_reviews} 条；任一窗口无成功标注时不计算占比变化。` : '；本轮未进行基线对比。'}</p>
          <Table size="small" rowKey="aspect" dataSource={item.data.issues.filter(row => !item.parameters.aspect || row.aspect === item.parameters.aspect)} pagination={false} scroll={{ x: 560 }} columns={[
            { title: '维度', dataIndex: 'aspect_label' }, { title: '负面数', dataIndex: 'negative_review_count' },
            { title: '负面占比', dataIndex: 'negative_rate', render: (value: number | null) => value === null ? '暂无成功标注' : rate(value) },
            ...(item.data.baseline ? [{ title: '较基线（百分点）', dataIndex: 'negative_rate_change_pp', render: (value: number | null) => value === null ? '样本不足' : `${value > 0 ? '+' : ''}${value.toFixed(1)}` }] : []),
          ]} />
        </> : item.kind === 'context' ? <p>{sectionSummary(item)}</p> : <ExtendedResults item={item} />}
    </Card>)}
    <details className="agent-audit">
      <summary>开发调试与结果校验</summary>
      <Card title="执行与校验" extra={<Button onClick={() => onDetail(result, 'Agent 完整结果')}>详情</Button>}>
        {result.validation ? <>
          <Tag color={result.validation.status === 'passed' ? 'green' : 'red'}>{result.validation.status === 'passed' ? '结果校验通过' : '结果校验未通过'}</Tag>
          <ul>{result.validation.checks.map(check => <li key={check.name}>{check.name}：{check.passed ? '通过' : '未通过'} · {check.detail}</li>)}</ul>
        </> : <p className="muted">此历史结果未保存自动校验记录；可通过统计依据核对分子、分母和原文。</p>}
        <details><summary>查看工具任务清单与执行记录</summary>
          <p className="muted">此清单记录实际查询及结果引用，不代表对问题语义的完整性保证。模型调用 {result.total_model_calls ?? result.model_calls} 次
            {result.answer_model_calls !== undefined && `（规划 ${result.model_calls} 次，解释 ${result.answer_model_calls} 次）`} · 工具调用 {result.tool_calls} 次 · 耗时 {result.elapsed_seconds.toFixed(1)} 秒</p>
          {result.performance && <p className="muted">阶段耗时：规划 {result.performance.planning_duration_ms.toFixed(1)} ms · 查询 {result.performance.query_duration_ms.toFixed(1)} ms · 校验 {result.performance.validation_duration_ms.toFixed(1)} ms · 压缩 {result.performance.compression_duration_ms.toFixed(1)} ms · 回答 {result.performance.answer_duration_ms.toFixed(1)} ms。</p>}
          {result.trace.filter(event => event.event === 'tool_call').map((event, index) =>
            <p key={index}>{index + 1}. {toolNames[event.tool || ''] || event.tool} · {event.status === 'success' ? '成功' : '失败'}{event.result_id ? ` [${event.result_id}]` : ''}</p>)}
          {result.warnings?.map(warning => <p key={warning} className="muted">{warning}</p>)}
        </details>
        <Button className="spaced" aria-label="下载 Agent JSON" icon={<DownloadOutlined />} onClick={() => exportResult(result, 'review-agent')}>下载 Agent JSON</Button>
        <Button className="spaced" icon={<DownloadOutlined />} onClick={() => exportAgentReport(result)}>导出 Markdown 报告</Button>
      </Card>
      <p className="result-footnote">标注版本：{result.scope.model} / {result.scope.prompt_version} · 范围：{result.scope.date_from} — {result.scope.date_to}（UTC） · 查询时刻 {result.snapshot_at} · 准备新数据后需重新分析。</p>
    </details>
  </div>;
}
