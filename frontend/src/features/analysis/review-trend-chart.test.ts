import { describe, expect, it } from 'vitest';
import { reviewTrendOption } from './review-trend-chart';
import type { Summary } from '../../types';

const month = (bucket: string, total: number, negative = 0, success = total): Summary & { bucket: string } => ({
  bucket, review_count: total, eligible_text_reviews: total, successful_reviews: success,
  failed_reviews: 0, unprocessed_reviews: total - success, negative_review_count: negative,
  labeling_coverage: total ? success / total : null, negative_rate: success ? negative / success : null,
});

// 使用真实 ECharts option 类型的结构子集，校验数据映射及悬停输出。
function chart(buckets: ReturnType<typeof month>[]) {
  return reviewTrendOption(buckets) as {
    xAxis: { data: string[]; boundaryGap: boolean; show?: boolean }[];
    yAxis: { min: number };
    series: { name: string; type: string; stack?: string; xAxisIndex: number; yAxisIndex: number;
      barWidth: number; barMaxWidth?: number; barMinWidth?: number; z: number; data: number[] }[];
    tooltip: { formatter: (params: { dataIndex: number } | { dataIndex: number }[]) => string };
    dataZoom: { startValue: number; endValue: number; maxValueSpan: number; xAxisIndex: number[] }[];
  };
}

describe('月度前后重叠柱状图', () => {
  it('两组柱子共用月份轴，裁掉首尾空月并保留中间零评论月', () => {
    const buckets = [month('2023-01', 0), month('2023-02', 12, 3, 6), month('2023-03', 0),
      month('2023-04', 8, 2), month('2023-05', 0)];
    const option = chart(buckets);
    expect(option.xAxis[0]).toMatchObject({ data: ['2023-02', '2023-03', '2023-04'], boundaryGap: true });
    expect(option.xAxis[1]).toMatchObject({ ...option.xAxis[0], show: false, axisPointer: { type: 'none' } });
    expect(option.series).toEqual([
      expect.objectContaining({ name: '总评论数', type: 'bar', data: [12, 0, 8] }),
      expect.objectContaining({ name: '差评数', type: 'bar', data: [3, 0, 2] }),
    ]);
    expect(option.series.every(series => series.stack === undefined)).toBe(true);
    expect(option.dataZoom).toEqual([]);
    expect(buckets).toHaveLength(5);
  });

  it('两组柱体均固定为 24px，共用从零开始的 Y 轴，不堆叠、不相加', () => {
    const option = chart([month('2023-02', 12, 3, 6)]);
    const [total, negative] = option.series;
    expect(total.xAxisIndex).toBe(0);
    expect(negative.xAxisIndex).toBe(1);
    expect(total.yAxisIndex).toBe(negative.yAxisIndex);
    expect(option.yAxis.min).toBe(0);
    expect(total.barWidth).toBe(24);
    expect(negative.barWidth).toBe(24);
    for (const series of option.series) {
      expect(series.barMaxWidth).toBeUndefined();
      expect(series.barMinWidth).toBeUndefined();
    }
    expect(negative.z).toBeGreaterThan(total.z);
    expect(total.stack).toBeUndefined();
    expect(negative.stack).toBeUndefined();
    expect(total.data).toEqual([12]);
    expect(negative.data).toEqual([3]);
  });

  it('悬停返回月份、总量、差评量以及服务端占比，不使用总评论作分母', () => {
    const row = { ...month('2023-02', 12, 3, 6), negative_rate: 0.500001 };
    const tooltip = chart([row]).tooltip.formatter([{ dataIndex: 0 }, { dataIndex: 0 }]);
    expect(tooltip).toContain('2023-02（UTC）');
    expect(tooltip).toContain('总评论数：12 条');
    expect(tooltip).toContain('差评数：3 条');
    expect(tooltip).toContain('差评率：50.0%');
    expect(tooltip).toContain('成功标注数（6 条）');
  });

  it('无成功标注显示未知占比，真实零差评显示 0.0%', () => {
    const option = chart([month('2023-01', 9, 0, 0), month('2023-02', 9)]);
    expect(option.tooltip.formatter({ dataIndex: 0 })).toContain('差评率：—（暂无成功标注）');
    expect(option.tooltip.formatter({ dataIndex: 1 })).toContain('差评率：0.0%');
  });

  it('长期范围默认展示最近 12 个月，保留所有月份供滑动查询', () => {
    const buckets = Array.from({ length: 24 }, (_, index) => month(`${2022 + Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, '0')}`, index + 1));
    const option = chart(buckets);
    expect(option.xAxis[0].data).toHaveLength(24);
    expect(option.xAxis[1].data).toEqual(option.xAxis[0].data);
    expect(option.dataZoom).toEqual([expect.objectContaining({ startValue: 12, endValue: 23, maxValueSpan: 11, xAxisIndex: [0, 1] })]);
    expect(option.tooltip.formatter([{ dataIndex: 23 }])).toContain('2023-12（UTC）');
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain('2022-01（UTC）');
  });

  it('单月仍为两组柱子，全空范围不制造月份或数值', () => {
    expect(chart([month('2023-02', 10, 2)]).series.map(series => series.data)).toEqual([[10], [2]]);
    for (const buckets of [[], [month('2023-01', 0), month('2023-02', 0)]]) {
      const option = chart(buckets);
      expect(option.xAxis.map(axis => axis.data)).toEqual([[], []]);
      expect(option.series.map(series => series.data)).toEqual([[], []]);
      expect(option.dataZoom).toEqual([]);
    }
  });
});
