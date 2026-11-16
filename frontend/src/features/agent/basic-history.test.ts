import { describe, expect, it } from 'vitest';
import { basicHistory, type BasicChatTurn, type ChatTurn } from './chat-state';

const turn = (i: number, answer = `回答${i}`): BasicChatTurn => ({ kind: 'basic', id: `${i}`, question: `问题${i}`, status: 'succeeded', answer });

describe('基础问答历史预算', () => {
  it('只携带最近六对成功基础问答，保持原始顺序', () => {
    expect(basicHistory(Array.from({ length: 10 }, (_, i) => turn(i)))).toEqual(
      Array.from({ length: 6 }, (_, i) => ({ question: `问题${i + 4}`, answer: `回答${i + 4}` })),
    );
  });
  it('排除失败与未完成回答，并在分析消息处切断基础历史', () => {
    const analysis = { kind: 'analysis', id: 'a' } as ChatTurn;
    expect(basicHistory([turn(0), analysis, turn(1), { ...turn(2), status: 'failed' }, { ...turn(3), status: 'pending' }]))
      .toEqual([{ question: '问题1', answer: '回答1' }]);
    expect(basicHistory([])).toEqual([]);
  });
  it('字符预算按整轮裁剪，不在最新回答放不下时回退到更老回答', () => {
    expect(basicHistory([turn(0), turn(1, '答'.repeat(12000))])).toEqual([]);
    expect(basicHistory([turn(0, '答'.repeat(7000)), turn(1, '答'.repeat(7000))])).toHaveLength(1);
  });
});
