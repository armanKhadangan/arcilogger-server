/** Mirror of api/src/arcilogger_api/activity/models.py (Wave 5 contract §6.2, M2 and N6). */
import type { DateTimeString, SubjectRef } from './api-types';

export type ActivityWindow = 1 | 5 | 15; // minutes; default 5
export type WanRange = '60m' | '24h';
export type WanStatus =
  'ok' | 'timeout' | 'auth_failed' | 'no_such_interface' | 'snmp_no_hc_counters' | 'disabled';
export type OperStatus =
  'up' | 'down' | 'testing' | 'unknown' | 'dormant' | 'not_present' | 'lower_layer_down'; // ifOperStatus (RFC 2863), by name

export interface ActivityDomain {
  domain: string; // as in /usage: registrable domain, FortiGuard service or address
  kind: 'domain' | 'service' | 'ip';
  bytes: number; // download + upload in the window
}
export interface ActivityApp {
  app: string;
  bytes: number; // download + upload in the window
}
export interface ActivityRow {
  subject: SubjectRef;
  download_bytes: number;
  upload_bytes: number;
  download_bps: number; // bytes * 8 / window seconds
  upload_bps: number;
  sessions: number; // traffic log rows in the window
  top_domains: ActivityDomain[]; // ≤ 3 by bytes, background excluded
  top_apps: ActivityApp[]; // ≤ 3 by bytes
  last_seen: string; // newest traffic row of the subject
}
export interface ActivityTotals {
  download_bytes: number; // every subject, also beyond the 200 rows
  upload_bytes: number;
  download_bps: number;
  upload_bps: number;
  sessions: number;
  subjects: number; // subjects with traffic in the window
}
export interface ActivityMinute {
  minute: string; // the minute's start
  download_bytes: number;
  upload_bytes: number;
}
export interface ActivityDevices {
  window: ActivityWindow;
  rows: ActivityRow[]; // sorted by download, at most 200
  more: boolean;
  totals: ActivityTotals;
  per_minute: ActivityMinute[]; // the last 60 minutes, oldest first
  data_until: string | null; // newest traffic row
  generated_at: string;
}

export interface WanCapacity {
  down: number; // Mbit/s, as configured
  up: number;
}
export interface WanPoint {
  time: DateTimeString;
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
  updated_at: DateTimeString | null; // newest good sample
  last_error_at: DateTimeString | null;
}
export interface WanRates {
  range: WanRange;
  lines: WanLine[];
  generated_at: DateTimeString;
}
