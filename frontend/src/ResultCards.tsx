import { lazy, Suspense, useMemo } from 'react';
import { Alert, Button, Card, Empty, Statistic, Table, Tabs, Tag } from 'antd';
import { DownloadOutlined, ExpandOutlined, FileTextOutlined } from '@ant-design/icons';
import { exportResult } from './local-history';
import type { Run } from './types';

const Chart = lazy(() => import('./Chart'));
export const rate = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`;

export default function ResultCards({ run, question, onDetail }: { run: Run; question: string; onDetail: (value: unknown, title: string) => void }) {
  const { summary, buckets } = run.results.trend;
  const overview = run.results.summary;
  const scope = run.request.scope;
  const issues = run.results.products.flatMap(item => item.top_issues.issues.map(issue => ({
    ...issue, key: `${item.product.product_id}:${issue.aspect}`, product_id: item.product.product_id,
    evaluation: item.evaluations.find(e => e.aspect === issue.aspect)?.status,
  })));
  const trendOption = useMemo(() => ({
    color: ['#127568', '#d49c4a'],
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    legend: { bottom: 0, icon: 'circle', textStyle: { color: '#76827e' } },
    grid: { top: 24, left: 45, right: 24, bottom: 60 },
    xAxis: { type: 'category', data: buckets.map(row => row.bucket), axisLine: { lineStyle: { color: '#dce5e1' } }, axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef1ef' } } },
    series: [
      { name: '评论数', type: buckets.length === 1 ? 'bar' : 'line', barMaxWidth: 48, data: buckets.map(row => row.review_count), symbolSize: 7, areaStyle: { opacity: 0.06 } },
      { name: '负面评论数', type: buckets.length === 1 ? 'bar' : 'line', barMaxWidth: 48, data: buckets.map(row => row.negative_review_count), symbolSize: 7 },
    ],
  }), [buckets]);
  const download = () => exportResult({ question_note: question, note_scope: 'local_browser_only', analysis: run }, `analysis-${run.run_id}`);
  const trendTable = <Table size="small" rowKey="bucket" dataSource={buckets} pagination={{ pageSize: 6, hideOnSinglePage: true }} columns={[
    { title: '月份（UTC）', dataIndex: 'bucket' }, { title: '评论数', dataIndex: 'review_count' },
    { title: '负面评论数', dataIndex: 'negative_review_count' },
    { title: '负面占比', dataIndex: 'negative_rate', render: rate },
  ]} />;
  return <div className="results-stack">
    <div className="section-heading"><div><span className="eyebrow">分析结果</span><h2>从评论中，看到变化</h2></div><Tag color="success">已保存快照</Tag></div>
    <Card className="conclusion-card" bordered={false}>
      <div className="card-kicker"><span className="small-icon"><FileTextOutlined /></span>统计结论</div>
      <p className="conclusion">当前窗口共 <strong>{summary.review_count.toLocaleString()}</strong> 条评论，其中 <strong>{summary.successful_reviews.toLocaleString()}</strong> 条已成功标注。
        {summary.negative_rate === null ? ' 暂无可计算负面占比的成功标注。' : <>负面评论 <strong>{summary.negative_review_count.toLocaleString()}</strong> 条，占成功标注评论的 <strong>{rate(summary.negative_rate)}</strong>。</>}
      </p>
      <p className="muted">以上文字由实际统计值整理。规则检查：{overview.triggered} 项触发、{overview.normal} 项正常、{overview.insufficient_data} 项数据不足。</p>
      {overview.insufficient_data > 0 && <Alert type="warning" showIcon message="部分维度样本或标注覆盖率不足，尚不能判断异常；零条负面评论不等同于没有负面问题。" />}
    </Card>
    <div className="metrics-grid">
      <Card><Statistic title="窗口评论数" value={summary.review_count} suffix={<span className="metric-unit">条</span>} /></Card>
      <Card><Statistic title="标注覆盖率" value={rate(summary.labeling_coverage)} /><span className="metric-hint">成功标注 / 有正文评论</span></Card>
      <Card><Statistic title="负面评论占比" value={rate(summary.negative_rate)} /><span className="metric-hint">负面评论 / 成功标注</span></Card>
      <Card><Statistic title="对比商品" value={overview.products} suffix={<span className="metric-unit">款</span>} /><span className="metric-hint">含当前与基线窗口</span></Card>
    </div>
    <Card title="评论趋势" extra={<span className="muted">按月 · 去重评论</span>}>
      {buckets.length ? <Tabs items={[
        { key: 'chart', label: '趋势图', children: <Suspense fallback={<div className="chart loading-chart">正在加载图表…</div>}><Chart option={trendOption} label="按月展示评论数量和负面评论数量" /></Suspense> },
        { key: 'table', label: '数据表', children: trendTable },
      ]} /> : <Empty description="当前范围没有趋势数据" />}
    </Card>
    <Card title="商品对比" extra={<Button type="text" size="small" icon={<ExpandOutlined />} onClick={() => onDetail(run.results.comparison, '商品对比详情')}>详情</Button>}>
      <Table size="middle" rowKey={row => JSON.stringify([row.source, row.dataset_version, row.product_id])} dataSource={run.results.comparison.products} scroll={{ x: 520 }} pagination={{ pageSize: 6, hideOnSinglePage: true }} columns={[
        { title: '商品', dataIndex: 'product_id' }, { title: '评论数', dataIndex: 'review_count', align: 'right' },
        { title: '已标注', dataIndex: 'successful_reviews', align: 'right' },
        { title: '覆盖率', dataIndex: 'labeling_coverage', align: 'right', render: rate },
        { title: '负面占比', dataIndex: 'negative_rate', align: 'right', render: rate },
      ]} locale={{ emptyText: '该窗口暂无商品评论' }} />
    </Card>
    <Card title="负面问题与基线变化" extra={<span className="muted">百分点 · pp</span>}>
      <Table size="middle" rowKey="key" dataSource={issues} scroll={{ x: 650 }} pagination={{ pageSize: 6, hideOnSinglePage: true }} columns={[
        { title: '商品', dataIndex: 'product_id' }, { title: '维度', dataIndex: 'aspect_label' },
        { title: '负面数', dataIndex: 'negative_review_count', align: 'right' },
        { title: '当前占比', dataIndex: 'negative_rate', render: rate, align: 'right' },
        { title: '较基线', dataIndex: 'negative_rate_change_pp', align: 'right', render: (value: number | null) => value === null ? '—' : <span className={value > 0 ? 'increase' : ''}>{value > 0 ? '+' : ''}{value.toFixed(1)} pp</span> },
        { title: '规则判断', dataIndex: 'evaluation', render: (value: string) => <Tag color={value === 'triggered' ? 'orange' : value === 'normal' ? 'green' : 'default'}>{({ triggered: '触发', normal: '正常', insufficient_data: '数据不足' } as Record<string, string>)[value] ?? '—'}</Tag> },
      ]} />
    </Card>
    <Card title="原文证据" extra={<span className="muted">来自本次冻结快照 · 含基线</span>}>
      <Table size="small" rowKey="review_id" dataSource={run.inputs} scroll={{ x: 500 }} pagination={{ pageSize: 5, hideOnSinglePage: true }} columns={[
        { title: '商品 / 日期', width: 160, render: (_, row) => <><strong>{row.product_id}</strong><div className="muted">{row.published_at.slice(0, 10)}</div></> },
        { title: '评论原文', dataIndex: 'text', render: (text: string) => <span className="clamp-two">{text || '（空正文）'}</span> },
        { title: '', width: 70, render: (_, row) => <Button type="link" onClick={() => onDetail(row, `证据 #${row.review_id}`)}>查看</Button> },
      ]} />
    </Card>
    <Card className="download-card">
      <div className="file-icon"><FileTextOutlined /></div>
      <div className="file-info"><strong>完整分析结果.json</strong><p className="muted">含输入范围、统计结果和原文证据 · 浏览器导出</p></div>
      <Button aria-label="下载 JSON" icon={<DownloadOutlined />} onClick={download}>下载 JSON</Button>
    </Card>
    <p className="result-footnote">范围：{scope.date_from} — {scope.date_to}（UTC） · 快照 {run.run_id.slice(0, 8)} · “—”表示分母为空，未按 0 处理。</p>
  </div>;
}
