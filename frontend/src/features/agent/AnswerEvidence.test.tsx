import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { PublicAnswer, SemanticEvidence } from '../../types';
import AnswerEvidence from './AnswerEvidence';

const evidence: SemanticEvidence = { key: 'synthetic:1', rank: 1, review_id: 11, product_id: 'synthetic', annotation_id: 5,
  quote: '制冷效果差', quote_start: 1, quote_end: 6, review_text: '🙂制冷效果差。其他背景。',
  file_sha256: 'a'.repeat(64), line_number: 7, effective_snapshot_sha256: 'b'.repeat(64) };
const answer = { evidence_refs: [{ review_id: 11, product_id: 'synthetic', evidence_ref: `sha256:${evidence.file_sha256}#line=7`,
  opinions: [{ opinion_key: evidence.key, quote: evidence.quote, quote_start: 1, quote_end: 6, annotation_id: 5,
    effective_snapshot_sha256: evidence.effective_snapshot_sha256 }] }] } as unknown as PublicAnswer;
afterEach(cleanup);
it('仅展开已许可引文并按 Python Unicode 字符区间高亮对应评论', () => {
  const { container } = render(<AnswerEvidence answer={answer} evidence={[evidence]} />);
  fireEvent.click(screen.getByText('查看原文 · 评论 #11'));
  expect(container.querySelector('details')!.open).toBe(true);
  expect(container.querySelector('mark')!.textContent).toBe(evidence.quote);
  expect(screen.getByText(/其余评论仅用于理解上下文/)).toBeTruthy();
});
it('拦截跨评论、商品、版本、来源和区间不一致的引用，不展示未许可候选', () => {
  for (const change of [{ review_id: 12 }, { product_id: 'other' }, { annotation_id: 6 },
    { effective_snapshot_sha256: 'other' }, { file_sha256: 'other' }, { quote_end: 99 }, { quote: '其他背景' }]) {
    const view = render(<AnswerEvidence answer={answer} evidence={[{ ...evidence, ...change }]} />);
    expect(view.container.textContent).toBe(''); view.unmount();
  }
  const view = render(<AnswerEvidence answer={{ ...answer, evidence_refs: [] }} evidence={[evidence]} />);
  expect(view.container.textContent).toBe('');
});
