import * as echarts from 'echarts';
import { useEffect, useRef } from 'react';

interface ChartProps {
  option: echarts.EChartsOption;
  height?: number | string;
  className?: string;
  onEvents?: Record<string, (params: unknown) => void>;
}

/** ECharts 封装：自动初始化/更新/随容器 resize */
export default function EChart({ option, height = 320, className, onEvents }: ChartProps) {
  const domRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = domRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(option, { notMerge: true });
    if (onEvents) {
      Object.keys(onEvents).forEach((name) => chart.off(name as never));
      Object.entries(onEvents).forEach(([name, handler]) => chart.on(name, (params) => handler(params)));
    }
  }, [option, onEvents]);

  return <div ref={domRef} className={className} style={{ width: '100%', height }} />;
}
