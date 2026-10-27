/**
 * What the Canlı Akış page (spec 12.3 page 4, contract 2.22.3) keeps and works out: the filter as
 * a query of the search language, the list of the newest 2,000 lines, the highlight rules (kept in
 * `sessionStorage`), the dropped-line totals and the hand-overs to Arama. No components here;
 * unit-tested in `live-model.test.ts`.
 */

import type { HandoffPayloads } from '@/lib/handoff';
import type { DropReason, LiveDropped, LiveLine } from '@/lib/live-types';
import { DROP_REASONS } from '@/lib/live-types';

/** Lines the page holds; the oldest go first (the hub's buffer covers more for a resume). */
export const MAX_LINES = 2_000;
/** How often lines that arrived are added to the list (one render for many lines). */
export const FLUSH_MS = 250;
/** The polite status region says what changed at most this often (the list itself is silent). */
export const ANNOUNCE_MS = 10_000;
/** Lines per second are averaged over this many seconds. */
export const RATE_SECONDS = 5;
/** "Arama'da aç" of one line: its source, this many minutes before and after it. */
export const AROUND_MINUTES = 5;

/** The folders of senders that are in no configured source (`unknown-<ip>`). */
export const UNKNOWN_SENDERS_QUERY = 'source:unknown-*';

// -- the filter -----------------------------------------------------------------------------------

export interface LiveFilter {
  /** Configured sources (a network's name also follows its `<name>-<ip>` members). */
  sources: string[];
  /** "Bilinmeyen göndericiler": the `unknown-<ip>` folders. */
  unknown: boolean;
  /** Kinds (`type:` of the search language). */
  kinds: string[];
  /** Free text or any query of the search language, as typed. */
  text: string;
}

export const EMPTY_FILTER: LiveFilter = { sources: [], unknown: false, kinds: [], text: '' };

/**
 * The filter as one query: the chosen sources and unknown senders in one `source:` clause (OR),
 * the kinds in one `type:` clause, then the typed text (ANDed by the language).
 */
export function liveQuery(filter: LiveFilter): string {
  const clauses: string[] = [];
  const sources = [...filter.sources, ...(filter.unknown ? ['unknown-*'] : [])];
  if (sources.length > 0) clauses.push(`source:${sources.join(',')}`);
  if (filter.kinds.length > 0) clauses.push(`type:${filter.kinds.join(',')}`);
  const text = filter.text.trim();
  if (text) clauses.push(text);
  return clauses.join(' ');
}

// -- the list -------------------------------------------------------------------------------------

/** A line's raw reference as a key: unique among every line of every stream. */
export function lineKey(line: Pick<LiveLine, 'source' | 'day' | 'line'>): string {
  return `${line.source}/${line.day}/${line.line}`;
}

/**
 * `current` with `incoming` added at the end, each line once (a resumed or reopened stream may
 * send a line again), at most `max` lines: the oldest are dropped first. `removed`: how many of
 * the lines that were on screen went (the list keeps its scroll position by them).
 */
export function appendLines(
  current: readonly LiveLine[],
  incoming: readonly LiveLine[],
  max = MAX_LINES,
): { lines: LiveLine[]; removed: number } {
  if (incoming.length === 0) return { lines: [...current], removed: 0 };
  const seen = new Set(current.map(lineKey));
  const added: LiveLine[] = [];
  for (const line of incoming) {
    const key = lineKey(line);
    if (seen.has(key)) continue;
    seen.add(key);
    added.push(line);
  }
  const all = [...current, ...added];
  const extra = Math.max(0, all.length - max);
  return { lines: all.slice(extra), removed: Math.min(extra, current.length) };
}

// -- dropped lines --------------------------------------------------------------------------------

export type DropTotals = Record<DropReason, number>;

export const NO_DROPS: DropTotals = { rate_limited: 0, slow_client: 0, parse_budget: 0, behind: 0 };

/** The totals since the stream started, with one `dropped` event (counts since the previous). */
export function addDropped(totals: DropTotals, event: LiveDropped): DropTotals {
  const next = { ...totals };
  for (const reason of DROP_REASONS) next[reason] += event[reason];
  return next;
}

export function droppedTotal(totals: DropTotals): number {
  return DROP_REASONS.reduce((sum, reason) => sum + totals[reason], 0);
}

// -- hand-overs to Arama --------------------------------------------------------------------------

function shift(time: string, minutes: number): string {
  return new Date(Date.parse(time) + minutes * 60_000).toISOString();
}

/** "Arama'da aç" of a line: its source, ±5 minutes around its receive time; `null` without one. */
export function searchAround(line: LiveLine): HandoffPayloads['/search'] | null {
  if (!line.received) return null;
  return {
    q: `source:${line.source}`,
    range: {
      from: shift(line.received, -AROUND_MINUTES),
      to: shift(line.received, AROUND_MINUTES),
    },
  };
}

/**
 * "Bu aralığı Arama'da aç" after a gap: the stream's query from the newest line seen before it
 * (a minute earlier: device and receive times differ a little) to now; without a line, the last
 * hour.
 */
export function searchGap(
  q: string,
  lastReceived: string | null,
  now: Date,
): HandoffPayloads['/search'] {
  const to = new Date(now.getTime() + 60_000).toISOString();
  const from = lastReceived
    ? shift(lastReceived, -1)
    : new Date(now.getTime() - 3_600_000).toISOString();
  return { q, range: { from, to } };
}

// -- highlight rules ------------------------------------------------------------------------------

/** Colours that pass contrast in light and dark (the status tokens' soft background and ink). */
export const HIGHLIGHT_COLORS = ['yellow', 'red', 'green', 'blue', 'sky', 'teal'] as const;
export type HighlightColor = (typeof HIGHLIGHT_COLORS)[number];

