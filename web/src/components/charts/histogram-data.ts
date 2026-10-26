/**
 * The bars of the search histogram (`histogram.tsx`): their spans, labels and whether the
 * statistics counted them, and the span a drag across bars zooms to.
 */

import { formatDate, formatTime } from '@/lib/format';
import type { SearchBucket } from '@/lib/search-types';
import { siteIso } from '@/lib/time-range';

/** One bar: `[start, end)` and its count; `counted: false` lies outside what was counted. */
export interface HistogramRow {
  index: number;
  start: string;
  end: string;
  count: number;
  counted: boolean;
  label: string;
}

/** `14:00`, `09.10 14:00` (a range over several days) or `09.10.2026` (day buckets). */
export function bucketLabel(start: string, bucketSeconds: number, severalDays: boolean): string {
  if (bucketSeconds >= 86_400) return formatDate(start);
  const time = formatTime(start);
  return severalDays ? `${formatDate(start).slice(0, 5)} ${time}` : time;
}

/** The bars of a histogram; a bar is counted when it lies inside `[covered.from, covered.to)`. */
export function histogramRows(
  buckets: readonly SearchBucket[],
  bucketSeconds: number,
  covered: { from: string; to: string } | null,
): HistogramRow[] {
  const first = buckets[0]?.start;
  const last = buckets.at(-1)?.start;
  const severalDays =
    first !== undefined && last !== undefined && formatDate(first) !== formatDate(last);
  const from = covered ? Date.parse(covered.from) : -Infinity;
  const to = covered ? Date.parse(covered.to) : Infinity;
  return buckets.map((bucket, index) => {
    const start = Date.parse(bucket.start);
    const end = start + bucketSeconds * 1000;
    return {
      index,
      start: bucket.start,
      end: siteIso(end),
      count: bucket.count,
      counted: start >= from && end <= to,
      label: bucketLabel(bucket.start, bucketSeconds, severalDays),
    };
  });
}

/** The span from bar `a` to bar `b` (either order), ends included: a zoom's new range. */
export function zoomSpan(rows: readonly HistogramRow[], a: number, b: number) {
  const first = rows[Math.min(a, b)];
  const last = rows[Math.max(a, b)];
  return first && last ? { from: first.start, to: last.end } : null;
}
