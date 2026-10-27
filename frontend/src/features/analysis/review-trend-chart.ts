import type { EChartsCoreOption } from 'echarts/core';
import type { TooltipComponentOption } from 'echarts/components';
import type { Run } from '../../types';

type Month = Run['results']['trend']['buckets'][number];

/** 仅裁掉首尾无评论月份，保留中间的空月，避免把断档误画成连续数据。 */
export function reviewTrendOption(buckets: Month[]): EChartsCoreOption {
  const first = buckets.findIndex(row => row.review_count > 0);
  let last = buckets.length - 1;
  while (last >= first && buckets[last]?.review_count === 0) last -= 1;
  const months = first < 0 ? [] : buckets.slice(first, last + 1);
  const scrollable = months.length > 12;
  const formatter: TooltipComponentOption['formatter'] = params => {
    const item = Array.isArray(params) ? params[0] : params;
    const month = months[item?.dataIndex];
    if (!month) return '';
    const rate = month.negative_rate === null ? '—（暂无成功标注）' : `${(month.negative_rate * 100).toFixed(1)}%`;
    return [
      `${month.bucket}（UTC）`,
      `总评论数：${month.review_count.toLocaleString()} 条`,
      `差评数：${month.negative_review_count.toLocaleString()} 条`,
      `差评率：${rate}`,
      `口径：差评数 / 成功标注数（${month.successful_reviews.toLocaleString()} 条）`,
    ].join('\n');
  };
  const monthAxis = { type: 'category', boundaryGap: true, data: months.map(row => row.bucket),
    axisTick: { show: false }, axisLine: { lineStyle: { color: '#dce5e1' } },
    axisLabel: { color: '#858585', fontSize: 10, hideOverlap: true } };
  // 顶部圆角让柱体更柔和，底边保持与零点基线贴齐。
  const barStyle = { borderRadius: [6, 6, 0, 0], shadowBlur: 4, shadowOffsetY: 2 };

  return {
    color: ['#789e8c', '#d5b176'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, renderMode: 'richText', confine: true, formatter },
    legend: { top: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8, textStyle: { color: '#76827e', fontSize: 11 } },
    grid: { top: 34, left: 4, right: 8, bottom: scrollable ? 44 : 4, containLabel: true },
    // 同步月份轴保留现有居中重叠与滑块联动。
    xAxis: [monthAxis, { ...monthAxis, show: false, axisPointer: { type: 'none' } }],
    yAxis: { type: 'value', min: 0, minInterval: 1, splitNumber: 3,
      axisLabel: { color: '#858585', fontSize: 10 }, splitLine: { lineStyle: { color: '#e7e7e7' } } },
    // 长跨度默认展示最近 12 个月；拖动滑块可查看更早月份，原始统计不截断。
    dataZoom: scrollable ? [{ type: 'slider', xAxisIndex: [0, 1], bottom: 0, height: 16, showDetail: false, brushSelect: false,
      startValue: months.length - 12, endValue: months.length - 1, maxValueSpan: 11,
      borderColor: '#dce5e1', fillerColor: 'rgba(78,116,104,0.15)' }] : [],
    series: [
      { name: '总评论数', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, barWidth: 24, z: 1,
        itemStyle: { ...barStyle, shadowColor: 'rgba(78,116,104,0.12)',
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: '#87aa9d' }, { offset: 1, color: '#6a9281' },
          ] } },
        data: months.map(row => row.review_count) },
      { name: '差评数', type: 'bar', xAxisIndex: 1, yAxisIndex: 0, barWidth: 24, z: 2,
        itemStyle: { ...barStyle, shadowColor: 'rgba(168,126,54,0.12)',
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: '#ddbe88' }, { offset: 1, color: '#cda463' },
          ] } },
        data: months.map(row => row.negative_review_count) },
    ],
  };
}
