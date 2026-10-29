import { Table } from 'antd';
import { useState } from 'react';
import type { Options } from '../../../types';
import { datasetKey } from '../../../analysis-draft';
import './dataset-summary.css';

/** 全数据集摘要独立于分析筛选，空分母与尚未标注保持不同状态。 */
export default function DatasetSummary({ options, dataset, compact = false }: {
  options: Options; dataset: string; compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const summary = options.datasets?.find(item => datasetKey(item) === dataset);
  if (!summary) return <p className="dataset-summary-unavailable" role="status">
    {dataset ? '数据集摘要暂不可用，请刷新数据。' : '选择数据集后查看摘要。'}
  </p>;
  const rows = options.profiles.map(profile => ({
    ...profile, ...summary.profiles.find(item => item.profile_id === profile.profile_id),
  }));
  const coverage = rows.length ? <div className="dataset-profile-list">
    <Table size="small" aria-label="数据集各标注版本覆盖率" rowKey="profile_id" dataSource={rows} pagination={false}
      scroll={{ x: 540 }} columns={[
        { title: '标注版本', render: (_, row) => <><span>{row.requested_model}</span><div className="dataset-profile-identity">{row.prompt_version} · {row.base_url}</div></> },
        { title: '成功标注', align: 'right', render: (_, row) => row.successful_reviews == null ? '—' : row.successful_reviews.toLocaleString() },
        { title: '有效正文', align: 'right', render: () => summary.eligible_text_reviews.toLocaleString() },
        { title: '覆盖率', align: 'right', render: (_, row) => row.labeling_coverage == null ? '—' : `${(row.labeling_coverage * 100).toFixed(1)}%` },
      ]} />
    {!summary.eligible_text_reviews && <p>暂无有效正文，覆盖率无法计算。</p>}
  </div> : <p>暂无标注版本，准备数据后可查看覆盖率。</p>;
  const definition = <p className="dataset-summary-definition">以下覆盖率包含全数据集、全部日期，不受商品或分析窗口筛选影响。</p>;
  return <section className={`dataset-summary${compact ? ' dataset-summary-compact' : ''}`} aria-label="数据集摘要">
    <div className="dataset-summary-metrics">
      <span>评论数 <strong>{summary.review_count.toLocaleString()}</strong></span>
      <span>商品数 <strong>{summary.product_count.toLocaleString()}</strong></span>
      <span>有效正文 <strong>{summary.eligible_text_reviews.toLocaleString()}</strong></span>
    </div>
    <p className="dataset-summary-dates">数据日期（UTC）：{summary.published_at_start && summary.published_at_end
      ? `${summary.published_at_start.slice(0, 10)} — ${summary.published_at_end.slice(0, 10)}` : '暂无评论日期'}</p>
    {compact ? <details className="dataset-profile-details" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>各标注版本覆盖率（全数据集 · {rows.length} 个版本）</summary>{expanded && <>{definition}{coverage}</>}
    </details> : <>{definition}{coverage}</>}
  </section>;
}

