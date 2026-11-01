import { Table } from 'antd';
import { rate } from '../../ResultCards';
import type { AgentToolResult, RatingGroup } from '../../types';

export default function ExtendedResults({ item }: { item: AgentToolResult }) {
  if (item.kind === 'aspect_drilldown') return <>
    <p>{item.data.population_mode === 'sample' ? '样本探索：非随机受限样本，占比不能代表总体。' : '对本次匹配观点集合进行二级主题分析。'}主题为模型归并候选，未经人工确认。</p>
    <p>纳入 {item.data.coverage.selected_reviews} 条评论、{item.data.coverage.selected_opinions} 条观点；已复核 {item.data.coverage.evaluated_opinions} 条，已归类 {item.data.coverage.classified_opinions} 条，未归类 {item.data.coverage.unclassified_opinions} 条。同一评论可进入多个主题，各主题内去重，数量不能相加作为总评论数。</p>
    <Table size="small" rowKey="topic_id" dataSource={item.data.topics} pagination={false} scroll={{ x: 640 }} columns={[
      { title: '候选子主题', dataIndex: 'name', width: 160 }, { title: '去重评论数', dataIndex: 'review_count' },
      { title: '观点数', dataIndex: 'opinion_count' }, { title: '占纳入评论', key: 'rate', render: (_, row) => <>{rate(row.review_rate)}<div className="muted">分母 {row.review_denominator} 条</div></> },
      { title: '代表评论', key: 'review', render: (_, row) => `#${row.review_ids[0]}` },
    ]} />
    {!item.data.topics.length && <p role="status">尚无真实观点与归属复核共同支持的主题。</p>}
  </>;
  if (item.kind === 'positive_aspects') return <>
    <p>正面提及按评论去重，同一评论可命中多个维度。正向观点评论 {item.data.summary.positive_review_count} 条，成功标注 {item.data.summary.successful_reviews} 条。</p>
    {item.data.data_status !== 'ok' && <p role="status">{item.data.data_status === 'insufficient_data' ? '暂无成功标注，无法分析正向维度。' : '当前成功标注中没有正向观点，维度占比暂无数据。'}</p>}
    <Table size="small" rowKey="aspect" pagination={false} dataSource={item.data.aspects.filter(row => !item.parameters.aspect || row.aspect === item.parameters.aspect)} scroll={{ x: 600 }} columns={[
      { title: '维度', dataIndex: 'aspect_label' }, { title: '正面提及评论数', dataIndex: 'positive_review_count' },
      { title: '占正向观点评论比例', key: 'positive_rate', render: (_, row) => <>{row.positive_rate == null ? '无数据' : rate(row.positive_rate)}<div className="muted">分母 {row.positive_rate_denominator} 条正向观点评论</div></> },
      { title: '占成功标注评论比例', key: 'positive_success_rate', render: (_, row) => <>{row.positive_success_rate == null ? '无数据' : rate(row.positive_success_rate)}<div className="muted">分母 {row.positive_success_rate_denominator} 条成功标注</div></> },
    ]} />
  </>;
  if (item.kind === 'quality') {
    const { summary, scope } = item.data;
    return <>
      <p className="muted">{scope.date_from} — {scope.date_to}（UTC）。缺失正文占全部评论；失败率和覆盖率以有效正文为分母。</p>
      <Table size="small" rowKey="name" pagination={false} dataSource={[
        { name: '全部评论', count: summary.review_count, ratio: null },
        { name: '缺失正文', count: summary.empty_text_reviews, ratio: item.data.missing_text_rate },
        { name: '有效正文', count: summary.eligible_text_reviews, ratio: null },
        { name: '成功标注 / 覆盖率', count: summary.successful_reviews, ratio: summary.labeling_coverage },
        { name: '失败标注 / 失败率', count: summary.failed_reviews, ratio: item.data.failed_rate },
        { name: '待标注', count: summary.unprocessed_reviews, ratio: null },
      ]} columns={[{ title: '指标', dataIndex: 'name' }, { title: '评论数', dataIndex: 'count' },
        { title: '占比', dataIndex: 'ratio', render: rate }]} />
      {!!item.data.failures.length && <p>失败类型：{item.data.failures.map(row => `${row.error_code || '未知'} ${row.review_count} 条`).join('；')}</p>}
      <details><summary>导入来源指纹（{item.data.sources.length} 个文件）</summary>
        {item.data.sources.map(row => <p key={row.file_sha256} className="result-footnote">sha256:{row.file_sha256} · {row.review_count} 条</p>)}
      </details>
    </>;
  }
  if (item.kind === 'aspect_trend') {
    const rows = item.data.buckets.flatMap(row => Object.entries(row.aspects).map(([aspect, counts]) => ({
      key: `${row.bucket}-${aspect}`, bucket: row.bucket, aspect: item.data.aspects[aspect],
      ...counts, successful_reviews: row.successful_reviews, labeling_coverage: row.labeling_coverage,
    })));
    return <>
      <p className="muted">每个维度按评论去重；占比分母为该时间段全部成功标注评论。首末时间段可能不完整。</p>
      <Table size="small" rowKey="key" dataSource={rows} pagination={{ pageSize: 12, hideOnSinglePage: true }} scroll={{ x: 680 }} columns={[
        { title: item.data.period === 'day' ? '日期（UTC）' : '月份（UTC）', dataIndex: 'bucket', width: 140 },
        { title: '维度', dataIndex: 'aspect' }, { title: '负面数', dataIndex: 'negative_review_count' },
        { title: '成功标注（分母）', dataIndex: 'successful_reviews' },
        { title: '负面占比', dataIndex: 'negative_rate', render: rate },
        { title: '覆盖率', dataIndex: 'labeling_coverage', render: rate },
      ]} />
    </>;
  }
  if (item.kind === 'opinion_breakdown') return <>
    <p className="muted">匹配 {item.data.total} 条评论、{item.data.total_opinions} 条观点。按原始观点文本和情感精确归组，同义表述未合并；不是标准细分主题排名。同一评论可进入多个组。</p>
    <Table size="small" rowKey={row => JSON.stringify([row.quote, row.sentiment])} dataSource={item.data.groups}
      pagination={false} scroll={{ x: 640 }} columns={[
        { title: '观点原文', dataIndex: 'quote', width: 260 }, { title: '评论数', dataIndex: 'review_count' },
        { title: '观点数', dataIndex: 'opinion_count' }, { title: '占匹配评论', dataIndex: 'review_rate', render: rate },
        { title: '情感', dataIndex: 'sentiment', render: (value: string) => ({ positive: '正面', negative: '负面', neutral: '中性' })[value] || value },
        { title: '评论依据', dataIndex: 'review_id', render: (value: number) => `#${value}` },
      ]} />
    {item.data.items.map(row => <blockquote key={row.review_id}>评论 #{row.review_id}：{row.opinions.map(opinion => opinion.quote).join('；')}</blockquote>)}
  </>;
  if (item.kind === 'ratings') return <>
    <p className="muted">{item.data.scope.date_from} — {item.data.scope.date_to}（UTC）。来源为原始评分，独立于标注。分母仅包括 1–5 整数星级；缺失及其他评分单列，不四舍五入。</p>
    <Table size="small" rowKey={row => JSON.stringify([row.source, row.dataset_version, row.group])} dataSource={item.data.groups}
      pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 1040 }} columns={[
        { title: { product: '商品', month: '月份（UTC）', all: '范围' }[item.data.group_by], dataIndex: 'group', width: 140,
          render: (value: string) => value === 'all' && item.data.group_by === 'all' ? '全部已选商品' : value },
        { title: '评论数', dataIndex: 'review_count', width: 85 },
        { title: '有效星级（分母）', dataIndex: 'rated_reviews', width: 140 },
        ...[1, 2, 3, 4, 5].map(star => ({ title: `${star} 星`, key: star, width: 105, render: (_: unknown, row: RatingGroup) => {
          const value = row.stars.find(entry => entry.star === star);
          return value ? <>{value.review_count} 条<div className="muted">{rate(value.rate)}</div></> : '—';
        } })),
        { title: '缺失评分', dataIndex: 'missing_rating_reviews', width: 90 },
        { title: '其他评分', dataIndex: 'other_rating_reviews', width: 90 },
      ]} />
  </>;
  if (item.kind === 'window_comparison') {
    const data = item.data;
    const rows = [
      ['review_count', '评论数'], ['eligible_text_reviews', '有效正文'], ['successful_reviews', '成功标注'],
      ['failed_reviews', '失败标注'], ['unprocessed_reviews', '待标注'], ['negative_review_count', '负面评论数'],
      ['labeling_coverage', '标注覆盖率'], ['negative_rate', '负面占比'],
    ].map(([key, name]) => {
      const percentage = key === 'labeling_coverage' || key === 'negative_rate';
      const current = data.current[key as keyof typeof data.current];
      const baseline = data.baseline[key as keyof typeof data.baseline];
      const change = data.changes[key + (percentage ? '_pp' : '')];
      return { key, name, current: percentage ? rate(current ?? null) : current, baseline: percentage ? rate(baseline ?? null) : baseline,
        change: change == null ? '—' : `${change > 0 ? '+' : ''}${percentage ? change.toFixed(1) + ' 个百分点' : change}` };
    });
    return <>
      <p>当前：{data.scope.date_from} — {data.scope.date_to}（{data.current_days} 天）<br />
        基线：{data.baseline_scope.date_from} — {data.baseline_scope.date_to}（{data.baseline_days} 天）</p>
      <p className="muted">均按 UTC 整日统计。窗口长度不同时，不宜只按评论数量判断问题变化；空分母不计算占比变化。</p>
      <Table size="small" rowKey="key" dataSource={rows} pagination={false} scroll={{ x: 560 }} columns={[
        { title: '指标', dataIndex: 'name' }, { title: '当前', dataIndex: 'current' },
        { title: '基线', dataIndex: 'baseline' }, { title: '变化', dataIndex: 'change' },
      ]} />
      <Table className="spaced" size="small" rowKey="aspect" dataSource={data.issues} pagination={false} scroll={{ x: 560 }} columns={[
        { title: '维度', dataIndex: 'aspect_label' }, { title: '当前负面占比', dataIndex: 'negative_rate', render: rate },
        { title: '基线负面占比', dataIndex: 'baseline_negative_rate', render: rate },
        { title: '变化（百分点）', dataIndex: 'negative_rate_change_pp', render: (value: number | null) => value == null ? '—' : value.toFixed(1) },
      ]} />
    </>;
  }
  return null;
}
