/** Texts of the reports page (no components: the `.tsx` files render). */

import type { TFunction } from 'i18next';

import type { ReportKind } from '@/lib/api-types';
import { formatDate, formatDateTime, formatTime } from '@/lib/format';

import { coveredPeriod, nextRun, ownPeriod, type ScheduleTiming } from './schedule-model';

/** "Gün sonu", "Haftalık", …; a run over a chosen period is "Özel dönem". */
export function kindLabel(kind: string, t: TFunction): string {
  return t(`reports.kinds.${kind}`, { defaultValue: kind });
}

/** "Her gün 18:30", "Her pazartesi 07:15", "Her ayın 1. günü 07:30". */
export function whenText(
  timing: { kind: ReportKind; time: string; weekday: number | null; monthday: number | null },
  t: TFunction,
): string {
  if (timing.kind === 'weekly')
    return t('reports.when.weekly', {
      weekday: t(`reports.weekdays.${timing.weekday ?? 1}`),
      time: timing.time,
    });
  if (timing.kind === 'monthly')
    return t('reports.when.monthly', { day: timing.monthday ?? 1, time: timing.time });
  return t('reports.when.daily', { time: timing.time });
}

function isMidnight(value: Date): boolean {
  return formatTime(value, true) === '00:00:00';
}

/**
 * A period in site time: whole days as days (`12.10.2026`, `05.10.2026 – 11.10.2026`; the end is
 * exclusive), else with times (`13.10.2026 00:00–18:30`).
 */
export function periodText(fromValue: Date | string, toValue: Date | string): string {
  const from = new Date(fromValue);
  const to = new Date(toValue);
  if (isMidnight(from) && isMidnight(to)) {
    const last = new Date(to.getTime() - 1);
    const first = formatDate(from);
    const end = formatDate(last);
    return first === end ? first : `${first} – ${end}`;
  }
  if (formatDate(from) === formatDate(to))
    return `${formatDate(from)} ${formatTime(from)}–${formatTime(to)}`;
  return `${formatDateTime(from).slice(0, 16)} – ${formatDateTime(to).slice(0, 16)}`;
}

/** "Şimdi çalışırsa kapsadığı dönem: 12.10.2026" (the default of "run now" and a preview). */
export function ownPeriodText(kind: ReportKind, now: Date, t: TFunction): string {
  const period = ownPeriod(kind, now);
  return t('reports.period.ownNow', { period: periodText(period.from, period.to) });
}

/** "Sonraki çalışma: 19.10.2026 07:15 · kapsadığı dönem: 12.10.2026 – 18.10.2026". */
export function nextRunText(timing: ScheduleTiming | null, now: Date, t: TFunction): string {
  if (!timing) return t('reports.next.invalid');
  const at = nextRun(timing, now);
  if (!at) return t('reports.next.invalid');
  const period = coveredPeriod(timing.kind, at);
  return t('reports.next.text', {
    at: `${formatDate(at)} ${formatTime(at)}`,
    period: periodText(period.from, period.to),
  });
}
