/**
 * The mocked sources API (`/sources`, Wave 2 contract 2.16) and the Panel's parse-failure box
 * (2.17) of the E2E tests: the refusals as the API answered them (`samples/sources.json`), the
 * site's records in the shapes of `src/lib/sources-types.ts` and `api-types.ts` — the capture has
 * four sources, the example site here every state; `sources.spec.ts` and `dashboard.spec.ts` also
 * run the pages on the captured answers as they are —, and the permission rules of the router: the list and the detail need `sources.read` (a pattern's `error` only with
 * `search.read` or `lookup.run` and the accepted KVKK notice, the raw-line rule), the last lines
 * also `search.read` or `lookup.run` and the accepted KVKK notice. Installed after `mockApi` (`page.route`: the newest route wins), it shares
 * the main mock's session, users and recorded calls.
 *
 * The site is the example network: a MikroTik `core-r1`, a FortiGate, a network `ap` with two
 * members, a silent switch that is not expected to send, a source switched off, a configured
 * source that never sent, an unknown sender in the site's network (`unknown-172.20.10.40`, the
 * "Kaynak olarak tanımla" case) and one promoted to `edge-1`. Example and tail lines are captured
 * raw lines (`samples/raw.json`), so their raw drawer opens (the capture's own lines of today are
 * not in `raw.json`).
 */

import { createHash } from 'node:crypto';

import type { Page, Request, Route } from '@playwright/test';

import type { DashboardParseFailures } from '../src/lib/api-types';
import type {
  SourceDetail,
  SourceHealth,
  SourceParseFailure,
  SourcesOverview,
  SourceTail,
} from '../src/lib/sources-types';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import { copy, RAW, SAMPLE_TODAY, SOURCES } from './samples';

/** 15:00 site time on the sample day (the tests' browser clock, `useSampleClock`). */
const NOW = `${SAMPLE_TODAY}T15:00:00+03:00`;
const RAW_PERMISSIONS = ['search.read', 'lookup.run'];

function at(minutesAgo: number): string {
  return new Date(Date.parse(NOW) - minutesAgo * 60_000).toISOString().replace('Z', '+00:00');
}

/** A sparkline of 60 minutes around `rate` (oldest first), the same in every run. */
function wave(rate: number): number[] {
  return Array.from({ length: 60 }, (_, index) =>
    Math.max(0, Math.round(rate * (1 + 0.15 * Math.sin(index / 4)))),
  );
}

function source(name: string, fields: Partial<SourceHealth>): SourceHealth {
  return {
    name,
    type: 'mikrotik',
    address: null,
    configured: true,
    network_of: null,
    unknown: false,
    enabled: true,
    expected: true,
    heartbeat_minutes: 15,
    first_seen: '2026-09-03T07:00:00+03:00',
    last_message: at(1),
    last_message_origin: 'collector',
    state: 'active',
    silent_minutes: null,
    messages_per_minute: 0,
    sparkline: wave(0),
    lines_today: 0,
    pending_bytes: 0,
    parse_failures_today: 0,
    parse_failure_permille: null,
    promoted_to: null,
    suggested: null,
    ...fields,
  };
}

/** Plain code-point order, as Python's `sorted` compares names. */
function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The API's order (`sourcehealth.overview`): by name, a network's members right after it. */
function apiOrder(items: SourceHealth[]): SourceHealth[] {
  return [...items].sort(
    (a, b) =>
      compare(a.network_of ?? a.name, b.network_of ?? b.name) ||
      Number(a.network_of !== null) - Number(b.network_of !== null) ||
      compare(a.name, b.name),
  );
}

