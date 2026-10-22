/**
 * The mocked scheduled-report endpoints of the E2E tests (contract section 4, MVP-CONTRACTS §13).
 * They start from the answers captured from the real API (`samples/reports.json`) and keep what a
 * browser session changes, with the API's rules (`arcilogger_api.routers.reports`):
 *
 * - every endpoint needs `reports.manage`; "run now", the preview and file downloads also the
 *   accepted KVKK notice (403 `privacy_notice_required`);
 * - schedules: name, kind, `HH:MM` (not 00:00 for end of day), a weekday (1 … 7) only and
 *   always for weekly and a month day (1 … 28) only and always for monthly reports, valid
 *   recipients (≤ 50), at least one format (422 `validation_error`); switched on without
 *   recipients: 422 `report_recipients_required`; a new schedule is off unless `enabled` is
 *   sent; a new `kind` clears a weekday / month day it does not use; deleting keeps the runs
 *   (their `schedule_id` becomes `null`);
 * - "run now" answers 202 with a queued run: the kind's own period now, or `custom` from/to (`to`
 *   cut at now, at most 92 days); sending without recipients is 422
 *   `report_recipients_required`, while e-mail is off ({@link ReportsMock.smtpEnabled}) 409
 *   `report_smtp_disabled`. The run is `running` after {@link ReportsMock.buildMs}/2 and done
 *   after `buildMs`: `sent` (or `partial` when an address is refused), with `send: false` `sent`
 *   with every delivery `skipped`; without the usage engine ({@link ReportsMock.usageEngine})
 *   `failed` with the runner's error. Only tried deliveries are listed;
 * - files: fixed bytes (the captured runs get these bytes' sizes and SHA-256, so the page's check
 *   passes); a run in {@link ReportsMock.expired} answers 410 `report_expired`;
 * - the preview: HTML (default) or PDF; 503 `usage_engine_unavailable` without the engine.
 *
 * The mock's clock is the sample day's 15:00 site time plus the time since the mock started (the
 * E2E browser clock, `useSampleClock`).
 */

import { createHash } from 'node:crypto';

import type {
  ReportKind,
  ReportRunKind,
  RunOut,
  RunRequest,
  ScheduleCreate,
  ScheduleOut,
  ScheduleUpdate,
} from '../src/lib/api-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { copy, REPORTS } from './samples';

const KINDS: readonly ReportKind[] = ['end_of_day', 'full_day', 'weekly', 'monthly'];
const STATUSES = ['queued', 'running', 'sent', 'partial', 'failed'];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const DAY_MS = 86_400_000;
const SITE_OFFSET_MS = 3 * 3_600_000;
const MAX_PERIOD_DAYS = 92;
const MAX_RECIPIENTS = 50;
/** The runner's error of a run made without the usage engine. */
export const ENGINE_MISSING_ERROR =
  'kullanım motoru yok / usage engine not installed: build_report_data';

/** The bytes served for a run file or a preview. */
export function fakeFile(name: string): Buffer {
  if (name.endsWith('.pdf'))
    return Buffer.from(`%PDF-1.7\n% ARCILOGGER E2E sample report ${name}\n%%EOF\n`);
  if (name.endsWith('.xlsx'))
    return Buffer.concat([
      Buffer.from([0x50, 0x4b, 0x05, 0x06]),
      Buffer.alloc(18),
      Buffer.from(name),
    ]);
  return Buffer.from(
    `<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>${name}</title></head>` +
      `<body><h1>ARCILOGGER kullanım raporu</h1><p>${name}</p></body></html>`,
  );
}

function sha256(data: Buffer): string {
  return createHash('sha256').update(data).digest('hex');
}

const CONTENT_TYPES: Record<string, string> = {
  pdf: 'application/pdf',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  html: 'text/html; charset=utf-8',
};

interface ReportsHost extends MockHost {
  privacyRequired(account: MockUser): boolean;
}

type Field = Partial<ScheduleCreate>;
type Problem = { loc: string[]; type: string; msg: string };
/** A run with what the mock needs to move it along. */
type MockRun = RunOut & { doneAt?: number; send?: boolean; recipients?: string[] };

const iso = (value: number) => new Date(value).toISOString().replace('.000Z', 'Z');

