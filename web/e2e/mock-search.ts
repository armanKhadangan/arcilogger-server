/**
 * The mocked search API (`/search`, Wave 2 contract 2.10) of the E2E tests, in the shapes of
 * `src/lib/search-types.ts` and with the permission rules of the router (`search.read` + the
 * accepted KVKK notice; `/search/parse` and `/search/fields` need `search.read` only; export
 * `search.export` plus a re-authentication within 5 minutes; references `evidence.create`).
 * Installed after `mockApi` (`page.route`: the newest route wins), it shares the main mock's
 * session, users and recorded calls. Like the API (every request model `extra="forbid"`), a body
 * with an unknown or without a required field answers 422 `validation_error`; so does a `limit`
 * above the site's `search.page_size_max` (without one: `min(100, page_size_max)` for a run, the
 * search's own for its pages), and a range longer than `search.max_days` answers
 * `search_range_too_long`. Options set those two settings, refuse the next call of a kind with 429
 * `search_busy` (`Retry-After: 2`) or 504 `search_timeout`, and expire a search.
 *
 * What the real API answered is taken from `samples/search.json` (`samples_search.py`): the field
 * catalogue, the texts of every refusal and notice (contract 2.20, the placeholders filled in
 * again), and with the option `captured` the captured runs themselves — their pages, the next
 * page of the captured cursor, their statistics and records, exactly as captured.
 *
 * The other runs answer hand rows: a small fictional day of the example network (VLAN 42
 * Planlama, 172.20.42.x, MAC `00:00:5e:00:53:xx`, RFC 5737 public addresses) whose raw references
 * point at the captured raw lines (`samples/raw.json`), so their raw drawer opens. They are the
 * states the capture does not hold. A few queries select a scenario:
 *  - `srcip:172.20.41.0/24`: 250 rows in pages of 100 ("Daha fazla");
 *  - `srcip:172.20.70.0/24`: two clients of a /24 that holds two smaller VLANs (70 Lab A and 71
 *    Lab B, each a /25), so its VLAN and department facets are the value `multiple`;
 *  - `"login failure"`: a scan cut by its time budget, going on page by page until the frontier
 *    reaches the end (`PARTIAL_STEPS` pages);
 *  - `srcip:172.20.42.250`: nothing;
 *  - otherwise the day's rows, filtered by the `type:`, `source:`, `action:` and `srcip:` clauses.
 */

import { createHash } from 'node:crypto';

import type { Page, Request, Route } from '@playwright/test';

import type {
  FieldCatalogue,
  Notice,
  ParseErrorItem,
  ParseResult,
  RawRef,
  SavedSearch,
  SavedSearchIn,
  SearchClause,
  SearchEvent,
  SearchPage,
  SearchRefs,
  SearchRow,
  SearchStats,
  SearchSummary,
} from '../src/lib/search-types';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import { copy, SEARCH } from './samples';

/** The byte order mark of the API's CSV files. */
const BOM = String.fromCharCode(0xfeff);
/** The site day of the rows' raw lines. */
export const SEARCH_DAY = '2026-10-09';
/** Pages of the cut scan of `"login failure"` before it is complete. */
export const PARTIAL_STEPS = 6;
/** Rows of the `srcip:172.20.41.0/24` scenario. */
export const MANY_ROWS = 250;

/**
 * The refusals' parameters the mock fills in, by code: their values are found in the captured
 * texts and turned back into `{name}` placeholders. The two refusals without `details.params`
 * take the values the capture ran with (`samples_search.py`: the default `search.max_days`, and
 * `search.request_budget_ms` 500 for the timeout).
 */
const FILLED: Record<string, Record<string, string | number> | string[]> = {
  search_syntax: ['position'],
  search_unknown_field: ['field'],
  search_value_invalid: ['value', 'field'],
  search_range_too_long: { days: SEARCH.fields.limits.max_days },
  search_timeout: { seconds: 0.5 },
};

/** A parameter as the API writes it into a text: a day (`date`) as `09.10.2026`, like the UI. */
function shownParam(name: string, value: unknown): string {
  const day = name === 'date' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value)) : null;
  return day ? `${day[3] ?? ''}.${day[2] ?? ''}.${day[1] ?? ''}` : String(value);
}

/** A captured text with the values of `params` turned back into `{name}` placeholders. */
function template(text: string, params: Record<string, unknown>, code: string): string {
  return Object.entries(params).reduce((found, [name, value]) => {
    const shown = shownParam(name, value);
    if (!found.includes(shown)) throw new Error(`search.json ${code}: "${shown}" not in "${text}"`);
    return found.split(shown).join(`{${name}}`);
  }, text);
}

/**
 * Texts of the search API: the refusals captured from the real API (`samples/search.json`, the
 * texts of contract 2.20), with the placeholders of {@link FILLED}.
 */
const TEXTS: Record<string, [string, string]> = Object.fromEntries(
  Object.values(SEARCH.errors).map(({ body: { error } }) => {
    const filled = FILLED[error.code] ?? [];
    const captured = (error.details as { params?: Record<string, unknown> } | null | undefined)
      ?.params;
    const params = Array.isArray(filled)
      ? Object.fromEntries(filled.map((name) => [name, captured?.[name]]))
      : filled;
    return [
      error.code,
      [
        template(error.message_tr, params, error.code),
        template(error.message_en, params, error.code),
      ],
    ];
  }),
);

/** The facet value of a /24 that holds several smaller VLANs, and its labels (`search.stats`). */
const MULTIPLE = 'multiple';
const MULTIPLE_VLAN: [string, string] = ['Birden çok VLAN', 'Several VLANs'];
const MULTIPLE_DEPARTMENT: [string, string] = ['Birden çok departman', 'Several departments'];

/**
 * The notices of the captured pages and statistics (`samples/search.json`) as templates, and the
 * two the capture shows none of, in the words of `arcilogger_api.search.messages`.
 */