/** The sources of the example site, in the API's order (a network followed by its members). */
export function sampleSources(): SourceHealth[] {
  return apiOrder([
    source('core-r1', {
      address: '172.20.10.1',
      messages_per_minute: 12.5,
      sparkline: wave(12.5),
      lines_today: 11_200,
      pending_bytes: 4_096,
      parse_failures_today: 1,
      parse_failure_permille: 0.1,
    }),
    source('fortigate', {
      type: 'fortigate',
      address: '172.31.255.1',
      heartbeat_minutes: 10,
      messages_per_minute: 1_150.4,
      sparkline: wave(1_150),
      lines_today: 1_200,
      parse_failures_today: 30,
      parse_failure_permille: 25,
    }),
    source('ap', {
      type: 'generic',
      address: '172.20.10.0/28',
      messages_per_minute: 3.2,
      sparkline: wave(3.2),
      lines_today: 2_400,
    }),
    source('ap-172.20.10.3', {
      type: 'generic',
      address: '172.20.10.3',
      network_of: 'ap',
      messages_per_minute: 3.2,
      sparkline: wave(3.2),
      lines_today: 2_400,
    }),
    source('ap-172.20.10.4', {
      type: 'generic',
      address: '172.20.10.4',
      network_of: 'ap',
      state: 'never',
      first_seen: null,
      last_message: null,
      last_message_origin: null,
    }),
    source('switch-3', {
      address: '172.20.10.24',
      expected: false,
      state: 'silent',
      last_message: at(180),
      last_message_origin: 'database',
      silent_minutes: 180,
    }),
    source('lab-fw', {
      type: 'fortigate',
      address: '172.20.10.50',
      enabled: false,
      state: 'disabled',
      last_message: at(4_320),
      last_message_origin: 'database',
    }),
    source('edge-1', {
      type: 'generic',
      address: '198.51.100.7',
      state: 'never',
      first_seen: null,
      last_message: null,
      last_message_origin: null,
    }),
    source('unknown-172.20.10.40', {
      type: 'generic',
      address: '172.20.10.40',
      configured: false,
      unknown: true,
      state: 'unknown',
      first_seen: at(42),
      last_message: at(2),
      messages_per_minute: 0.6,
      sparkline: wave(0.6),
      lines_today: 25,
      suggested: { name: 'kaynak-40', type: 'mikrotik' },
    }),
    source('unknown-198.51.100.7', {
      type: 'generic',
      address: '198.51.100.7',
      configured: false,
      unknown: true,
      state: 'promoted',
      first_seen: '2026-10-08T10:00:00+03:00',
      last_message: '2026-10-11T09:12:00+03:00',
      last_message_origin: 'database',
      promoted_to: 'edge-1',
    }),
  ]);
}

/** A captured raw line's reference, so that the raw-line drawer opens it. */
function rawRef(key: string): { source: string; day: string; line: number; offset: number } {
  const line = RAW.lines[key];
  if (!line) throw new Error(`no captured raw line ${key}`);
  return { source: line.source, day: line.day, line: line.line, offset: line.offset };
}

function failures(name: string): SourceParseFailure[] {
  if (name === 'core-r1')
    return [
      {
        day: SAMPLE_TODAY,
        pattern: 'rawlog.invalid',
        count: 1,
        first_seen: at(300),
        last_seen: at(300),
        // A line that is not in the spool format has no parser detail.
        error: null,
        example: rawRef('core-r1|2026-10-09|4'),
      },
    ];
  if (name === 'fortigate')
    return [
      {
        day: SAMPLE_TODAY,
        pattern: 'fortigate.kv',
        count: 30,
        first_seen: at(600),
        last_seen: at(12),
        error: 'value was not key=value',
        example: rawRef('fortigate|2026-10-12|49'),
      },
    ];
  return [];
}