export class ReportsMock {
  readonly schedules = new Map<number, ScheduleOut>();
  /** Every run, newest first. */
  readonly runs: MockRun[];
  /** Milliseconds a new run takes (queued, then running, then finished). */
  buildMs = 1_500;
  /** `[smtp] enabled` of the server: "run now" with e-mail is refused while it is off. */
  smtpEnabled = true;
  /** The usage engine is installed: without it the preview is 503 and runs fail. */
  usageEngine = true;
  /** An address the mock's mail server refuses (its delivery fails, the run is partial). */
  refused: string | null = null;
  /** Runs whose files were deleted after `reports.keep_days` (410). */
  readonly expired = new Set<number>();
  private readonly started = Date.now();
  private readonly clock = Date.parse(`${REPORTS.meta.site_today}T15:00:00+03:00`);
  private nextSchedule: number;
  private nextRun: number;

  constructor() {
    for (const item of REPORTS.schedules) this.schedules.set(item.id, copy(item));
    this.runs = REPORTS.runs.items.map((run) => this.withFakeFiles(copy(run)));
    this.nextSchedule = Math.max(...this.schedules.keys()) + 1;
    this.nextRun = Math.max(...this.runs.map((run) => run.id)) + 1;
  }

  now(): Date {
    return new Date(this.clock + (Date.now() - this.started));
  }

  answer(call: RecordedCall, host: ReportsHost): Answer | null {
    const { method, path } = call;
    if (!path.startsWith('/reports/')) return null;
    const account = host.requirePermission(call, 'reports.manage');
    if (Array.isArray(account)) return account;
    const notice = () =>
      host.privacyRequired(account) && !account.privacy_accepted
        ? host.error(403, 'privacy_notice_required')
        : null;
    let match: RegExpExecArray | null;
    if (path === '/reports/schedules' && method === 'GET')
      return [200, [...this.schedules.values()].map((item) => this.scheduleOut(item))];
    if (path === '/reports/schedules' && method === 'POST')
      return this.create(call, host, account.username);
    if ((match = /^\/reports\/schedules\/(\d+)(\/run)?$/.exec(path))) {
      const id = Number(match[1]);
      const found = this.schedules.get(id);
      if (!found) return host.error(404, 'not_found');
      if (match[2] && method === 'POST')
        return notice() ?? this.run(call, host, found, account.username);
      if (!match[2] && method === 'PATCH') return this.update(call, host, found, account.username);
      if (!match[2] && method === 'DELETE') {
        this.schedules.delete(id);
        for (const run of this.runs) if (run.schedule_id === id) run.schedule_id = null;
        return [204, null];
      }
    }
    if (path === '/reports/runs' && method === 'GET') return this.listRuns(call, host);
    if ((match = /^\/reports\/runs\/(\d+)(?:\/files\/([^/]+))?$/.exec(path)) && method === 'GET') {
      const run = this.runs.find((item) => item.id === Number(match?.[1]));
      if (!run) return host.error(404, 'not_found');
      this.advance(run);
      if (!match[2]) return [200, this.runOut(run)];
      const refusal = notice();
      if (refusal) return refusal;
      return this.file(host, run, decodeURIComponent(match[2]));
    }
    if (path === '/reports/preview' && method === 'GET')
      return notice() ?? this.preview(call, host);
    return null;
  }

  // -- schedules --------------------------------------------------------------------------------

  /** The schedule as the list shows it: its newest run, the next run while enabled. */
  private scheduleOut(item: ScheduleOut): ScheduleOut {
    const last = this.runs.find((run) => run.schedule_id === item.id);
    if (last) this.advance(last);
    return {
      ...copy(item),
      next_run_at: item.enabled ? (item.next_run_at ?? this.nextRunAt(item)) : null,
      last_run: last ? this.runOut(last) : null,
    };
  }

  /** The next run of a new or changed schedule (site time, a simple version of the server's). */
  private nextRunAt(item: Pick<ScheduleOut, 'kind' | 'time' | 'weekday' | 'monthday'>): string {
    const [hours = 0, minutes = 0] = item.time.split(':').map(Number);
    const now = this.now().getTime();
    const midnight = now + SITE_OFFSET_MS - ((now + SITE_OFFSET_MS) % DAY_MS) - SITE_OFFSET_MS;
    for (let day = 0; day < 62; day += 1) {
      const start = midnight + day * DAY_MS;
      const wall = new Date(start + SITE_OFFSET_MS);
      const weekday = ((wall.getUTCDay() + 6) % 7) + 1;
      const at = start + (hours * 60 + minutes) * 60_000;
      if (at <= now) continue;
      if (item.kind === 'weekly' && weekday !== item.weekday) continue;
      if (item.kind === 'monthly' && wall.getUTCDate() !== item.monthday) continue;
      return iso(at);
    }
    return iso(now + DAY_MS);
  }