const NOTICES: Record<string, [string, string]> = {
  text_case: [
    'Serbest metinde büyük/küçük harf farkı yalnızca İngilizce harflerde yok sayılır; İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç yazıldığı gibi aranır.',
    'Free text ignores case for English letters only; İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç are matched as typed.',
  ],
  department_by_vlan: [
    "Departman, istemci adresinin VLAN'ına tanımlı varsayılan departmandır; kişi ataması değildir.",
    "The department is the default department of the client address's VLAN, not the person's assignment.",
  ],
  ...Object.fromEntries(
    [...Object.values(SEARCH.search), ...Object.values(SEARCH.stats)]
      .flatMap((answer) => answer.notices)
      .map((item) => [
        item.code,
        [
          template(item.message_tr, item.params, item.code),
          template(item.message_en, item.params, item.code),
        ],
      ]),
  ),
};

/** `text` with its `{name}` placeholders filled in. */
function fill(text: string, params: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (_, name: string) =>
    params[name] === undefined ? `{${name}}` : shownParam(name, params[name]),
  );
}

function notice(code: string, params: Record<string, string> = {}): Notice {
  const [tr, en] = NOTICES[code] ?? [code, code];
  return { code, message_tr: fill(tr, params), message_en: fill(en, params), params };
}

// -- the field catalogue (contract 2.4) ---------------------------------------------------------

/** The field catalogue as the API sends it (`GET /search/fields`, captured). */
export const CATALOGUE: FieldCatalogue = copy(SEARCH.fields);

const NAMES = new Map<string, string>(
  CATALOGUE.fields.flatMap((item) => [item.name, ...item.aliases].map((name) => [name, item.name])),
);

const KIND_LABELS = new Map(CATALOGUE.kinds.map((kind) => [kind.key, kind]));

// -- the day's rows ----------------------------------------------------------------------------

const EMPTY_SUMMARY: SearchSummary = {
  srcip: null,
  srcport: null,
  dstip: null,
  dstport: null,
  natip: null,
  natport: null,
  action: null,
  domain: null,
  url: null,
  app: null,
  category: null,
  user: null,
  mac: null,
  proto: null,
  policy: null,
  interface: null,
  vlan_id: null,
};

interface Base {
  table: string;
  kind: string;
  type: string;
  subtype: string | null;
  source: string;
  sender: string;
  summary: Partial<SearchSummary>;
  text: string;
  raw: RawRef;
}

/** The day's records, newest first; the raw references are lines of `samples/raw.json`. */
const BASE: Base[] = [
  {
    table: 'fg_traffic',
    kind: 'traffic',
    type: 'traffic',
    subtype: 'forward',
    source: 'fortigate',
    sender: '172.31.255.1',
    summary: {
      srcip: '172.20.42.57',
      srcport: 61001,
      dstip: '203.0.113.10',
      dstport: 443,
      natip: '198.51.100.7',
      natport: 61001,
      action: 'accept',
      proto: 'tcp',
      vlan_id: 42,
    },
    text: 'type="traffic" subtype="forward" srcip=172.20.42.57 dstip=203.0.113.10 dstport=443 action="accept"',
    raw: { source: 'fortigate', day: SEARCH_DAY, line: 27, offset: 10260 },
  },
  {
    table: 'mt_arp',
    kind: 'arp',
    type: 'arp',
    subtype: null,
    source: 'core-r1',
    sender: '172.20.10.1',
    summary: {
      srcip: '172.20.42.57',
      mac: '00:00:5e:00:53:57',
      interface: 'vlan42',
      vlan_id: 42,
    },
    text: 'ARP-LOG 172.20.42.57 00:00:5E:00:53:57 vlan42',
    raw: { source: 'core-r1', day: SEARCH_DAY, line: 7, offset: 937 },
  },
  {
    table: 'fg_web',
    kind: 'web',
    type: 'utm',
    subtype: 'webfilter',
    source: 'fortigate',
    sender: '172.31.255.1',
    summary: {
      srcip: '172.20.42.57',
      domain: 'www.example.com',
      url: '/gundem',
      action: 'blocked',
      category: 'Haber',
      vlan_id: 42,
    },
    text: 'type="utm" subtype="webfilter" srcip=172.20.42.57 hostname="www.example.com" action="blocked"',
    raw: { source: 'fortigate', day: SEARCH_DAY, line: 2, offset: 160 },
  },
  {
    table: 'fg_dns',
    kind: 'dns',
    type: 'utm',
    subtype: 'dns',
    source: 'fortigate',
    sender: '172.31.255.1',
    summary: { srcip: '172.20.60.142', domain: 'news.example.com', vlan_id: 60 },
    text: 'type="utm" subtype="dns" srcip=172.20.60.142 qname="news.example.com"',
    raw: { source: 'fortigate', day: SEARCH_DAY, line: 1, offset: 0 },
  },
  {
    table: 'mt_dhcp',
    kind: 'dhcp',
    type: 'dhcp',
    subtype: 'assigned',
    source: 'core-r1',
    sender: '172.20.10.1',
    summary: {
      srcip: '172.20.42.57',
      mac: '00:00:5e:00:53:57',
      action: 'assigned',
      vlan_id: 42,
    },
    text: 'dhcp42 assigned 172.20.42.57 for 00:00:5E:00:53:57 PLN-PC14',
    raw: { source: 'core-r1', day: SEARCH_DAY, line: 5, offset: 635 },
  },
];

/** The rows of the "many" scenario: traffic of VLAN 41 (raw references cycle through the day's). */
function manyRows(): Base[] {
  return Array.from({ length: MANY_ROWS }, (_, index) => {
    const base = BASE[0] as Base;
    return {
      ...base,
      summary: {
        ...base.summary,
        srcip: `172.20.41.${(index % 200) + 10}`,
        srcport: 50000 + index,
        vlan_id: 41,
      },
    };
  });
}

/** The /24 of the "Lab" scenario: two VLANs of a /25 each (70 Lab A, 71 Lab B). */
const LAB_NET = '172.20.70.';

