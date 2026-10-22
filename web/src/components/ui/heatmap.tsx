import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export interface HeatmapRow {
  key: string;
  label: string;
  /** One value per column (24 hours by default). */
  values: number[];
}

interface HeatmapProps {
  rows: HeatmapRow[];
  /** Column labels; defaults to hours 00–23. */
  columns?: string[];
  /** Value that maps to the darkest step (default: largest value in the data). */
  max?: number;
  formatValue: (value: number) => string;
  /** Accessible name (table caption). */
  caption: string;
  className?: string;
}

const HOURS = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0'));
const STEP_CLASS = ['bg-heat-0', 'bg-heat-1', 'bg-heat-2', 'bg-heat-3', 'bg-heat-4', 'bg-heat-5'];

/** Map a value to a ramp step: 0 = no activity, 1–5 = sequential teal steps. */
export function heatStep(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.min(5, Math.max(1, Math.ceil((value / max) * 5)));
}

/** Rows × hours grid, sequential single-hue ramp; hover shows the exact value. */
export function Heatmap({
  rows,
  columns = HOURS,
  max,
  formatValue,
  caption,
  className,
}: HeatmapProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);
  const top = max ?? Math.max(0, ...rows.flatMap((row) => row.values));

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div
        ref={containerRef}
        className="relative overflow-x-auto"
        onMouseLeave={() => {
          setHover(null);
        }}
      >
        <table className="border-separate border-spacing-[2px] text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="sr-only">
                {t('heatmap.day')}
              </th>
              {columns.map((column, index) => (
                <th key={column} scope="col" className="h-5 min-w-6 font-normal text-muted tabular">
                  <span className={cn(index % 3 !== 0 && 'sr-only')}>{column}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th
                  scope="row"
                  className="pr-2 text-right font-normal whitespace-nowrap text-muted tabular"
                >
                  {row.label}
                </th>
                {columns.map((column, index) => {
                  const value = row.values[index] ?? 0;
                  const text = `${row.label} · ${column}:00 · ${value > 0 ? formatValue(value) : t('heatmap.none')}`;
                  return (
                    <td
                      key={column}
                      aria-label={text}
                      className={cn('h-6 min-w-6 rounded-[3px]', STEP_CLASS[heatStep(value, top)])}
                      onMouseEnter={(event) => {
                        const box = containerRef.current?.getBoundingClientRect();
                        const cell = event.currentTarget.getBoundingClientRect();
                        if (!box) return;
                        setHover({
                          text,
                          x:
                            cell.left -
                            box.left +
                            cell.width / 2 +
                            (containerRef.current?.scrollLeft ?? 0),
                          y: cell.top - box.top,
                        });
                      }}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {hover ? (
          <div
            role="presentation"
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md bg-foreground px-2 py-1 text-xs whitespace-nowrap text-background shadow-sm"
            style={{ left: hover.x, top: hover.y - 4 }}
          >
            {hover.text}
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="size-3 rounded-[3px] border border-border bg-heat-0"
          />
          {t('heatmap.none')}
        </span>
        <span className="flex items-center gap-1.5">
          {t('heatmap.less')}
          {STEP_CLASS.slice(1).map((step) => (
            <span key={step} aria-hidden="true" className={cn('size-3 rounded-[3px]', step)} />
          ))}
          {t('heatmap.more')}
          <span className="tabular">({formatValue(top)})</span>
        </span>
      </div>
    </div>
  );
}
