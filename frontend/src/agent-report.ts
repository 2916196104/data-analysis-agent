import type { AgentToolResult, Summary } from './types';

export function sampleState(row: Summary): { label: string; color?: string; reason: string } {
  if (!row.review_count) return { label: '无评论', reason: '当前时间范围内没有评论' };
  if (!row.eligible_text_reviews) return { label: '无有效正文', reason: '评论没有可标注正文，需补充带正文的数据' };
  if (!row.successful_reviews) {
    return { label: row.unprocessed_reviews ? '待标注' : '标注失败', color: 'orange',
      reason: `当前标注版本：待标注 ${row.unprocessed_reviews} 条，失败 ${row.failed_reviews} 条；成功数为 0` };
  }
  if ((row.labeling_coverage ?? 0) < 0.8 || row.successful_reviews < 20) {
    return { label: '样本有限', color: 'gold', reason: '成功标注不足 20 条或覆盖率低于 80%，仅描述已标注样本' };
  }
  return { label: '可分析', color: 'green', reason: '占比基于当前版本成功标注的评论' };
}

export function sectionSummary(item: AgentToolResult): string {
  const data = item.data;
  switch (item.kind) {
    case 'context': return `当前范围共 ${item.data.summary.review_count} 条评论，成功标注 ${item.data.summary.successful_reviews} 条。`;
    case 'trend': return `按${item.data.period === 'day' ? '日' : '月'}汇总 ${item.data.buckets.length} 个时间段，共 ${item.data.summary.review_count} 条评论，成功标注 ${item.data.summary.successful_reviews} 条。`;
    case 'comparison': {
      const rows = item.data.products;
      const ready = rows.filter(row => row.successful_reviews > 0).length;
      const limited = rows.filter(row => row.successful_reviews > 0 && sampleState(row).label === '样本有限').length;
      const missing = item.data.no_reviews_product_ids?.length ?? 0;
      return `对比 ${rows.length + missing} 款商品：${ready} 款可计算占比（其中 ${limited} 款样本有限），${rows.length - ready} 款暂无成功标注${missing ? `，${missing} 款在窗口内无评论` : ''}。`;
    }
    case 'issues': return `汇总 ${item.data.issues.length} 个维度的负面问题；占比变化按当前窗口与基线各自的成功标注数计算。`;
    case 'evidence': return `找到 ${item.data.total} 条匹配证据，本页展示 ${item.data.items.length} 条；可展开查看完整原文及引用。`;
    default: return String(data);
  }
}

export function comparisonRows(item: Extract<AgentToolResult, { kind: 'comparison' }>) {
  // 没有评论的已选商品也保留一行，避免被误认为已完成有效对比。
  return [...item.data.products, ...(item.data.no_reviews_product_ids ?? []).map(product_id => ({
    product_id, review_count: 0, eligible_text_reviews: 0, successful_reviews: 0,
    failed_reviews: 0, unprocessed_reviews: 0, negative_review_count: 0,
    labeling_coverage: null, negative_rate: null,
  }))];
}
