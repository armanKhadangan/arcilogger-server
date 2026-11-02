/**
 * The days of "Kiraları yeniden oluştur" (contract 2.9 `lease_rebuild_range`): at most 31 days,
 * none in the future (site time). The API also refuses days before the oldest log.
 */

import { parseDay } from '@/lib/dates';
import { SITE_UTC_OFFSET } from '@/lib/site-day';
import type { FieldErrors } from '@/lib/validation';

export const LEASE_REBUILD_MAX_DAYS = 31;

/** Today at the site (`YYYY-MM-DD`, UTC+03:00 all year). */
export function siteToday(now: Date = new Date()): string {
  const [sign, hours = '0', minutes = '0'] =
    /^([+-])(\d{2}):(\d{2})$/.exec(SITE_UTC_OFFSET)?.slice(1) ?? [];
  const offset = (sign === '-' ? -1 : 1) * (Number(hours) * 60 + Number(minutes)) * 60_000;
  return new Date(now.getTime() + offset).toISOString().slice(0, 10);
}

function dayNumber(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`) / 86_400_000;
}

/** i18n keys of the problems of a typed range. */
export function leaseRangeErrors(
  from: string,
  to: string,
  today: string,
): FieldErrors<'from' | 'to'> {
  const errors: FieldErrors<'from' | 'to'> = {};
  const first = parseDay(from);
  const last = parseDay(to);
  if (!from.trim()) errors.from = 'validation.required';
  else if (!first) errors.from = 'settings.leaseRebuild.errors.day';
  if (!to.trim()) errors.to = 'validation.required';
  else if (!last) errors.to = 'settings.leaseRebuild.errors.day';
  if (!first || !last) return errors;
  if (last > today) errors.to = 'settings.leaseRebuild.errors.future';
  else if (first > last) errors.to = 'settings.leaseRebuild.errors.order';
  else if (dayNumber(last) - dayNumber(first) + 1 > LEASE_REBUILD_MAX_DAYS)
    errors.to = 'settings.leaseRebuild.errors.tooLong';
  return errors;
}
