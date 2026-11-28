export interface StructuredUnsupportedInput {
  unsupportedCode?: string | null;
  suggestedQuestions?: string[];
  clarificationChoices?: { label: string; question: string }[];
}

export interface UnsupportedGuidance {
  category: string;
  hint: string;
  suggestions: string[];
  choices?: { label: string; question: string }[];
}

const CODE_GUIDANCE: Record<string, { category: string; hint: string }> = {
  statistics: {
    category: '统计粒度限制',
    hint: '当前支持按六大评价维度统计趋势、占比与对比；细分表达可直接检索相关买家原文反馈。',
  },
  subtopic_count: {
    category: '统计粒度限制',
    hint: '当前支持按六大评价维度统计趋势、占比与对比；细分表达可直接检索相关买家原文反馈。',
  },
  subtopic_trend: {
    category: '统计粒度限制',
    hint: '当前支持按六大评价维度统计趋势、占比与对比；细分表达可直接检索相关买家原文反馈。',
  },
  subtopic_comparison: {
    category: '统计粒度限制',
    hint: '当前支持按六大评价维度统计趋势、占比与对比；细分表达可直接检索相关买家原文反馈。',
  },
  cause_check: {
    category: '不支持因果证明',
    hint: '评论数据仅反映买家提及频次与原文表达，不能证明客观因果关系，可改为查看差评维度或原文。',
  },
  forecast: {
    category: '不支持销量预测',
    hint: '评论助手不含销量或营收数据，仅支持基于已标注评论分析正负向趋势、维度分布与原文。',
  },
  denominator_conflict: {
    category: '统计口径需明确',
    hint: '单次查询仅支持一种分母口径，请将不同分母的问题拆开提问。',
  },
  missing_product: {
    category: '缺少必要查询范围',
    hint: '可在下方范围栏确认商品与日期，或在问题中补充具体商品 ID、评价维度后重新提问。',
  },
  missing_scope: {
    category: '缺少必要查询范围',
    hint: '可在下方范围栏确认商品与日期，或在问题中补充具体商品 ID、评价维度后重新提问。',
  },
  clarification_needed: {
    category: '需要进一步澄清',
    hint: '当前问题需要补充更明确的查询条件或维度，可直接点击下方推荐问法重新发起分析。',
  },
  insufficient_evidence: {
    category: '样本或证据不足',
    hint: '当前筛选范围内满足条件的已核验样本不足，可放宽时间范围、切换维度或查看数据质量。',
  },
  unsupported_capability: {
    category: '当前能力暂未覆盖',
    hint: '可将复合问题拆分为单一维度的趋势、对比或原文检索，以便获得可核验的统计结果。',
  },
};

const ASPECT_KEYWORDS: { pattern: RegExp; label: string }[] = [
  { pattern: /物流|配送|快递|发货|送货/, label: '物流' },
  { pattern: /客服|售后|安装|维修|师傅/, label: '客服' },
  { pattern: /制冷|制热|效果|静音|噪音|声音|风力|耗电|省电/, label: '使用效果' },
  { pattern: /质量|做工|材质|故障|坏了|漏水/, label: '商品质量' },
  { pattern: /价格|性价比|划算|贵|便宜/, label: '价格' },
  { pattern: /包装|破损|外箱/, label: '包装' },
];

const SUBTOPIC_KEYWORDS: { pattern: RegExp; label: string }[] = [
  { pattern: /制冷/, label: '制冷' },
  { pattern: /静音|噪音|声音/, label: '静音或噪音' },
  { pattern: /安装/, label: '安装' },
  { pattern: /物流|配送|发货/, label: '物流配送' },
  { pattern: /客服|售后/, label: '客服售后' },
];

function detectAspect(question: string): string | undefined {
  return ASPECT_KEYWORDS.find(item => item.pattern.test(question))?.label;
}

function detectSubtopic(question: string): string | undefined {
  return SUBTOPIC_KEYWORDS.find(item => item.pattern.test(question))?.label;
}

function uniqueSuggestions(items: string[]): string[] {
  return [...new Set(items.filter(Boolean))].slice(0, 3);
}

function extractSuggestedQuestions(summary: string): string[] {
  const matches: string[] = [];
  const pattern = /(?:可以先问|例如回复|可改为问|可以问)[：:]\s*[“"]([^”"]+)[”"]/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(summary)) !== null) {
    const candidate = match[1].trim();
    if (candidate) matches.push(candidate);
  }
  return matches;
}

function fallbackSuggestionsForCode(code: string, aspect?: string, subtopic?: string): string[] {
  switch (code) {
    case 'statistics':
    case 'subtopic_count':
    case 'subtopic_trend':
    case 'subtopic_comparison':
      return [
        subtopic ? `有没有${subtopic}相关的买家反馈？` : '有没有制冷或安装相关的买家反馈？',
        aspect ? `按月分析${aspect}好评占比趋势` : '按月分析使用效果好评占比趋势',
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
      ];
    case 'cause_check':
      return [
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
        aspect ? `给我看 3 条${aspect}差评原文` : '给我看 3 条差评原文',
        '查看缺失正文、失败标注和覆盖率',
      ];
    case 'forecast':
      return [
        '按月分析整体好评占比趋势',
        '主要好评和差评集中在哪些维度？',
        '查看缺失正文、失败标注和覆盖率',
      ];
    case 'denominator_conflict':
      return [
        aspect ? `按月分析${aspect}好评占比趋势` : '按月分析物流好评占比趋势',
        aspect ? `按月分析${aspect}好评占成功标注评论的比例趋势` : '按月分析物流好评占成功标注评论的比例趋势',
        '对比当前窗口与基线的物流好评占比',
      ];
    case 'missing_product':
    case 'missing_scope':
      return [
        '查看缺失正文、失败标注和覆盖率',
        '主要差评集中在哪些维度？',
        '按月分析物流好评占比趋势',
      ];
    default:
      return [
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
        aspect ? `给我看 3 条${aspect}差评原文` : '给我看 3 条差评原文',
        '查看缺失正文、失败标注和覆盖率',
      ];
  }
}

