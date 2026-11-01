/**
 * What the "Panel" (spec 12.3 item 2) shows to whom, and the small calculations behind its cards
 * (no components here; unit-tested in `dashboard-model.test.ts`).
 *
 * Three requests feed the page: `GET /dashboard/summary` (`dashboard.view`; every block of it is
 * `null` when the user lacks that block's own permission), today's `GET /usage/company`
 * (`dashboard.usage`, `usage.person` or `usage.anonymous`) and `GET /system/disk` (`system.read`,
 * the disk card). A block the API leaves out, or a request the user may not make, is not shown at
 * all — it is not an error.
 */

import type {
  AlertSeverity,
  CategoryRow,
  CompanyUsage,
  DashboardSummary,
  HourPoint,
} from '@/lib/api-types';
import { isApiError } from '@/lib/api';
import { formatDuration, formatNumber, formatTime, type Lang } from '@/lib/format';
import { hasAny } from '@/lib/permissions';
import type { SystemDisk } from '@/lib/system-types';
import { NAV_ITEMS } from '@/nav';

/** Who may ask `GET /dashboard/summary`. */
export const SUMMARY_PERMISSIONS: readonly string[] = ['dashboard.view'];
/** Who may ask `GET /usage/company` (the HR role sees the Panel with usage only). */
export const USAGE_PERMISSIONS: readonly string[] = [
  'dashboard.usage',
  'usage.person',
  'usage.anonymous',
];
/** Who may ask `GET /system/disk` (the disk card; administrators). */
export const DISK_PERMISSIONS: readonly string[] = ['system.read'];

export type DashboardBlock =
  | 'ingest'
  | 'parseFailures'
  | 'seal'
  | 'credit'
  | 'clock'
  | 'disk'
  | 'usage'
  | 'alerts'
  | 'sites'
  | 'categories'
  | 'calendar';

/** The requests the page makes for a user with `granted` permissions. */
export function dashboardRequests(granted: ReadonlySet<string>): {
  summary: boolean;
  usage: boolean;
} {
  return {
    summary: hasAny(granted, SUMMARY_PERMISSIONS),
    usage: hasAny(granted, USAGE_PERMISSIONS),
  };
}

/** Whether the page asks `GET /system/disk` for a user with `granted` permissions. */
export function diskRequested(granted: ReadonlySet<string>): boolean {
  return hasAny(granted, DISK_PERMISSIONS);
}

/** A refusal means "not for this user": the block is left out instead of showing an error. */
export function isRefusal(error: unknown): boolean {
  return isApiError(error) && error.status === 403;
}

/**
 * The blocks to show once the answers are in. `summary`/`usage`/`disk` are `undefined` while
 * loading or when not asked; a refused usage request hides the usage blocks. The disk card shows
 * only with an answer: not while loading, not on any error (the Sistem page shows the error).
 */
export function visibleBlocks(
  granted: ReadonlySet<string>,
  summary: DashboardSummary | undefined,
  usage: { data: CompanyUsage | undefined; error: unknown },
  disk?: SystemDisk,
): Set<DashboardBlock> {
  const asked = dashboardRequests(granted);
  const blocks = new Set<DashboardBlock>();
  if (asked.summary && summary) {
    if (summary.ingest) blocks.add('ingest');
    // Absent in answers of an API before Wave 2: no box, not an error.
    if (summary.parse_failures) blocks.add('parseFailures');
    if (summary.seal) {
      blocks.add('seal');
      blocks.add('calendar');
    }
    if (summary.credit) blocks.add('credit');
    if (summary.clock) blocks.add('clock');
    if (summary.alerts) blocks.add('alerts');
  }
  if (diskRequested(granted) && disk) blocks.add('disk');
  if (asked.usage && (usage.data || (usage.error && !isRefusal(usage.error)))) {
    blocks.add('usage');
    blocks.add('sites');
    blocks.add('categories');
  }
  return blocks;
}

/** Severities from the most to the least urgent (chips, lists). */
export const SEVERITY_ORDER: readonly AlertSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];

/** Where a card links to; `null` when the user may not open that page (then the card is text). */
export function linkTarget(key: string, granted: ReadonlySet<string>): string | null {
  const item = NAV_ITEMS.find((entry) => entry.key === key);
  if (!item || !hasAny(granted, item.permissions)) return null;
  return item.path;
}

/** Who may open a parse failure's example line (the rule of `GET /raw`). */
export const RAW_LINE_PERMISSIONS: readonly string[] = ['search.read', 'lookup.run'];
/** Above 1 % of a day's lines (the R20-parse threshold) the parse-failure card says "Yüksek". */
export const HIGH_FAILURE_PERMILLE = 10;

/** The parse-failure card's verdict: no failure today, some, or above the alert threshold. */
export function failureLevel(box: {
  failures: number;
  permille: number | null;
}): 'none' | 'some' | 'high' {
  if (box.failures === 0) return 'none';
  return box.permille !== null && box.permille >= HIGH_FAILURE_PERMILLE ? 'high' : 'some';
}

