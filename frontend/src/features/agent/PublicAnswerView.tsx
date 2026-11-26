import { useState } from 'react';
import { Button, Tag } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import type { PublicAnswer, SemanticEvidence } from '../../types';
import { publicAnswerCaveats, publicAnswerText } from './public-answer';
import { classifyUnsupportedAnswer } from './unsupported-guidance';
import AnswerEvidence from './AnswerEvidence';

const VISIBLE_CAVEATS = 2;

export default function PublicAnswerView({
  answer,
  evidence = [],
  question = '',
  onSuggestQuestion,
}: {
  answer: PublicAnswer;
  evidence?: SemanticEvidence[];
  question?: string;
  onSuggestQuestion?: (question: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const caveats = publicAnswerCaveats(answer);
  const primaryCaveats = caveats.slice(0, VISIBLE_CAVEATS);
  const extraCaveats = caveats.slice(VISIBLE_CAVEATS);
  const unsupported = answer.status === 'unsupported';
  const guidance = unsupported ? classifyUnsupportedAnswer(answer.summary, question) : undefined;

  async function copyAnswer() {
    const text = publicAnswerText(answer);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const textarea = document.createElement('textarea');
        textarea.value = text; textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed'; textarea.style.opacity = '0';
        document.body.appendChild(textarea); textarea.select(); document.execCommand('copy'); textarea.remove();
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { setCopied(false); }
  }

  return <section className={unsupported ? 'public-answer public-answer-unsupported' : 'public-answer'} aria-label="分析回答">
    <div className="public-answer-heading">
      <div className="public-answer-title-group">
        <span className="public-answer-kicker">{unsupported ? '查询受限说明' : '分析结论'}</span>
        {guidance && <Tag color="warning">{guidance.category}</Tag>}
      </div>
      <Button type="text" size="small" icon={<CopyOutlined />} aria-label="复制回答" onClick={() => void copyAnswer()}>
        {copied ? '已复制' : '复制回答'}
      </Button>
    </div>
    <p className="public-answer-summary">{answer.status === 'partial' ? `部分完成：${answer.summary}` : answer.summary}</p>
    {guidance && <div className="public-answer-guidance">
      <p className="muted">{guidance.hint}</p>
      {onSuggestQuestion && !!guidance.suggestions.length && <div className="public-answer-rewrites" aria-label="推荐改写问法">
        <span className="public-answer-rewrites-label">试试这样问：</span>
        {guidance.suggestions.map(suggestion => <Button key={suggestion} size="small"
          onClick={() => onSuggestQuestion(suggestion)}>{suggestion}</Button>)}
      </div>}
    </div>}
    {answer.status === 'ok' && <>
      {!!answer.highlights.length && <ol className="public-answer-highlights">{answer.highlights.map(item => <li key={item.finding_ref}>{item.text}</li>)}</ol>}
      <AnswerEvidence answer={answer} evidence={evidence} />
      {answer.insights?.map((item, index) => <p key={`${item.kind}-${index}`}><strong>{item.kind === 'interpretation' ? '可能的解释：' : '分析建议：'}</strong>{item.text}</p>)}
      {answer.sample_note && <p>{answer.sample_note}</p>}
      {primaryCaveats.map(note => <p className="muted" key={note}>{note}</p>)}
      {!!extraCaveats.length && <details className="public-answer-caveats-more">
        <summary>展开其余 {extraCaveats.length} 条口径与限制说明</summary>
        {extraCaveats.map(note => <p className="muted" key={note}>{note}</p>)}
      </details>}
      {answer.generation_status === 'fallback' && <p role="status" className="muted">解释生成未完成，已根据核验统计生成可靠摘要。</p>}
    </>}
  </section>;
}
