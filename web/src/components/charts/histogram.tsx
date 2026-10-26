import { BarChart3, Table2, ZoomIn } from 'lucide-react';
import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipProps } from 'recharts';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { SearchBucket, SearchTotal } from '@/lib/search-types';

import { histogramRows, zoomSpan, type HistogramRow } from './histogram-data';
import { CHART_OTHER, seriesColor } from './palette';

/** Runs of bars that were not counted, as `[first label, last label]` (for the shading). */
function uncountedRuns(rows: readonly HistogramRow[]): [string, string][] {
  const runs: [string, string][] = [];
  let first: string | null = null;
  for (const [index, row] of rows.entries()) {
    if (row.counted) continue;
    first ??= row.label;
    if (rows[index + 1]?.counted !== false) {
      runs.push([first, row.label]);
      first = null;
    }
  }
  return runs;
}

function HistogramTooltip({ active, payload }: TooltipProps<number, string>) {
  const { t } = useTranslation();
  const row = payload?.[0]?.payload as HistogramRow | undefined;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-13 shadow-sm">
      <p className="font-medium text-foreground tabular">
        {`${formatDateTime(row.start)} – ${formatTime(row.end)}`}
      </p>
      <p className="text-muted tabular">
        {row.counted
          ? t('search.histogram.count', {
              count: row.count,
              formatted: formatNumber(row.count, 0, currentLang()),
            })
          : t('search.histogram.uncounted')}
      </p>
    </div>
  );
}

interface HistogramProps {
  buckets: readonly SearchBucket[];
  bucketSeconds: number;
  /** The counted part of the range; `null`: all of it. */
  covered: { from: string; to: string } | null;
  total: SearchTotal;
  /** A span chosen by dragging across bars (or with a row's button in the table view). */
  onZoom?: (span: { from: string; to: string }) => void;
  height?: number;
}

/**
 * The search's records over time (recharts bars, one per bucket of `bucket_seconds`). Dragging
 * across bars chooses a shorter range (a new search); the part of the range the statistics did not
 * count is shaded and the total reads "≥ N". The chart is hidden from screen readers; the table
 * view ("Tablo olarak göster") has the same values and a zoom button per row.
 */
export function Histogram({
  buckets,
  bucketSeconds,
  covered,
  total,
  onZoom,
  height = 160,
}: HistogramProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const [showTable, setShowTable] = useState(false);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const rows = histogramRows(buckets, bucketSeconds, covered);
  const formatted = formatNumber(total.value, 0, lang);
  const totalText = total.exact
    ? t('search.histogram.total', { count: total.value, formatted })
    : t('search.histogram.totalAtLeast', { count: total.value, formatted });
  const dragged = drag ? zoomSpan(rows, drag.from, drag.to) : null;
  const first = drag ? rows[Math.min(drag.from, drag.to)] : undefined;
  const last = drag ? rows[Math.max(drag.from, drag.to)] : undefined;

  function finish() {
    if (drag && drag.from !== drag.to && dragged) onZoom?.(dragged);
    setDrag(null);
  }

  return (
    <Card className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
        <div className="flex flex-col">
          <h2 className="text-sm font-semibold text-foreground">{t('search.histogram.title')}</h2>
          <p className="text-13 text-muted tabular" aria-live="polite">
            {totalText}
            {covered ? ` · ${t('search.histogram.partly')}` : ''}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={showTable}
          onClick={() => {
            setShowTable((value) => !value);
          }}
        >
          {showTable ? <BarChart3 aria-hidden="true" /> : <Table2 aria-hidden="true" />}
          {showTable ? t('search.histogram.showChart') : t('search.histogram.showTable')}
        </Button>
      </div>
      <div className="px-4 pt-2 pb-3">
        {showTable ? (
          <div className="max-h-72 overflow-auto rounded-lg border border-border">
            <table className="w-full text-13">
              <caption className="sr-only">{t('search.histogram.title')}</caption>
              <thead className="sticky top-0 bg-subtle text-left text-muted-strong">
                <tr>
                  <th scope="col" className="px-3 py-1.5 font-medium">
                    {t('search.histogram.from')}
                  </th>
                  <th scope="col" className="px-3 py-1.5 font-medium">
                    {t('search.histogram.to')}
                  </th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">
                    {t('search.histogram.records')}
                  </th>
                  {onZoom ? (
                    <th scope="col" className="px-3 py-1.5 font-medium">
                      <span className="sr-only">{t('search.histogram.zoom')}</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.start} className="border-t border-border">
                    <td className="px-3 py-1.5 tabular">{formatDateTime(row.start)}</td>
                    <td className="px-3 py-1.5 tabular">{formatDateTime(row.end)}</td>
                    <td className="px-3 py-1.5 text-right tabular">
                      {row.counted
                        ? formatNumber(row.count, 0, lang)
                        : t('search.histogram.uncountedShort')}
                    </td>
                    {onZoom ? (
                      <td className="px-3 py-1 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={t('search.histogram.zoomTo', {
                            from: formatDateTime(row.start),
                            to: formatDateTime(row.end),
                          })}
                          onClick={() => {
                            onZoom({ from: row.start, to: row.end });
                          }}
                        >
                          <ZoomIn aria-hidden="true" />
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div
            style={{ height }}
            className="w-full cursor-crosshair select-none"
            aria-hidden="true"
            data-testid="histogram"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={rows}
                margin={{ top: 4, right: 4, bottom: 0, left: 0 }}
                barCategoryGap={1}
                onMouseDown={(state: { activeTooltipIndex?: number } | null) => {
                  const index = state?.activeTooltipIndex;
                  if (onZoom && index !== undefined) setDrag({ from: index, to: index });
                }}
                onMouseMove={(state: { activeTooltipIndex?: number } | null) => {
                  const index = state?.activeTooltipIndex;
                  if (drag && index !== undefined && index !== drag.to)
                    setDrag({ from: drag.from, to: index });
                }}
                onMouseUp={finish}
                onMouseLeave={() => {
                  setDrag(null);
                }}
              >
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={{ stroke: 'var(--border)' }}
                  tick={{ fill: 'var(--muted)', fontSize: 12 }}
                  interval="preserveStartEnd"
                  minTickGap={24}
                />
                <YAxis
                  tickFormatter={(value: number) => formatNumber(value, 0, lang)}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: 'var(--muted)', fontSize: 12 }}
                  width={48}
                  allowDecimals={false}
                />
                <Tooltip cursor={{ fill: 'var(--subtle)' }} content={<HistogramTooltip />} />
                {uncountedRuns(rows).map(([x1, x2]) => (
                  <ReferenceArea
                    key={x1}
                    x1={x1}
                    x2={x2}
                    fill="var(--subtle)"
                    fillOpacity={0.9}
                    stroke="var(--border)"
                    strokeDasharray="3 3"
                  />
                ))}
                {first && last ? (
                  <ReferenceArea
                    x1={first.label}
                    x2={last.label}
                    fill="var(--primary)"
                    fillOpacity={0.12}
                  />
                ) : null}
                <Bar dataKey="count" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                  {rows.map((row) => (
                    <Cell key={row.start} fill={row.counted ? seriesColor(0) : CHART_OTHER} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        {!showTable && onZoom ? (
          <p className="pt-1 text-xs text-muted">{t('search.histogram.dragHint')}</p>
        ) : null}
      </div>
    </Card>
  );
}
