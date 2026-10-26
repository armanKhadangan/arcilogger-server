/**
 * The search API's requests and answers (`/search`, docs/MVP-CONTRACTS.md section 17): search
 * pages, statistics, the field catalogue, saved searches, evidence references.
 *
 * Every interface here is checked against the OpenAPI document (`api-types.test.ts`, which lists
 * this file in `TYPES_FILES`), so a renamed, retyped or removed field fails a test.
 *
 * A search runs once (`POST /search`, audited `search.run`) and answers with a `search_id`; its
 * further pages, statistics, evidence references and export take only that id. The query text
 * travels in request bodies, never in an address.
 */

import type { DateString, DateTimeString } from './api-types';

// -- names ------------------------------------------------------------------------------------------

/** The time range presets (`saved_searches.range_preset`), resolved against the clock. */
export type RangePreset = '15m' | '1h' | '24h' | 'today' | 'yesterday' | '7d' | '30d';

/** `match` (field:value), `present` (field:*), `text` (free text). */
export type ClauseOp = 'match' | 'present' | 'text';

/** Which tables answer: the typed tables (`fg_*`, `mt_*`) or `messages`. */
export type SearchDriver = 'messages' | 'typed';

/** Where the statistics came from: the summary table, partly, or counted directly. */
export type StatsMode = 'rollup' | 'hybrid' | 'direct';

export type FacetStatus = 'ok' | 'sampled' | 'unavailable';

export type ExportFormat = 'csv' | 'xlsx';

// -- requests ---------------------------------------------------------------------------------------

/**
 * `POST /search`: a query and a half-open range `[from, to)` on device time (both with an offset),
 * or `saved_id` alone (the server takes the query and the range from the saved search).
 */
export interface SearchRequest {
  q?: string;
  from?: DateTimeString | null;
  to?: DateTimeString | null;
  /** 1 … `search.page_size_max`; the web app asks for 100 rows a page. */
  limit?: number | null;
  saved_id?: number | null;
}

/** `POST /search/page`: the next page of a search (`next_cursor` of the page before). */
export interface SearchPageRequest {
  search_id: string;
  cursor: string;
  limit?: number | null;
}

/** `POST /search/stats`. */
export interface SearchIdRequest {
  search_id: string;
}

/** `POST /search/parse`: no database work. */
export interface ParseRequest {
  q: string;
}

/** `POST /search/refs`: the raw references of the whole search, at most 10,000. */
export interface SearchRefsRequest {
  search_id: string;
  limit?: number;
}

/** `POST /search/export` (re-authentication within 5 minutes). */
export interface SearchExportRequest {
  search_id: string;
  format: ExportFormat;
}

// -- a search page ----------------------------------------------------------------------------------

/** One clause of the parsed query, with its characters `[start, end)` in the query text. */
export interface SearchClause {
  field: string;
  op: ClauseOp;
  values: string[];
  negated: boolean;
  start: number;
  end: number;
}

export interface SearchQuery {
  /** The canonical text (field names, values and spaces): it names the search that ran. */
  normalized: string;
  clauses: SearchClause[];
  /** The positive free-text terms. */
  text: string[];
}

export interface SearchRange {
  from: DateTimeString;
  to: DateTimeString;
}

export interface SearchPlan {
  driver: SearchDriver;
  tables: string[];
  kinds: string[];
  /** At least one branch is index-backed. */
  selective: boolean;
  rollup_eligible: boolean;
}

/** Line `line` (1-based) at byte `offset` of `spool/<source>/<day>.log`, the sealed file's line. */
export interface RawRef {
  source: string;
  day: DateString;
  line: number;
  offset: number;
}

/** The fields a result row shows in its summary (by its kind; the rest are `null` or absent). */
export interface SearchSummary {
  srcip?: string | null;
  srcport?: number | null;
  dstip?: string | null;
  dstport?: number | null;
  natip?: string | null;
  natport?: number | null;
  action?: string | null;
  domain?: string | null;
  url?: string | null;
  app?: string | null;
  category?: string | null;
  user?: string | null;
  mac?: string | null;
  proto?: string | null;
  policy?: string | null;
  interface?: string | null;
  vlan_id?: number | null;
}

/** One record: a typed row or a `messages` row, with its raw line. */
export interface SearchRow {
  /** `<table>:<time>:<id>`, unique in a search. */
  key: string;
  table: string;
  time: DateTimeString;
  time_received: DateTimeString;
  source: string;
  sender: string;
  kind: string;
  type: string | null;
  subtype: string | null;
  summary: SearchSummary;
  /** `messages.text`, at most 500 characters, display-safe (escapes visible). */
  text: string | null;
  raw: RawRef;
  parse_error: string | null;
}

/** How far the time-budgeted scan got (newest first): `[scanned_from, scanned_to)`. */
export interface SearchScan {
  complete: boolean;
  scanned_from: DateTimeString;
  scanned_to: DateTimeString;
  windows: number;
  elapsed_ms: number;
}

