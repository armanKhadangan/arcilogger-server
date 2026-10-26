/**
 * Arama's pure helpers: the top bar's term as a query, adding and removing clauses (facets, "Hariç
 * tut"), completions for the query bar, a row's summary, the state of a partial scan and the
 * hand-over of a search's raw lines to the evidence wizard.
 *
 * The query text is the single source of truth: a facet click edits the text, and the server
 * parses it again (`POST /search`). The small lexer here only finds where the clauses of the text
 * are (quotes respected); whether a clause is valid is the server's answer (`POST /search/parse`).
 */

import { isApiError } from '@/lib/api';
import type { DateString, RawLine } from '@/lib/api-types';
import { endpoint, siteDateTime, visibleText } from '@/lib/display';
import type { HandoffPayloads } from '@/lib/handoff';
import { SEARCH_AUTO_CONTINUES, SEARCH_VIEW_ROWS } from '@/lib/search-hooks';
import type {
  CatalogueField,
  FieldCatalogue,
  SearchFacets,
  SearchPage,
  SearchRefs,
  SearchRow,
} from '@/lib/search-types';

/** The evidence extract's maximum (`routers.evidence.MAX_EXTRACT_LINES`). */
export const MAX_REFS = 10_000;

// -- the top bar's term ---------------------------------------------------------------------------

const IPV4 =
  /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(\/([12]?\d|3[0-2]))?$/;
const MAC_HEX = /^[0-9a-f]{12}$/i;
const MAC_FORMS = [
  /^[0-9a-f]{2}([:-])[0-9a-f]{2}(\1[0-9a-f]{2}){4}$/i,
  /^[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}$/i,
];
const HOST_NAME =
  /^(?=.{1,253}\.?$)(?:[\p{L}\p{N}](?:[\p{L}\p{N}-]{0,61}[\p{L}\p{N}])?\.)+\p{L}[\p{L}\p{N}-]{0,62}\.?$/u;

/** `00:00:5e:00:53:21` of a MAC in any separator and case, else `null`. */
export function normalizeMac(text: string): string | null {
  if (!MAC_FORMS.some((form) => form.test(text)) && !MAC_HEX.test(text)) return null;
  const hex = text.replace(/[^0-9a-f]/gi, '').toLowerCase();
  return hex.match(/../g)?.join(':') ?? null;
}

/** `"…"` with `\` and `"` escaped (the grammar's quoted form). */
export function quoted(text: string): string {
  return `"${text.replace(/[\\"]/g, (char) => `\\${char}`)}"`;
}

/**
 * The top bar's term as a query (contract 2.22.1): an IPv4 address (or network) → `ip:`, a MAC
 * address → `mac:`, a host name → `domain:`, anything else → quoted free text.
 */
export function termToQuery(term: string): string {
  const text = term.trim();
  if (!text) return '';
  if (IPV4.test(text)) return `ip:${text}`;
  const mac = normalizeMac(text);
  if (mac) return `mac:${mac}`;
  if (HOST_NAME.test(text)) return `domain:${text.replace(/\.$/, '').toLowerCase()}`;
  return quoted(text);
}

// -- clauses ----------------------------------------------------------------------------------------

