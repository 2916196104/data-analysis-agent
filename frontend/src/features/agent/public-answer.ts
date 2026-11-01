import type { PublicAnswer } from '../../types';

export function publicAnswerText(answer: PublicAnswer): string {
  return [answer.summary, ...answer.highlights.map((item, index) => `${index + 1}. ${item.text}`),
    ...(answer.status === 'ok' ? (answer.insights || []).map(item => `${item.kind === 'interpretation' ? '可能的解释：' : '分析建议：'}${item.text}`) : []),
    answer.sample_note, ...answer.caveats].filter(Boolean).join('\n\n');
}
