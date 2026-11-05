/**
 * Canlı Durum's API (`/activity/devices` W5-M, `/activity/wan` W5-N; contract W5 §6 M2, M3,
 * N6) for the e2e mock. Until those back ends merge and their answers are captured
 * (`capture_api_samples.py`), the answers are built here from the captured people of
 * `usage.json`, with the types of `src/lib/activity-types.ts` and fictional example data only;
 * a captured sample replaces each part when it exists (W5-P-9).
 */

import type {
  ActivityDevices,
  ActivityRow,
  ActivityWindow,
  WanLine,
  WanRange,
  WanRates,
} from '../src/lib/activity-types';
import type { SubjectRef } from '../src/lib/api-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { USAGE } from './samples';

/** What the activity mock needs from the main mock. */
export interface ActivityHost extends MockHost {
  privacyRequired(account: MockUser): boolean;
}

export interface ActivityOptions {
  /** No source has SNMP on: `/activity/wan` answers no lines. */
  noLines?: boolean;
  /** The second line's device does not answer (`timeout`). */
  lineDown?: boolean;
}

/** "Now" of the mock's answers: the sample clock's day at 14:35:20 site time. */
const NOW = Date.parse('2026-10-13T11:35:20Z');
const WINDOWS: readonly ActivityWindow[] = [1, 5, 15];

function iso(time: number): string {
  return new Date(time).toISOString().replace('.000Z', 'Z');
}

/** The captured people and devices, each with a fixed share of the traffic. */
function subjects(): SubjectRef[] {
  const seen = new Map<string, SubjectRef>();
  for (const row of USAGE.subjects_persons.items) seen.set(row.subject.key, row.subject);
  for (const row of USAGE.subjects_devices.items) seen.set(row.subject.key, row.subject);
  return [...seen.values()];
}

const DOMAINS = [
  { domain: 'video.example', kind: 'domain' as const },
  { domain: 'portal.example', kind: 'domain' as const },
  { domain: 'Microsoft.Teams', kind: 'service' as const },
  { domain: '198.51.100.20', kind: 'ip' as const },
];
const APPS = ['HTTPS.BROWSER', 'Microsoft.Teams', 'Windows.Update'];

function rows(window: ActivityWindow): ActivityRow[] {
  const seconds = window * 60;
  return subjects()
    .map((subject, index) => {
      // Fixed volumes per minute, decreasing by rank (so the order is by download).
      const down = Math.round((48_000_000 / (index + 1)) * window);
      const up = Math.round((3_200_000 / (index + 2)) * window);
      return {
        subject,
        download_bytes: down,
        upload_bytes: up,
        download_bps: (down * 8) / seconds,
        upload_bps: (up * 8) / seconds,
        sessions: 4 + index * window,
        top_domains: DOMAINS.slice(index % 2, (index % 2) + 3).map((item, rank) => ({
          ...item,
          bytes: Math.round(down / (rank + 2)),
        })),
        top_apps: APPS.slice(0, 1 + (index % 3)).map((app, rank) => ({
          app,
          bytes: Math.round(down / (rank + 2)),
        })),
        last_seen: iso(NOW - (index + 1) * 7_000),
      };
    })
    .sort((a, b) => b.download_bytes - a.download_bytes);
}

function devices(window: ActivityWindow): ActivityDevices {
  const list = rows(window);
  const seconds = window * 60;
  const down = list.reduce((sum, row) => sum + row.download_bytes, 0);
  const up = list.reduce((sum, row) => sum + row.upload_bytes, 0);
  const start = NOW - (NOW % 60_000) - 59 * 60_000;
  return {
    window,
    rows: list,
    more: false,
    totals: {
      download_bytes: down,
      upload_bytes: up,
      download_bps: (down * 8) / seconds,
      upload_bps: (up * 8) / seconds,
      sessions: list.reduce((sum, row) => sum + row.sessions, 0),
      subjects: list.length,
    },
    per_minute: Array.from({ length: 60 }, (_, index) => ({
      minute: iso(start + index * 60_000),
      download_bytes: 120_000_000 + ((index * 37) % 11) * 9_000_000,
      upload_bytes: 9_000_000 + ((index * 13) % 7) * 800_000,
    })),
    data_until: iso(NOW - 2_000),
    generated_at: iso(NOW),
  };
}

function line(
  source: string,
  name: string,
  range: WanRange,
  status: WanLine['status'],
  capacity: { down: number; up: number } | null,
  level: number,
): WanLine {
  const step = range === '60m' ? 15_000 : 60_000;
  const count = range === '60m' ? 240 : 1_440;
  const ok = status === 'ok';
  const series = ok
    ? Array.from({ length: count }, (_, index) => ({
        time: iso(NOW - (count - 1 - index) * step),
        download_bps: level * (0.55 + 0.25 * Math.sin(index / 9)),
        upload_bps: level * 0.12 * (0.6 + 0.3 * Math.cos(index / 7)),
      }))
    : [];
  const last = series.at(-1);
  return {
    source,
    name,
    oper_status: ok ? 'up' : null,
    download_bps: last ? last.download_bps : null,
    upload_bps: last ? last.upload_bps : null,
    capacity_mbps: capacity,
    series,
    status,
    updated_at: last ? last.time : null,
    last_error_at: ok ? null : iso(NOW - 11 * 60_000),
  };
}

export class ActivityMock {
  constructor(readonly options: ActivityOptions = {}) {}

  answer(call: RecordedCall, host: ActivityHost): Answer | null {
    if (call.method !== 'GET') return null;
    if (call.path === '/activity/devices') return this.devices(call, host);
    if (call.path === '/activity/wan') return this.wan(call, host);
    return null;
  }

  /** `usage.person` or `usage.anonymous`, after the KVKK notice (M3). */
  private devices(call: RecordedCall, host: ActivityHost): Answer {
    let denied: Answer | null = null;
    for (const permission of ['usage.person', 'usage.anonymous']) {
      const account = host.requirePermission(call, permission);
      if (Array.isArray(account)) {
        denied = account;
        continue;
      }
      if (host.privacyRequired(account) && !account.privacy_accepted)
        return host.error(403, 'privacy_notice_required');
      const window = Number(call.query.get('window') ?? '5');
      if (!(WINDOWS as readonly number[]).includes(window))
        return host.error(422, 'activity_window_invalid');
      return [200, devices(window as ActivityWindow)];
    }
    return denied ?? host.error(403, 'forbidden');
  }

  /** `dashboard.view` or `system.read`; no person data, not licence-gated (N6). */
  private wan(call: RecordedCall, host: ActivityHost): Answer {
    const allowed = ['dashboard.view', 'system.read'].some(
      (permission) => !Array.isArray(host.requirePermission(call, permission)),
    );
    if (!allowed) return host.error(403, 'forbidden');
    const asked = call.query.get('range') ?? '60m';
    if (asked !== '60m' && asked !== '24h') return host.error(422, 'validation_error');
    const range: WanRange = asked;
    const lines = this.options.noLines
      ? []
      : [
          line('fortigate', 'wan1', range, 'ok', { down: 500, up: 100 }, 180_000_000),
          line(
            'fortigate',
            'wan2',
            range,
            this.options.lineDown ? 'timeout' : 'ok',
            null,
            40_000_000,
          ),
        ];
    return [200, { range, lines, generated_at: iso(NOW) } satisfies WanRates];
  }
}
