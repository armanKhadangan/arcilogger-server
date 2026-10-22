/**
 * Text and table helpers of the "Kullanım" page (no components; unit-tested in
 * `usage-format.test.ts`). Numbers use the shared Turkish formatters (`@/lib/format`): durations
 * "3 sa 12 dk", volumes "38,2 GB", days `dd.MM.yyyy`, times in Europe/Istanbul.
 */

import type { TFunction } from 'i18next';

import type { DonutSlice } from '@/components/charts/donut-chart';
import type { CategoryRow, Metrics, SubjectHour, SubjectRef } from '@/lib/api-types';
import { formatDay, siteDateTime } from '@/lib/display';
import { formatNumber, type Lang } from '@/lib/format';

/**
 * The Turkish dative suffix after a clock time as it is read aloud — the minutes, or the hour
 * when the minutes are zero: 14:35 → `'e` (otuz beşe), 14:30 → `'a` (otuza), 14:20 → `'ye`
 * (yirmiye), 16:00 → `'ya` (on altıya), 00:00 → `'a` (sıfıra).
 */
export function dativeSuffix(clock: string): string {
  const [hours = '0', minutes = '0'] = clock.split(':');
  const value = Number(minutes) !== 0 ? Number(minutes) : Number(hours);
  if (!Number.isInteger(value) || value === 0) return "'a"; // sıfır
  const units = value % 10;
  // bir, iki, üç, dört, beş, altı, yedi, sekiz, dokuz
  const UNITS = ["'e", "'ye", "'e", "'e", "'e", "'ya", "'ye", "'e", "'a"];
  if (units !== 0) return UNITS[units - 1] ?? "'e";
  // on, yirmi, otuz, kırk, elli
  const TENS = ["'a", "'ye", "'a", "'a", "'ye"];
  return TENS[Math.floor(value / 10) - 1] ?? "'a";
}

/**
 * "Veriler 14:35'e kadar işlendi" (today), "Veriler 12.10.2026 23:55'e kadar işlendi" or, at a
 * site midnight (a day computed to its end), "Veriler 12.10.2026 sonuna kadar işlendi": how far
 * the rollups have processed traffic (`computed_until` of every usage answer).
 */
export function computedUntilText(
  computedUntil: string | null,
  todayDay: string,
  t: TFunction,
): string {
  if (!computedUntil) return t('usage.computed.none');
  const { date, time } = siteDateTime(computedUntil);
  const clock = time.slice(0, 5);
  if (clock === '00:00') {
    const dayBefore = new Date(Date.parse(`${date}T00:00:00Z`) - 86_400_000)
      .toISOString()
      .slice(0, 10);
    return t('usage.computed.endOfDay', { date: formatDay(dayBefore) });
  }
  const suffix = dativeSuffix(clock);
  return date === todayDay
    ? t('usage.computed.today', { time: clock, suffix })
    : t('usage.computed.other', { date: formatDay(date), time: clock, suffix });
}

/**
 * The description of a capped detail list (a site's or category's users, a category's sites):
 * the hint, and when the answer holds fewer rows than there are (`users_total`, `sites_total`),
 * "260 satırdan en çok süresi olan 200 tanesi gösteriliyor; dışa aktarılan dosyada hepsi var."
 */
export function cappedHint(
  hint: string,
  shown: number,
  total: number,
  t: TFunction,
  lang: Lang,
): string {
  if (shown >= total) return hint;
  const note = t('usage.capped', {
    shown: formatNumber(shown, 0, lang),
    total: formatNumber(total, 0, lang),
  });
  return `${hint}. ${note}`;
}

/** A share of 0…1 as `12,5 %`. */
export function shareText(share: number, lang: Lang): string {
  return `${formatNumber(Math.max(0, share) * 100, 1, lang)} %`;
}

/** Slices of the category donut before "Diğer". */
export const DONUT_SLICES = 6;

/**
 * The categories as donut slices: the six largest, the rest folded into "Diğer". The API sends
 * the ten largest only, so "Diğer" is the active time `total` less the six (never less than the
 * rest it sent): the donut adds up to the active time in its middle.
 */
export function donutSlices(
  rows: readonly CategoryRow[],
  other: string,
  total: number,
): DonutSlice[] {
  const sorted = [...rows].sort((a, b) => b.seconds - a.seconds);
  const slices: DonutSlice[] = sorted
    .slice(0, DONUT_SLICES)
    .map((row) => ({ label: row.category, value: row.seconds }));
  if (sorted.length <= DONUT_SLICES) return slices;
  const shown = slices.reduce((sum, slice) => sum + slice.value, 0);
  const sent = sorted.slice(DONUT_SLICES).reduce((sum, row) => sum + row.seconds, 0);
  const rest = Math.max(sent, total - shown);
  if (rest > 0) slices.push({ label: other, value: rest, other: true });
  return slices;
}

function earlier(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) <= Date.parse(b) ? a : b;
}

function later(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
}

/** The sum of metrics (first activity the earliest, last the latest). */
export function sumMetrics(list: readonly Metrics[]): Metrics {
  return list.reduce<Metrics>(
    (sum, item) => ({
      online_seconds: sum.online_seconds + item.online_seconds,
      active_seconds: sum.active_seconds + item.active_seconds,
      download_bytes: sum.download_bytes + item.download_bytes,
      upload_bytes: sum.upload_bytes + item.upload_bytes,
      sessions: sum.sessions + item.sessions,
      blocked: sum.blocked + item.blocked,
      off_hours_active_seconds: sum.off_hours_active_seconds + item.off_hours_active_seconds,
      first_activity: earlier(sum.first_activity, item.first_activity),
      last_activity: later(sum.last_activity, item.last_activity),
    }),
    {
      online_seconds: 0,
      active_seconds: 0,
      download_bytes: 0,
      upload_bytes: 0,
      sessions: 0,
      blocked: 0,
      off_hours_active_seconds: 0,
      first_activity: null,
      last_activity: null,
    },
  );
}