  /** Problems of a schedule as it would be after the change (422 `details`), `[]` when none. */
  private problems(item: Field): Problem[] {
    const problems: Problem[] = [];
    const add = (field: string, msg: string) =>
      problems.push({ loc: ['body', field], type: 'value_error', msg });
    if (!item.name?.trim() || item.name.length > 200)
      add('name', 'Name must be 1 to 200 characters');
    if (!item.kind || !KINDS.includes(item.kind)) add('kind', 'Unknown kind');
    if (!item.time || !/^([01]\d|2[0-3]):[0-5]\d$/.test(item.time))
      add('time', 'Time must be HH:MM');
    else if (item.kind === 'end_of_day' && item.time === '00:00')
      add('time', 'an end_of_day report at 00:00 would cover nothing');
    if ((item.kind === 'weekly') !== (item.weekday != null))
      add('weekday', 'weekday is required for weekly schedules and only for them');
    else if (item.weekday != null && !(item.weekday >= 1 && item.weekday <= 7))
      add('weekday', 'Input should be between 1 and 7');
    if ((item.kind === 'monthly') !== (item.monthday != null))
      add('monthday', 'monthday is required for monthly schedules and only for them');
    else if (item.monthday != null && !(item.monthday >= 1 && item.monthday <= 28))
      add('monthday', 'Input should be between 1 and 28');
    const recipients = item.recipients ?? [];
    if (recipients.length > MAX_RECIPIENTS || recipients.some((address) => !EMAIL.test(address)))
      add('recipients', 'Invalid e-mail address');
    const formats = item.formats;
    if (!formats || !(formats.email_html || formats.pdf || formats.xlsx))
      add('formats', 'at least one format (email_html, pdf, xlsx) is required');
    return problems;
  }

  /** 422 for the fields, then 422 `report_recipients_required` (on without recipients). */
  private refusal(host: MockHost, item: Field): Answer | null {
    const problems = this.problems(item);
    if (problems.length > 0) return host.error(422, 'validation_error', problems.slice(0, 1));
    if (item.enabled && (item.recipients ?? []).length === 0)
      return host.error(422, 'report_recipients_required');
    return null;
  }

  private create(call: RecordedCall, host: MockHost, user: string): Answer {
    const body = call.body as ScheduleCreate;
    const item: Field = { enabled: false, person_level: true, pseudonymize: false, ...body };
    const refused = this.refusal(host, item);
    if (refused) return refused;
    const at = this.now().toISOString();
    const created: ScheduleOut = {
      id: this.nextSchedule++,
      name: body.name.trim(),
      kind: body.kind,
      enabled: item.enabled ?? false,
      time: body.time,
      weekday: body.weekday ?? null,
      monthday: body.monthday ?? null,
      recipients: [...body.recipients],
      formats: { ...body.formats },
      person_level: item.person_level ?? true,
      pseudonymize: item.pseudonymize ?? false,
      created_at: at,
      updated_at: at,
      updated_by: user,
      next_run_at: null,
      last_run: null,
    };
    if (created.enabled && created.recipients.length > 0)
      created.next_run_at = this.nextRunAt(created);
    this.schedules.set(created.id, created);
    return [201, this.scheduleOut(created)];
  }

  private update(call: RecordedCall, host: MockHost, found: ScheduleOut, user: string): Answer {
    const body = call.body as ScheduleUpdate;
    const required = Object.entries(body).find(
      ([key, value]) => value === null && key !== 'weekday' && key !== 'monthday',
    );
    if (required)
      return host.error(422, 'validation_error', [
        { loc: ['body', required[0]], type: 'value_error', msg: 'may not be null' },
      ]);
    const changes = { ...body } as Partial<ScheduleOut>;
    const next = { ...found, ...changes };
    // A new kind clears the weekday / month day it does not use.
    if (body.kind && body.kind !== found.kind) {
      if (next.kind !== 'weekly' && body.weekday === undefined) next.weekday = null;
      if (next.kind !== 'monthly' && body.monthday === undefined) next.monthday = null;
    }
    const refused = this.refusal(host, next);
    if (refused) return refused;
    Object.assign(found, next, { updated_at: this.now().toISOString(), updated_by: user });
    found.next_run_at = found.enabled && found.recipients.length > 0 ? this.nextRunAt(found) : null;
    return [200, this.scheduleOut(found)];
  }

