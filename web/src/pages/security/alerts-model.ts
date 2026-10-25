/**
 * The alert workflow of spec 11 and the state of the "Güvenlik" page that lives in the address bar
 * (no components here; unit-tested in `alerts-model.test.ts`).
 *
 * Workflow (spec 11 names; the API's `arcilogger_worker.alerts.store.TRANSITIONS`): Yeni (`open`)
 * → İnceleniyor (`acknowledged`), Kapandı (`resolved`) or Yanlış alarm (`dismissed`); İnceleniyor
 * → any other; Kapandı ↔ Yanlış alarm; a closed alert back to Yeni (re-open) only while no other
 * active alert holds the same problem — the check opens a new alert when a problem persists after
 * it was closed (409 `alert_key_active` names it); for a licence alert only once the situation
 * changes. The API checks every change again: 409 `alert_state_unchanged` /
 * `alert_transition_not_allowed` when someone else (or the system) changed the alert since the
 * screen showed it.
 *
 * The address keeps the filters, the sort order and the open alert (`?alert=17`) so a view can be
 * linked from the dashboard or an e-mail; never the search text (it may hold an IP address or a
 * name: KVKK data minimisation, as on the devices page).
 */

import type { SortingState } from '@tanstack/react-table';

import type {
  AlertFilters,
  AlertOut,
  AlertSeverity,
  AlertSort,
  AlertState,
  SortOrder,
} from '@/lib/api-types';
import type { Lang } from '@/lib/format';
import { parseId } from '@/lib/display';
import { siteDayEnd, siteDayStart } from '@/lib/site-day';
import { codePointLength } from '@/lib/validation';

export const ALERT_STATES: readonly AlertState[] = [
  'open',
  'acknowledged',
  'resolved',
  'dismissed',
];
export const ALERT_SEVERITIES: readonly AlertSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];
/** The orders the API offers; the list's columns use these four. */
export const ALERT_SORTS: readonly AlertSort[] = ['time', 'severity', 'state', 'rule'];

/** The changes a person may make, per current state (the API's rule). */
export const TRANSITIONS: Readonly<Record<AlertState, readonly AlertState[]>> = {
  open: ['acknowledged', 'resolved', 'dismissed'],
  acknowledged: ['resolved', 'dismissed', 'open'],
  resolved: ['open', 'dismissed'],
  dismissed: ['open', 'resolved'],
};

export type AlertAction =
  'acknowledge' | 'resolve' | 'dismiss' | 'release' | 'reopen' | 'markResolved' | 'markDismissed';

export function isAlertState(value: string | null | undefined): value is AlertState {
  return (ALERT_STATES as readonly (string | null | undefined)[]).includes(value);
}

export function isSeverity(value: string | null | undefined): value is AlertSeverity {
  return (ALERT_SEVERITIES as readonly (string | null | undefined)[]).includes(value);
}

/** The states an alert in `state` may be moved to. */
export function nextStates(state: AlertState): readonly AlertState[] {
  return TRANSITIONS[state];
}

export function canTransition(from: AlertState, to: AlertState): boolean {
  return TRANSITIONS[from].includes(to);
}

/**
 * The button of a change: "İncelemeye al", "Uyarıyı kapat", "Yanlış alarm", "İncelemeyi bırak"
 * (İnceleniyor → Yeni), "Yeniden aç" (a closed alert), and between the two closed states
 * "Kapandı olarak işaretle" / "Yanlış alarm olarak işaretle".
 */
export function actionOf(from: AlertState, to: AlertState): AlertAction {
  const closed = from === 'resolved' || from === 'dismissed';
  if (to === 'acknowledged') return 'acknowledge';
  if (to === 'open') return closed ? 'reopen' : 'release';
  if (to === 'resolved') return closed ? 'markResolved' : 'resolve';
  return closed ? 'markDismissed' : 'dismiss';
}

/**
 * Closing an active alert by hand while its problem persists makes the check open a new one: the
 * page says so next to these buttons.
 */
export function closesActive(from: AlertState, to: AlertState): boolean {
  return isActive(from) && (to === 'resolved' || to === 'dismissed');
}

/**
 * The licence alerts (`arcilogger_worker.alerts.license`): a licence situation can last for good,
 * so one a person closed is not raised again for the same situation, only for the next one.
 */
