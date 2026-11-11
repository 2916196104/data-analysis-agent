import { useState } from 'react';
import { Alert, Button, Tag } from 'antd';
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons';
import type { AgentResult } from '../../types';
import { exportAgentReport } from './report-export';
import { p6StateLabel, readP6Publication, type P6Reference } from './p6-publication';
import './p6-results.css';

const percent = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`;

function Reference({ reference: r }: { reference: P6Reference }) {
  const chars = Array.from(r.text);
  return <details className="p6-reference"><summary>查看支持引用 · 评论 {r.review_id}</summary>
    <blockquote>{r.quote}</blockquote>
    <p className="p6-original">{chars.slice(0, r.quote_start).join('')}<mark>{r.quote}</mark>{chars.slice(r.quote_end).join('')}</p>
    <dl><div><dt>原文位置</dt><dd>[{r.quote_start}, {r.quote_end}) · Unicode 字符</dd></div>
      <div><dt>商品与时间</dt><dd>{r.product_id} · {r.published_at}</dd></div>
      <div><dt>来源</dt><dd>{r.evidence_ref}</dd></div>
      <div><dt>观点来源</dt><dd>{r.opinion_source} · 标注 {r.annotation_id} · 人工修订 {r.revision_id ?? '无'}</dd></div>
      <div><dt>观点 / 主题</dt><dd>{r.opinion_id} / {r.topic_id}</dd></div></dl>
  </details>;
}

export default function P6Results({ result, taskId }: { result: AgentResult; taskId?: string }) {
  const [copied, setCopied] = useState(false);
  const p = readP6Publication(result);
  if (!p) return <Alert type="error" message="多维度结果未通过校验，未发布主题与引用。" />;
  const unfinished = p.dimensions.filter(d => d.status !== 'complete');
  async function copy() {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(p!.answer_text);
      else {
        const area = document.createElement('textarea');
        area.value = p!.answer_text; area.style.position = 'fixed'; area.style.opacity = '0';
        document.body.appendChild(area); area.select(); document.execCommand('copy'); area.remove();
      }
      setCopied(true);
    } catch { setCopied(false); }
  }
  return <section className="p6-results" aria-label="分析回答">
    <div className="p6-heading"><h3>主要负面维度与具体主题</h3>
      <Tag color={p.status === 'ok' ? 'green' : p.status === 'error' ? 'red' : 'gold'}>{p6StateLabel[p.parent_status]}</Tag></div>
    <p>商品 {p.scope.product_ids.join('、')} · {p.scope.date_from} — {p.scope.date_to}（UTC）</p>
    <p>成功标注评论数 <strong>D={p.D}</strong>。按可信排名分析 {p.required_aspects.length} 个必做维度；已完成 {p.publishable_aspects.length} 个。</p>
    {p.parent_status !== 'complete' && <Alert showIcon type={p.status === 'error' ? 'error' : p.status === 'partial' ? 'warning' : 'info'}
      message={p6StateLabel[p.parent_status]} description={p.status === 'partial'
        ? '完整目标尚未完成。以下仅发布通过独立验收的维度；未完成维度保留在任务中。'
        : p.status === 'unsupported' ? '正确查询后没有足够支持主题的证据，未生成主题结论。' : '未发布主题结论，请查看各维度原因。'} />}
    {unfinished.length > 0 && <section className="p6-unfinished" aria-label="未完成维度"><h4>未完成维度及原因</h4>
      <ul>{unfinished.map(d => <li key={d.aspect}><strong>{d.label}</strong> · {p6StateLabel[d.status]}：{d.reason}
        <span className="muted">（{d.reason_code}）；M={d.M_a}，S={d.S_a}</span></li>)}</ul></section>}
    <div className="p6-dimensions">{p.dimensions.filter(d => d.status === 'complete').map(d => <section className="p6-dimension" key={d.aspect} aria-label={`${d.label}主题`}>
      <h4>{d.rank}. {d.label}</h4>
      <p>全量负面去重评论 <strong>M={d.M_a}</strong></p>
      <p>维度负面率 M/D = {d.M_a}/{p.D}（{percent(d.negative_rate)}）</p>
      <p>分类样本 <strong>S={d.S_a}</strong> · 样本覆盖 S/M = {percent(d.review_coverage)}</p>
      {d.coverage && <p>观点归属覆盖：{d.coverage.classified_opinions}/{d.coverage.selected_opinions}（{percent(d.coverage.opinion_coverage)}）；未归属观点仍保留在分母中。</p>}
      {d.S_a < d.M_a && <p className="p6-sample-warning">样本覆盖有限，主题频次不能外推至全量。</p>}
      <ol className="p6-topics">{d.topics.map(t => <li key={t.topic_id}><h5>{t.name}</h5><p>{t.definition}</p>
        <p>样本内去重评论 <strong>N={t.N_a_t}</strong> · N/S = {t.N_a_t}/{d.S_a}（{percent(t.sample_rate)}）</p>
        {t.evidence.map(r => <Reference key={`${r.opinion_id}:${t.topic_id}`} reference={r} />)}</li>)}</ol>
      {d.omitted_topic_count > 0 && <p className="muted">每维最多展示三个主题；另有 {d.omitted_topic_count} 个已支持候选未展示。</p>}
    </section>)}</div>
    <section className="p6-limitations" aria-label="统计口径与限制"><h4>统计口径与限制</h4><ul>{p.limitations.map(note => <li key={note}>{note}</li>)}</ul></section>
    <div className="p6-actions"><Button type="primary" icon={<DownloadOutlined />} onClick={() => exportAgentReport(result, taskId)}>导出分析报告</Button>
      <Button icon={<CopyOutlined />} aria-label="复制回答" onClick={() => void copy()}>{copied ? '已复制' : '复制回答'}</Button></div>
  </section>;
}