function detailOf(item: SourceHealth): SourceDetail {
  const now = Date.parse(NOW);
  const hourStart = now - (now % 3_600_000);
  const rate = item.messages_per_minute * 60;
  const hourly = Array.from({ length: 48 }, (_, index) => ({
    hour: new Date(hourStart - (47 - index) * 3_600_000).toISOString(),
    count: Math.round(rate * (0.6 + 0.4 * Math.sin(index / 5) ** 2)),
  }));
  const daily = Array.from({ length: 14 }, (_, index) => {
    const day = new Date(Date.parse(`${SAMPLE_TODAY}T12:00:00Z`) - index * 86_400_000)
      .toISOString()
      .slice(0, 10);
    return {
      day,
      lines: index === 0 ? item.lines_today : Math.round(rate * 24),
      failures: index === 0 ? item.parse_failures_today : 0,
      sealed: index >= 1 && day <= '2026-10-10',
    };
  });
  const gaps: SourceDetail['gaps'] =
    item.name === 'core-r1'
      ? [
          {
            from: '2026-10-09T14:00:00.003000+03:00',
            to: '2026-10-10T00:00:00+03:00',
            minutes: 599,
            reason: 'no messages > 60 min',
            origin: 'manifest',
          },
          {
            from: '2026-10-09T10:42:18.003000+03:00',
            to: '2026-10-09T13:00:00.003000+03:00',
            minutes: 137,
            reason: 'no messages > 60 min',
            origin: 'manifest',
          },
        ]
      : item.name === 'switch-3'
        ? [{ from: at(180), to: NOW, minutes: 180, reason: 'no messages > 60 min', origin: 'live' }]
        : [];
  return { ...item, hourly, daily, gaps, parse_failures: failures(item.name) };
}

/** The message part of a captured line's display text (after the time stamp and the sender). */
function messageOf(text: string): string {
  return text.split(' ').slice(2).join(' ');
}

function tailOf(name: string): SourceTail {
  const keys = Object.keys(RAW.lines)
    .filter((key) => key.startsWith(`${name}|2026-10-09|`))
    .sort((a, b) => Number(a.split('|')[2]) - Number(b.split('|')[2]));
  if (keys.length === 0)
    return {
      source: name,
      day: null,
      origin: null,
      from_line: null,
      to_line: null,
      from_offset: null,
      to_offset: null,
      range_sha256: null,
      lines: [],
      reason: 'no_file_today',
    };
  const lines = keys.flatMap((key) => {
    const line = RAW.lines[key];
    return line ? [line] : [];
  });
  const bytes = Buffer.concat(
    lines.map((line) => Buffer.concat([Buffer.from(line.raw_base64, 'base64'), Buffer.from('\n')])),
  );
  const first = lines[0];
  const last = lines.at(-1);
  return {
    source: name,
    day: '2026-10-09',
    origin: 'spool',
    from_line: first?.line ?? null,
    to_line: last?.line ?? null,
    from_offset: first?.offset ?? null,
    to_offset: last ? last.offset + last.length + 1 : null,
    range_sha256: createHash('sha256').update(bytes).digest('hex'),
    lines: lines.map((line) => ({
      line: line.line,
      offset: line.offset,
      received: line.received,
      sender: line.sender,
      text: messageOf(line.text),
      sha256: line.sha256,
    })),
  };
}

/** The Panel's parse-failure box of the example site (two patterns; one not in the format). */
export function sampleParseFailures(): DashboardParseFailures {
  return {
    day: SAMPLE_TODAY,
    lines: 12_400,
    failures: 31,
    permille: 2.5,
    by_source: [
      { source: 'fortigate', lines: 1_200, failures: 30, permille: 25 },
      { source: 'core-r1', lines: 11_200, failures: 1, permille: 0.1 },
    ],
    top_patterns: [
      {
        source: 'fortigate',
        pattern: 'fortigate.kv',
        count: 30,
        example: rawRef('fortigate|2026-10-12|49'),
      },
      {
        source: 'core-r1',
        pattern: 'rawlog.invalid',
        count: 1,
        example: rawRef('core-r1|2026-10-09|4'),
      },
    ],
    last7: Array.from({ length: 7 }, (_, index) => {
      const day = new Date(Date.parse(`${SAMPLE_TODAY}T12:00:00Z`) - (6 - index) * 86_400_000)
        .toISOString()
        .slice(0, 10);
      return { day, lines: index === 6 ? 12_400 : 9_000, failures: index === 6 ? 31 : index };
    }),
  };
}

