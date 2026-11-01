import { useState } from 'react';
import { Button } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import type { PublicAnswer } from '../../types';
import { publicAnswerText } from './public-answer';

export default function PublicAnswerView({ answer }: { answer: PublicAnswer }) {
  const [copied, setCopied] = useState(false);
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
  return <section className="public-answer" aria-label="分析回答">
    <div className="public-answer-heading">
      <span className="public-answer-kicker">分析结论</span>
      <Button type="text" size="small" icon={<CopyOutlined />} aria-label="复制回答" onClick={() => void copyAnswer()}>
        {copied ? '已复制' : '复制回答'}
      </Button>
    </div>
    <p className="public-answer-summary">{answer.status === 'partial' ? `部分完成：${answer.summary}` : answer.summary}</p>
    {answer.status === 'ok' && <>
      {!!answer.highlights.length && <ol className="public-answer-highlights">{answer.highlights.map(item => <li key={item.finding_ref}>{item.text}</li>)}</ol>}
      {answer.insights?.map((item, index) => <p key={`${item.kind}-${index}`}><strong>{item.kind === 'interpretation' ? '可能的解释：' : '分析建议：'}</strong>{item.text}</p>)}
      {answer.sample_note && <p>{answer.sample_note}</p>}
      {answer.caveats.map(note => <p className="muted" key={note}>{note}</p>)}
      {answer.generation_status === 'fallback' && <p role="status" className="muted">解释生成未完成，已根据核验统计生成可靠摘要。</p>}
    </>}
  </section>;
}