/** A day on the sealing page: its month's calendar with the day selected. */
export function sealDayHref(sealingPath: string, day: string): string {
  return `${sealingPath}?month=${day.slice(0, 7)}&day=${day}`;
}

/** How long the credit lasts: days below two months, months below two years, else years. */
export function creditDuration(days: number): { unit: 'days' | 'months' | 'years'; count: number } {
  if (days < 60) return { unit: 'days', count: Math.max(0, Math.floor(days)) };
  if (days < 730) return { unit: 'months', count: Math.floor(days / 30) };
  return { unit: 'years', count: Math.floor(days / 365) };
}

/**
 * The clock check's verdict is the server's `ok`; `null` (no sample of the last 3 hours) is
 * unknown — an older sample's offset says nothing about the clock now.
 */
export function clockVerdict(clock: {
  offset_ms: number | null;
  ok: boolean | null;
}): 'ok' | 'off' | 'unknown' {
  if (clock.ok === null) return 'unknown';
  return clock.ok ? 'ok' : 'off';
}

/**
 * Badge status of a dashboard seal state (the sealing calendar's own statuses). Yesterday's seal
 * is green only once verified; a calendar day (no verification flag) is green when sealed.
 */
export function sealStatus(
  state: string,
  verified?: boolean | null,
): 'sealed' | 'unverified' | 'pending' | 'failed' | 'noData' {
  if (state === 'sealed') return verified === undefined || verified ? 'sealed' : 'unverified';
  if (state === 'pending') return 'pending';
  if (state === 'failed') return 'failed';
  return 'noData';
}

/**
 * A sum of many people's time ("186 sa", spec 12.3 item 2): whole hours from one day on — "7 gün
 * 18 sa" would read as a calendar span —, below that as a duration ("3 sa 12 dk").
 */
export function formatTotalTime(seconds: number, lang: Lang): string {
  if (seconds < 86_400) return formatDuration(seconds, lang);
  return `${formatNumber(Math.floor(seconds / 3600), 0, lang)} ${lang === 'tr' ? 'sa' : 'h'}`;
}

/** Rows of the hourly chart: site hour label, downloaded and uploaded bytes. */
export function hourlyRows(
  hourly: readonly HourPoint[],
): { hour: string; download: number; upload: number }[] {
  return hourly.map((point) => ({
    hour: formatTime(point.hour),
    download: point.download_bytes,
    upload: point.upload_bytes,
  }));
}

/**
 * At most `limit` categories by time with the server's `share` of the active time, the rest
 * folded into one "Diğer" slice. The API sends the ten largest categories only, so "Diğer" is
 * the day's active time `total` less the slices shown (never less than the rest it sent): the
 * slices add up to the active time of the usage card.
 */
export function categorySlices(
  rows: readonly CategoryRow[],
  otherLabel: string,
  total: number,
  limit = 6,
): { label: string; value: number; share: number; other?: boolean }[] {
  const sorted = [...rows].filter((row) => row.seconds > 0).sort((a, b) => b.seconds - a.seconds);
  const head = sorted
    .slice(0, limit)
    .map((row) => ({ label: row.category, value: row.seconds, share: row.share }));
  const tail = sorted.slice(limit);
  if (tail.length === 0) return head;
  const sum = (values: number[]) => values.reduce((acc, value) => acc + value, 0);
  const value = Math.max(
    sum(tail.map((row) => row.seconds)),
    total - sum(head.map((slice) => slice.value)),
  );
  const share = Math.max(
    sum(tail.map((row) => row.share)),
    1 - sum(head.map((slice) => slice.share)),
  );
  return [...head, { label: otherLabel, value, share, other: true }];
}

/**
 * The last `days` site days ending `today`, oldest first, each with its seal state (`no_data`
 * when the API sent none), padded with `null` so the first day sits under its weekday (Monday
 * first): a compact calendar of 7 columns.
 */
export function lastDays(
  calendar: readonly { day: string; status: string }[],
  today: string,
  days = 30,
): ({ day: string; status: string } | null)[] {
  const byDay = new Map(calendar.map((entry) => [entry.day, entry.status]));
  const [year = 1970, month = 1, date = 1] = today.split('-').map(Number);
  const cells: { day: string; status: string }[] = [];
  for (let back = days - 1; back >= 0; back -= 1) {
    const value = new Date(Date.UTC(year, month - 1, date - back));
    const day = value.toISOString().slice(0, 10);
    cells.push({ day, status: byDay.get(day) ?? 'no_data' });
  }
  const first = cells[0];
  const weekday = first ? (new Date(`${first.day}T12:00:00Z`).getUTCDay() + 6) % 7 : 0;
  return [...Array<null>(weekday).fill(null), ...cells];
}
