import {
  Area,
  AreaChart as RechartsAreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipProps } from 'recharts';

import { seriesColor } from './palette';

export interface AreaSeries {
  key: string;
  label: string;
}

interface StackedAreaChartProps<Row extends Record<string, string | number>> {
  data: Row[];
  /** Key of the category axis (e.g. "hour"). */
  xKey: keyof Row & string;
  series: AreaSeries[];
  formatValue: (value: number) => string;
  formatX?: (value: string | number) => string;
  height?: number;
}

function ChartTooltip({
  active,
  payload,
  label,
  formatValue,
  formatX,
}: TooltipProps<number, string> & {
  formatValue: (value: number) => string;
  formatX: (value: string | number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-13 shadow-sm">
      <p className="mb-1 font-medium text-foreground">{formatX(label as string | number)}</p>
      <ul className="flex flex-col gap-0.5">
        {payload.map((item) => (
          <li key={item.dataKey as string} className="flex items-center gap-2 text-muted">
            <span
              aria-hidden="true"
              className="size-2.5 rounded-sm"
              style={{ background: item.color }}
            />
            <span className="text-foreground">{item.name}</span>
            <span className="ml-auto pl-4 text-foreground tabular">
              {formatValue(Number(item.value))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Stacked area over time (e.g. hourly download / upload). One y-axis, recessive grid. */
export function StackedAreaChart<Row extends Record<string, string | number>>({
  data,
  xKey,
  series,
  formatValue,
  formatX = (value) => String(value),
  height = 220,
}: StackedAreaChartProps<Row>) {
  return (
    <div style={{ height }} className="w-full" aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey={xKey}
            tickFormatter={formatX}
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            tick={{ fill: 'var(--muted)', fontSize: 12 }}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis
            tickFormatter={(value: number) => formatValue(value)}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--muted)', fontSize: 12 }}
            width={64}
          />
          <Tooltip
            cursor={{ stroke: 'var(--muted)', strokeWidth: 1, strokeDasharray: '3 3' }}
            content={<ChartTooltip formatValue={formatValue} formatX={formatX} />}
          />
          {series.map((item, index) => (
            <Area
              key={item.key}
              dataKey={item.key}
              name={item.label}
              type="monotone"
              stackId="stack"
              stroke={seriesColor(index)}
              strokeWidth={2}
              fill={seriesColor(index)}
              fillOpacity={0.18}
              activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          ))}
        </RechartsAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
