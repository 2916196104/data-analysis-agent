import type { PublicAnswer, SemanticEvidence } from '../../types';

export default function AnswerEvidence({ answer, evidence }: { answer: PublicAnswer; evidence: SemanticEvidence[] }) {
  // Only the answer's permitted references may open context, never the entire candidate list.
  const quotes = answer.evidence_refs.flatMap(ref => Array.isArray(ref.opinions) ? ref.opinions.flatMap(opinion => {
    if (!opinion || typeof opinion !== 'object') return [];
    const row = evidence.find(item => item.key === opinion.opinion_key && item.review_id === ref.review_id
      && item.product_id === ref.product_id && item.annotation_id === opinion.annotation_id
      && item.effective_snapshot_sha256 === opinion.effective_snapshot_sha256
      && ref.evidence_ref === `sha256:${item.file_sha256}#line=${item.line_number}`
      && item.quote === opinion.quote && item.quote_start === opinion.quote_start && item.quote_end === opinion.quote_end
      && Number.isInteger(item.quote_start) && Number.isInteger(item.quote_end)
      && item.quote_start >= 0 && item.quote_end > item.quote_start && item.quote_end <= Array.from(item.review_text).length
      && Array.from(item.review_text).slice(item.quote_start, item.quote_end).join('') === item.quote);
    return row ? [row] : [];
  }) : []);
  if (!quotes.length) return null;
  return <div className="answer-evidence" aria-label="回答原文引用">
    {quotes.map(row => {
      const text = Array.from(row.review_text);
      return <details key={row.key} className="answer-evidence-item">
        <summary>查看原文 · 评论 #{row.review_id}</summary>
        <p>商品 {row.product_id}</p>
        <blockquote>{row.quote}</blockquote>
        <p className="answer-review-context">{text.slice(0, row.quote_start).join('')}<mark>{text.slice(row.quote_start, row.quote_end).join('')}</mark>{text.slice(row.quote_end).join('')}</p>
        <p className="muted">高亮部分为本次引用；其余评论仅用于理解上下文，不作为新增支持事实。</p>
        <details><summary>引用信息</summary><p>观点：{row.key}</p><p>原文区间：[{row.quote_start}, {row.quote_end}) · 标注：{row.annotation_id}</p><p>来源：sha256:{row.file_sha256}#line={row.line_number}</p></details>
      </details>;
    })}
  </div>;
}