  // -- runs -------------------------------------------------------------------------------------

  /** The period a run of `kind` started now covers (end of day: today until now). */
  private ownPeriod(kind: ReportKind): { from: string; to: string } {
    const now = this.now().getTime();
    const midnight = now + SITE_OFFSET_MS - ((now + SITE_OFFSET_MS) % DAY_MS) - SITE_OFFSET_MS;
    if (kind === 'end_of_day') return { from: iso(midnight), to: iso(now) };
    if (kind === 'full_day') return { from: iso(midnight - DAY_MS), to: iso(midnight) };
    const wall = new Date(midnight + SITE_OFFSET_MS);
    if (kind === 'weekly') {
      const monday = midnight - ((wall.getUTCDay() + 6) % 7) * DAY_MS;
      return { from: iso(monday - 7 * DAY_MS), to: iso(monday) };
    }
    const first = Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth(), 1) - SITE_OFFSET_MS;
    const previous = Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth() - 1, 1) - SITE_OFFSET_MS;
    return { from: iso(previous), to: iso(first) };
  }

  /** `(kind, period)` of a run or preview; a refusal for a wrong `from`/`to`. */
  private period(
    host: MockHost,
    kind: ReportRunKind,
    from: string | null | undefined,
    to: string | null | undefined,
    where: 'body' | 'query',
  ): { kind: ReportRunKind; from: string; to: string } | Answer {
    const invalid = (field: string, msg: string) =>
      host.error(422, 'validation_error', [{ loc: [where, field], type: 'value_error', msg }]);
    if (from == null && to == null) {
      if (kind === 'custom') return invalid('from', 'a custom report needs from and to');
      return { kind, ...this.ownPeriod(kind) };
    }
    if (from == null || to == null)
      return invalid(from == null ? 'from' : 'to', 'give both from and to, or neither');
    // A time without an offset is site time.
    const parse = (value: string) =>
      Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}+03:00`);
    const start = parse(from);
    const end = Math.min(parse(to), this.now().getTime());
    if (!(end > start)) return invalid('to', 'to must be after from (and not after now)');
    if (end - start > MAX_PERIOD_DAYS * DAY_MS)
      return invalid('from', `the period may cover at most ${MAX_PERIOD_DAYS} days`);
    return { kind: 'custom', from: iso(start), to: iso(end) };
  }

  private run(call: RecordedCall, host: MockHost, schedule: ScheduleOut, user: string): Answer {
    const body = (call.body ?? {}) as RunRequest;
    const period = this.period(host, schedule.kind, body.from, body.to, 'body');
    if (Array.isArray(period)) return period;
    const send = body.send ?? true;
    if (send && schedule.recipients.length === 0)
      return host.error(422, 'report_recipients_required');
    if (send && !this.smtpEnabled) return host.error(409, 'report_smtp_disabled');
    const run: MockRun = {
      id: this.nextRun++,
      schedule_id: schedule.id,
      kind: period.kind,
      period: { from: period.from, to: period.to },
      status: 'queued',
      started_at: this.now().toISOString(),
      finished_at: null,
      error: null,
      files: [],
      deliveries: [],
      triggered_by: 'manual',
      requested_by: user,
      doneAt: Date.now() + this.buildMs,
      send,
      recipients: [...schedule.recipients],
    };
    this.runs.unshift(run);
    return [202, this.runOut(run)];
  }

  /** The captured runs with the mock's file bytes (sizes and hashes the downloads match). */
  private withFakeFiles(run: MockRun): MockRun {
    run.files = run.files.map((file) => {
      const bytes = fakeFile(file.name);
      return { ...file, size: bytes.length, sha256: sha256(bytes) };
    });
    return run;
  }

  /** Move a new run along: queued → running → sent / partial / failed. */
  private advance(run: MockRun): void {
    if (run.doneAt === undefined || run.finished_at !== null) return;
    const left = run.doneAt - Date.now();
    if (left > this.buildMs / 2) return;
    if (left > 0) {
      run.status = 'running';
      return;
    }
    const at = new Date(Date.parse(run.started_at) + this.buildMs).toISOString();
    run.finished_at = at;
    if (!this.usageEngine) {
      run.status = 'failed';
      run.error = ENGINE_MISSING_ERROR;
      return;
    }
    const schedule = run.schedule_id === null ? undefined : this.schedules.get(run.schedule_id);
    const formats = schedule?.formats ?? { email_html: true, pdf: true, xlsx: false };
    run.files = (['html', 'pdf', 'xlsx'] as const)
      .filter((format) => (format === 'html' ? formats.email_html : formats[format]))
      .map((format) => {
        const name = `ARCILOGGER-kullanim-${run.kind.replace(/_/g, '-')}-r${run.id}.${format}`;
        const bytes = fakeFile(name);
        return { name, format, size: bytes.length, sha256: sha256(bytes) };
      });
    run.deliveries = (run.recipients ?? []).map((recipient) =>
      !run.send
        ? { recipient, status: 'skipped' as const, error: null, sent_at: null }
        : recipient === this.refused
          ? {
              recipient,
              status: 'failed' as const,
              error: 'recipient refused: 550 5.1.1 mailbox unavailable',
              sent_at: null,
            }
          : { recipient, status: 'sent' as const, error: null, sent_at: at },
    );
    run.status = run.deliveries.some((item) => item.status === 'failed') ? 'partial' : 'sent';
  }

  private runOut(run: MockRun): RunOut {
    const out: MockRun = copy(run);
    delete out.doneAt;
    delete out.send;
    delete out.recipients;
    return out;
  }

  private listRuns(call: RecordedCall, host: MockHost): Answer {
    const q = call.query;
    const limit = Number(q.get('limit') ?? 50);
    const offset = Number(q.get('offset') ?? 0);
    const scheduleId = q.get('schedule_id');
    const status = q.get('status');
    if (!Number.isInteger(limit) || limit < 1 || limit > 200)
      return host.error(422, 'validation_error', [
        { loc: ['query', 'limit'], type: 'value_error', msg: 'Input should be between 1 and 200' },
      ]);
    if (status !== null && !STATUSES.includes(status))
      return host.error(422, 'validation_error', [
        { loc: ['query', 'status'], type: 'enum', msg: 'Unknown status' },
      ]);
    for (const run of this.runs) this.advance(run);
    const rows = this.runs
      .filter((run) => scheduleId === null || run.schedule_id === Number(scheduleId))
      .filter((run) => status === null || run.status === status);
    return [
      200,
      {
        items: rows.slice(offset, offset + limit).map((run) => this.runOut(run)),
        total: rows.length,
        limit,
        offset,
      },
    ];
  }

  private file(host: MockHost, run: MockRun, name: string): Answer {
    const file = run.files.find((item) => item.name === name);
    if (!file) return host.error(404, 'not_found');
    if (this.expired.has(run.id)) return host.error(410, 'report_expired');
    const body = fakeFile(name);
    return [
      200,
      null,
      {
        body,
        contentType: CONTENT_TYPES[file.format] ?? 'application/octet-stream',
        headers: {
          'Content-Disposition': `attachment; filename="${name}"`,
          'X-Arci-Sha256': sha256(body),
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  private preview(call: RecordedCall, host: MockHost): Answer {
    const q = call.query;
    const kind = q.get('kind');
    const format = q.get('format') ?? 'html';
    if (!kind || ![...KINDS, 'custom'].includes(kind) || !['pdf', 'html'].includes(format))
      return host.error(422, 'validation_error', [
        { loc: ['query', 'kind'], type: 'value_error', msg: 'kind and format are required' },
      ]);
    const period = this.period(host, kind as ReportRunKind, q.get('from'), q.get('to'), 'query');
    if (Array.isArray(period)) return period;
    if (!this.usageEngine) return host.error(503, 'usage_engine_unavailable');
    const body = fakeFile(`onizleme-${period.kind}.${format}`);
    return [
      200,
      null,
      {
        body,
        contentType: CONTENT_TYPES[format] ?? 'application/octet-stream',
        headers: {
          ...(format === 'pdf'
            ? { 'Content-Disposition': 'inline; filename="ARCILOGGER-kullanim-onizleme.pdf"' }
            : {
                'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox",
              }),
          'Cache-Control': 'no-store',
        },
      },
    ];
  }
}