export const LICENSE_RULES: readonly string[] = [
  'R20-license',
  'R20-license-limit',
  'R20-license-maintenance',
];

/** The hint next to the buttons that close an active alert of `rule` (an i18n key). */
export function closeHintKey(rule: string): string {
  return LICENSE_RULES.includes(rule)
    ? 'security.actions.closeHintLicense'
    : 'security.actions.closeHint';
}

/** Reason codes of the checks' automatic changes (`by: "system"`) the page has a text for. */
export const SYSTEM_REASONS: readonly string[] = [
  'messages_again',
  'not_monitored',
  'condition_cleared',
  'below_threshold',
  'credit_ok',
  'clock_ok',
  'job_ok',
  'no_recent_runs',
  'backup_ok',
  'backup_found',
  'replica_ok',
  'replica_disabled',
  'verified_again',
  'sealed',
  'sealed_again',
  'skipped',
  'deleted',
  'not_pending',
  'email_sent',
  'lease_rebuild',
  'conflict_found_again',
  'licensed',
  'license_changed',
  'limit_ok',
  'maintenance_renewed',
];

/** The active alert a refused re-open points to (`details.alert_id` of 409 `alert_key_active`). */
export function activeAlertOf(details: unknown): number | null {
  if (typeof details !== 'object' || details === null) return null;
  const id = (details as { alert_id?: unknown }).alert_id;
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** Open or being investigated: counted as open work. */
export function isActive(state: AlertState): boolean {
  return state === 'open' || state === 'acknowledged';
}

/** Longest note the API accepts (characters). */
export const NOTE_MAX = 2000;
/** Format (bidi, zero-width) and surrogate characters. */
const INVISIBLE = /[\p{Cf}\p{Cs}]/u;

/** Control characters other than tab and line breaks, or invisible formatting characters. */
function hasForbiddenCharacters(text: string): boolean {
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if ((code < 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d) || code === 0x7f)
      return true;
  }
  return INVISIBLE.test(text);
}

/**
 * Problem of a note (an i18n key), `undefined` when it may be sent. `optional`: an empty note is
 * fine (the note of a state change); line breaks and tabs are allowed.
 */
export function noteProblem(text: string, optional = false): string | undefined {
  const value = text.trim();
  if (!value) return optional ? undefined : 'validation.required';
  if (codePointLength(value) > NOTE_MAX) return 'security.validation.noteTooLong';
  if (hasForbiddenCharacters(value)) return 'validation.controlCharacters';
  return undefined;
}

// -- the page's address -----------------------------------------------------------------------

/** Filters and sort order of the list (everything but the search text). */
export interface AlertView {
  state: AlertState | null;
  severity: AlertSeverity | null;
  /** Rule id, e.g. `R15` (empty: every rule). */
  rule: string;
  /** Site days `YYYY-MM-DD`, inclusive. */
  from: string | null;
  to: string | null;
  sort: AlertSort;
  order: SortOrder;
}

export const DEFAULT_ALERT_VIEW: AlertView = {
  state: null,
  severity: null,
  rule: '',
  from: null,
  to: null,
  sort: 'time',
  order: 'desc',
};

const KEYS = ['state', 'severity', 'rule', 'from', 'to', 'sort', 'order'] as const;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const RULE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

function day(value: string | null): string | null {
  if (!value || !DAY.test(value)) return null;
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value ? null : value;
}

/** A rule id as typed (`r15` → `R15`, blanks removed), `''` when it is not one. */
export function normalizeRule(text: string): string {
  const value = text.trim();
  if (!RULE.test(value)) return '';
  return /^r\d/i.test(value) ? `R${value.slice(1)}` : value;
}

export function alertViewFromSearch(params: URLSearchParams): AlertView {
  const state = params.get('state');
  const severity = params.get('severity');
  const sort = params.get('sort');
  const order = params.get('order');
  let from = day(params.get('from'));
  let to = day(params.get('to'));
  if (from && to && from > to) [from, to] = [to, from];
  return {
    state: isAlertState(state) ? state : null,
    severity: isSeverity(severity) ? severity : null,
    rule: normalizeRule(params.get('rule') ?? ''),
    from,
    to,
    sort: (ALERT_SORTS as readonly (string | null)[]).includes(sort)
      ? (sort as AlertSort)
      : DEFAULT_ALERT_VIEW.sort,
    order: order === 'asc' || order === 'desc' ? order : DEFAULT_ALERT_VIEW.order,
  };
}