/** A remark on the answer (`vault_only_before`, `text_case`, `query_not_selective`, …). */
export interface Notice {
  code: string;
  message_tr: string;
  message_en: string;
  params: Record<string, unknown>;
}

export interface SearchPage {
  search_id: string;
  expires_at: DateTimeString;
  query: SearchQuery;
  range: SearchRange;
  plan: SearchPlan;
  items: SearchRow[];
  /** More rows (a full page) or, with an incomplete scan, where the scan goes on. */
  next_cursor: string | null;
  scan: SearchScan;
  notices: Notice[];
}

// -- statistics -------------------------------------------------------------------------------------

export interface SearchTotal {
  value: number;
  /** `false`: at least `value` (the count did not cover the whole range). */
  exact: boolean;
}

export interface SearchBucket {
  start: DateTimeString;
  count: number;
}

export interface SearchHistogram {
  bucket_seconds: number;
  buckets: SearchBucket[];
}

export interface FacetValue {
  value: string;
  label_tr: string;
  label_en: string;
  count: number;
}

export interface Facet {
  status: FacetStatus;
  values: FacetValue[];
  /** Rows of the values not listed. */
  other: number;
}

export interface SearchFacets {
  source: Facet;
  kind: Facet;
  action: Facet;
  vlan: Facet;
  /** The VLAN's default department (owner decision D1), not a person's assignment. */
  department: Facet;
}

export interface SearchStats {
  search_id: string;
  mode: StatsMode;
  complete: boolean;
  /** The part of the range the counts cover. */
  covered_from: DateTimeString;
  covered_to: DateTimeString;
  total: SearchTotal;
  histogram: SearchHistogram;
  facets: SearchFacets;
  sampled: boolean;
  sample_rows: number | null;
  notices: Notice[];
  elapsed_ms: number;
}

// -- parsing and the field catalogue ----------------------------------------------------------------

export interface ParseErrorItem {
  code: string;
  message_tr: string;
  message_en: string;
  start: number;
  end: number;
}

export interface ParseResult {
  normalized: string;
  clauses: SearchClause[];
  kinds: string[];
  selective: boolean;
  errors: ParseErrorItem[];
}

export interface CatalogueField {
  name: string;
  aliases: string[];
  /** The value form (`ip`, `port`, `mac`, `domain`, `text`, `kind`, `freetext`, …). */
  kind: string;
  label_tr: string;
  label_en: string;
  help_tr: string;
  help_en: string;
  tables: string[];
  /** The tables where this field alone is index-backed (fast). */
  indexed: string[];
  examples: string[];
}

export interface CatalogueKind {
  key: string;
  label_tr: string;
  label_en: string;
}

export interface CatalogueSource {
  name: string;
  type: string;
  /** A source defined as a network (`ap` also finds its members `ap-<ip>`). */
  network: boolean;
}

export interface CatalogueLimits {
  max_chars: number;
  max_clauses: number;
  max_values: number;
  max_terms: number;
  max_days: number;
  page_size_max: number;
}

export interface FieldCatalogue {
  fields: CatalogueField[];
  kinds: CatalogueKind[];
  /** Configured and seen sources. */
  sources: CatalogueSource[];
  limits: CatalogueLimits;
}

// -- one record, evidence references --------------------------------------------------------------

export interface SearchEventMessage {
  type: string;
  subtype: string | null;
  severity: number | null;
  facility: number | null;
  program: string | null;
  host: string | null;
  text: string;
  parse_error: string | null;
}

/** `GET /search/event` (audited `search.event`): every column of the row. */
export interface SearchEvent {
  table: string;
  kind: string;
  fields: Record<string, unknown>;
  extra: Record<string, unknown>;
  message: SearchEventMessage | null;
  raw: RawRef;
}

/** `POST /search/refs` (audited `search.refs`): the lines for an evidence extract. */
export interface SearchRefs {
  refs: RawRef[];
  /** More than the limit matched: the first `limit` lines are listed. */
  truncated: boolean;
  /** The collection covered the whole range. */
  complete: boolean;
  /** Days of the lines that are not sealed yet (an evidence package refuses them). */
  unsealed_days: DateString[];
  sha256: string;
}

// -- saved searches (personal) --------------------------------------------------------------------

export interface PresetRange {
  preset: RangePreset;
}

export interface FixedRange {
  from: DateTimeString;
  to: DateTimeString;
}

export interface SavedSearchIn {
  name: string;
  q: string;
  range: PresetRange | FixedRange;
}

export interface SavedSearch {
  id: number;
  name: string;
  q: string;
  range: PresetRange | FixedRange;
  created_at: DateTimeString;
  updated_at: DateTimeString;
  last_run_at: DateTimeString | null;
}

export interface SavedSearchList {
  items: SavedSearch[];
}