/** A value as the query writes it: bare when it can be, else quoted (and then literal, no `*`). */
export function quoteValue(value: string): string {
  return /^[^\s",()*\\]+$/u.test(value) ? value : quoted(value);
}

/** One clause of a query text, `[start, end)` in it. */
export interface ClauseToken {
  start: number;
  end: number;
  text: string;
  negated: boolean;
  /** The field name as typed (folded, {@link fold}), `null` for free text. */
  field: string | null;
  /** The text after `field:` (as typed, quotes included). */
  value: string;
}

const FIELD_PREFIX = /^([\p{L}_][\p{L}\p{N}_]*):/u;

/** Where the clauses of `q` are: white space separates them, outside quotes. */
export function splitClauses(q: string): ClauseToken[] {
  const tokens: ClauseToken[] = [];
  let index = 0;
  let negateNext = false;
  while (index < q.length) {
    if (/\s/.test(q[index] ?? '')) {
      index += 1;
      continue;
    }
    const start = index;
    let inQuote = false;
    while (index < q.length) {
      const char = q[index] ?? '';
      if (inQuote && char === '\\') index += 2;
      else {
        if (char === '"') inQuote = !inQuote;
        else if (!inQuote && /\s/.test(char)) break;
        index += 1;
      }
    }
    const end = Math.min(index, q.length);
    const text = q.slice(start, end);
    if (text === 'NOT') {
      negateNext = true;
      continue;
    }
    const negated = negateNext || (text.startsWith('-') && text.length > 1);
    const body = text.startsWith('-') ? text.slice(1) : text;
    const field = FIELD_PREFIX.exec(body);
    tokens.push({
      start,
      end,
      text,
      negated,
      field: field?.[1] ? fold(field[1]) : null,
      value: field ? body.slice(field[0].length) : body,
    });
    negateNext = false;
  }
  return tokens;
}

/** Folded field name → its canonical name, for every name and alias of the catalogue. */
export function fieldNames(fields: readonly CatalogueField[]): Map<string, string> {
  const names = new Map<string, string>();
  for (const field of fields) {
    names.set(fold(field.name), field.name);
    for (const alias of field.aliases) names.set(fold(alias), field.name);
  }
  return names;
}

const FOLD_FROM = 'ABCDEFGHIJKLMNOPQRSTUVWXYZİıŞĞÜÖÇÂÎÛ';
const FOLD_TO = 'abcdefghijklmnopqrstuvwxyziişğüöçâîû';

/**
 * Text folded for comparison like the API's field names and department names
 * (`arcilogger_api.textfold.fold`): ASCII capitals to lower case, `İ I ı` → `i`, `Ş` → `ş`, …
 */
export function fold(text: string): string {
  return text.replace(/[A-ZİıŞĞÜÖÇÂÎÛ]/g, (char) => FOLD_TO[FOLD_FROM.indexOf(char)] ?? char);
}

/** The text of a written value without its quotes and escapes. */
function bareValue(value: string): string {
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"'))
    return value.slice(1, -1).replace(/\\(.)/g, '$1');
  return value;
}

/** `q` without the characters `[start, end)` (white space tidied). */
export function removeClause(q: string, start: number, end: number): string {
  return `${q.slice(0, start)} ${q.slice(end)}`.replace(/\s+/g, ' ').trim();
}

/**
 * `q` with the clause `field:value` (`-field:value` when `negated`) added: nothing changes when
 * it is there already; the opposite clause (the value kept, now excluded, or the other way round)
 * is replaced. Fields compare by their canonical names (`names`: aliases → names).
 */
export function addClause(
  q: string,
  field: string,
  value: string,
  negated = false,
  names: ReadonlyMap<string, string> = new Map(),
): string {
  const canonical = (name: string | null) => (name ? (names.get(fold(name)) ?? fold(name)) : null);
  const wanted = canonical(field);
  const same = (token: ClauseToken) =>
    token.field !== null &&
    canonical(token.field) === wanted &&
    fold(bareValue(token.value)) === fold(value);
  const tokens = splitClauses(q);
  if (tokens.some((token) => same(token) && token.negated === negated)) return q.trim();
  let text = q;
  for (const token of [...tokens].reverse()) {
    if (same(token) && token.negated !== negated) text = removeClause(text, token.start, token.end);
  }
  const clause = `${negated ? '-' : ''}${field}:${quoteValue(value)}`;
  return `${text.trim()} ${clause}`.trim();
}

/** The field a facet filters (`kind` is the field `type`). */
export const FACET_FIELDS: Record<keyof SearchFacets, string> = {
  source: 'source',
  kind: 'type',
  action: 'action',
  vlan: 'vlan',
  department: 'department',
};

// -- completions ----------------------------------------------------------------------------------

export interface Completion {
  /** The text that replaces `[start, end)`. */
  insert: string;
  label: string;
  /** A second line (the field's label, a count). */
  detail?: string;
}

export interface Completions {
  start: number;
  end: number;
  items: Completion[];
}

/** Most suggestions shown at a time. */
export const MAX_COMPLETIONS = 8;

/**
 * Suggestions for the word at `caret`: field names (and aliases) while it has no `:`, then the
 * values the current statistics found for that field (and the kinds and sources of the
 * catalogue). `null` when there is nothing to suggest.
 */
export function completions(
  q: string,
  caret: number,
  catalogue: FieldCatalogue | undefined,
  facets: SearchFacets | undefined,
  lang: 'tr' | 'en',
): Completions | null {
  if (!catalogue) return null;
  let start = caret;
  while (start > 0 && !/[\s"]/.test(q[start - 1] ?? '')) start -= 1;
  let end = caret;
  while (end < q.length && !/[\s"]/.test(q[end] ?? '')) end += 1;
  const word = q.slice(start, caret);
  const sign = word.startsWith('-') ? '-' : '';
  const body = word.slice(sign.length);
  if (!body && !sign) return null;
  const names = fieldNames(catalogue.fields);
  const colon = body.indexOf(':');
  let items: Completion[];
  if (colon < 0) {
    const typed = body.toLowerCase();
    // A field once, by the first of its names that fits what is typed.
    items = catalogue.fields.flatMap((field) =>
      [field.name, ...field.aliases]
        .filter((name) => name.toLowerCase().startsWith(typed) && name.toLowerCase() !== typed)
        .slice(0, 1)
        .map((name) => ({
          insert: `${sign}${name}:`,
          label: `${name}:`,
          detail: lang === 'en' ? field.label_en : field.label_tr,
        })),
    );
  } else {
    const field = names.get(fold(body.slice(0, colon)));
    if (!field) return null;
    const typed = body.slice(colon + 1).toLowerCase();
    const prefix = `${sign}${body.slice(0, colon)}:`;
    const values = valuesOf(field, catalogue, facets, lang).filter(
      (item) => item.value.toLowerCase().startsWith(typed) && item.value.toLowerCase() !== typed,
    );
    items = values.map((item) => ({
      insert: `${prefix}${quoteValue(item.value)}`,
      label: item.value,
      ...(item.detail ? { detail: item.detail } : {}),
    }));
  }
  const shown = items.slice(0, MAX_COMPLETIONS);
  return shown.length > 0 ? { start, end, items: shown } : null;
}

/** Known values of a field: the facets of the current statistics, the catalogue's kinds and sources. */
function valuesOf(
  field: string,
  catalogue: FieldCatalogue,
  facets: SearchFacets | undefined,
  lang: 'tr' | 'en',
): { value: string; detail?: string }[] {
  const facet = (Object.keys(FACET_FIELDS) as (keyof SearchFacets)[]).find(
    (key) => FACET_FIELDS[key] === field,
  );
  const seen = new Map<string, string | undefined>();
  for (const item of facet && facets ? facets[facet].values : [])
    seen.set(item.value, lang === 'en' ? item.label_en : item.label_tr);
  if (field === 'type')
    for (const kind of catalogue.kinds)
      if (!seen.has(kind.key)) seen.set(kind.key, lang === 'en' ? kind.label_en : kind.label_tr);
  if (field === 'source')
    for (const source of catalogue.sources)
      if (!seen.has(source.name)) seen.set(source.name, undefined);
  return [...seen].map(([value, detail]) =>
    detail && detail !== value ? { value, detail } : { value },
  );
}

// -- rows -------------------------------------------------------------------------------------------

/**
 * The "Özet" of a row, from the fields its kind has (`summary`): who went where (`→`), how
 * (protocol, action), what (application, category, user, MAC, interface), the NAT address; the
 * record's text when it has none of them. Device-provided text with its escapes visible.
 */
export function rowSummary(row: SearchRow): string {
  const s = row.summary;
  const parts: string[] = [];
  const source = s.srcip ? endpoint(s.srcip, s.srcport ?? null) : null;
  const target = s.domain ?? (s.dstip ? endpoint(s.dstip, s.dstport ?? null) : null);
  if (source && target) parts.push(`${source} → ${target}`);
  else if (source) parts.push(source);
  else if (target) parts.push(`→ ${target}`);
  if (s.domain && s.dstip) parts.push(endpoint(s.dstip, s.dstport ?? null));
  for (const value of [s.proto, s.action, s.app, s.category, s.user, s.mac, s.interface, s.policy])
    if (value) parts.push(value);
  if (s.natip) parts.push(`NAT ${endpoint(s.natip, s.natport ?? null)}`);
  if (s.url) parts.push(s.url);
  if (parts.length === 0) return row.text ? visibleText(row.text) : '—';
  return visibleText(parts.join(' · '));
}

// -- partial scans ---------------------------------------------------------------------------------

/**
 * What the newest page means for the list: `complete` (nothing more), `more` (a full page: "Daha
 * fazla"), `continuing` (the time budget ended before the range did; the scan goes on by itself
 * up to {@link SEARCH_AUTO_CONTINUES} times), `paused` (then "Devam et"), `capped` (the view
 * holds {@link SEARCH_VIEW_ROWS} rows).
 */
export type ScanState = 'complete' | 'more' | 'continuing' | 'paused' | 'capped';

export function scanState(last: SearchPage, continues: number, rows: number): ScanState {
  if (last.next_cursor === null) return 'complete';
  if (rows >= SEARCH_VIEW_ROWS) return 'capped';
  if (last.scan.complete) return 'more';
  return continues < SEARCH_AUTO_CONTINUES ? 'continuing' : 'paused';
}

// -- errors -------------------------------------------------------------------------------------------

/** A problem of the query at `[start, end)` of its text, in both languages. */
export interface QueryProblem {
  code: string;
  message_tr: string;
  message_en: string;
  start: number;
  end: number;
}

/** The span of a refused search (`details: {start, end}` of a 422 `search_*`), else `null`. */
export function problemOf(error: unknown): QueryProblem | null {
  if (!isApiError(error) || error.status !== 422) return null;
  const details = error.details as { start?: unknown; end?: unknown } | null | undefined;
  if (typeof details?.start !== 'number' || typeof details.end !== 'number') return null;
  return {
    code: error.code,
    message_tr: error.messageTr,
    message_en: error.messageEn,
    start: details.start,
    end: details.end,
  };
}

// -- hand-over to the evidence wizard ------------------------------------------------------------

/** The site day of a moment. */
function siteDay(moment: string): DateString {
  return siteDateTime(moment).date;
}

/**
 * What "Kanıt paketine aktar" leaves for `/evidence`: the search's raw lines as an extract, the
 * days they span (the search's range when there are none) and the API's warnings.
 */
export function evidenceHandoff(page: SearchPage, refs: SearchRefs): HandoffPayloads['/evidence'] {
  const extract: RawLine[] = refs.refs.map((ref) => ({
    day: ref.day,
    source: ref.source,
    line: ref.line,
  }));
  const days = extract.map((line) => line.day).sort();
  // `to` is exclusive: a range ending at midnight ends on the day before.
  const lastMoment = new Date(Date.parse(page.range.to) - 1).toISOString();
  return {
    extract,
    from_day: days[0] ?? siteDay(page.range.from),
    to_day: days.at(-1) ?? siteDay(lastMoment),
    truncated: refs.truncated,
    complete: refs.complete,
    unsealed_days: [...refs.unsealed_days].sort(),
    query: page.query.normalized,
  };
}