/** `params` with `view` written into it (defaults left out); other parameters (`alert`) stay. */
export function alertViewToSearch(view: AlertView, params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of KEYS) next.delete(key);
  if (view.state) next.set('state', view.state);
  if (view.severity) next.set('severity', view.severity);
  if (view.rule) next.set('rule', view.rule);
  if (view.from) next.set('from', view.from);
  if (view.to) next.set('to', view.to);
  if (view.sort !== DEFAULT_ALERT_VIEW.sort || view.order !== DEFAULT_ALERT_VIEW.order) {
    next.set('sort', view.sort);
    next.set('order', view.order);
  }
  return next;
}

export function isAlertViewFiltered(view: AlertView, search: string): boolean {
  return (
    search.trim() !== '' ||
    view.state !== null ||
    view.severity !== null ||
    view.rule !== '' ||
    view.from !== null ||
    view.to !== null
  );
}

function localDay(value: string): Date {
  const [year = 1970, month = 1, date = 1] = value.split('-').map(Number);
  return new Date(year, month - 1, date, 12);
}

/** The API's query parameters for one page of the view (days as site-time bounds). */
export function alertQuery(
  view: AlertView,
  q: string,
  page: number,
  pageSize: number,
): AlertFilters {
  const filters: AlertFilters = {
    sort: view.sort,
    order: view.order,
    limit: pageSize,
    offset: page * pageSize,
  };
  if (view.state) filters.state = view.state;
  if (view.severity) filters.severity = view.severity;
  if (view.rule) filters.rule = view.rule;
  if (q) filters.q = q;
  if (view.from) filters.from = siteDayStart(localDay(view.from));
  if (view.to) filters.to = siteDayEnd(localDay(view.to));
  return filters;
}

export function sortingOfAlerts(view: AlertView): SortingState {
  return [{ id: view.sort, desc: view.order === 'desc' }];
}

/** The view sorted as the table header asks (a third click returns to the default order). */
export function alertViewWithSorting(view: AlertView, sorting: SortingState): AlertView {
  const first = sorting[0];
  if (!first || !(ALERT_SORTS as readonly string[]).includes(first.id))
    return { ...view, sort: DEFAULT_ALERT_VIEW.sort, order: DEFAULT_ALERT_VIEW.order };
  return { ...view, sort: first.id as AlertSort, order: first.desc ? 'desc' : 'asc' };
}

/** The security page with one alert open (dashboard and e-mail links). */
export function alertHref(id: number): string {
  return `/security?alert=${id}`;
}

/** The open alert of the address (`?alert=17`), `null` without a valid one. */
export function alertIdFrom(params: URLSearchParams): number | null {
  return parseId(params.get('alert'));
}

// -- texts ----------------------------------------------------------------------------------------

/** The alert's summary in the interface language (its title when the summary is empty). */
export function alertSummaryText(
  alert: Pick<AlertOut, 'summary_tr' | 'summary_en' | 'title'>,
  lang: Lang,
): string {
  const summary = lang === 'en' ? alert.summary_en : alert.summary_tr;
  return summary.trim() ? summary : alert.title;
}

export function ruleName(
  alert: Pick<AlertOut, 'rule_name_tr' | 'rule_name_en' | 'rule'>,
  lang: Lang,
) {
  const name = lang === 'en' ? alert.rule_name_en : alert.rule_name_tr;
  return name.trim() ? name : alert.rule;
}

/**
 * The rows of an alert's `details` for a two-column table: nested values as compact JSON, `null`
 * as a dash. Keys keep the API's order.
 */
export function detailRows(details: Record<string, unknown>): [string, string][] {
  return Object.entries(details).map(([key, value]) => [
    key,
    value === null || value === undefined
      ? '—'
      : typeof value === 'string'
        ? value
        : typeof value === 'number' || typeof value === 'boolean'
          ? String(value)
          : JSON.stringify(value),
  ]);
}
