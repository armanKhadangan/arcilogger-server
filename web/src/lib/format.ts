/**
 * Display formatting (Turkish first, English second).
 *
 * Twin of `core/src/arcilogger_core/fmt.py`; both are tested against
 * `tests/fixtures/format-vectors.json` so the screen and the PDF reports always agree.
 * Rounding: half away from zero on the exact binary value (`Number.prototype.toFixed`),
 * which is what the Python side does with `Decimal(...).quantize(ROUND_HALF_UP)`.
 */

export type Lang = 'tr' | 'en';

export const SITE_TIME_ZONE = 'Europe/Istanbul';

const SEPARATORS: Record<Lang, { thousands: string; decimal: string }> = {
  tr: { thousands: '.', decimal: ',' },
  en: { thousands: ',', decimal: '.' },
};

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;

const DURATION_UNITS: Record<Lang, readonly [string, string, string, string]> = {
  tr: ['gün', 'sa', 'dk', 'sn'],
  en: ['d', 'h', 'min', 's'],
};

const siteParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: SITE_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

type DateInput = Date | string | number;

function toDate(value: DateInput): Date {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new RangeError(`invalid date: ${String(value)}`);
  }
  return date;
}

function partsInSiteZone(value: DateInput): Record<string, string> {
  const result: Record<string, string> = {};
  for (const part of siteParts.formatToParts(toDate(value))) {
    result[part.type] = part.value;
  }
  return result;
}

/** `23.09.2026 09:44:07` in Europe/Istanbul, whatever the browser's time zone is. */
export function formatDateTime(value: DateInput): string {
  const p = partsInSiteZone(value);
  return `${p.day ?? ''}.${p.month ?? ''}.${p.year ?? ''} ${p.hour ?? ''}:${p.minute ?? ''}:${p.second ?? ''}`;
}

/** `12.10.2026` in Europe/Istanbul. */
export function formatDate(value: DateInput): string {
  const p = partsInSiteZone(value);
  return `${p.day ?? ''}.${p.month ?? ''}.${p.year ?? ''}`;
}

/** `09:44` in Europe/Istanbul. */
export function formatTime(value: DateInput, withSeconds = false): string {
  const p = partsInSiteZone(value);
  const base = `${p.hour ?? ''}:${p.minute ?? ''}`;
  return withSeconds ? `${base}:${p.second ?? ''}` : base;
}

/** `1.234.567` / `1,5` (tr) or `1,234,567` / `1.5` (en). */
export function formatNumber(value: number, decimals = 0, lang: Lang = 'tr'): string {
  if (!Number.isFinite(value)) throw new RangeError(`cannot format non-finite number: ${value}`);
  if (decimals < 0) throw new RangeError('decimals must be >= 0');
  let fixed = Math.abs(value).toFixed(decimals);
  const negative = value < 0 && Number(fixed) !== 0;
  const [integer = '0', fraction] = fixed.split('.');
  const { thousands, decimal } = SEPARATORS[lang];
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, thousands);
  fixed = fraction === undefined ? grouped : `${grouped}${decimal}${fraction}`;
  return negative ? `-${fixed}` : fixed;
}

/** `1,5 GB` — binary multiples (1 KB = 1024 B) as Windows shows them, at most one decimal. */
export function formatBytes(bytes: number, lang: Lang = 'tr'): string {
  if (!Number.isFinite(bytes) || bytes < 0) throw new RangeError(`invalid byte count: ${bytes}`);
  if (bytes < 1024) return `${formatNumber(bytes, 0, lang)} B`;
  let exponent = 0;
  while (exponent < BYTE_UNITS.length - 1 && bytes >= 1024 ** (exponent + 1)) exponent += 1;
  let rounded = Number((bytes / 1024 ** exponent).toFixed(1));
  if (rounded >= 1024 && exponent < BYTE_UNITS.length - 1) {
    exponent += 1; // 1023,96 KB is shown as 1 MB, not 1.024 KB
    rounded = Number((bytes / 1024 ** exponent).toFixed(1));
  }
  const decimals = Number.isInteger(rounded) ? 0 : 1;
  return `${formatNumber(rounded, decimals, lang)} ${BYTE_UNITS[exponent] ?? 'B'}`;
}

/** `2 sa 15 dk` — the two most significant non-zero units, truncated to whole seconds. */
export function formatDuration(seconds: number, lang: Lang = 'tr'): string {
  if (!Number.isFinite(seconds) || seconds < 0)
    throw new RangeError(`invalid duration: ${seconds}`);
  const total = Math.floor(seconds);
  const values = [
    Math.floor(total / 86_400),
    Math.floor((total % 86_400) / 3_600),
    Math.floor((total % 3_600) / 60),
    total % 60,
  ] as const;
  const units = DURATION_UNITS[lang];
  let first = values.findIndex((amount) => amount !== 0);
  if (first === -1) first = values.length - 1;
  let text = `${values[first] ?? 0} ${units[first] ?? ''}`;
  const next = values[first + 1];
  if (next !== undefined && next !== 0) text += ` ${next} ${units[first + 1] ?? ''}`;
  return text;
}

/** Compact counts for KPI tiles: `1.284`, `12,9 bin`, `4,2 Mn` (tr) or `12.9 K`, `4.2 M` (en). */
export function formatCompact(value: number, lang: Lang = 'tr'): string {
  const steps: readonly [number, string, string][] = [
    [1_000_000_000, 'Mr', 'B'],
    [1_000_000, 'Mn', 'M'],
    [1_000, 'bin', 'K'],
  ];
  if (Math.abs(value) >= 10_000) {
    for (const [size, tr, en] of steps) {
      if (Math.abs(value) >= size) {
        const rounded = Number((value / size).toFixed(1));
        const decimals = Number.isInteger(rounded) ? 0 : 1;
        return `${formatNumber(rounded, decimals, lang)} ${lang === 'tr' ? tr : en}`;
      }
    }
  }
  return formatNumber(value, 0, lang);
}
