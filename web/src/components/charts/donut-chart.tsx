import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import type { TooltipProps } from 'recharts';

import { sliceColor } from './palette';

export interface DonutSlice {
  label: string;
  value: number;
  /** Marks the folded "Diğer" slice, which always uses the neutral colour. */
  other?: boolean;
}

interface DonutChartProps {
  data: DonutSlice[];
  formatValue: (value: number) => string;
  /** Large text in the hole, e.g. the total. */
  centerLabel?: string;
  centerHint?: string;
  height?: number;
}

function DonutTooltip({
  active,
  payload,
  formatValue,
}: TooltipProps<number, string> & { formatValue: (value: number) => string }) {
  const item = payload?.[0];
  if (!active || !item) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-13 shadow-sm">
      <span className="text-foreground">{item.name}</span>
      <span className="ml-3 text-foreground tabular">{formatValue(Number(item.value))}</span>
    </div>
  );
}

/** Part-to-whole at a glance, at most 6 slices + "Diğer". Pair it with a legend that shows values. */
export function DonutChart({
  data,
  formatValue,
  centerLabel,
  centerHint,
  height = 200,
}: DonutChartProps) {
  return (
    <div style={{ height }} className="relative w-full" aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="label"
            innerRadius="62%"
            outerRadius="92%"
            paddingAngle={1}
            stroke="var(--surface)"
            strokeWidth={2}
            isAnimationActive={false}
            rootTabIndex={-1}
          >
            {data.map((slice, index) => (
              <Cell key={slice.label} fill={sliceColor(slice, index)} />
            ))}
          </Pie>
          <Tooltip content={<DonutTooltip formatValue={formatValue} />} />
        </PieChart>
      </ResponsiveContainer>
      {centerLabel ? (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold text-foreground tabular">{centerLabel}</span>
          {centerHint ? <span className="text-xs text-muted">{centerHint}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
