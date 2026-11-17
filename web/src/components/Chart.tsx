import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';
import { chartThemeColors } from '../features/preferences/settings';
import { useThemeMode } from '../hooks/useThemeMode';

echarts.use([CandlestickChart, LineChart, BarChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);
export const lineStyles = ['solid', 'dashed', 'dotted', 'solid', 'dashed'] as const;

export function Chart({ option, description }: { option: EChartsCoreOption; description: string }) {
  const dark = useThemeMode() === 'dark';
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: 'canvas' });
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => {
      const c = chartThemeColors(dark);
      // 全局 cartesian 轴默认样式；页面 option 中逐轴配置优先级更高。
      const axis = {
        axisLabel: { color: c.axisLabel },
        axisLine: { lineStyle: { color: c.axisLine } },
        splitLine: { lineStyle: { color: c.splitLine } },
      };
      const baseTooltip = typeof option.tooltip === 'object' && option.tooltip !== null
        ? option.tooltip
        : {};
      chart.setOption({
        ...option,
        // 主题默认调色板；逐系列 itemStyle/lineStyle 色（调用方自定义）优先于调色板。
        color: [...c.palette],
        animation: !media.matches,
        animationDuration: 150,
        textStyle: { fontFamily: 'system-ui, sans-serif', fontSize: 12, color: c.text },
        categoryAxis: axis,
        valueAxis: axis,
        timeAxis: axis,
        logAxis: axis,
        tooltip: {
          // 合并边界：先 spread 调用方 tooltip（trigger/renderMode/formatter 等全部保留），
          // 仅覆写与表面相关的背景/边框/文字色；调用方显式声明同名字段会被主题表面色覆盖。
          ...baseTooltip,
          backgroundColor: c.tooltipBg,
          borderColor: c.tooltipBorder,
          textStyle: { color: c.tooltipText },
        },
      }, true);
    };
    update();
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container.current);
    media.addEventListener('change', update);
    // data-theme 变化由 useThemeMode 订阅并经 dark 依赖重跑本 effect；此处不再自管 MutationObserver。
    return () => {
      observer.disconnect();
      media.removeEventListener('change', update);
      chart.dispose();
    };
  }, [option, dark]);
  return <div className="chart" ref={container} role="img" aria-label={description} />;
}