/** Tailwind classes of each colour: background and text of a highlighted match. */
export const HIGHLIGHT_CLASSES: Record<HighlightColor, string> = {
  yellow: 'bg-warning-soft text-warning-ink',
  red: 'bg-danger-soft text-danger-ink',
  green: 'bg-success-soft text-success-ink',
  blue: 'bg-accent-soft text-accent-ink',
  sky: 'bg-info-soft text-info-ink',
  teal: 'bg-primary-soft text-primary-ink',
};

export interface HighlightRule {
  /** A text (marked where it occurs) or `field:value` (the whole line is marked). */
  match: string;
  color: HighlightColor;
}

export const MAX_RULES = 10;
/** Kept for the tab only: a search term must not stay on a shared administration PC (R14). */
export const RULES_KEY = 'arci.live.highlights';
const MAX_MATCH = 200;

/** The fields a `field:value` rule may name: the line's own and its preview summary's. */
export const RULE_FIELDS: readonly string[] = [
  'source',
  'sender',
  'type',
  'srcip',
  'srcport',
  'dstip',
  'dstport',
  'natip',
  'natport',
  'action',
  'domain',
  'url',
  'app',
  'category',
  'user',
  'mac',
  'proto',
  'policy',
  'interface',
  'vlan',
];

/** A rule's field and value when it is `field:value` with a known field, else `null` (text). */
export function ruleField(match: string): { field: string; value: string } | null {
  const found = /^([a-z]+):(.+)$/i.exec(match.trim());
  const field = found?.[1]?.toLowerCase();
  const value = found?.[2]?.trim();
  if (!field || !value || !RULE_FIELDS.includes(field)) return null;
  return { field, value };
}

function isRule(value: unknown): value is HighlightRule {
  if (typeof value !== 'object' || value === null) return false;
  const rule = value as Record<string, unknown>;
  return (
    typeof rule.match === 'string' &&
    rule.match.trim() !== '' &&
    rule.match.length <= MAX_MATCH &&
    (HIGHLIGHT_COLORS as readonly unknown[]).includes(rule.color)
  );
}

/** The tab's rules (none when storage is unavailable or holds something else). */
export function loadRules(storage: () => Storage = () => window.sessionStorage): HighlightRule[] {
  try {
    const raw = storage().getItem(RULES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter(isRule).slice(0, MAX_RULES) : [];
  } catch {
    return [];
  }
}

/** Keep `rules` for this tab; `false` when the browser refused (they still apply until reload). */
export function saveRules(
  rules: readonly HighlightRule[],
  storage: () => Storage = () => window.sessionStorage,
): boolean {
  try {
    const kept = rules.filter(isRule).slice(0, MAX_RULES);
    if (kept.length === 0) storage().removeItem(RULES_KEY);
    else storage().setItem(RULES_KEY, JSON.stringify(kept));
    return true;
  } catch {
    return false;
  }
}

/**
 * Lower case for ASCII letters only, as the search language compares (locale C): the text keeps
 * its length, so positions found in the folded text are positions in the text.
 */
export function asciiLower(text: string): string {
  return text.replace(/[A-Z]/g, (char) => char.toLowerCase());
}

/** The value of `field` of a line as text (`null`: the line has none). */
function fieldValue(line: LiveLine, field: string): string | null {
  switch (field) {
    case 'source':
      return line.source;
    case 'sender':
      return line.sender;
    case 'type':
      return line.kind ?? line.type;
    case 'vlan':
      return line.summary.vlan_id === undefined || line.summary.vlan_id === null
        ? null
        : String(line.summary.vlan_id);
    default: {
      const value = (line.summary as Record<string, string | number | null | undefined>)[field];
      return value === undefined || value === null ? null : String(value);
    }
  }
}

/** Whether a `field:value` rule marks the line: equal, or a prefix when the value ends in `*`. */
function fieldMatches(line: LiveLine, field: string, value: string): boolean {
  const found = fieldValue(line, field);
  if (found === null) return false;
  const actual = asciiLower(found);
  const wanted = asciiLower(value);
  return wanted.endsWith('*') ? actual.startsWith(wanted.slice(0, -1)) : actual === wanted;
}

/** The colour of the first `field:value` rule that marks the whole line, else `null`. */
export function lineColor(line: LiveLine, rules: readonly HighlightRule[]): HighlightColor | null {
  for (const rule of rules) {
    const field = ruleField(rule.match);
    if (field && fieldMatches(line, field.field, field.value)) return rule.color;
  }
  return null;
}

export interface TextPart {
  text: string;
  color: HighlightColor | null;
}

/** `text` cut into parts, the occurrences of the text rules marked (the first rule wins). */
export function highlightText(text: string, rules: readonly HighlightRule[]): TextPart[] {
  const terms = rules
    .filter((rule) => ruleField(rule.match) === null)
    .map((rule) => ({ term: asciiLower(rule.match), color: rule.color }))
    .filter((item) => item.term.trim() !== '');
  if (terms.length === 0 || text === '') return [{ text, color: null }];
  const lower = asciiLower(text);
  const colors: (HighlightColor | null)[] = new Array<HighlightColor | null>(text.length).fill(
    null,
  );
  for (const { term, color } of terms) {
    for (let at = lower.indexOf(term); at >= 0; at = lower.indexOf(term, at + term.length)) {
      for (let index = at; index < at + term.length; index += 1) colors[index] ??= color;
    }
  }
  const parts: TextPart[] = [];
  for (let index = 0; index < text.length; index += 1) {
    const color = colors[index] ?? null;
    const last = parts.at(-1);
    if (last && last.color === color) last.text += text[index] ?? '';
    else parts.push({ text: text[index] ?? '', color });
  }
  return parts;
}
