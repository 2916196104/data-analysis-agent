import type { ChatMode } from './chat-state';

export interface ModeSuggestion {
  targetMode: ChatMode;
  reason: string;
  actionLabel: string;
}

const ANALYSIS_SIGNAL_RE = /(?:差评率|好评率|好评占比|负面占比|按月|按日|按天|趋势|基线|窗口对比|数据质量|标注覆盖率|覆盖率|缺失正文|失败标注|星级分布|评分分布|差评原文|好评原文|评论原文|(?:有没有|是否有).{1,24}(?:反馈|抱怨|评论|证据)|商品\s*[A-Za-z0-9_-]{3,}|主要(?:差评|好评|问题).{0,8}维度)/;
const CONCEPT_QUESTION_RE = /(?:什么是|什么叫|如何理解|是什么意思|怎么理解|怎么算的)/;
const BASIC_GENERAL_RE = /^(?:请问)?(?:你是谁|你能做什么|你会什么|你可以做什么|怎么使用|使用说明|什么是|什么叫|如何理解|如何看待|解释一下)[^，,。]{0,40}[？?。!！]*$/;
const CONCRETE_QUERY_VERB_RE = /(?:查看|统计|分析|按月|按日|对比|给我看|列出|展示|商品\s*[A-Za-z0-9_-]{3,}|(?:有没有|是否有).{1,24}(?:反馈|抱怨|评论|证据))/;

/**
 * 轻量非侵入式模式建议：仅提供显式切换入口，绝不拦截发送或静默自动切换模式。
 */
export function suggestChatMode(input: string, mode: ChatMode): ModeSuggestion | null {
  const text = input.trim();
  if (text.length < 2) return null;
  if (mode === 'basic') {
    if (ANALYSIS_SIGNAL_RE.test(text) && !CONCEPT_QUESTION_RE.test(text)) {
      return {
        targetMode: 'analysis',
        reason: '检测到评论数据分析类问题，切换到「评论分析」可查询真实标注统计与原文。',
        actionLabel: '切换到评论分析',
      };
    }
    return null;
  }
  if (BASIC_GENERAL_RE.test(text) && !CONCRETE_QUERY_VERB_RE.test(text)) {
    return {
      targetMode: 'basic',
      reason: '这是概念解释或使用咨询，切换到「基础问答」可流式快速回答且不占用分析队列。',
      actionLabel: '切换到基础问答',
    };
  }
  return null;
}
