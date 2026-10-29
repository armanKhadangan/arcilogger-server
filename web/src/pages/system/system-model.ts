/**
 * What the Sistem page (Wave 4 contract 2.8.2) and the Panel's disk card (2.8.3) show, as plain
 * functions and maps (no components here; unit-tested in `system-model.test.ts`): bytes and
 * percentages in the interface language (`lib/format.ts`), the disk forecast's sentence, and every
 * server state as a badge of tone, icon and text (status is never shown by colour alone).
 */

import type { TFunction } from 'i18next';
import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Clock,
  EyeOff,
  FileQuestion,
  Hourglass,
  SignalZero,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import type { BadgeProps } from '@/components/ui/badge';
import type { LicenseMaintenanceOut } from '@/lib/api-types';
import { formatDay, siteToday } from '@/lib/display';
import { formatBytes, formatNumber, type Lang } from '@/lib/format';
import type {
  AgentHealth,
  ApplySystemHealth,
  ClockSampleOut,
  JobHealth,
  TaskHealth,
} from '@/lib/system-health-types';
import type { DriveInfo, HostInfo, StatusFileState } from '@/lib/system-types';

type Tone = NonNullable<BadgeProps['tone']>;

/** The blocks of the page, in the order of contract 2.8.2. */
export const SYSTEM_BLOCKS = [
  'resources',
  'disks',
  'database',
  'collector',
  'ingest',
  'services',
  'tasks',
  'jobs',
  'clock',
  'version',
] as const;
export type SystemBlock = (typeof SYSTEM_BLOCKS)[number];

/** A state as the page shows it: badge tone, icon and the key of its text. */
export interface StateStyle {
  tone: Tone;
  icon: LucideIcon;
  label: string;
}

/** `%54` (tr) or `54%` (en): the Turkish sign comes first, as in the forecast sentence. */
export function formatPercent(value: number, lang: Lang, decimals = 0): string {
  const number = formatNumber(value, decimals, lang);
  return lang === 'tr' ? `%${number}` : `${number}%`;
}

/** `9 GB / 16 GB` and the share of the total, or `null` when the host's memory is not read. */
export function memoryText(host: HostInfo, lang: Lang): string | null {
  const { memory_total_bytes: total, memory_used_bytes: used } = host;
  if (total === null || used === null || total <= 0) return null;
  return `${formatBytes(used, lang)} / ${formatBytes(total, lang)} (${formatPercent(
    Math.round((used * 100) / total),
    lang,
  )})`;
}

/**
 * A null CPU percentage: the API's first request has no earlier reading to compare with
 * ("ölçülüyor", the next refresh has it); on a platform the server cannot read at all (`other`)
 * it stays null ("bu platformda okunamıyor").
 */
export function cpuPending(host: HostInfo): 'measuring' | 'unreadable' {
  return host.platform === 'other' ? 'unreadable' : 'measuring';
}

// -- drives ------------------------------------------------------------------------------------

export type DriveLevel = 'ok' | 'warning' | 'critical';

/**
 * A drive's level by the server's marks, as the disk alert judges it
 * (`worker/src/arcilogger_worker/alerts/system.py` `check_disk`): nothing up to `warning_percent`;
 * above it a warning, critical from `critical_percent` on. A warning mark set above the critical
 * one (`alerts.disk_used_percent` takes up to 99) raises nothing below it, critical or not.
 */
export function driveLevel(drive: DriveInfo): DriveLevel {
  if (drive.used_percent <= drive.warning_percent) return 'ok';
  return drive.used_percent >= drive.critical_percent ? 'critical' : 'warning';
}

export const DRIVE_STYLE: Record<DriveLevel, StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.drive.ok' },
  warning: { tone: 'warning', icon: TriangleAlert, label: 'system.state.drive.warning' },
  critical: { tone: 'danger', icon: CircleX, label: 'system.state.drive.critical' },
};

/**
 * Whole days from `from` (a site day, `YYYY-MM-DD`) to `day`; never negative. Both are calendar
 * days of the site, so the difference is counted in UTC, where no day is 23 or 25 hours long.
 */
export function daysBetween(from: string, day: string): number {
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${day}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 86_400_000));
}

/**
 * The forecast's sentence (contract 2.8.2) in the language of `t`. The "~N gün" counts from the
 * site day of `checkedAt` — the moment the server measured the drives — to `full_on`, so the text
 * matches the figures beside it and does not move with the browser's clock or time zone.
 */
export function forecastText(
  drive: DriveInfo,
  checkedAt: string,
  t: TFunction,
  lang: Lang,
): string {
  const forecast = drive.forecast;
  switch (forecast.state) {
    case 'insufficient':
      return t('system.forecast.insufficient', {
        samples: formatNumber(forecast.samples, 0, lang),
      });
    case 'not_growing':
      return t('system.forecast.notGrowing');
    case 'estimated': {
      // A drive that grows so slowly that its full day lies beyond the server's horizon (about
      // 100 years, W4-B deviation `forecast.HORIZON_DAYS`) has no day; its growth may round to 0.
      if (forecast.full_on === null) return t('system.forecast.noDate');
      const values = {
        count: daysBetween(siteToday(new Date(checkedAt)), forecast.full_on),
        warning: formatNumber(drive.warning_percent, 0, lang),
        fullOn: formatDay(forecast.full_on),
      };
      // With a full day, `warning_on` is null only when a measurement has reached the mark (at or
      // above it, W4-B): only the day it is full. "Reached", not "passed": at exactly the mark
      // the badge still says "Olağan" (`driveLevel`).
      return forecast.warning_on === null
        ? t('system.forecast.estimatedFull', values)
        : t('system.forecast.estimated', { ...values, warningOn: formatDay(forecast.warning_on) });
    }
  }
}