/** The rows of the "Lab" scenario: one client in each of the /24's two VLANs. */
function labRows(): Base[] {
  const base = BASE[0] as Base;
  return [
    ['172.20.70.20', 70],
    ['172.20.70.140', 71],
  ].map(([srcip, vlan]) => ({
    ...base,
    summary: { ...base.summary, srcip: String(srcip), vlan_id: Number(vlan) },
  }));
}

/** A client clause's address (`srcip:` and its aliases). */
function clauseValue(q: string, fieldName: string): string[] {
  const values: string[] = [];
  for (const token of tokens(q)) {
    if (token.field && NAMES.get(token.field.toLowerCase()) === fieldName && !token.negated)
      values.push(...token.values);
  }
  return values;
}

/** The base rows kept by the `type:`, `source:`, `action:` and `srcip:` clauses of `q`. */
function filtered(q: string): Base[] {
  let rows = BASE;
  for (const token of tokens(q)) {
    const name = token.field ? NAMES.get(token.field.toLowerCase()) : undefined;
    const value = (row: Base): string | null | undefined =>
      name === 'type'
        ? row.kind
        : name === 'source'
          ? row.source
          : name === 'action'
            ? row.summary.action
            : name === 'srcip'
              ? row.summary.srcip
              : undefined;
    if (!name || value(BASE[0] as Base) === undefined) continue;
    rows = rows.filter((row) => {
      const found = token.values.some(
        (item) => item.toLowerCase() === (value(row) ?? '').toLowerCase(),
      );
      return token.negated ? !found : found;
    });
  }
  return rows;
}

// -- a small reading of the query ----------------------------------------------------------------

interface Token {
  start: number;
  end: number;
  text: string;
  negated: boolean;
  field: string | null;
  values: string[];
}

function unquote(value: string): string {
  return value.startsWith('"') && value.endsWith('"') && value.length >= 2
    ? value.slice(1, -1).replace(/\\(.)/g, '$1')
    : value;
}

