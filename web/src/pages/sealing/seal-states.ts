/**
 * The sealing API's day states (`arcilogger_api.sealinfo.STATES`) on the calendar.
 *
 * `verified` (sealed, verified within the last 35 days) is the only green state; `sealed` (not
 * verified recently), `late` and `pending` are amber; `failed` is red; `open` (today), `future`,
 * `no_data` and `deleted` (retention) are grey — each with its own icon and label.
 */

import type { SealDayStatus } from '@/components/ui/seal-calendar';
import type { BadgeProps } from '@/components/ui/badge';
import type { Calendar } from '@/lib/api-types';

export const STATE_STATUS: Record<string, SealDayStatus> = {
  verified: 'sealed',
  sealed: 'unverified',
  late: 'late',
  pending: 'pending',
  failed: 'failed',
  open: 'open',
  future: 'noData',
  no_data: 'noData',
  deleted: 'deleted',
};

/** Calendar status of an API state (unknown states: no data). */
export function calendarStatus(state: string): SealDayStatus {
  return STATE_STATUS[state] ?? 'noData';
}

/**
 * What a day's time stamp may be called (decision 5): `legal` (claimed, verified, root listed in
 * `tsa.legal_root_fingerprints`), `claimed` (a Kamu SM production stamp whose root is not listed
 * or that did not verify — never called a test stamp) or `test` (a test / mock provider, also a
 * production claim on a stamp of ARCILOGGER's test TSA: `test_stamp`).
 */
export function legalGrade(token: {
  legal_grade: boolean;
  legal_grade_claimed?: boolean;
  test_stamp?: boolean;
}): 'legal' | 'claimed' | 'test' {
  if (token.legal_grade) return 'legal';
  return token.legal_grade_claimed && !token.test_stamp ? 'claimed' : 'test';
}

/** Badge tone of the API's `tone` (`ok`, `warning`, `error`, `neutral`). */
export function toneOf(tone: string): NonNullable<BadgeProps['tone']> {
  if (tone === 'ok') return 'success';
  if (tone === 'warning') return 'warning';
  if (tone === 'error') return 'danger';
  return 'neutral';
}

/** `{ "2026-10-09": "sealed", … }` and the messages for the calendar's accessible names. */
export function calendarDays(
  calendar: Calendar,
  lang: 'tr' | 'en',
): { days: Record<string, SealDayStatus>; notes: Record<string, string> } {
  const days: Record<string, SealDayStatus> = {};
  const notes: Record<string, string> = {};
  for (const cell of calendar.days) {
    days[cell.day] = calendarStatus(cell.state);
    // The server's message (the state itself is in the calendar's own label).
    const message = lang === 'en' ? cell.message_en : cell.message_tr;
    if (message) notes[cell.day] = message;
  }
  return { days, notes };
}

/** `2026-10` → a Date inside that month (local noon: no time-zone edge). */
export function monthDate(month: string): Date {
  const [year, number] = month.split('-').map(Number);
  return new Date(year ?? 1970, (number ?? 1) - 1, 1, 12);
}

/** `2026-10-12` → that local day at noon (the calendar compares days, not instants). */
export function dayDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year ?? 1970, (month ?? 1) - 1, date ?? 1, 12);
}

export function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

/** The most recent day of the calendar worth opening (sealed, failed, pending …), else null. */
export function defaultDay(calendar: Calendar): string | null {
  const interesting = new Set(['verified', 'sealed', 'late', 'pending', 'failed', 'deleted']);
  const cells = calendar.days.filter((cell) => interesting.has(cell.state));
  return cells.at(-1)?.day ?? null;
}

/**
 * Locale key of a gap reason as the sealer writes it into the manifest (`arcilogger_core.seal`:
 * `no messages`, `no messages > 60 min`), `null` for wording this page does not know (then the
 * manifest's own text is shown). The manifest text itself never changes; it stays in a tooltip.
 */
export function gapReason(reason: string): { key: string; minutes?: number } | null {
  if (reason === 'no messages') return { key: 'sealing.gaps.reasons.none' };
  const longer = /^no messages > (\d+) min$/.exec(reason);
  if (longer) return { key: 'sealing.gaps.reasons.longer', minutes: Number(longer[1]) };
  return null;
}

/**
 * The refusals of "Şimdi mühürle" and "Kontör sorgula" that come from the administration service
 * itself (switched off, or ArciAdmin not running): the page then names the console path, the
 * task in Task Scheduler.
 */
export const AGENT_CODES = ['admin_disabled', 'admin_agent_unavailable'] as const;
