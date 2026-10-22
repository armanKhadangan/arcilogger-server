/**
 * Calendar days of the site (Europe/Istanbul) as API time bounds.
 *
 * A day picked in the date picker means the site's day, whatever time zone the browser is set
 * to. Türkiye has used UTC+03:00 all year since 2016 (build specification rule 0.8: "UTC+03:00, no
 * DST"); `site-day.test.ts` checks that the browser's time-zone database agrees, so a future rule
 * change fails a test instead of silently shifting every filter by an hour.
 */

export const SITE_UTC_OFFSET = '+03:00';

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** `2026-10-12T00:00:00+03:00` for the calendar date of `day` (its local Y-M-D). */
export function siteDayStart(day: Date): string {
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}T00:00:00${SITE_UTC_OFFSET}`;
}

/** Start of the following site day: the exclusive upper bound of a range ending on `day`. */
export function siteDayEnd(day: Date): string {
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
  return siteDayStart(next);
}
