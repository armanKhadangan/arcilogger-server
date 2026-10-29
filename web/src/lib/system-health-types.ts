/** Mirror of api/src/arcilogger_api/system_health/models.py (Wave 4 contract 2.6.1). */
import type { DateTimeString, Float } from './api-types';
import type { ServiceName } from './settings-types';

export type TaskName =
  | 'Seal'
  | 'VerifyDaily'
  | 'VerifyWeekly'
  | 'VerifyMonthly'
  | 'Replica'
  | 'Retention'
  | 'Credit'
  | 'ClockCheck'
  | 'Backup'
  | 'Requests'
  | 'ApplySystem';

export interface ServiceHealth {
  name: ServiceName;
  state: 'running' | 'stale' | 'missing';
  updated_at: DateTimeString | null;
}

export interface AgentHealth {
  state: 'ok' | 'stale' | 'missing' | 'disabled';
  heartbeat_at: DateTimeString | null;
}

export interface ApplySystemHealth {
  state: 'ok' | 'failed' | 'never' | 'disabled';
  at: DateTimeString | null;
  code: string | null;
}

export interface TaskHealth {
  task: TaskName;
  schedule_tr: string;
  schedule_en: string;
  state: 'ok' | 'late' | 'missing' | 'not_used' | 'not_observable';
  last_evidence_at: DateTimeString | null;
  evidence: string;
}

export interface JobHealth {
  job: string;
  schedule_tr: string;
  schedule_en: string;
  module: string | null;
  state: 'ok' | 'failing' | 'skipped' | 'running' | 'never';
  last_status: string | null;
  last_started: DateTimeString | null;
  last_finished: DateTimeString | null;
  duration_ms: number | null;
  failures: number;
  next_due: DateTimeString | null;
}

export interface ClockSampleOut {
  time: DateTimeString;
  server: string | null;
  ok: boolean;
  offset_ms: Float | null;
  delay_ms: Float | null;
  stratum: number | null;
}

export interface TimeHealth {
  ntp_server: string | null;
  fallback_server: string | null;
  alert_offset_ms: number;
  applied_peers: string | null;
  applied_at: DateTimeString | null;
  samples: ClockSampleOut[];
}

export interface SystemHealth {
  checked_at: DateTimeString;
  services: ServiceHealth[];
  agent: AgentHealth;
  apply_system: ApplySystemHealth;
  tasks: TaskHealth[];
  jobs: JobHealth[];
  time: TimeHealth;
}
