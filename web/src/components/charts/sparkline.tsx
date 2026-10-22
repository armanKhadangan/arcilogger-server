import { useId, useState } from 'react';

import { formatNumber } from '@/lib/format';
import { cn } from '@/lib/utils';

interface SparklineProps {
  values: number[];
  /** Accessible name, e.g. "Mesaj/dk, son 12 saat". */
  label: string;
  formatValue?: (value: number) => string;
  className?: string;
}

const WIDTH = 96;
const HEIGHT = 32;
const PAD = 4;

/**
 * Tiny trend line for KPI tiles: 2 px line in the de-emphasis colour, the current (last) point in
 * the primary colour. Hover shows the value under the pointer.
 */
export function Sparkline({
  values,
  label,
  formatValue = (v) => formatNumber(v),
  className,
}: SparklineProps) {
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (WIDTH - PAD * 2) / Math.max(values.length - 1, 1);
  const points = values.map((value, index) => ({
    x: PAD + index * step,
    y: HEIGHT - PAD - ((value - min) / span) * (HEIGHT - PAD * 2),
    value,
  }));
  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' ');
  const last = points.at(-1);
  const active = hover === null ? null : points[hover];
  const lastValue = values.at(-1) ?? 0;

  return (
    <div className={cn('relative', className)}>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-full w-full overflow-visible"
        role="img"
        aria-labelledby={titleId}
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - box.left) / box.width) * WIDTH;
          setHover(Math.min(values.length - 1, Math.max(0, Math.round((x - PAD) / step))));
        }}
        onPointerLeave={() => {
          setHover(null);
        }}
      >
        <title id={titleId}>
          {`${label}: ${formatValue(lastValue)} (min ${formatValue(min)}, max ${formatValue(max)})`}
        </title>
        <path
          d={path}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        {last ? (
          <circle
            cx={last.x}
            cy={last.y}
            r={3}
            fill="var(--primary)"
            stroke="var(--surface)"
            strokeWidth={2}
          />
        ) : null}
        {active ? (
          <circle
            cx={active.x}
            cy={active.y}
            r={3.5}
            fill="var(--text)"
            stroke="var(--surface)"
            strokeWidth={2}
          />
        ) : null}
      </svg>
      {active ? (
        <span className="pointer-events-none absolute -top-7 right-0 rounded-md bg-foreground px-1.5 py-0.5 text-xs whitespace-nowrap text-background tabular">
          {formatValue(active.value)}
        </span>
      ) : null}
    </div>
  );
}