/**
 * 优先消费后端结构化拒答协议（unsupported_code / suggested_questions / clarification_choices）；
 * 对缺少结构化字段的历史任务保留正则兜底，确保向后兼容。
 */
export function classifyUnsupportedAnswer(
  summary: string,
  originalQuestion = '',
  structured?: StructuredUnsupportedInput,
): UnsupportedGuidance {
  const aspect = detectAspect(originalQuestion);
  const subtopic = detectSubtopic(originalQuestion);
  const quoted = extractSuggestedQuestions(summary);
  const backendSuggestions = (structured?.suggestedQuestions ?? [])
    .map(item => (typeof item === 'string' ? item.trim() : ''))
    .filter(Boolean);
  const choices = (structured?.clarificationChoices ?? []).filter(
    item => item && typeof item.label === 'string' && item.label.trim()
      && typeof item.question === 'string' && item.question.trim(),
  );
  const code = typeof structured?.unsupportedCode === 'string' ? structured.unsupportedCode.trim() : '';

  if (code && CODE_GUIDANCE[code]) {
    const meta = CODE_GUIDANCE[code];
    return {
      category: meta.category,
      hint: meta.hint,
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        ...fallbackSuggestionsForCode(code, aspect, subtopic),
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/细分语义主题|细分反馈|独立细分标签|固定版本规则|分类分子|分类计数|分类时间序列|分类统计/.test(summary)) {
    return {
      category: '统计粒度限制',
      hint: '当前支持按六大评价维度统计趋势、占比与对比；细分表达可直接检索相关买家原文反馈。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        subtopic ? `有没有${subtopic}相关的买家反馈？` : '有没有制冷或安装相关的买家反馈？',
        aspect ? `按月分析${aspect}好评占比趋势` : '按月分析使用效果好评占比趋势',
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/因果|故障原因|不能证明.*原因/.test(summary)) {
    return {
      category: '不支持因果证明',
      hint: '评论数据仅反映买家提及频次与原文表达，不能证明客观因果关系，可改为查看差评维度或原文。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
        aspect ? `给我看 3 条${aspect}差评原文` : '给我看 3 条差评原文',
        '查看缺失正文、失败标注和覆盖率',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/销量|销售额|成交金额|预测/.test(summary)) {
    return {
      category: '不支持销量预测',
      hint: '评论助手不含销量或营收数据，仅支持基于已标注评论分析正负向趋势、维度分布与原文。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        '按月分析整体好评占比趋势',
        '主要好评和差评集中在哪些维度？',
        '查看缺失正文、失败标注和覆盖率',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/分母/.test(summary)) {
    return {
      category: '统计口径需明确',
      hint: '单次查询仅支持一种分母口径，请将不同分母的问题拆开提问。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        aspect ? `按月分析${aspect}好评占比趋势` : '按月分析物流好评占比趋势',
        aspect ? `按月分析${aspect}好评占成功标注评论的比例趋势` : '按月分析物流好评占成功标注评论的比例趋势',
        '对比当前窗口与基线的物流好评占比',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/商品\s*ID|指定一款商品|两款商品|起止日期|不存在的商品|选择.*维度|未给出具体商品/.test(summary)) {
    return {
      category: '缺少必要查询范围',
      hint: '可在下方范围栏确认商品与日期，或在问题中补充具体商品 ID、评价维度后重新提问。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        '查看缺失正文、失败标注和覆盖率',
        '主要差评集中在哪些维度？',
        '按月分析物流好评占比趋势',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  if (/未覆盖|无匹配|未检索到|候选|混合分析|混合问题|暂不支持|语义/.test(summary)) {
    return {
      category: '当前能力暂未覆盖',
      hint: '可将复合问题拆分为单一维度的趋势、对比或原文检索，以便获得可核验的统计结果。',
      suggestions: uniqueSuggestions([
        ...backendSuggestions,
        ...quoted,
        aspect ? `${aspect}差评具体体现在哪里？` : '主要差评集中在哪些维度？',
        aspect ? `给我看 3 条${aspect}差评原文` : '给我看 3 条差评原文',
        '查看缺失正文、失败标注和覆盖率',
      ]),
      ...(choices.length ? { choices } : {}),
    };
  }

  return {
    category: '暂不支持当前问法',
    hint: '可改为查询数据质量、六大评价维度统计、按月趋势、窗口对比或评论原文。',
    suggestions: uniqueSuggestions([
      ...backendSuggestions,
      ...quoted,
      '查看缺失正文、失败标注和覆盖率',
      '主要差评集中在哪些维度？',
      '按月分析物流好评占比趋势',
    ]),
    ...(choices.length ? { choices } : {}),
  };
}
