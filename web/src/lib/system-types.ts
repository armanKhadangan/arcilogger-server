/** Mirror of api/src/arcilogger_api/system/models.py (Wave 4 contract 2.5.1). */
import type { DateString, DateTimeString } from './api-types';

export type DriveRole = 'data' | 'system';
export type StatusFileState = 'running' | 'stale' | 'missing';

export interface HostInfo {
  platform: 'windows' | 'linux' | 'other';
  cpu_percent: number | null;
  memory_total_bytes: number | null;
  memory_used_bytes: number | null;
  uptime_seconds: number | null;
}

export interface DiskForecast {
  state: 'estimated' | 'insufficient' | 'not_growing';
  samples: number;
  growth_bytes_per_day: number | null;
  warning_on: DateString | null;
  full_on: DateString | null;
}

export interface DriveInfo {
  role: DriveRole;
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
  warning_percent: number;
  critical_percent: number;
  forecast: DiskForecast;
}

export interface TableSize {
  name: string;
  bytes: number;
}

export interface DatabaseInfo {
  storage: 'postgresql' | 'timescaledb';
  total_bytes: number | null;
  tables: TableSize[];
  measured_at: DateTimeString | null;
}

export interface CollectorSource {
  name: string;
  messages: number;
  bytes: number;
  last_received: DateTimeString | null;
}

/** Status-file entries that are not a configured source (2.5.2), never listed by name. */
export interface UnknownSenders {
  count: number;
  messages: number;
  bytes: number;
}

export interface CollectorInfo {
  state: StatusFileState;
  updated_at: DateTimeString | null;
  started_at: DateTimeString | null;
  sources: CollectorSource[];
  unknown_senders: UnknownSenders;
  dropped_unknown: number;
  dropped_transport: number;
  write_failures: number;
  write_error_since: DateTimeString | null;
  torn_lines: number;
  tcp_connections: number;
  tcp_framing_errors: number;
}

export interface IngestSource {
  name: string;
  lag_bytes: number;
  lag_seconds: number | null;
  lines_per_second: number | null;
  unfinished_days: number;
  has_error: boolean;
}

export interface IngestInfo {
  state: StatusFileState;
  service_state: string | null;
  updated_at: DateTimeString | null;
  database_ok: boolean | null;
  workers_configured: number | null;
  workers_active: number | null;
  lines_per_second: number | null;
  sources: IngestSource[];
  unknown_sources: number;
}

export interface VersionInfo {
  product: string;
  version: string;
  core_version: string;
  release_date: DateString;
}

export interface SystemSummary {
  checked_at: DateTimeString;
  host: HostInfo;
  drives: DriveInfo[];
  database: DatabaseInfo;
  collector: CollectorInfo;
  ingest: IngestInfo;
  version: VersionInfo;
}

export interface SystemDisk {
  checked_at: DateTimeString;
  drives: DriveInfo[];
}