/**
 * The MAC address of a device, else its IP address (an address without a lease), else what its
 * key says (`device:aa:bb:…`); `null` for a person and for a pseudonym.
 */
export function addressOf(subject: SubjectRef): string | null {
  if (subject.kind === 'person' || subject.pseudonymous) return null;
  if (subject.mac) return subject.mac;
  if (subject.ip) return subject.ip;
  const match = /^(?:device|ip):(.+)$/.exec(subject.key);
  return match?.[1] ?? null;
}

/** `14:00` of an hour on a single day, `13.10 14:00` within a longer period. */
export function hourLabel(hour: string, singleDay: boolean): string {
  const { date, time } = siteDateTime(hour);
  const clock = time.slice(0, 5);
  return singleDay ? clock : `${date.slice(8, 10)}.${date.slice(5, 7)} ${clock}`;
}

/** The site day of an hour. */
export function dayOfHour(hour: string): string {
  return siteDateTime(hour).date;
}

export interface DayTotals {
  day: string;
  active_seconds: number;
  online_seconds: number;
  download_bytes: number;
  upload_bytes: number;
  /** The domain that was the top domain of the day's hours for the most active time. */
  top_domain: string | null;
}

/** A subject's hourly answer summed per site day (the per-day table of a period), oldest first. */
export function perDay(hourly: readonly SubjectHour[]): DayTotals[] {
  const days = new Map<string, DayTotals & { weights: Map<string, number> }>();
  for (const hour of hourly) {
    const day = dayOfHour(hour.hour);
    let row = days.get(day);
    if (!row) {
      row = {
        day,
        active_seconds: 0,
        online_seconds: 0,
        download_bytes: 0,
        upload_bytes: 0,
        top_domain: null,
        weights: new Map(),
      };
      days.set(day, row);
    }
    row.active_seconds += hour.active_seconds;
    row.online_seconds += hour.online_seconds;
    row.download_bytes += hour.download_bytes;
    row.upload_bytes += hour.upload_bytes;
    if (hour.top_domain)
      row.weights.set(
        hour.top_domain,
        (row.weights.get(hour.top_domain) ?? 0) + hour.active_seconds,
      );
  }
  return [...days.values()]
    .sort((a, b) => a.day.localeCompare(b.day))
    .map(({ weights, ...row }) => {
      let top: string | null = null;
      let best = 0;
      for (const [domain, seconds] of weights) {
        if (seconds > best || top === null) {
          top = domain;
          best = seconds;
        }
      }
      return { ...row, top_domain: top };
    });
}

/** The sum of the per-day rows (the "Toplam" line). */
export function dayTotals(rows: readonly DayTotals[]): Omit<DayTotals, 'day' | 'top_domain'> {
  return rows.reduce(
    (sum, row) => ({
      active_seconds: sum.active_seconds + row.active_seconds,
      online_seconds: sum.online_seconds + row.online_seconds,
      download_bytes: sum.download_bytes + row.download_bytes,
      upload_bytes: sum.upload_bytes + row.upload_bytes,
    }),
    { active_seconds: 0, online_seconds: 0, download_bytes: 0, upload_bytes: 0 },
  );
}

/** Text search in the browser: case-insensitive, Turkish letters folded (`ı` = `i`, `ş` = `s` …). */
export function fold(text: string): string {
  return text
    .toLocaleLowerCase('tr')
    .replace(
      /[çğıöşü]/g,
      (char) => ({ ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u' })[char] ?? char,
    )
    .normalize('NFD')
    .replace(/\p{M}/gu, '');
}

const API_FOLD_FROM = 'ABCDEFGHIJKLMNOPQRSTUVWXYZİıŞĞÜÖÇÂÎÛ';
const API_FOLD_TO = 'abcdefghijklmnopqrstuvwxyziişğüöçâîû';

/** The API's search folding (`arcilogger_api.textfold`): Turkish-aware lower case, nothing else. */
export function apiFold(text: string): string {
  let out = '';
  for (const char of text) {
    const at = API_FOLD_FROM.indexOf(char);
    out += at < 0 ? char : (API_FOLD_TO[at] ?? char);
  }
  return out;
}

/**
 * The API's `q` of the usage lists: the trimmed text occurs in one of `texts` (folded with
 * {@link apiFold}). The browser filters the loaded list the same way, so an export with `q`
 * holds exactly the rows on the screen.
 */
export function matchesApiSearch(
  query: string,
  texts: readonly (string | null | undefined)[],
): boolean {
  const needle = apiFold(query.trim());
  if (needle === '') return true;
  return texts.some((text) => Boolean(text) && apiFold(text ?? '').includes(needle));
}

/** True when every word of `query` occurs in one of `texts`. */
export function matchesSearch(
  query: string,
  texts: readonly (string | null | undefined)[],
): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = texts
    .filter((text): text is string => Boolean(text))
    .map(fold)
    .join('\n');
  return words.every((word) => haystack.includes(word));
}