// -- service, agent, ApplySystem, task and job states ------------------------------------------

/** The collector's and the ingest's status files, and the services judged by them. */
export const STATUS_FILE_STYLE: Record<StatusFileState, StateStyle> = {
  running: { tone: 'success', icon: CircleCheck, label: 'system.state.file.running' },
  stale: { tone: 'warning', icon: Clock, label: 'system.state.file.stale' },
  missing: { tone: 'danger', icon: CircleX, label: 'system.state.file.missing' },
};

export const AGENT_STYLE: Record<AgentHealth['state'], StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.agent.ok' },
  stale: { tone: 'warning', icon: Clock, label: 'system.state.agent.stale' },
  missing: { tone: 'danger', icon: CircleX, label: 'system.state.agent.missing' },
  disabled: { tone: 'neutral', icon: CircleMinus, label: 'system.state.agent.disabled' },
};

export const APPLY_SYSTEM_STYLE: Record<ApplySystemHealth['state'], StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.apply.ok' },
  failed: { tone: 'danger', icon: CircleX, label: 'system.state.apply.failed' },
  never: { tone: 'neutral', icon: CircleDashed, label: 'system.state.apply.never' },
  disabled: { tone: 'neutral', icon: CircleMinus, label: 'system.state.apply.disabled' },
};

/** A scheduled task judged by its evidence file (contract 2.6.2). */
export const TASK_STYLE: Record<TaskHealth['state'], StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.task.ok' },
  late: { tone: 'warning', icon: Clock, label: 'system.state.task.late' },
  missing: { tone: 'warning', icon: FileQuestion, label: 'system.state.task.missing' },
  not_used: { tone: 'neutral', icon: CircleMinus, label: 'system.state.task.not_used' },
  not_observable: { tone: 'neutral', icon: EyeOff, label: 'system.state.task.not_observable' },
};

export const JOB_STYLE: Record<JobHealth['state'], StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.job.ok' },
  failing: { tone: 'danger', icon: CircleX, label: 'system.state.job.failing' },
  skipped: { tone: 'neutral', icon: CircleMinus, label: 'system.state.job.skipped' },
  running: { tone: 'info', icon: Hourglass, label: 'system.state.job.running' },
  never: { tone: 'neutral', icon: CircleDashed, label: 'system.state.job.never' },
};

export type SampleState = 'ok' | 'over' | 'noAnswer';

/**
 * A clock sample against the alert limit: as the Uyum Durumu clock card (owner question Q9), an
 * offset equal to the limit is already over it.
 */
export function sampleState(sample: ClockSampleOut, limitMs: number): SampleState {
  if (!sample.ok || sample.offset_ms === null) return 'noAnswer';
  return Math.abs(sample.offset_ms) < limitMs ? 'ok' : 'over';
}

/** Both are "Sorun" on the Uyum Durumu clock card (contract 2.3.3, card 6). */
export const SAMPLE_STYLE: Record<SampleState, StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.sample.ok' },
  over: { tone: 'danger', icon: CircleX, label: 'system.state.sample.over' },
  noAnswer: { tone: 'danger', icon: SignalZero, label: 'system.state.sample.noAnswer' },
};

/** The ingest's database connection: `null` when its status file does not say. */
export function databaseState(ok: boolean | null): 'ok' | 'failed' | 'unknown' {
  return ok === null ? 'unknown' : ok ? 'ok' : 'failed';
}

export const DATABASE_STYLE: Record<ReturnType<typeof databaseState>, StateStyle> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'system.state.database.ok' },
  failed: { tone: 'danger', icon: CircleX, label: 'system.state.database.failed' },
  unknown: { tone: 'neutral', icon: CircleDashed, label: 'system.state.database.unknown' },
};

export type MaintenanceState = 'valid' | 'endsSoon' | 'expired' | 'none';

/** Licence maintenance as `GET /license` reports it (`null`: no licence installed, the trial). */
export function maintenanceState(maintenance: LicenseMaintenanceOut | null): MaintenanceState {
  if (!maintenance) return 'none';
  if (maintenance.expired) return 'expired';
  return maintenance.ends_soon ? 'endsSoon' : 'valid';
}

export const MAINTENANCE_STYLE: Record<MaintenanceState, StateStyle> = {
  valid: { tone: 'success', icon: CircleCheck, label: 'system.state.maintenance.valid' },
  endsSoon: { tone: 'warning', icon: Clock, label: 'system.state.maintenance.endsSoon' },
  expired: { tone: 'warning', icon: TriangleAlert, label: 'system.state.maintenance.expired' },
  none: { tone: 'neutral', icon: CircleDashed, label: 'system.state.maintenance.none' },
};

/** A task's or a job's schedule as the server words it, in the interface language. */
export function scheduleText(
  item: { schedule_tr: string; schedule_en: string },
  lang: Lang,
): string {
  return lang === 'en' ? item.schedule_en : item.schedule_tr;
}
