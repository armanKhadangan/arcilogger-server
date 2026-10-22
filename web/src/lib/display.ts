/**
 * Small display helpers for evidence data (5651 lookup, sealing, evidence packages).
 *
 * Device-provided text (DHCP host names, log summaries) is attacker-controlled: a host name with
 * a right-to-left override (U+202E) between `fatura` and `xcod.exe` reads as `fatura.docx`. Such
 * text is shown through {@link visibleText}, which writes control, format (bidi, zero-width),
 * surrogate, private-use, unassigned and line/paragraph separator characters as `\uXXXX` — the
 * same rule as the API's raw-line display text (`arcilogger_api.rawlines.display_text`).
 */

import { formatDuration, formatNumber, SITE_TIME_ZONE, type Lang } from './format';

const UNSAFE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/gu;

/** Text with invisible / reordering characters made visible (`\u202e`). */
export function visibleText(text: string): string {
  return text.replace(
    UNSAFE,
    (char) => `\\u${(char.codePointAt(0) ?? 0).toString(16).padStart(4, '0')}`,
  );
}

const PROTOCOLS: Record<number, string> = { 1: 'ICMP', 6: 'TCP', 17: 'UDP' };

/** `TCP`, `UDP`, `ICMP` or the protocol number. */
export function protocolName(protocol: number | null | undefined): string | null {
  if (protocol === null || protocol === undefined) return null;
  return PROTOCOLS[protocol] ?? String(protocol);
}

/** `100.64.12.9:51001` (IPv6 in brackets), the address alone without a port. */
export function endpoint(ip: string | null | undefined, port: number | null | undefined): string {
  if (!ip) return port === null || port === undefined ? '—' : `:${port}`;
  const host = ip.includes(':') ? `[${ip}]` : ip;
  return port === null || port === undefined ? ip : `${host}:${port}`;
}

/** A time distance in milliseconds: `2,5 sn`, `30 sn`, `1 dk 5 sn`. */
export function formatDistance(ms: number, lang: Lang = 'tr'): string {
  if (ms < 60_000) {
    const decimals = ms % 1000 === 0 ? 0 : 1;
    return `${formatNumber(ms / 1000, decimals, lang)} ${lang === 'tr' ? 'sn' : 's'}`;
  }
  return formatDuration(ms / 1000, lang);
}

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

/** `{date: '2026-10-12', time: '09:44:07'}` of a moment in Europe/Istanbul. */
export function siteDateTime(value: Date | string): { date: string; time: string } {
  const parts: Record<string, string> = {};
  for (const part of siteParts.formatToParts(typeof value === 'string' ? new Date(value) : value))
    parts[part.type] = part.value;
  return {
    date: `${parts.year ?? ''}-${parts.month ?? ''}-${parts.day ?? ''}`,
    time: `${parts.hour ?? ''}:${parts.minute ?? ''}:${parts.second ?? ''}`,
  };
}

/** Today's site day (Europe/Istanbul) as `YYYY-MM-DD`. */
export function siteToday(now: Date = new Date()): string {
  return siteDateTime(now).date;
}

/** `12.10.2026` for an ISO calendar date (no time-zone shift: it already is a site day). */
export function formatDay(day: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return match ? `${match[3] ?? ''}.${match[2] ?? ''}.${match[1] ?? ''}` : day;
}

/** A database id from the URL (`/lookup/17`, `?lookup=17`), `null` unless 1 … 2^53-1. */
export function parseId(value: string | null | undefined): number | null {
  if (!value || !/^[1-9]\d{0,15}$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

/** First 12 characters of a hash with an ellipsis (the full value is in the title). */
export function shortHash(hash: string | null | undefined, length = 12): string {
  if (!hash) return '—';
  return hash.length > length ? `${hash.slice(0, length)}…` : hash;
}
