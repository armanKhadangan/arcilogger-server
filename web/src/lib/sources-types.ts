/**
 * The sources' state as the main menu's Kaynaklar page reads it (`/sources`,
 * docs/MVP-CONTRACTS.md section 17): overview, detail, the last raw lines.
 *
 * Every interface here is checked against the OpenAPI document (`api-types.test.ts`, which lists
 * this file in `TYPES_FILES`), so a renamed, retyped or removed field fails a test.
 *
 * Kaynaklar only reads: the sources are configured in Ayarlar → Kaynaklar
 * (`/admin/config/sources`). The overview and the detail hold counts only (not audited); the last
 * lines are raw lines (`raw.tail`, audited with the exact byte range).
 */

import type { DateString, DateTimeString, Float } from './api-types';
import type { RawRef } from './live-types';

/**
 * `active` (newest message within the heartbeat), `silent` (older), `never`, `disabled`,
 * `unknown` (an `unknown-<ip>` sender not in the configuration), `promoted` (an `unknown-<ip>`
 * whose address is configured now: its files stay under the old name).
 */
export type SourceState = 'active' | 'silent' | 'never' | 'disabled' | 'unknown' | 'promoted';

export interface CollectorState {
  /** When the collector last wrote `state/collector-status.json`. */
  updated_at: DateTimeString | null;
  /** Within 5 minutes: its receive times count for the state. */
  fresh: boolean;
}

/** What "Kaynak olarak tanımla" fills in for an unknown sender. */
export interface Suggested {
  name: string;
  type: 'fortigate' | 'mikrotik' | 'generic';
}

export interface SourceHealth {
  name: string;
  /** `fortigate`, `mikrotik`, `generic`. */
  type: string;
  address: string | null;
  configured: boolean;
  /** A member `<name>-<ip>` of the configured network `name`. */
  network_of: string | null;
  unknown: boolean;
  enabled: boolean;
  expected: boolean;
  heartbeat_minutes: number;
  first_seen: DateTimeString | null;
  last_message: DateTimeString | null;
  last_message_origin: 'collector' | 'database' | null;
  state: SourceState;
  silent_minutes: number | null;
  messages_per_minute: Float;
  /** Messages a minute (device time) of the 60 whole minutes before now, oldest first. */
  sparkline: number[];
  lines_today: number;
  /** Bytes of today's spool file ingest has not loaded yet (the ingest lag). */
  pending_bytes: number;
  parse_failures_today: number;
  parse_failure_permille: Float | null;
  /** A promoted unknown sender: the source it is written under now. */
  promoted_to: string | null;
  suggested: Suggested | null;
}

/** `GET /sources` (`sources.read`; cached 15 s on the server, refreshed every 30 s here). */
export interface SourcesOverview {
  generated_at: DateTimeString;
  today: DateString;
  collector: CollectorState;
  items: SourceHealth[];
}

export interface HourCount {
  hour: DateTimeString;
  count: number;
}

export interface DayHealth {
  day: DateString;
  lines: number;
  failures: number;
  sealed: boolean;
}

export interface SourceGap {
  from: DateTimeString;
  to: DateTimeString;
  minutes: number;
  reason: string;
  /** `manifest`: recorded by the sealer; `live`: today's, since the last message. */
  origin: 'manifest' | 'live';
}

export interface SourceParseFailure {
  day: DateString;
  pattern: string;
  count: number;
  first_seen: DateTimeString;
  last_seen: DateTimeString;
  /** The parser's detail; `null` without `search.read` or `lookup.run`. */
  error: string | null;
  example: RawRef;
}

/** `GET /sources/{name}` (`sources.read`; cached 30 s per source on the server). */
export interface SourceDetail extends SourceHealth {
  /** Lines per hour of the last 48 hours, oldest first. */
  hourly: HourCount[];
  /** The last 14 days, today first. */
  daily: DayHealth[];
  /** The last 30 days, newest first. */
  gaps: SourceGap[];
  /** The 50 most frequent patterns of the last 7 days. */
  parse_failures: SourceParseFailure[];
}

export interface TailLine {
  line: number;
  offset: number;
  received: DateTimeString | null;
  sender: string | null;
  /** The message, display-safe (the spool's escapes visible). */
  text: string;
  /** SHA-256 of the line as stored (without its line feed), as `GET /raw` gives it. */
  sha256: string;
}

/**
 * `GET /sources/{name}/tail` ("Son satırlar"; `sources.read` and `search.read` or `lookup.run`,
 * KVKK notice; audited `raw.tail`): today's spool file, else yesterday's.
 */
export interface SourceTail {
  source: string;
  day: DateString | null;
  /** `spool` (the vault is never decompressed for it); `null` without a file. */
  origin: string | null;
  from_line: number | null;
  to_line: number | null;
  from_offset: number | null;
  /** Offset after the last line's line feed. */
  to_offset: number | null;
  /** SHA-256 of the file's bytes `[from_offset, to_offset)`. */
  range_sha256: string | null;
  lines: TailLine[];
  /**
   * `no_file_today`: neither today's nor yesterday's spool file exists; `network_source`: a
   * configured network, whose lines are in its members' folders (`<name>-<ip>`).
   */
  reason?: string | null;
}