export interface SourcesOptions {
  /** The list (default: `sampleSources()`). */
  items?: SourceHealth[];
  /** The Panel's parse-failure box (default: `sampleParseFailures()`; `null`: none). */
  parseFailures?: DashboardParseFailures | null;
}

export class SourcesMock {
  items: SourceHealth[];
  parseFailures: DashboardParseFailures | null;

  constructor(
    private readonly api: MockApi,
    options: SourcesOptions = {},
  ) {
    this.items = options.items ?? sampleSources();
    this.parseFailures =
      options.parseFailures === undefined ? sampleParseFailures() : options.parseFailures;
  }

  async install(page: Page): Promise<void> {
    await page.route('**/api/v1/sources**', (route, request) => this.handle(route, request));
    await page.route('**/api/v1/dashboard/summary', (route, request) =>
      this.handle(route, request),
    );
  }

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const headers = request.headers();
    const call: RecordedCall = {
      method: request.method(),
      path: url.pathname.replace(/^\/api\/v1/, ''),
      query: url.searchParams,
      body: undefined,
      raw: null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.api.calls.push(call);
    const [status, payload] = this.answer(call);
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify(payload),
    });
  }

  /** A refusal of this router as the API answered it (captured), else the main mock's. */
  private error(status: number, code: string): [number, unknown] {
    const sample = SOURCES.errors[code];
    return sample ? [sample.status, copy(sample.body)] : this.api.error(status, code);
  }

  private rawAllowed(account: MockUser): boolean {
    const granted = this.api.permissionsOf(account);
    return RAW_PERMISSIONS.some((permission) => granted.includes(permission));
  }

  private answer(call: RecordedCall): [number, unknown] {
    if (call.path === '/dashboard/summary') return this.dashboard(call);
    const account = this.api.requirePermission(call, 'sources.read');
    if (Array.isArray(account)) return account;
    if (call.method !== 'GET') return this.api.error(405, 'method_not_allowed');
    if (call.path === '/sources') {
      const overview: SourcesOverview = {
        generated_at: NOW,
        today: SAMPLE_TODAY,
        collector: { updated_at: at(0.5), fresh: true },
        items: this.items,
      };
      return [200, overview];
    }
    const found = /^\/sources\/([^/]+)(\/tail)?$/.exec(call.path);
    const name = found?.[1] ? decodeURIComponent(found[1]) : '';
    const item = this.items.find((entry) => entry.name === name);
    if (found?.[2]) {
      if (!this.rawAllowed(account)) return this.error(403, 'forbidden');
      if (!account.privacy_accepted) return this.error(403, 'privacy_notice_required');
      if (!item) return this.error(404, 'source_not_found');
      const tail = tailOf(item.name);
      const limit = Number(call.query.get('limit') ?? 50);
      return [200, { ...tail, lines: tail.lines.slice(-limit) }];
    }
    if (!item) return this.error(404, 'source_not_found');
    const detail = detailOf(item);
    if (!this.rawAllowed(account) || !account.privacy_accepted)
      detail.parse_failures = detail.parse_failures.map((row) => ({ ...row, error: null }));
    return [200, detail];
  }

  /** The main mock's summary with the parse-failure box beside the ingest box. */
  private dashboard(call: RecordedCall): [number, unknown] {
    const [status, payload] = this.api.alerts.answer(call, this.api) ?? [404, null];
    if (status !== 200 || typeof payload !== 'object' || payload === null) return [status, payload];
    const summary = payload as { ingest: unknown };
    return [
      200,
      { ...summary, parse_failures: summary.ingest ? structuredClone(this.parseFailures) : null },
    ];
  }
}

/** Install the sources mock on top of `api` (call after `mockApi`). */
export async function mockSources(
  page: Page,
  api: MockApi,
  options: SourcesOptions = {},
): Promise<SourcesMock> {
  const sources = new SourcesMock(api, options);
  await sources.install(page);
  return sources;
}
