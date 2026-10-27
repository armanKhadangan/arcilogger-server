/**
 * What the Kaynaklar page (spec 12.3 page 3, contract 2.16 and 2.22.4) shows of the sources'
 * state, and the small calculations behind it (no components here; unit-tested in
 * `sources-model.test.ts`).
 */

import {
  Ban,
  CircleDashed,
  CircleHelp,
  CornerDownRight,
  Signal,
  SignalZero,
  type LucideIcon,
} from 'lucide-react';

import type { BadgeProps } from '@/components/ui/badge';
import type { HandoffPayloads, SearchRange } from '@/lib/handoff';
import { SITE_UTC_OFFSET } from '@/lib/site-day';
import type { SourceHealth, SourceState } from '@/lib/sources-types';

/** Who may open "Son satırlar" and an example line (the rule of `GET /raw`). */
export const RAW_PERMISSIONS: readonly string[] = ['search.read', 'lookup.run'];
/** "Kaynak olarak tanımla" and the link to Ayarlar → Kaynaklar. */
export const MANAGE_PERMISSION = 'sources.manage';
/** The pattern of lines that are not in the collector's format: they have no `messages` row. */
export const INVALID_LINE_PATTERN = 'rawlog.invalid';

export type SourceFilter = 'all' | 'silent' | 'unknown';
export const SOURCE_FILTERS: readonly SourceFilter[] = ['all', 'silent', 'unknown'];

/** The state as shown: a silent source that is not expected to send says so. */
export type ShownState = SourceState | 'silentUnwatched';

export function shownState(item: Pick<SourceHealth, 'state' | 'expected'>): ShownState {
  return item.state === 'silent' && !item.expected ? 'silentUnwatched' : item.state;
}

/** Badge of each state: tone and icon (a state is never shown by colour alone). */
export const STATE_STYLE: Record<
  ShownState,
  { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }
> = {
  active: { tone: 'success', icon: Signal },
  silent: { tone: 'danger', icon: SignalZero },
  silentUnwatched: { tone: 'neutral', icon: SignalZero },
  never: { tone: 'warning', icon: CircleDashed },
  disabled: { tone: 'neutral', icon: Ban },
  unknown: { tone: 'info', icon: CircleHelp },
  promoted: { tone: 'neutral', icon: CornerDownRight },
};

/** A row of the table: a source, and whether it is a member listed under its network. */
export interface SourceRow {
  item: SourceHealth;
  member: boolean;
}

function matches(item: SourceHealth, filter: SourceFilter): boolean {
  if (filter === 'silent') return item.state === 'silent' || item.state === 'never';
  if (filter === 'unknown') return item.unknown;
  return true;
}

/**
 * The rows for `filter`, in the API's order, each configured network followed by its members
 * (`<name>-<ip>` folders). A member whose network the filter leaves out stands on its own.
 */
export function sourceRows(items: readonly SourceHealth[], filter: SourceFilter): SourceRow[] {
  const shown = items.filter((item) => matches(item, filter));
  const names = new Set(shown.map((item) => item.name));
  const rows: SourceRow[] = [];
  for (const item of shown) {
    if (item.network_of !== null && names.has(item.network_of)) continue;
    rows.push({ item, member: false });
    for (const member of shown) {
      if (member.network_of === item.name) rows.push({ item: member, member: true });
    }
  }
  return rows;
}

/** How many rows each filter shows (the filter buttons' counts). */
export function filterCounts(items: readonly SourceHealth[]): Record<SourceFilter, number> {
  return {
    all: items.length,
    silent: items.filter((item) => matches(item, 'silent')).length,
    unknown: items.filter((item) => matches(item, 'unknown')).length,
  };
}

/**
 * The header's summary: configured sources (not the members of a network) that are switched
 * on, how many of them are active, and the unknown senders not configured yet.
 */
export function sourcesSummary(items: readonly SourceHealth[]): {
  active: number;
  total: number;
  unknown: number;
} {
  const configured = items.filter(
    (item) => item.configured && item.network_of === null && item.enabled,
  );
  return {
    active: configured.filter((item) => item.state === 'active').length,
    total: configured.length,
    unknown: items.filter((item) => item.state === 'unknown').length,
  };
}

/** Failures per 1,000 lines as a percentage (`null`: no lines today). */
export function failurePercent(permille: number | null): number | null {
  return permille === null ? null : permille / 10;
}

/**
 * "Kaynak olarak tanımla": what Ayarlar → Kaynaklar's add dialog is filled in with (the
 * administrator reviews the name). `null` for anything but an unknown sender with an address.
 */
export function promotion(item: SourceHealth): HandoffPayloads['/settings/sources'] | null {
  if (item.state !== 'unknown' || item.address === null || item.suggested === null) return null;
  return {
    prefill: { name: item.suggested.name, type: item.suggested.type, address: item.address },
  };
}

/** A query value of the search language: bare when it can be, else quoted (literal: no `*`). */
export function queryValue(value: string): string {
  if (/^[^\s",()*\\]+$/.test(value)) return value;
  return `"${value.replace(/["\\]/g, (char) => `\\${char}`)}"`;
}

/** The site day `day` (`YYYY-MM-DD`) as a search range `[00:00, next day 00:00)`. */
export function dayRange(day: string): SearchRange {
  const [year = 1970, month = 1, date = 1] = day.split('-').map(Number);
  const next = new Date(Date.UTC(year, month - 1, date + 1)).toISOString().slice(0, 10);
  return { from: `${day}T00:00:00${SITE_UTC_OFFSET}`, to: `${next}T00:00:00${SITE_UTC_OFFSET}` };
}

/** Arama with the lines of a source (`source:` also finds a network's members). */
export function sourceSearch(name: string, range?: SearchRange): HandoffPayloads['/search'] {
  const q = `source:${queryValue(name)}`;
  return range ? { q, range } : { q };
}

/**
 * Arama with the lines of a parse-failure pattern (`error:`). A line not in the collector's
 * format (`rawlog.invalid`) has no `messages` row, so Arama cannot find it: `null`.
 */
export function failureSearch(
  source: string,
  pattern: string,
  range?: SearchRange,
): HandoffPayloads['/search'] | null {
  if (pattern === INVALID_LINE_PATTERN) return null;
  const q = `source:${queryValue(source)} error:${queryValue(pattern)}`;
  return range ? { q, range } : { q };
}
