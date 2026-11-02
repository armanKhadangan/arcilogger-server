/**
 * The mocked security alert, dashboard and company-usage endpoints of the E2E tests (group 2
 * contract sections 2 and 5). The alerts start from the answers captured from the real API
 * (`samples/alerts.json`, `samples/dashboard.json`) and the company usage of the sample day
 * (`samples/usage.json` → `company`). The mock keeps the state a browser session changes, with the
 * API's rules (`arcilogger_api.routers.alerts`, `arcilogger_worker.alerts.store`):
 *
 * - `GET /alerts`, `/alerts/summary`, `/alerts/{id}`: `alerts.read`; `POST /alerts/{id}/state`
 *   and `/notes`: `alerts.manage`. Filters repeat or take comma-separated values (at most 20);
 *   `q` looks in the title, the rule and `details.source`.
 * - The workflow: open → acknowledged | resolved | dismissed; acknowledged → open | resolved |
 *   dismissed; resolved ↔ dismissed; back to an active state only while no other active alert
 *   holds the same problem (409 `alert_key_active` with `details.alert_id`). The same state is
 *   409 `alert_state_unchanged`, another change 409 `alert_transition_not_allowed`. A note sent
 *   with the change is a separate `note` event right after the state event.
 * - `GET /dashboard/summary`: `dashboard.view`; each block `null` without its own permission
 *   (ingest: `sources.read` or `dashboard.view`; seal and credit: `seal.read`; clock:
 *   `system.read` or `seal.read`; alerts: `alerts.read`, counted from the alerts as they are now).
 * - `GET /usage/company`: `dashboard.usage`, `usage.person` or `usage.anonymous`.
 *
 * The mock's clock is the sample day's 15:00 site time plus the time since the mock started (the
 * E2E browser clock, `useSampleClock`).
 */

import type {
  AlertDetail,
  AlertEvent,
  AlertOut,
  AlertSeverity,
  AlertState,
  AlertSummary,
  DashboardSummary,
  OpenBySeverity,
} from '../src/lib/api-types';
import { codePointLength } from '../src/lib/validation';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { ALERTS, copy, DASHBOARD, USAGE } from './samples';

/** In the API's natural order (the sort by state and severity follows it). */
const STATES: readonly AlertState[] = ['open', 'acknowledged', 'resolved', 'dismissed'];
const SEVERITIES: readonly AlertSeverity[] = ['info', 'low', 'medium', 'high', 'critical'];
const ACTIVE: readonly AlertState[] = ['open', 'acknowledged'];
const TRANSITIONS: Record<AlertState, readonly AlertState[]> = {
  open: ['acknowledged', 'resolved', 'dismissed'],
  acknowledged: ['open', 'resolved', 'dismissed'],
  resolved: ['open', 'dismissed'],
  dismissed: ['open', 'resolved'],
};
const SORTS = ['time', 'created_at', 'updated_at', 'severity', 'state', 'rule'] as const;
const MAX_FILTER_VALUES = 20;
const DAY_MS = 86_400_000;
const SITE_OFFSET_MS = 3 * 3_600_000;
const NOTE_MAX = 2000;

/** Fields of `AlertDetail` that `AlertOut` does not have. */
const DETAIL_ONLY = ['details', 'events', 'runbook_tr', 'runbook_en', 'deliveries'];

function alertOut(detail: AlertDetail): AlertOut {
  return Object.fromEntries(
    Object.entries(copy(detail)).filter(([key]) => !DETAIL_ONLY.includes(key)),
  ) as unknown as AlertOut;
}

/** A moment in site time as the dashboard sends it (`2026-10-13T15:00:00.000+03:00`). */
function siteIso(moment: Date): string {
  return new Date(moment.getTime() + SITE_OFFSET_MS).toISOString().replace('Z', '+03:00');
}

/**
 * The problem an alert is about (the API's `dedup_key`, which `AlertOut` leaves out): the rule
 * and the drive, source, day or address of its details.
 */
function problemKey(alert: AlertDetail): string {
  const details = alert.details;
  const subject = ['drive', 'source', 'day', 'ip', 'address']
    .map((key) => details[key])
    .find((value) => typeof value === 'string' || typeof value === 'number');
  return `${alert.rule}:${subject === undefined ? `#${alert.id}` : String(subject)}`;
}

