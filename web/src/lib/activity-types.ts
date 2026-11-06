/** Mirror of api/src/arcilogger_api/activity/models.py (Wave 5 contract §6.2, M2 and N6). */
import type { DateTimeString, Float, SubjectRef } from './api-types';

export type ActivityWindow = 1 | 5 | 15; // minutes; default 5
export type WanRange = '60m' | '24h';
export type WanStatus =
  'ok' | 'timeout' | 'auth_failed' | 'no_such_interface' | 'snmp_no_hc_counters' | 'disabled';
export type OperStatus =
  'up' | 'down' | 'testing' | 'unknown' | 'dormant' | 'not_present' | 'lower_layer_down'; // ifOperStatus (RFC 2863), by name

export interface ActivityDomain {
  /** As in /usage: a registrable domain, a FortiGuard service name or an address. */
  domain: string;
  kind: 'domain' | 'service' | 'ip';
  /** Download and upload in the window. */
  bytes: number;
}
export interface ActivityApp {
  app: string;
  /** Download and upload in the window. */
  bytes: number;
}
export interface ActivityRow {
  subject: SubjectRef;
  download_bytes: number;
  upload_bytes: number;
  /** Bytes × 8 / the window's seconds. */
  download_bps: Float;
  upload_bps: Float;
  /** Sessions started in the window, each counted once. */
  sessions: number;
  /** At most 3, by bytes; background traffic excluded. */
  top_domains: ActivityDomain[];
  /** At most 3, by bytes. */
  top_apps: ActivityApp[];
  /** The end of the subject's newest minute with traffic (at most `data_until`). */
  last_seen: DateTimeString;
}
export interface ActivityTotals {
  /** Every subject, also those beyond the 200 rows (and persons left out of the rows). */
  download_bytes: number;
  upload_bytes: number;
  download_bps: Float;
  upload_bps: Float;
  sessions: number;
  /** Subjects with traffic in the window. */
  subjects: number;
}
export interface ActivityMinute {
  /** The minute's start. */
  minute: DateTimeString;
  download_bytes: number;
  upload_bytes: number;
}
export interface ActivityDevices {
  /** Minutes: 1, 5 or 15 (`ActivityWindow`). */
  window: number;
  /** Sorted by download, at most 200. */
  rows: ActivityRow[];
  /** More subjects had traffic than `rows` holds. */
  more: boolean;
  totals: ActivityTotals;
  /** Each of the last 60 minutes, oldest first. */
  per_minute: ActivityMinute[];
  /** The newest traffic row (null with none). */
  data_until: DateTimeString | null;
  /**
   * The worker's snapshot does not cover the last 60 minutes yet (after a start, or none
   * written), or it is more than 2 minutes old (the worker stopped): the figures are partial.
   */
  warming_up: boolean;
  generated_at: DateTimeString;
}

export interface WanCapacity {
  down: number; // Mbit/s, as configured
  up: number;
}
export interface WanPoint {
  time: string;
  download_bps: number;
  upload_bps: number;
}
export interface WanLine {
  source: string; // the [[sources]] name the line is polled on
  name: string; // ifName
  oper_status: OperStatus | null;
  download_bps: number | null; // newest rate; null before the first good sample
  upload_bps: number | null;
  capacity_mbps: WanCapacity | null;
  series: WanPoint[]; // 60m: full resolution; 24h: 1-minute averages; oldest first
  status: WanStatus;
  updated_at: string | null; // newest good sample
  last_error_at: string | null;
}
export interface WanRates {
  range: WanRange;
  lines: WanLine[];
  generated_at: string;
}
