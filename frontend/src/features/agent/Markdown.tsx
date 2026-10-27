import type { ReactNode } from 'react';

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\))/g).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__'))) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('*') && part.endsWith('*')) return <em key={index}>{part.slice(1, -1)}</em>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const safe = /^(https?:\/\/|mailto:|#)/i.test(link[2]);
      return safe ? <a key={index} href={link[2]} target={link[2].startsWith('#') ? undefined : '_blank'} rel="noreferrer">{link[1]}</a> : part;
    }
    return part;
  });
}

/** 使用 React 文本节点呈现常用 Markdown，不解释原始 HTML。 */
export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  for (let i = 0; i < lines.length;) {
    const key = i;
    if (!lines[i].trim()) { i++; continue; }
    if (/^\s*```/.test(lines[i])) {
      const language = lines[i++].trim().slice(3);
      const code: string[] = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push(<pre key={key}><code data-language={language}>{code.join('\n')}</code></pre>);
      continue;
    }
    const heading = lines[i].match(/^(#{1,6})\s+(.+)$/);
    if (heading) { blocks.push(<p className={`markdown-heading level-${heading[1].length}`} key={key}>{inline(heading[2])}</p>); i++; continue; }
    if (i + 1 < lines.length && lines[i].includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split('|').map(value => value.trim());
      const headers = cells(lines[i]); i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(<div className="markdown-table" key={key}><table><thead><tr>{headers.map((cell, j) => <th key={j}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^\s*(?:[-*+] |\d+\. )/.test(lines[i])) {
      const ordered = /^\s*\d+\. /.test(lines[i]);
      const items: ReactNode[] = [];
      while (i < lines.length && (ordered ? /^\s*\d+\. / : /^\s*[-*+] /).test(lines[i])) items.push(<li key={i}>{inline(lines[i++].replace(/^\s*(?:[-*+] |\d+\. )/, ''))}</li>);
      blocks.push(ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>); continue;
    }
    if (/^>\s?/.test(lines[i])) {
      const quote: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ''));
      blocks.push(<blockquote key={key}>{inline(quote.join('\n'))}</blockquote>); continue;
    }
    blocks.push(<p key={key}>{inline(lines[i++])}</p>);
  }
  return <div className="chat-markdown">{blocks}</div>;
}