/** Every alert of the captured list with its captured detail (and the note added after it). */
function capturedAlerts(): AlertDetail[] {
  const details = new Map<number, AlertDetail>();
  for (const sample of [
    ALERTS.detail,
    ALERTS.detail_auto_resolved,
    ALERTS.detail_dismissed,
    ALERTS.detail_seal,
    ALERTS.state_acknowledged,
    ALERTS.state_reopened,
    ALERTS.state_resolved,
  ])
    details.set(sample.id, copy(sample));
  return ALERTS.list.items.map((item) => {
    const detail = details.get(item.id);
    if (!detail) throw new Error(`alerts.json has no detail of alert ${item.id}`);
    const merged: AlertDetail = { ...detail, ...copy(item) };
    const notes = merged.events.filter((event) => event.kind === 'note').length;
    if (item.notes_count > notes && !merged.events.some((e) => e.id === ALERTS.note_created.id))
      merged.events.push(copy(ALERTS.note_created));
    return merged;
  });
}

function validation(host: MockHost, field: string, msg: string): Answer {
  return host.error(422, 'validation_error', [{ loc: ['query', field], type: 'value_error', msg }]);
}

/** The first of `permissions` the user holds decides; otherwise the refusal of the last. */
export function requireAny(
  call: RecordedCall,
  host: MockHost,
  permissions: readonly string[],
): MockUser | [number, unknown] {
  let refusal: [number, unknown] | null = null;
  for (const permission of permissions) {
    const result = host.requirePermission(call, permission);
    if (!Array.isArray(result)) return result;
    refusal = result;
  }
  return refusal ?? host.error(403, 'forbidden');
}

/** A repeatable filter (`?state=open&state=acknowledged` or `?state=open,acknowledged`). */
function values(query: URLSearchParams, name: string): string[] {
  return [
    ...new Set(
      query
        .getAll(name)
        .flatMap((item) => item.split(','))
        .map((part) => part.trim())
        .filter(Boolean),
    ),
  ];
}

function rank(list: readonly string[], value: string): number {
  return list.indexOf(value);
}

export class AlertsMock {
  readonly alerts = new Map<number, AlertDetail>();
  private readonly started = Date.now();
  private readonly clock = Date.parse(`${ALERTS.meta.site_today}T15:00:00+03:00`);
  private nextEvent: number;

  constructor() {
    for (const item of capturedAlerts()) this.alerts.set(item.id, item);
    this.nextEvent =
      Math.max(0, ...[...this.alerts.values()].flatMap((item) => item.events.map((e) => e.id))) + 1;
  }

  /** The mock's "now": the sample day's 15:00 plus the time the test has run. */
  now(): Date {
    return new Date(this.clock + (Date.now() - this.started));
  }

  answer(call: RecordedCall, host: MockHost): Answer | null {
    const { method, path } = call;
    let match: RegExpExecArray | null;
    if (method === 'GET' && path === '/dashboard/summary') return this.dashboard(call, host);
    if (method === 'GET' && path === '/usage/company') {
      const account = requireAny(call, host, [
        'dashboard.usage',
        'usage.person',
        'usage.anonymous',
      ]);
      return Array.isArray(account) ? account : [200, copy(USAGE.company)];
    }
    if (method === 'GET' && path === '/alerts') return this.list(call, host);
    if (method === 'GET' && path === '/alerts/summary') {
      const account = host.requirePermission(call, 'alerts.read');
      return Array.isArray(account) ? account : [200, this.summary()];
    }
    if ((match = /^\/alerts\/(\d+)(\/state|\/notes)?$/.exec(path))) {
      const id = Number(match[1]);
      if (method === 'GET' && !match[2]) return this.detail(call, host, id);
      if (method === 'POST' && match[2] === '/state') return this.changeState(call, host, id);
      if (method === 'POST' && match[2] === '/notes') return this.addNote(call, host, id);
    }
    return null;
  }

  // -- reads ------------------------------------------------------------------------------------

