import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, AriaComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';

echarts.use([LineChart, BarChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, CanvasRenderer]);

export default function Chart({ option, label }: { option: EChartsCoreOption; label: string }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current);
    chart.setOption({ ...option, aria: { enabled: true, label: { description: label } }, animation: false });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option, label]);
  return <div ref={container} role="img" aria-label={label} className="chart" />;
}
