import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { BarChartOutlined } from '@ant-design/icons';
import { analysisApi } from '../../shared/api/analysis-api';
import { errorMessage } from '../../api';
import type { AnalysisParameters, Run } from '../../types';
import { reviewTrendOption } from './review-trend-chart';

const Chart = lazy(() => import('../../Chart'));
type Trend = Run['results']['trend'];

/** 首页只读统计与任务提交分离；范围改变时立即撤下旧统计并取消旧请求。 */
export default function ReviewDashboard({ parameters, productCount, hasProducts, refreshKey = 0 }: {
  parameters?: AnalysisParameters; productCount: number; hasProducts: boolean; refreshKey?: number;
}) {
  const { baseline_from: _from, baseline_to: _to, question: _question, ...scope } = parameters ?? {};
  const key = parameters ? JSON.stringify(scope) : '';
  const [state, setState] = useState<{ key: string; data?: Trend; error?: string }>({ key: '' });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    setState({ key });
    analysisApi.trend(JSON.parse(key), controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ key, data });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ key, error: errorMessage(error) });
    });
    return () => controller.abort();
  }, [key, revision, refreshKey]);
  const data = key && state.key === key ? state.data : undefined;
  const error = key && state.key === key ? state.error : undefined;
  const loading = !!key && !data && !error;
  const summary = data?.summary;
  const metrics = [
    ['评论总数', summary?.review_count.toLocaleString(), '当前筛选时间内的评论'],
    ['已标注', summary?.successful_reviews.toLocaleString(), '当前筛选范围内成功标注的评论'],
    ['标注内负面占比', summary?.negative_rate != null ? `${(summary.negative_rate * 100).toFixed(1)}%` : undefined, '负面评论 / 成功标注；空分母显示 —'],
    ['商品数', hasProducts ? productCount.toLocaleString() : undefined, '当前选择范围的商品数'],
  ];
  const option = useMemo(() => reviewTrendOption(data?.buckets ?? []), [data]);
  return <>
    <section className="review-metrics" aria-label="当前范围统计" aria-busy={loading}>
      {metrics.map(([label, value, hint]) => <article className="review-metric" key={label} title={hint}>
        <span>{label}</span><strong>{loading && label !== '商品数' ? <Spin size="small" /> : value ?? '—'}</strong>
      </article>)}
    </section>
    <section className="review-trend" aria-labelledby="review-trend-title">
      <div className="review-trend-heading"><h2 id="review-trend-title">评论趋势</h2><span title="差评指含负面观点的已标注评论；差评率 = 差评数 / 成功标注数。首尾无评论月份已隐藏，超过 12 个月可滑动查看。">按月（UTC）</span></div>
      {error ? <div className="trend-state"><Alert type="error" showIcon message="趋势统计加载失败" description={error} action={<Button size="small" onClick={() => setRevision(value => value + 1)}>重试统计</Button>} /></div>
        : loading ? <div className="trend-state" role="status"><Spin size="small" /><span>正在读取当前范围统计…</span></div>
        : data && data.summary.review_count > 0 ? <Suspense fallback={<div className="trend-state"><Spin /></div>}><Chart option={option} label="每月总评论数和差评数的前后重叠柱状图" /></Suspense>
        : <div className="trend-state"><BarChartOutlined /><span>{!hasProducts ? '导入评论后查看趋势' : !parameters ? '选择可用标注版本与时间后查看趋势' : '当前时间范围没有评论'}</span></div>}
    </section>
  </>;
}
