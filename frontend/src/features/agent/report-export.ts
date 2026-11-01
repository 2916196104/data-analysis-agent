import type { AgentResult, AgentToolResult, Summary } from '../../types';
import { sectionSummary } from '../../agent-report';
import { publicAnswerText } from './public-answer';

const clean = (value: unknown) => String(value ?? '—').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/[\\`*_[\]#|]/g, '\\$&').replace(/\r?\n/g, ' ');
const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
const table = (headers: string[], rows: unknown[][]) => [headers, headers.map(() => '---'), ...rows]
  .map(row => `| ${row.map(clean).join(' | ')} |`).join('\n');
const summaryTable = (rows: [string, Summary][]) => table(
  ['范围', '评论数', '有效正文', '成功标注', '失败', '待标注', '缺失正文', '负面数', '覆盖率', '负面占比'],
  rows.map(([name, row]) => [name, row.review_count, row.eligible_text_reviews, row.successful_reviews,
    row.failed_reviews, row.unprocessed_reviews, row.empty_text_reviews, row.negative_review_count,
    percent(row.labeling_coverage), percent(row.negative_rate)]));

function statistics(item: AgentToolResult): string {
  switch (item.kind) {
    case 'aspect_drilldown': return `${item.data.population_mode === 'sample' ? '样本探索，占比不代表总体。' : '二级主题分析。'}模型归并候选未经人工确认。\n\n` + table(['候选主题', '去重评论数', '观点数', '占纳入评论', '分母'], item.data.topics.map(row => [row.name, row.review_count, row.opinion_count, percent(row.review_rate), row.review_denominator]));
    case 'opinion_breakdown': return `按原始观点文本和情感归组，同义表述未合并，非标准细分主题排名。匹配 ${item.data.total} 条评论、${item.data.total_opinions} 条观点。\n\n` + table(['观点原文', '情感', '评论数', '观点数', '占匹配评论', '评论依据'], item.data.groups.map(row => [row.quote, row.sentiment, row.review_count, row.opinion_count, percent(row.review_rate), row.review_id]));
    case 'positive_aspects': return table(['维度', '正面提及评论数', '正向观点评论（分母）', '正向提及占比', '成功标注（分母）', '占成功标注比例'],
      item.data.aspects.map(row => [row.aspect_label, row.positive_review_count, row.positive_rate_denominator,
        percent(row.positive_rate), row.positive_success_rate_denominator, percent(row.positive_success_rate)]));
    case 'context': return summaryTable([['查询范围', item.data.summary]]);
    case 'quality': return summaryTable([['查询范围', item.data.summary]]) + '\n\n' + table(['文件 SHA-256', '评论数'],
      item.data.sources.map(row => [row.file_sha256, row.review_count]));
    case 'trend': return summaryTable(item.data.buckets.map(row => [row.bucket, row]));
    case 'comparison': return summaryTable(item.data.products.map(row => [row.product_id, row]))
      + `\n\n无评论商品：${clean(item.data.no_reviews_product_ids?.join('、') || '无')}`;
    case 'issues': return table(['维度', '负面数', '当前占比', '基线占比', '变化（百分点）'],
      item.data.issues.map(row => [row.aspect_label, row.negative_review_count, percent(row.negative_rate),
        percent(row.baseline_negative_rate), row.negative_rate_change_pp]));
    case 'window_comparison': return summaryTable([['当前', item.data.current], ['基线', item.data.baseline]])
      + '\n\n' + table(['指标', '变化（占比为百分点）'], Object.entries(item.data.changes));
    case 'aspect_trend': return table(['时间（UTC）', '维度', '负面数', '成功标注（分母）', '占比', '覆盖率'],
      item.data.buckets.flatMap(row => Object.entries(row.aspects).map(([aspect, counts]) => [row.bucket,
        item.data.aspects[aspect], counts.negative_review_count, row.successful_reviews,
        percent(counts.negative_rate), percent(row.labeling_coverage)])));
    case 'ratings': return table(['分组', '星级', '评论数', '有效星级（分母）', '占比', '缺失评分', '其他评分'],
      item.data.groups.flatMap(row => row.stars.map(star => [row.group, star.star, star.review_count,
        row.rated_reviews, percent(star.rate), row.missing_rating_reviews, row.other_rating_reviews])));
    case 'evidence': return table(['评论 ID', '商品', '发布时间', '观点来源', '引用', '来源指纹和行号'],
      item.data.items.map(row => [row.review_id, row.product_id, row.published_at, row.opinion_source,
        row.opinions.map(opinion => opinion.quote).join('；'), row.evidence_ref]));
  }
}

export function buildAgentReport(result: AgentResult, taskId?: string): string {
  const scope = result.scope;
  const sections = [
    '# 评论分析报告',
    `任务：${clean(taskId || '未记录')}  \n查询时刻：${clean(result.snapshot_at)}  \n状态：${clean(result.status)}`,
    `## 问题\n\n${clean(result.question)}`,
    '## 固定分析范围',
    table(['字段', '值'], [
      ['来源', scope.source], ['数据集版本', scope.dataset_version], ['商品 ID', scope.product_ids.join('、') || '全部商品'],
      ['当前窗口（UTC）', `${scope.date_from} — ${scope.date_to}`], ['基线窗口（UTC）', `${scope.baseline_from ?? '未记录'} — ${scope.baseline_to ?? '未记录'}`],
      ['评论截止时间', scope.as_of], ['标注模型', scope.model], ['标注服务', scope.base_url], ['标注提示词版本', scope.prompt_version],
      ['执行模型', result.agent_model_identity?.model], ['执行服务', result.agent_model_identity?.base_url],
      ['助手提示词版本', result.agent_model_identity?.prompt_version],
    ]),
    '## 分析结论\n\n' + (result.public_answer?.status === 'ok' ? publicAnswerText(result.public_answer)
      : result.answer || '未生成结论。').split('\n').map(clean).join('\n'),
    '## 统计口径与来源\n\n统计由 Python 计算，本报告只格式化已保存结果，不重新查询数据。'
      + '负面占比 = 去重负面评论数 / 成功标注评论数；覆盖率 = 成功标注数 / 有效正文数。'
      + '正向维度提及占比以至少一个正向观点的评论数为分母；占成功标注比例另列，两者不可混用。'
      + '星级来自原始评分，以有效 1–5 整数星级评论为分母；缺失和其他评分不推断。空分母记为 —。'
      + '人工复核使用查询时最新快照；截止时间仅限制评论发布时间。子查询实际范围、定义及参数保存在附录。'
      + '文件指纹来自数据质量或原文证据查询；未查询原文时不补造引文。',
  ];
  for (const id of result.result_ids || []) {
    const item = result.results[id];
    if (!item) continue;
    sections.push(`## 统计依据 [${clean(id)}]\n\n${clean(sectionSummary(item))}`, statistics(item));
    const data = item.data as unknown as { scope?: { date_from: string; date_to: string }; baseline_scope?: { date_from: string; date_to: string } };
    if (data.scope) sections.push(`实际查询窗口（UTC）：${clean(data.scope.date_from)} — ${clean(data.scope.date_to)}`);
    if (data.baseline_scope) sections.push(`实际基线窗口（UTC）：${clean(data.baseline_scope.date_from)} — ${clean(data.baseline_scope.date_to)}`);
  }
  sections.push('## 校验与限制', ...(result.validation?.checks.map(check => `- ${clean(check.name)}：${check.passed ? '通过' : '未通过'}；${clean(check.detail)}`)
    || ['未保存自动校验记录。']), ...(result.warnings || []).map(warning => `- ${clean(warning)}`));
  // 动态围栏防止评论原文中的反引号提前结束 JSON 附录。
  const json = JSON.stringify({ task_id: taskId ?? null, ...result }, null, 2);
  const fence = '`'.repeat(Math.max(3, ...Array.from(json.matchAll(/`+/g), match => match[0].length + 1)));
  sections.push(`## 完整结果与来源附录\n\n${fence}json\n${json}\n${fence}`);
  return sections.join('\n\n') + '\n';
}

export function exportAgentReport(result: AgentResult, taskId?: string) {
  const blob = new Blob([buildAgentReport(result, taskId)], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `review-analysis-${result.snapshot_at.slice(0, 10).replace(/[^0-9-]/g, '') || 'report'}.md`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