  private list(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'alerts.read');
    if (Array.isArray(account)) return account;
    const q = call.query;
    const limit = Number(q.get('limit') ?? 50);
    const offset = Number(q.get('offset') ?? 0);
    const sort = q.get('sort') ?? 'time';
    const order = q.get('order') ?? 'desc';
    const states = values(q, 'state');
    const severities = values(q, 'severity');
    const rules = values(q, 'rule');
    if (!Number.isInteger(limit) || limit < 1 || limit > 200)
      return validation(host, 'limit', 'Input should be between 1 and 200');
    if (!Number.isInteger(offset) || offset < 0)
      return validation(host, 'offset', 'Input should be >= 0');
    if (!(SORTS as readonly string[]).includes(sort))
      return validation(host, 'sort', 'Input should be a known order');
    if (!['asc', 'desc'].includes(order))
      return validation(host, 'order', "Input should be 'asc' or 'desc'");
    for (const [name, list, known] of [
      ['state', states, STATES],
      ['severity', severities, SEVERITIES],
      ['rule', rules, null],
    ] as const) {
      if (
        list.length > MAX_FILTER_VALUES ||
        list.some((value) =>
          known ? !(known as readonly string[]).includes(value) : !/^[\w.:-]{1,64}$/.test(value),
        )
      )
        return validation(host, name, 'unknown value');
    }
    const from = q.get('from');
    const to = q.get('to');
    if (from && to && Date.parse(from) >= Date.parse(to))
      return validation(host, 'to', 'Input should be after from');
    const text = (q.get('q') ?? '').trim().toLocaleLowerCase('tr');
    const sign = order === 'asc' ? 1 : -1;
    const key = (item: AlertDetail): number | string => {
      if (sort === 'severity') return rank(SEVERITIES, item.severity);
      if (sort === 'state') return rank(STATES, item.state);
      if (sort === 'rule') return item.rule;
      return Date.parse(item[sort as 'time' | 'created_at' | 'updated_at']);
    };
    const compare = (a: number | string, b: number | string): number =>
      typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    const rows = [...this.alerts.values()]
      .filter((item) => states.length === 0 || states.includes(item.state))
      .filter((item) => severities.length === 0 || severities.includes(item.severity))
      .filter((item) => rules.length === 0 || rules.includes(item.rule))
      .filter((item) => !from || Date.parse(item.time) >= Date.parse(from))
      .filter((item) => !to || Date.parse(item.time) < Date.parse(to))
      .filter(
        (item) =>
          !text ||
          [
            item.title,
            item.rule,
            typeof item.details.source === 'string' ? item.details.source : '',
          ].some((value) => value.toLocaleLowerCase('tr').includes(text)),
      )
      .sort(
        (a, b) =>
          sign *
          (compare(key(a), key(b)) || Date.parse(a.time) - Date.parse(b.time) || a.id - b.id),
      );
    return [
      200,
      {
        items: rows.slice(offset, offset + limit).map(alertOut),
        total: rows.length,
        limit,
        offset,
      },
    ];
  }

  private detail(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = host.requirePermission(call, 'alerts.read');
    if (Array.isArray(account)) return account;
    const found = this.alerts.get(id);
    return found ? [200, copy(found)] : host.error(404, 'alert_not_found');
  }

  openBySeverity(): OpenBySeverity {
    const counts: OpenBySeverity = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
    for (const item of this.alerts.values()) if (item.state === 'open') counts[item.severity] += 1;
    return counts;
  }

  /** The five newest open or acknowledged alerts. */
  latest(): AlertOut[] {
    return [...this.alerts.values()]
      .filter((item) => ACTIVE.includes(item.state))
      .sort((a, b) => Date.parse(b.time) - Date.parse(a.time) || b.id - a.id)
      .slice(0, 5)
      .map(alertOut);
  }

  /** Mean seconds from `created_at` to the newest closing (active → closed) of closed alerts. */
  private meanTimeToClose(): number | null {
    const now = this.now().getTime();
    const seconds: number[] = [];
    for (const item of this.alerts.values()) {
      if (ACTIVE.includes(item.state)) continue;
      const closes = item.events.filter(
        (event) =>
          event.kind === 'state' &&
          event.from_state !== null &&
          ACTIVE.includes(event.from_state) &&
          event.to_state !== null &&
          !ACTIVE.includes(event.to_state) &&
          Date.parse(event.at) >= now - 30 * DAY_MS &&
          Date.parse(event.at) <= now,
      );
      const last = closes.at(-1);
      if (last)
        seconds.push(Math.max(0, (Date.parse(last.at) - Date.parse(item.created_at)) / 1000));
    }
    if (seconds.length === 0) return null;
    return Math.round(seconds.reduce((sum, value) => sum + value, 0) / seconds.length);
  }

  summary(): AlertSummary {
    return {
      open_by_severity: this.openBySeverity(),
      acknowledged: [...this.alerts.values()].filter((item) => item.state === 'acknowledged')
        .length,
      latest: this.latest(),
      mean_time_to_close_seconds_30d: this.meanTimeToClose(),
    };
  }

  private dashboard(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'dashboard.view');
    if (Array.isArray(account)) return account;
    const granted = new Set(host.permissionsOf(account));
    const sample = copy(DASHBOARD.admin);
    const summary: DashboardSummary = {
      generated_at: siteIso(this.now()),
      ingest: granted.has('sources.read') || granted.has('dashboard.view') ? sample.ingest : null,
      seal: granted.has('seal.read') ? sample.seal : null,
      credit: granted.has('seal.read') ? sample.credit : null,
      clock: granted.has('system.read') || granted.has('seal.read') ? sample.clock : null,
      alerts: granted.has('alerts.read')
        ? { open_by_severity: this.openBySeverity(), latest: this.latest() }
        : null,
      parse_failures:
        granted.has('sources.read') || granted.has('dashboard.view')
          ? (sample.parse_failures ?? null)
          : null,
    };
    return [200, summary];
  }

  // -- changes ----------------------------------------------------------------------------------

  /** Another active alert of the same problem as `alert`. */
  private otherActive(alert: AlertDetail): AlertDetail | undefined {
    const key = problemKey(alert);
    return [...this.alerts.values()]
      .filter((item) => item.id !== alert.id && ACTIVE.includes(item.state))
      .find((item) => problemKey(item) === key);
  }

  private event(fields: Omit<AlertEvent, 'id'>): AlertEvent {
    return { id: this.nextEvent++, ...fields };
  }

  private noteRefusal(host: MockHost, field: string, text: unknown): Answer | null {
    if (typeof text !== 'string' || text.trim().length === 0)
      return host.error(422, 'validation_error', [
        { loc: ['body', field], type: 'string_too_short', msg: 'String should not be empty' },
      ]);
    if (codePointLength(text.trim()) > NOTE_MAX)
      return host.error(422, 'validation_error', [
        {
          loc: ['body', field],
          type: 'string_too_long',
          msg: 'String should have at most 2000 characters',
        },
      ]);
    return null;
  }

  private changeState(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = host.requirePermission(call, 'alerts.manage');
    if (Array.isArray(account)) return account;
    const found = this.alerts.get(id);
    if (!found) return host.error(404, 'alert_not_found');
    const body = call.body as { state: string; note?: string | null };
    if (!(STATES as readonly string[]).includes(body.state))
      return host.error(422, 'validation_error', [
        { loc: ['body', 'state'], type: 'enum', msg: 'Input should be a known state' },
      ]);
    const target = body.state as AlertState;
    const previous = found.state;
    if (target === previous) return host.error(409, 'alert_state_unchanged', { state: target });
    if (!TRANSITIONS[previous].includes(target))
      return host.error(409, 'alert_transition_not_allowed', {
        from: previous,
        to: target,
        allowed: [...TRANSITIONS[previous]].sort(),
      });
    if (body.note !== undefined && body.note !== null) {
      const refusal = this.noteRefusal(host, 'note', body.note);
      if (refusal) return refusal;
    }
    if (ACTIVE.includes(target)) {
      const holder = this.otherActive(found);
      if (holder) return host.error(409, 'alert_key_active', { alert_id: holder.id });
    }
    const at = this.now().toISOString();
    found.events.push(
      this.event({
        kind: 'state',
        from_state: previous,
        to_state: target,
        text: null,
        by: account.username,
        at,
      }),
    );
    const note = body.note?.trim();
    if (note) {
      found.events.push(
        this.event({
          kind: 'note',
          from_state: null,
          to_state: null,
          text: note,
          by: account.username,
          at,
        }),
      );
      found.notes_count += 1;
    }
    found.state = target;
    found.updated_at = at;
    found.auto_resolved = false;
    delete found.details.auto_resolved;
    found.resolved_at = target === 'resolved' ? at : null;
    return [200, copy(found)];
  }

  private addNote(call: RecordedCall, host: MockHost, id: number): Answer {
    const account = host.requirePermission(call, 'alerts.manage');
    if (Array.isArray(account)) return account;
    const found = this.alerts.get(id);
    if (!found) return host.error(404, 'alert_not_found');
    const text = (call.body as { text: unknown }).text;
    const refusal = this.noteRefusal(host, 'text', text);
    if (refusal) return refusal;
    const event = this.event({
      kind: 'note',
      from_state: null,
      to_state: null,
      text: String(text).trim(),
      by: account.username,
      at: this.now().toISOString(),
    });
    found.events.push(event);
    found.notes_count += 1;
    found.updated_at = event.at;
    return [201, copy(event)];
  }
}
