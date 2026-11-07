/**
 * Mirror of api/src/arcilogger_api/system/models.py (Wave 4 contract 2.5.1; the Veritabanı page,
 * Wave 5 addendum C, C7).
 */
import type { DateString, DateTimeString, Float } from './api-types';

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
  lag_seconds: Float | null;
  lines_per_second: Float | null;
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
  lines_per_second: Float | null;
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

/** The database's growth a day, fitted like a drive's forecast (`GET /system/database`). */
export interface DatabaseGrowth {
  state: 'estimated' | 'insufficient' | 'not_growing';
  samples: number;
  bytes_per_day: number | null;
}

export interface DatabaseSample {
  day: DateString;
  bytes: number;
}

/** The Backup task's last attempt (`state/backup/last.json`); file name and size after success. */
export interface LastBackup {
  finished: DateTimeString | null;
  ok: boolean;
  file: string | null;
  bytes: number | null;
}

/** `GET /system/database`: the Veritabanı page. */
export interface DatabaseDetails {
  checked_at: DateTimeString;
  storage: 'postgresql' | 'timescaledb';
  server_version: string | null;
  timescaledb_version: string | null;
  name: string | null;
  host: string;
  port: number;
  total_bytes: number | null;
  measured_at: DateTimeString | null;
  tables: TableSize[];
  connections: number | null;
  max_connections: number | null;
  /** Days of logs the database keeps (`db.retention_days`); older days are archived. */
  window_days: number | null;
  growth: DatabaseGrowth;
  history: DatabaseSample[];
  data_drive: DriveInfo | null;
  last_backup: LastBackup | null;
}

/** A dump of the backup folder (`GET /system/backups`, `state/backup/list.json`). */
export interface BackupDump {
  name: string;
  taken: DateTimeString | null;
  bytes: number;
  sha256: string | null;
}

/** The last copy to the backup copy's target (`state/backup/mirror.json`). */
export interface BackupMirrorState {
  finished: DateTimeString | null;
  ok: boolean;
  copied: number;
  present: number;
  pruned: number;
  prune_refused: number;
  failed: Record<string, string>;
  error: string | null;
}

/** `GET /system/backups`: Ayarlar → Yedekleme's list (Wave 5 addendum C, C5-C6). */
export interface BackupsInfo {
  checked_at: DateTimeString;
  enabled: boolean;
  folder: string;
  list_updated: DateTimeString | null;
  dumps: BackupDump[];
  mirror_target: string | null;
  mirror: BackupMirrorState | null;
}
