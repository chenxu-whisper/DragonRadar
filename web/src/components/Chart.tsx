import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);
export const palette = ['#1759ad', '#a64d00', '#733eac', '#007d76', '#a33360'];
export const lineStyles = ['solid', 'dashed', 'dotted', 'solid', 'dashed'] as const;

export function Chart({ option, description }: { option: EChartsCoreOption; description: string }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: 'canvas' });
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => chart.setOption({ ...option, animation: !media.matches, animationDuration: 150, textStyle: { fontFamily: 'system-ui, sans-serif', fontSize: 12, color: '#4b5563' } }, true);
    update();
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    media.addEventListener('change', update);
    return () => { observer.disconnect(); media.removeEventListener('change', update); chart.dispose(); };
  }, [option]);
  return <div className="chart" ref={container} role="img" aria-label={description} />;
}