function tokens(q: string): Token[] {
  const found: Token[] = [];
  const pattern = /(?:[^\s"]|"(?:[^"\\]|\\.)*"?)+/g;
  for (const match of q.matchAll(pattern)) {
    const text = match[0];
    const negated = text.startsWith('-') && text.length > 1;
    const body = negated ? text.slice(1) : text;
    const fielded = /^([\p{L}_][\p{L}\p{N}_]*):(.*)$/su.exec(body);
    found.push({
      start: match.index,
      end: match.index + text.length,
      text,
      negated,
      field: fielded?.[1] ?? null,
      values: fielded ? (fielded[2] ?? '').split(',').map(unquote) : [unquote(body)],
    });
  }
  return found;
}

/** A query problem: the item `POST /search/parse` lists, and the `params` a run's 422 carries. */
interface Problem {
  item: ParseErrorItem;
  params: Record<string, string | number>;
}

/** A problem of `code` over `[start, end)`, in the API's words with `params` filled in. */
function problem(
  code: string,
  start: number,
  end: number,
  params: Record<string, string | number> = {},
): Problem {
  const [tr, en] = TEXTS[code] ?? [code, code];
  return {
    item: { code, message_tr: fill(tr, params), message_en: fill(en, params), start, end },
    params,
  };
}

/** The query's problems (the first of each clause), as `POST /search/parse` lists them. */
function problems(q: string): Problem[] {
  const quote =
    q.split('').filter((char) => char === '"').length % 2 === 1 ? q.lastIndexOf('"') : -1;
  if (quote >= 0)
    return [
      problem('search_syntax', quote, q.length, { position: quote + 1, reason: 'unclosed_quote' }),
    ];
  const items: Problem[] = [];
  for (const token of tokens(q)) {
    if (/^[()]|[()]$/.test(token.text) || token.text === 'OR') {
      items.push(problem('search_or_unsupported', token.start, token.end));
    } else if (token.field && !NAMES.has(token.field.toLowerCase())) {
      items.push(problem('search_unknown_field', token.start, token.end, { field: token.field }));
    } else if (token.field && NAMES.get(token.field.toLowerCase()) === 'srcip') {
      const value = token.values.find(
        (item) =>
          !/^(\d{1,3})(\.(\d{1,3}|\*)){0,3}(\/\d{1,2})?$/.test(item) ||
          item.split('.').some((part) => Number(part.split('/')[0]) > 255),
      );
      if (value !== undefined)
        items.push(
          problem('search_value_invalid', token.start + token.text.indexOf(':') + 1, token.end, {
            field: 'srcip',
            format: 'ip',
            value,
          }),
        );
    } else if (
      !token.field &&
      token.text !== 'NOT' &&
      token.text !== 'AND' &&
      unquote(token.negated ? token.text.slice(1) : token.text).length < 3
    ) {
      items.push(problem('search_term_too_short', token.start, token.end));
    }
  }
  return items;
}

/** Canonical field names, single spaces. */
function normalize(q: string): string {
  return tokens(q)
    .map((token) => {
      if (!token.field) return token.text;
      const name = NAMES.get(token.field.toLowerCase()) ?? token.field;
      return `${token.negated ? '-' : ''}${name}:${token.text.slice(token.text.indexOf(':') + 1)}`;
    })
    .join(' ');
}

function clauses(q: string): SearchClause[] {
  return tokens(q)
    .filter((token) => token.text !== 'NOT' && token.text !== 'AND')
    .map((token) => ({
      field: token.field ? (NAMES.get(token.field.toLowerCase()) ?? token.field) : 'text',
      op: token.field ? (token.values.join() === '*' ? 'present' : 'match') : 'text',
      values: token.values,
      negated: token.negated,
      start: token.start,
      end: token.end,
    }));
}

function parse(q: string): ParseResult {
  const errors = problems(q).map((found) => found.item);
  if (errors.length > 0)
    return { normalized: '', clauses: [], kinds: [], selective: false, errors };
  const found = clauses(q);
  const kinds = found
    .filter((clause) => clause.field === 'type' && !clause.negated)
    .flatMap((clause) => clause.values);
  return {
    normalized: normalize(q),
    clauses: found,
    kinds: kinds.length > 0 ? kinds : CATALOGUE.kinds.map((kind) => kind.key),
    selective: found.some((clause) =>
      ['srcip', 'mac', 'domain', 'qname', 'sessionid'].includes(clause.field),
    ),
    errors: [],
  };
}

// -- the mock -------------------------------------------------------------------------------------

/** A saved search as the API shows it (without its owner). */
function shown(item: SavedSearch & { owner: number }): SavedSearch {
  return {
    id: item.id,
    name: item.name,
    q: item.q,
    range: item.range,
    created_at: item.created_at,
    updated_at: item.updated_at,
    last_run_at: item.last_run_at,
  };
}

/** `[from, to)` of a saved search's preset now (whole hours of the site day for the days). */
function presetSpan(preset: string): { from: string; to: string } {
  const end = Math.ceil(Date.now() / 1000) * 1000;
  const spans: Record<string, number> = {
    '15m': 15 * 60_000,
    '1h': 3_600_000,
    '24h': 86_400_000,
    '7d': 7 * 86_400_000,
    '30d': 30 * 86_400_000,
  };
  const midnight = Date.parse(
    `${new Date(end + 3 * 3_600_000).toISOString().slice(0, 10)}T00:00:00+03:00`,
  );
  const [from, to] =
    preset === 'today'
      ? [midnight, Math.max(end, midnight + 1000)]
      : preset === 'yesterday'
        ? [midnight - 86_400_000, midnight]
        : [end - (spans[preset] ?? 86_400_000), end];
  return { from: new Date(from).toISOString(), to: new Date(to).toISOString() };
}

type Answer =
  | [number, unknown]
  | [number, unknown, { headers: Record<string, string> }]
  | [number, unknown, { body: Buffer; contentType: string; headers: Record<string, string> }];

interface Search {
  id: string;
  owner: number;
  q: string;
  from: string;
  to: string;
  /** Rows per page: the run's `limit`, else `min(100, search.page_size_max)`. */
  limit: number;
  rows: SearchRow[];
  scenario: 'rows' | 'partial';
  savedId: number | null;
  /** The captured page this search answered with (`captured` option). */
  captured: SearchPage | null;
}

/** The captured runs (`samples/search.json`); `next_page` is the second page of `first_page`. */
const CAPTURED_RUNS = Object.entries(SEARCH.search).filter(([name]) => name !== 'next_page');

/** The same instant (the web app sends UTC, the capture shows site time). */
function sameTime(a: string, b: string): boolean {
  return Date.parse(a) === Date.parse(b);
}

/** The calls that take one of the server's search slots (contract 2.6.4). */
export type SlotCall = 'run' | 'page' | 'stats' | 'event' | 'refs' | 'export';

const SLOT_CALLS: Record<string, SlotCall> = {
  'POST /search': 'run',
  'POST /search/page': 'page',
  'POST /search/stats': 'stats',
  'GET /search/event': 'event',
  'POST /search/refs': 'refs',
  'POST /search/export': 'export',
};

/** Request models (`extra="forbid"`): required and optional fields. */
const BODIES: Record<string, { required: string[]; optional: string[] }> = {
  'POST /search': { required: [], optional: ['q', 'from', 'to', 'limit', 'saved_id'] },
  'POST /search/page': { required: ['search_id', 'cursor'], optional: ['limit'] },
  'POST /search/stats': { required: ['search_id'], optional: [] },
  'POST /search/parse': { required: ['q'], optional: [] },
  'POST /search/refs': { required: ['search_id'], optional: ['limit'] },
  'POST /search/export': { required: ['search_id', 'format'], optional: [] },
  'POST /search/saved': { required: ['name', 'q', 'range'], optional: [] },
  'PUT /search/saved/{id}': { required: ['name', 'q', 'range'], optional: [] },
};

export interface SearchOptions {
  /** The next call of this kind answers 409 `search_expired` (the search is forgotten). */
  expireNext?: 'page' | 'stats' | 'refs' | 'export' | null;
  /**
   * The next call of this kind is refused before it runs: 429 `search_busy` (with
   * `Retry-After: 2`; the web app asks once more) or 504 `search_timeout`.
   */
  refuseNext?: { call: SlotCall; code: 'search_busy' | 'search_timeout' } | null;
  /** The export file is cut short (`X-Arci-Export-Truncated`). */
  exportTruncated?: 'rows' | 'time' | null;
  /** Days the references are not sealed on. */
  unsealedDays?: string[];
  /** The site's `search.page_size_max` (the setting's default: 500; at least 50). */
  pageSizeMax?: number;
  /** The site's `search.max_days` (the setting's default: 31; at least 1). */
  maxDays?: number;
  /**
   * Answer what the capture holds exactly as captured: a run with the query and range of a
   * captured page (the auditor's to the auditor only) gets that page, then its next page for the
   * captured cursor, its statistics when the capture has them, and the captured records. Every
   * other run gets the hand rows, numbered after the captured searches.
   */
  captured?: boolean;
}

export class SearchMock {
  readonly searches = new Map<string, Search>();
  readonly saved = new Map<number, SavedSearch & { owner: number }>();
  expireNext: SearchOptions['expireNext'];
  refuseNext: SearchOptions['refuseNext'];
  exportTruncated: SearchOptions['exportTruncated'];
  unsealedDays: string[];
  pageSizeMax: number;
  maxDays: number;
  readonly captured: boolean;
  private nextSearch: number;
  private nextSaved = 1;

  constructor(
    private readonly api: MockApi,
    options: SearchOptions = {},
  ) {
    this.expireNext = options.expireNext ?? null;
    this.refuseNext = options.refuseNext ?? null;
    this.exportTruncated = options.exportTruncated ?? null;
    this.unsealedDays = options.unsealedDays ?? [];
    this.pageSizeMax = options.pageSizeMax ?? CATALOGUE.limits.page_size_max;
    this.maxDays = options.maxDays ?? CATALOGUE.limits.max_days;
    this.captured = options.captured ?? false;
    // Captured searches are `ornek-arama-01` … `-11`: hand searches follow them.
    this.nextSearch = this.captured ? 51 : 1;
  }

  /** The field catalogue with this site's settings. */
  catalogue(): FieldCatalogue {
    return {
      ...CATALOGUE,
      limits: { ...CATALOGUE.limits, page_size_max: this.pageSizeMax, max_days: this.maxDays },
    };
  }

  async install(page: Page): Promise<void> {
    await page.route('**/api/v1/search**', (route, request) => this.handle(route, request));
  }

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, '');
    let body: unknown;
    try {
      body = request.postDataJSON() as unknown;
    } catch {
      body = undefined;
    }
    const headers = request.headers();
    const call: RecordedCall = {
      method: request.method(),
      path,
      query: url.searchParams,
      body,
      raw: request.postDataBuffer()?.toString('utf8') ?? null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.api.calls.push(call);
    const [status, payload, file] = this.answer(call);
    await route.fulfill(
      file && 'body' in file
        ? { status, contentType: file.contentType, headers: file.headers, body: file.body }
        : {
            status,
            contentType: 'application/json',
            headers: { 'Cache-Control': 'no-store', ...file?.headers },
            body: status === 204 ? '' : JSON.stringify(payload),
          },
    );
  }

  private error(
    status: number,
    code: string,
    details?: unknown,
    params: Record<string, string | number> = {},
  ): [number, unknown] {
    const texts = TEXTS[code];
    if (!texts) return this.api.error(status, code, details);
    return [
      status,
      {
        error: {
          code,
          message_tr: fill(texts[0], params),
          message_en: fill(texts[1], params),
          ...(details ? { details } : {}),
        },
      },
    ];
  }

  /** 422 `validation_error` of a `limit` above `search.page_size_max` (as the router words it). */
  private limitProblem(limit: number | null | undefined): [number, unknown] | null {
    if (limit === undefined || limit === null || limit <= this.pageSizeMax) return null;
    return this.error(422, 'validation_error', [
      {
        loc: ['body', 'limit'],
        type: 'less_than_equal',
        msg: `Input should be less than or equal to ${this.pageSizeMax}`,
      },
    ]);
  }

  /** `search.read` (and `more`), the accepted KVKK notice unless `noticeFree`. */
  private person(
    call: RecordedCall,
    more: string[] = [],
    noticeFree = false,
  ): MockUser | [number, unknown] {
    let account: MockUser | [number, unknown] = this.api.requirePermission(call, 'search.read');
    for (const permission of more) {
      if (Array.isArray(account)) break;
      account = this.api.requirePermission(call, permission, permission === 'search.export');
    }
    if (Array.isArray(account)) return account;
    if (!noticeFree && !account.privacy_accepted)
      return this.api.error(403, 'privacy_notice_required');
    return account;
  }

  private bodyProblems(route: string, body: unknown): unknown[] {
    const model = BODIES[route];
    if (!model) return [];
    if (typeof body !== 'object' || body === null || Array.isArray(body))
      return [{ loc: ['body'], type: 'model_attributes_type', msg: 'Input should be an object' }];
    const known = new Set([...model.required, ...model.optional]);
    const problems: unknown[] = [
      ...Object.keys(body)
        .filter((key) => !known.has(key))
        .map((key) => ({
          loc: ['body', key],
          type: 'extra_forbidden',
          msg: 'Extra inputs are not permitted',
        })),
      ...model.required
        .filter((key) => !Object.hasOwn(body, key))
        .map((key) => ({ loc: ['body', key], type: 'missing', msg: 'Field required' })),
    ];
    const range = (body as { range?: unknown }).range;
    if (range !== undefined) {
      const keys =
        typeof range === 'object' && range !== null ? Object.keys(range).sort().join() : '';
      if (keys !== 'preset' && keys !== 'from,to')
        problems.push({
          loc: ['body', 'range'],
          type: 'union_tag_invalid',
          msg: 'Input should be a preset or a fixed range',
        });
    }
    return problems;
  }

  private answer(call: RecordedCall): Answer {
    const { method, path } = call;
    const route = `${method} ${path.replace(/\/\d+$/, '/{id}')}`;
    const problems = this.bodyProblems(route, call.body);
    if (problems.length > 0) {
      const account = this.api.requirePermission(call, 'search.read');
      return Array.isArray(account) ? account : this.error(422, 'validation_error', problems);
    }
    const slot = SLOT_CALLS[route];
    const refusal = this.refuseNext;
    if (slot !== undefined && refusal?.call === slot) {
      const account = this.person(call);
      if (Array.isArray(account)) return account;
      this.refuseNext = null;
      if (refusal.code === 'search_busy') {
        const [status, payload] = this.error(429, 'search_busy');
        return [status, payload, { headers: { 'Retry-After': '2' } }];
      }
      return this.error(504, 'search_timeout', undefined, { seconds: 4 });
    }
    switch (route) {
      case 'GET /search/fields': {
        const account = this.person(call, [], true);
        return Array.isArray(account) ? account : [200, this.catalogue()];
      }
      case 'POST /search/parse': {
        const account = this.person(call, [], true);
        return Array.isArray(account) ? account : [200, parse((call.body as { q: string }).q)];
      }
      case 'POST /search':
        return this.run(call);
      case 'POST /search/page':
        return this.page(call);
      case 'POST /search/stats':
        return this.stats(call);
      case 'GET /search/event':
        return this.event(call);
      case 'POST /search/refs':
        return this.refs(call);
      case 'POST /search/export':
        return this.export(call);
      case 'GET /search/saved': {
        const account = this.person(call);
        if (Array.isArray(account)) return account;
        return [200, { items: this.savedOf(account) }];
      }
      case 'POST /search/saved':
        return this.saveSearch(call, null);
      case 'PUT /search/saved/{id}':
        return this.saveSearch(call, Number(path.split('/').pop()));
      case 'DELETE /search/saved/{id}':
        return this.deleteSaved(call, Number(path.split('/').pop()));
      default:
        return this.api.error(404, 'not_found');
    }
  }

  // -- searching ----------------------------------------------------------------------------------

  private savedOf(account: MockUser): SavedSearch[] {
    return [...this.saved.values()].filter((item) => item.owner === account.id).map(shown);
  }

  private run(call: RecordedCall): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const body = call.body as {
      q?: string;
      from?: string | null;
      to?: string | null;
      limit?: number | null;
      saved_id?: number | null;
    };
    let q = body.q ?? '';
    let from = body.from ?? null;
    let to = body.to ?? null;
    let savedId: number | null = null;
    if (body.saved_id !== undefined && body.saved_id !== null) {
      const item = this.saved.get(body.saved_id);
      if (!item || item.owner !== account.id) return this.error(404, 'saved_search_not_found');
      savedId = item.id;
      q = item.q;
      ({ from, to } = 'preset' in item.range ? presetSpan(item.range.preset) : item.range);
      item.last_run_at = new Date().toISOString();
    }
    const tooMany = this.limitProblem(body.limit);
    if (tooMany) return tooMany;
    if (!from || !to || !(Date.parse(from) < Date.parse(to)))
      return this.error(422, 'search_range_invalid');
    if (Date.parse(to) - Date.parse(from) > this.maxDays * 86_400_000)
      return this.error(422, 'search_range_too_long', undefined, { days: this.maxDays });
    const found = problems(q)[0];
    if (found)
      return [
        422,
        {
          error: {
            code: found.item.code,
            message_tr: found.item.message_tr,
            message_en: found.item.message_en,
            details: { start: found.item.start, end: found.item.end, params: found.params },
          },
        },
      ];
    const page = this.captured ? this.capturedRun(account, q, from, to) : null;
    if (page) {
      this.searches.set(page.search_id, {
        id: page.search_id,
        owner: account.id,
        q,
        from,
        to,
        limit: body.limit ?? Math.min(100, this.pageSizeMax),
        rows: page.items,
        scenario: page.scan.complete ? 'rows' : 'partial',
        savedId,
        captured: page,
      });
      return [200, copy(page)];
    }
    const search: Search = {
      id: `ornek-arama-${String(this.nextSearch++).padStart(2, '0')}`,
      owner: account.id,
      q,
      from,
      to,
      limit: body.limit ?? Math.min(100, this.pageSizeMax),
      rows: [],
      scenario: q.includes('"login failure"') ? 'partial' : 'rows',
      savedId,
      captured: null,
    };
    const bases = q.includes('172.20.42.250')
      ? []
      : clauseValue(q, 'srcip').includes('172.20.41.0/24')
        ? manyRows()
        : clauseValue(q, 'srcip').includes(`${LAB_NET}0/24`)
          ? labRows()
          : search.scenario === 'partial'
            ? [BASE[0] as Base, BASE[2] as Base]
            : filtered(q);
    search.rows = this.place(bases, from, to, this.nextSearch - 1);
    this.searches.set(search.id, search);
    return [
      200,
      this.pageOf(search, search.scenario === 'partial' ? 'frontier:1' : 'rows:0', search.limit),
    ];
  }

  /** The captured page of this query and range, for this user's role. */
  private capturedRun(account: MockUser, q: string, from: string, to: string): SearchPage | null {
    const normalized = parse(q).normalized;
    for (const [name, page] of CAPTURED_RUNS) {
      if ((name === 'auditor') !== (account.role === 'auditor')) continue;
      if (page.query.normalized !== normalized) continue;
      if (sameTime(page.range.from, from) && sameTime(page.range.to, to)) return page;
    }
    return null;
  }

  /** The rows, newest first, spread over the last hours of `[from, to)`. */
  private place(bases: Base[], from: string, to: string, number: number): SearchRow[] {
    const end = Date.parse(to);
    const span = end - Date.parse(from);
    const step = Math.max(1000, Math.min(span / (bases.length + 1), 37 * 60_000));
    return bases.map((base, index) => {
      // Whole seconds, like the device times of the parsers.
      const moment = Math.floor((end - (index + 1) * step) / 1000) * 1000;
      const time = new Date(moment).toISOString().replace('.000Z', 'Z');
      return {
        // The database id: unique over the mock's searches.
        key: `${base.table}:${time}:${number * 10_000 + index + 1}`,
        table: base.table,
        time,
        time_received: new Date(moment + 3).toISOString(),
        source: base.source,
        sender: base.sender,
        kind: base.kind,
        type: base.type,
        subtype: base.subtype,
        summary: { ...EMPTY_SUMMARY, ...base.summary },
        text: base.text,
        raw: { ...base.raw },
        parse_error: null,
      };
    });
  }

  /** The page at `cursor` (`rows:<offset>` or, for the cut scan, `frontier:<step>`). */
  private pageOf(search: Search, cursor: string, limit: number): SearchPage {
    const [kind, value] = cursor.split(':');
    const position = Number(value);
    let items: SearchRow[];
    let next: string | null;
    let complete = true;
    if (kind === 'frontier') {
      // The first page has the newest row; the scan goes on empty until the last step, which
      // finds the older one.
      items =
        position === 1
          ? search.rows.slice(0, 1)
          : position > PARTIAL_STEPS
            ? search.rows.slice(1)
            : [];
      complete = position > PARTIAL_STEPS;
      next = complete ? null : `frontier:${position + 1}`;
    } else {
      items = search.rows.slice(position, position + limit);
      next = position + limit < search.rows.length ? `rows:${position + limit}` : null;
    }
    const span = Date.parse(search.to) - Date.parse(search.from);
    const reached = complete
      ? search.from
      : new Date(Date.parse(search.to) - (position * span) / (PARTIAL_STEPS + 4)).toISOString();
    const notices = [notice('vault_only_before', { date: SEARCH_DAY })];
    if (search.scenario === 'partial')
      notices.push(notice('text_case'), notice('query_not_selective'));
    const parsed = parse(search.q);
    return {
      search_id: search.id,
      expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
      query: {
        normalized: parsed.normalized,
        clauses: parsed.clauses,
        text: parsed.clauses
          .filter((clause) => clause.op === 'text' && !clause.negated)
          .flatMap((clause) => clause.values),
      },
      range: { from: search.from, to: search.to },
      plan: {
        driver: parsed.clauses.some((clause) => clause.op !== 'text') ? 'typed' : 'messages',
        tables: [...new Set(search.rows.map((row) => row.table))],
        kinds: parsed.kinds,
        selective: parsed.selective,
        rollup_eligible: false,
      },
      items,
      next_cursor: next,
      scan: {
        complete,
        scanned_from: reached,
        scanned_to: search.to,
        windows: 5,
        elapsed_ms: complete ? 14 : 3990,
      },
      notices,
    };
  }

  /** The search of a request's `search_id`, or the refusal; `expireNext` of `kind` expires it. */
  private searchOf(
    call: RecordedCall,
    kind: SearchOptions['expireNext'],
    account: MockUser,
  ): Search | [number, unknown] {
    const id = (call.body as { search_id?: string } | undefined)?.search_id;
    const search = id === undefined ? undefined : this.searches.get(id);
    if (this.expireNext === kind && search) {
      this.expireNext = null;
      this.searches.delete(search.id);
      return this.error(409, 'search_expired');
    }
    if (!search || search.owner !== account.id) return this.error(409, 'search_expired');
    return search;
  }

  private page(call: RecordedCall): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const search = this.searchOf(call, 'page', account);
    if (Array.isArray(search)) return search;
    const { cursor, limit } = call.body as { cursor: string; limit?: number | null };
    const tooMany = this.limitProblem(limit);
    if (tooMany) return tooMany;
    if (search.captured) {
      // Only the first page's cursor has a captured answer: the next page of that search.
      const next = SEARCH.search.next_page;
      return search.captured.next_cursor === cursor && next.search_id === search.id
        ? [200, copy(next)]
        : this.error(422, 'search_cursor_invalid');
    }
    if (!/^(rows|frontier):\d+$/.test(cursor)) return this.error(422, 'search_cursor_invalid');
    return [200, this.pageOf(search, cursor, limit ?? search.limit)];
  }

  private stats(call: RecordedCall): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const search = this.searchOf(call, 'stats', account);
    if (Array.isArray(search)) return search;
    const captured = Object.values(SEARCH.stats).find((stats) => stats.search_id === search.id);
    if (search.captured && captured) return [200, copy(captured)];
    return [200, this.statsOf(search)];
  }

  private statsOf(search: Search): SearchStats {
    const from = Date.parse(search.from);
    const to = Date.parse(search.to);
    const span = to - from;
    const bucket =
      [60, 300, 900, 3600, 10_800, 86_400].find((seconds) => span / (seconds * 1000) <= 48) ??
      86_400;
    const buckets: { start: string; count: number }[] = [];
    // Whole buckets counted from the site midnight before `from`, as the API counts them: the
    // first may start before the range and the last end after it.
    const midnight = Date.parse(
      `${new Date(from + 3 * 3_600_000).toISOString().slice(0, 10)}T00:00:00+03:00`,
    );
    const firstStart = midnight + Math.floor((from - midnight) / (bucket * 1000)) * bucket * 1000;
    for (let start = firstStart; start < to; start += bucket * 1000) {
      const count = search.rows.filter((row) => {
        const time = Date.parse(row.time);
        return time >= start && time < start + bucket * 1000;
      }).length;
      buckets.push({ start: new Date(start).toISOString(), count });
    }
    const count = (pick: (row: SearchRow) => string | number | null | undefined) => {
      const counts = new Map<string, number>();
      for (const row of search.rows) {
        const value = pick(row);
        if (value !== null && value !== undefined)
          counts.set(String(value), (counts.get(String(value)) ?? 0) + 1);
      }
      return [...counts].sort((a, b) => b[1] - a[1]);
    };
    const facet = (
      values: [string, number][],
      label: (value: string) => [string, string] = (value) => [value, value],
    ) => ({
      status: 'ok' as const,
      values: values.slice(0, 10).map(([value, total]) => {
        const [label_tr, label_en] = label(value);
        return { value, label_tr, label_en, count: total };
      }),
      other: values.slice(10).reduce((sum, [, total]) => sum + total, 0),
    });
    const partial = search.scenario === 'partial';
    const lab = (row: SearchRow) => row.summary.srcip?.startsWith(LAB_NET) ?? false;
    return {
      search_id: search.id,
      mode: partial ? 'direct' : 'rollup',
      complete: !partial,
      covered_from: partial ? new Date(to - span / 4).toISOString() : search.from,
      covered_to: search.to,
      total: { value: search.rows.length, exact: !partial },
      histogram: { bucket_seconds: bucket, buckets },
      facets: {
        source: facet(count((row) => row.source)),
        kind: facet(
          count((row) => row.kind),
          (value) => {
            const kind = KIND_LABELS.get(value);
            return [kind?.label_tr ?? value, kind?.label_en ?? value];
          },
        ),
        action: facet(count((row) => row.summary.action)),
        // By the client's /24: the Lab /24 holds two VLANs, so it is `multiple` in both.
        vlan: facet(
          count((row) => (lab(row) ? MULTIPLE : row.summary.vlan_id)),
          (value) => {
            if (value === MULTIPLE) return MULTIPLE_VLAN;
            const name = value === '42' ? 'Planlama' : value === '41' ? 'Finans' : 'Wi-Fi Personel';
            return [`VLAN ${value} (${name})`, `VLAN ${value} (${name})`];
          },
        ),
        department: {
          ...facet(
            count((row) =>
              lab(row)
                ? MULTIPLE
                : row.summary.vlan_id === 42
                  ? 'Planlama'
                  : row.summary.vlan_id === 41
                    ? 'Finans'
                    : null,
            ),
            (value) => (value === MULTIPLE ? MULTIPLE_DEPARTMENT : [value, value]),
          ),
          status: partial ? 'unavailable' : 'ok',
        },
      },
      sampled: false,
      sample_rows: null,
      notices: [notice('department_by_vlan')],
      elapsed_ms: 8,
    };
  }

  private event(call: RecordedCall): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const table = call.query.get('table');
    const id = call.query.get('id');
    const time = call.query.get('time') ?? '';
    if (this.captured) {
      const event = Object.values(SEARCH.event).find(
        (item) =>
          item.table === table &&
          String(item.fields.id) === id &&
          sameTime(String(item.fields.time), time),
      );
      if (event) return [200, copy(event)];
    }
    for (const search of this.searches.values()) {
      const row = search.rows.find(
        (item) => item.table === table && item.key.endsWith(`:${id ?? ''}`),
      );
      if (!row) continue;
      const event: SearchEvent = {
        table: row.table,
        kind: row.kind,
        fields: {
          id: Number(id),
          time: row.time,
          time_received: row.time_received,
          source: row.source,
          sender: row.sender,
          ...Object.fromEntries(Object.entries(row.summary).filter(([name]) => name !== 'vlan_id')),
          raw_day: row.raw.day,
          raw_line: row.raw.line,
          raw_offset: row.raw.offset,
        },
        extra: {},
        message: {
          type: row.type ?? row.kind,
          subtype: row.subtype,
          severity: 5,
          facility: 23,
          program: null,
          host: null,
          text: row.text ?? '',
          parse_error: null,
        },
        raw: row.raw,
      };
      return [200, event];
    }
    return this.error(404, 'search_event_not_found');
  }

  private refs(call: RecordedCall): Answer {
    const account = this.person(call, ['evidence.create']);
    if (Array.isArray(account)) return account;
    const search = this.searchOf(call, 'refs', account);
    if (Array.isArray(search)) return search;
    const seen = new Set<string>();
    const refs = search.rows
      .map((row) => row.raw)
      .filter((ref) => {
        const key = `${ref.source}|${ref.day}|${ref.line}`;
        return !seen.has(key) && seen.add(key);
      });
    const answer: SearchRefs = {
      refs,
      truncated: false,
      complete: search.scenario !== 'partial',
      unsealed_days: this.unsealedDays,
      sha256: createHash('sha256').update(JSON.stringify(refs)).digest('hex'),
    };
    return [200, answer];
  }

  private export(call: RecordedCall): Answer {
    const account = this.person(call, ['search.export']);
    if (Array.isArray(account)) return account;
    const search = this.searchOf(call, 'export', account);
    if (Array.isArray(search)) return search;
    const format = (call.body as { format: string }).format;
    const lines = ['Zaman;Alınma zamanı;Kaynak;Tür;Kaynak IP;Hedef IP;Eylem;Ham kayıt'];
    for (const row of this.exportTruncated ? search.rows.slice(0, 1) : search.rows)
      lines.push(
        [
          row.time,
          row.time_received,
          row.source,
          row.kind,
          row.summary.srcip ?? '',
          row.summary.dstip ?? '',
          row.summary.action ?? '',
          `${row.raw.source}/${row.raw.day}/${row.raw.line}/${row.raw.offset}`,
        ].join(';'),
      );
    const body = Buffer.from(`${BOM}${lines.join('\r\n')}\r\n`, 'utf8');
    const stamp = (moment: string) => {
      const local = new Date(Date.parse(moment) + 3 * 3_600_000).toISOString();
      return `${local.slice(0, 10).replace(/-/g, '')}T${local.slice(11, 16).replace(':', '')}`;
    };
    const name = `arama-${stamp(search.from)}-${stamp(search.to)}${this.exportTruncated ? '-kismi' : ''}.${format}`;
    return [
      200,
      null,
      {
        body,
        contentType:
          format === 'csv'
            ? 'text/csv; charset=utf-8'
            : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: {
          'Content-Disposition': `attachment; filename="${name}"`,
          'Repr-Digest': `sha-256=:${createHash('sha256').update(body).digest('base64')}:`,
          'Cache-Control': 'no-store',
          ...(this.exportTruncated ? { 'X-Arci-Export-Truncated': this.exportTruncated } : {}),
        },
      },
    ];
  }

  // -- saved searches -----------------------------------------------------------------------------

  private saveSearch(call: RecordedCall, id: number | null): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const body = call.body as SavedSearchIn;
    const existing = id === null ? null : this.saved.get(id);
    if (id !== null && (!existing || existing.owner !== account.id))
      return this.error(404, 'saved_search_not_found');
    const taken = [...this.saved.values()].some(
      (item) =>
        item.owner === account.id &&
        item.id !== id &&
        item.name.toLocaleLowerCase('tr') === body.name.toLocaleLowerCase('tr'),
    );
    if (taken) return this.error(409, 'saved_search_name_taken');
    const now = new Date().toISOString();
    const item = {
      id: existing?.id ?? this.nextSaved++,
      owner: account.id,
      name: body.name,
      q: body.q,
      range: body.range,
      created_at: existing?.created_at ?? now,
      updated_at: now,
      last_run_at: existing?.last_run_at ?? null,
    };
    this.saved.set(item.id, item);
    return [existing ? 200 : 201, shown(item)];
  }

  private deleteSaved(call: RecordedCall, id: number): Answer {
    const account = this.person(call);
    if (Array.isArray(account)) return account;
    const item = this.saved.get(id);
    if (!item || item.owner !== account.id) return this.error(404, 'saved_search_not_found');
    this.saved.delete(id);
    return [204, null];
  }
}

/** Install the search mock on top of `api` (call after `mockApi`). */
export async function mockSearch(
  page: Page,
  api: MockApi,
  options: SearchOptions = {},
): Promise<SearchMock> {
  const search = new SearchMock(api, options);
  await search.install(page);
  return search;
}
