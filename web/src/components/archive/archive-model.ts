/**
 * The archive reload of the lookup and search pages (Wave 5 contract §5, G10): which days a
 * reload asks for, how far it got, and the text of a failure. Pure; the card and its hook use it.
 */

import { isApiError } from '@/lib/api';
import type { DateString } from '@/lib/api-types';
import type {
  ArchivedRange,
  ArchiveReload,
  ArchiveReloadRequired,
  ArchiveReloadStatus,
} from '@/lib/archive-reload-types';
import type { SearchPage } from '@/lib/search-types';

/** At most this many days in one reload (G5, `archive_reload_too_large` above it). */
export const MAX_RELOAD_DAYS = 14;
/** A reload in progress is read again every 2 s (G10). */
export const ARCHIVE_RELOAD_POLL_MS = 2_000;

/**
 * `error_code` of a `failed` reload, set by ArciIngest while it loads.
 *
 * Interim until W5-G (#121) adds it to lib/archive-reload-types.ts.
 */
export type ArchiveReloadFailureCode =
  | 'archive_day_not_in_vault' // vault retention removed a day after the request was queued
  | 'archive_file_unreadable'; // a sealed file kept failing to read or no longer matched

const FAILURE_CODES: readonly string[] = [
  'archive_day_not_in_vault',
  'archive_file_unreadable',
] satisfies readonly ArchiveReloadFailureCode[];

/**
 * The `archived` days of a search answer (G8): the part of its range outside the window.
 *
 * Interim until W5-G (#121) adds `archived` to `SearchPage` in lib/search-types.ts.
 */
export function archivedOf(page: SearchPage): ArchivedRange | null {
  return (page as { archived?: ArchivedRange | null }).archived ?? null;
}

/** The days one reload asks for: the whole range, or its newest {@link MAX_RELOAD_DAYS} days. */
export interface ReloadSpan {
  first_day: DateString;
  last_day: DateString;
  /** Days of the span, both ends included. */
  days: number;
  /** The range was longer: its older days are not reloaded (they need a narrower range). */
  clipped: boolean;
}

const DAY_MS = 86_400_000;

/** Milliseconds since the epoch of a `YYYY-MM-DD` day at 00:00 UTC (no time-zone shift). */
function dayValue(day: DateString): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) throw new RangeError(`not a day: ${day}`);
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function dayString(value: number): DateString {
  return new Date(value).toISOString().slice(0, 10);
}

/**
 * The days to reload for `first` … `last`: all of them, or — when there are more than
 * {@link MAX_RELOAD_DAYS} — the newest ones, nearest the window (manager, issue #112): the card
 * does not split a range.
 */
export function reloadSpan(first: DateString, last: DateString): ReloadSpan {
  const end = dayValue(last);
  const days = Math.round((end - dayValue(first)) / DAY_MS) + 1;
  if (days <= MAX_RELOAD_DAYS) return { first_day: first, last_day: last, days, clipped: false };
  return {
    first_day: dayString(end - (MAX_RELOAD_DAYS - 1) * DAY_MS),
    last_day: last,
    days: MAX_RELOAD_DAYS,
    clipped: true,
  };
}

/** No further change comes: the poll stops. */
export function isFinished(status: ArchiveReloadStatus): boolean {
  return status === 'ready' || status === 'failed' || status === 'expired';
}

/** How far the reload got, 0 … 1: `bytes_done / bytes_total`, 1 once ready. */
export function progressOf(reload: ArchiveReload): number {
  if (reload.status === 'ready') return 1;
  if (reload.bytes_total <= 0) return 0;
  return Math.min(1, Math.max(0, reload.bytes_done / reload.bytes_total));
}

/** The locale key of a failure's text: one per known `error_code`, else the generic one. */
export function failureKey(code: string | null): string {
  return code !== null && FAILURE_CODES.includes(code)
    ? `archive.failure.${code}`
    : 'archive.failure.unknown';
}

/**
 * How many days reloaded days stay in the database (`db.reload_keep_days`), from the 409's
 * `details.keep_days` (C23); `null` when the answer does not say. Interim until W5-G (#121) adds
 * `keep_days` to `ArchiveReloadRequired`: read through a cast, and the dialog names the setting
 * with its default when it is absent.
 */
export function keepDaysOf(required: ArchiveReloadRequired | null): number | null {
  const days = (required as { keep_days?: unknown } | null)?.keep_days;
  return typeof days === 'number' && Number.isInteger(days) && days >= 1 ? days : null;
}

/** The days of a 409 `archive_reload_required` (G7, G8), `null` for any other error. */
export function requiredOf(error: unknown): ArchiveReloadRequired | null {
  if (!isApiError(error, 'archive_reload_required')) return null;
  const details = error.details as Partial<Record<keyof ArchiveReloadRequired, unknown>> | null;
  if (
    typeof details !== 'object' ||
    details === null ||
    typeof details.first_day !== 'string' ||
    typeof details.last_day !== 'string'
  )
    return null;
  return details as ArchiveReloadRequired;
}
