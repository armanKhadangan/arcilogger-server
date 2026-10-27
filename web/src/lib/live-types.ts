/**
 * The live tail's requests, answers and stream events (`/live`, docs/MVP-CONTRACTS.md
 * section 17).
 *
 * Every interface here is checked against the OpenAPI document (`api-types.test.ts`, which lists
 * this file in `TYPES_FILES`), so a renamed, retyped or removed field fails a test.
 *
 * A stream opens in two steps: `POST /live/streams` (refusals are plain JSON errors; audited
 * `live.open`) answers a `stream_id`, then `GET /live/streams/{id}` sends server-sent events, one
 * JSON object per `data:` line, whose shapes are {@link LiveEvents}. The query travels in the
 * POST body only; the stream's address holds the random id.
 */

import type { DateString, DateTimeString, Float } from './api-types';

// -- shared with Arama ----------------------------------------------------------------------------
// A live line, a Kaynaklar parse failure and the Panel's parse-failure box carry the same objects
// as a search row. `search-types.ts` (Arama) declares them too; once both are on one branch these
// two give way to `export type { RawRef, SearchSummary } from './search-types'`.

/** Line `line` of `spool/<source>/<day>.log` (or the sealed file), starting at byte `offset`. */
export interface RawRef {
  source: string;
  day: DateString;
  line: number;
  offset: number;
}

/** The typed fields of a line, for its summary (only those the line has). */
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

// -- requests and answers -------------------------------------------------------------------------

/**
 * `POST /live/streams`: a query of the search language (empty: every line; no time range) and how
 * many matching lines from before the stream opened to show first (absent: `live.backfill_lines`).
 */
export interface LiveStreamIn {
  q?: string;
  backfill?: number | null;
}

/** A spool folder the stream follows: lines after `start_line` of its `day` are new. */
export interface LiveFolder {
  folder: string;
  day: DateString;
  start_line: number;
}

export interface LiveLimits {
  max_lines_per_second: number;
  keepalive_seconds: number;
  buffer_lines: number;
  max_minutes: number;
}

export interface LiveStreamOut {
  stream_id: string;
  /** Changes when the API restarts; `Last-Event-ID` is `<epoch>:<seq>`. */
  epoch: string;
  /** When the stream ends with `time_limit` unless it is closed before. */
  expires_at: DateTimeString;
  folders: LiveFolder[];
  limits: LiveLimits;
}

export interface LiveHubStatus {
  running: boolean;
  /** Spool files followed. */
  files: number;
  lines_per_second: Float;
  parsed_per_second: Float;
  /** Lines not parsed (parse budget) since the hub started. */
  skipped_total: number;
}

/** `GET /live/status` (support; no person data). */
export interface LiveStatus {
  streams: number;
  max_streams: number;
  hub: LiveHubStatus;
}

// -- stream events --------------------------------------------------------------------------------

/** `event: hello` — the first event of every connection. */
export interface LiveHello {
  stream_id: string;
  epoch: string;
  /** The KVKK notice version the viewer accepted (a newer one ends the stream). */
  notice_version: string;
  folders: LiveFolder[];
  limits: LiveLimits;
}

/**
 * `event: line` (`id: <epoch>:<seq>`) — one raw line of the spool. The parsed fields are a
 * preview ("önizleme": ingest's row may differ in details); `parsed: false` with
 * `parse_skipped: true`: the server had no parse budget left; `parse_error: "rawlog.invalid"`:
 * not in the collector's format ("biçim dışı"). The whole line: `GET /raw` with its reference.
 */
export interface LiveLine {
  seq: number;
  received: DateTimeString | null;
  /** The spool folder (the source name ingest gives the line). */
  source: string;
  sender: string | null;
  day: DateString;
  line: number;
  offset: number;
  kind: string | null;
  type: string | null;
  subtype: string | null;
  summary: SearchSummary;
  /** The message, display-safe (escapes visible), at most 8 KiB. */
  text: string;
  truncated: boolean;
  parse_error: string | null;
  parsed: boolean;
  parse_skipped: boolean;
}

/** `event: dropped` (at most once a second) — matching lines not shown since `since`. */
export interface LiveDropped {
  rate_limited: number;
  slow_client: number;
  parse_budget: number;
  behind: number;
  since: DateTimeString;
  message_tr: string;
  message_en: string;
}

export type LiveGapReason = 'resume_too_late' | 'hub_restarted' | 'file_replaced';

/** `event: gap` — lines in between may be missing (open them in Arama). */
export interface LiveGap {
  reason: LiveGapReason;
  /** Matching lines known to be missing; `null` when the server cannot tell. */
  missed: number | null;
  message_tr: string;
  message_en: string;
}

/** `event: end` — the stream is closed; the page does not reconnect. */
export interface LiveEnd {
  code: string;
  message_tr: string;
  message_en: string;
}

/** The `data:` of each event of `GET /live/streams/{id}` (`text/event-stream`). */
export interface LiveEvents {
  hello: LiveHello;
  line: LiveLine;
  dropped: LiveDropped;
  gap: LiveGap;
  end: LiveEnd;
}

export type LiveEventName = keyof LiveEvents;

/** The reasons of a `dropped` event, in the order the page lists them. */
export const DROP_REASONS = ['rate_limited', 'slow_client', 'parse_budget', 'behind'] as const;
export type DropReason = (typeof DROP_REASONS)[number];
