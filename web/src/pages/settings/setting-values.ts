/**
 * The editable form of a database setting (`GET /settings`) and back (contract 2.13).
 *
 * Every setting has the JSON kind of its shipped default (`type`); the editor of a kind keeps a
 * draft (text for numbers and lists, so a half-typed value stays as typed) and turns it into the
 * value `PATCH /settings` sends, or names the problem. A few settings have their own editors:
 * the work hours (`usage.work_hours`), the holidays (`usage.holidays`), the lease time per DHCP
 * server (`lease.server_seconds`), the internet exits (`nat.egresses`, `egress-model.ts`), the
 * alert e-mail's lowest severity and the roles that must use two-factor authentication. The
 * server validates every value again (`validate_setting`).
 */

import type { SettingOut, SettingValue } from '@/lib/settings-types';
import { EMAIL_PATTERN } from '@/lib/validation';

import { EGRESSES_KEY, rowsFromValue, valueFromRows, type EgressRow } from './egress-model';
import { integerError, parseInteger } from './settings-validation';

/** A break of the work hours (`HH:MM`, end excluded). */
export interface WorkBreak {
  start: string;
  end: string;
}

/** The hours of one weekday that differ from the general ones (`by_day`). */
export interface DayHours {
  start: string;
  end: string;
  /** Absent: no breaks that day (the general breaks are not inherited). */
  breaks?: WorkBreak[];
}

/** `usage.work_hours` (contract W5 §6 K1): `breaks` and `by_day` are optional. */
export interface WorkHours {
  /** ISO weekdays, 1 = Monday. */
  days: number[];
  start: string;
  end: string;
  breaks?: WorkBreak[];
  /** ISO weekday `"1"`…`"7"` (a work day) → its own hours. */
  by_day?: Record<string, DayHours>;
}

/** One row of the work-hours editor: a weekday with its hours. */
export interface WorkDayDraft {
  work: boolean;
  start: string;
  end: string;
  breaks: WorkBreak[];
}

/** The work-hours editor's draft: seven rows, Monday first. */
export interface WorkWeek {
  week: WorkDayDraft[];
}

export interface ServerSeconds {
  server: string;
  seconds: string;
}

export type Draft = boolean | string | string[] | WorkWeek | ServerSeconds[] | EgressRow[];

/** Settings with an editor of their own. */
export const WORK_HOURS_KEY = 'usage.work_hours';
export const HOLIDAYS_KEY = 'usage.holidays';
export const SERVER_SECONDS_KEY = 'lease.server_seconds';
/** Each server of `lease.server_seconds` has the bounds of this setting (W5-G-19). */
export const LEASE_DEFAULT_KEY = 'lease.default_seconds';
/** The least lease time of a server when the group does not carry `lease.default_seconds`. */
const SERVER_SECONDS_MIN = 60;
export const SEVERITY_KEY = 'alerts.email_min_severity';
export const TOTP_ROLES_KEY = 'auth.totp_required_roles';
/** Lists of e-mail addresses. */
const EMAIL_LISTS = new Set(['alerts.email_recipients']);
/** The alert severities, lowest first (`arcilogger_db.models.ALERT_SEVERITIES`). */
export const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'] as const;
/** Spec 13: these roles must use two-factor authentication; more may be added. */
export const TOTP_MANDATORY_ROLES = ['admin', 'auditor'] as const;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
/** The server's limits of the breaks (`arcilogger_db.settings`, W5-K). */
export const WORK_BREAKS_MAX = 6;
export const WORK_BREAK_MIN_MINUTES = 5;
const HOLIDAY = /^(?:(\d{4})-)?(\d{2})-(\d{2})$/;

/** The control id of a setting (also the target of "focus the first error"). */
export function settingControlId(key: string): string {
  return `setting-${key.replace(/[^A-Za-z0-9]/g, '-')}`;
}

export function isWorkHours(value: unknown): value is WorkHours {
  return (
    typeof value === 'object' &&
    value !== null &&
    Array.isArray((value as WorkHours).days) &&
    typeof (value as WorkHours).start === 'string' &&
    typeof (value as WorkHours).end === 'string'
  );
}

export function isWorkWeek(draft: unknown): draft is WorkWeek {
  return (
    typeof draft === 'object' &&
    draft !== null &&
    Array.isArray((draft as WorkWeek).week) &&
    (draft as WorkWeek).week.length === 7
  );
}

/** `HH:MM` → minutes since 00:00. */
export function minutesOf(time: string): number {
  return Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
}

/** The editor's week of a stored value: each weekday with its own or the general hours. */
export function weekOf(value: WorkHours): WorkWeek {
  const week = [1, 2, 3, 4, 5, 6, 7].map((day): WorkDayDraft => {
    const own = value.by_day?.[String(day)];
    const hours = own ?? { start: value.start, end: value.end, breaks: value.breaks };
    return {
      work: value.days.includes(day),
      start: hours.start,
      end: hours.end,
      breaks: (hours.breaks ?? []).map((item) => ({ ...item })),
    };
  });
  return { week };
}

/** The part of each minute of a day row, for the strip preview (`null` for a day off). */
export function dayParts(
  row: WorkDayDraft,
): { start: number; end: number; part: 'work' | 'break' }[] {
  if (!row.work || !HHMM.test(row.start) || !HHMM.test(row.end) || row.start >= row.end) return [];
  const first = minutesOf(row.start);
  const last = minutesOf(row.end);
  const pauses = row.breaks
    .filter((item) => HHMM.test(item.start) && HHMM.test(item.end) && item.start < item.end)
    .map((item) => ({
      start: Math.max(first, minutesOf(item.start)),
      end: Math.min(last, minutesOf(item.end)),
    }))
    .filter((item) => item.start < item.end)
    .sort((a, b) => a.start - b.start);
  const parts: { start: number; end: number; part: 'work' | 'break' }[] = [];
  let at = first;
  for (const pause of pauses) {
    if (pause.start > at) parts.push({ start: at, end: pause.start, part: 'work' });
    parts.push({ start: Math.max(at, pause.start), end: pause.end, part: 'break' });
    at = Math.max(at, pause.end);
  }
  if (at < last) parts.push({ start: at, end: last, part: 'work' });
  return parts.filter((item) => item.start < item.end);
}

/** The text of a draft kept as text (numbers, lists, JSON). */
export function draftText(draft: Draft): string {
  return typeof draft === 'string' ? draft : '';
}

/** The entries of a list kept as text: one per line, or separated by commas or semicolons. */
export function stringListItems(text: string): string[] {
  return text
    .split(/[\n,;]/)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

/** The draft an editor starts from. */
export function toDraft(setting: Pick<SettingOut, 'key' | 'type' | 'value'>): Draft {
  const { key, type, value } = setting;
  if (key === WORK_HOURS_KEY && isWorkHours(value)) return weekOf(value);
  if (key === HOLIDAYS_KEY || key === TOTP_ROLES_KEY)
    return Array.isArray(value) ? value.map(String) : [];
  if (key === SERVER_SECONDS_KEY && typeof value === 'object' && !Array.isArray(value))
    return Object.entries(value).map(([server, seconds]) => ({
      server,
      seconds: String(seconds as number),
    }));
  if (key === EGRESSES_KEY) return rowsFromValue(value);
  switch (type) {
    case 'boolean':
      return value === true;
    case 'integer':
      return typeof value === 'number' ? String(value) : '';
    case 'string':
      return typeof value === 'string' ? value : '';
    case 'string_list':
    case 'integer_list':
      return Array.isArray(value) ? value.join('\n') : '';
    case 'object':
      return JSON.stringify(value, null, 2);
  }
}

/** A holiday entry: `MM-DD` every year, `YYYY-MM-DD` once. */
export function validHoliday(text: string): boolean {
  const match = HOLIDAY.exec(text);
  if (!match) return false;
  const year = match[1] ? Number(match[1]) : 2000; // a leap year: 02-29 is a date
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export type Parsed =
  | { value: SettingValue; error?: undefined }
  /** `params` fill the placeholders of the error's text (a limit, a type name). */
  | { value?: undefined; error: string; params?: Record<string, string | number> };

/**
 * The settings of a group with the bounds the browser checks. The API states the bounds of each
 * server's lease time (`LEASE_SERVER_SECONDS`) as those of `lease.default_seconds` and lists
 * none for `lease.server_seconds`, an object (W5-G-19): it takes them from the setting beside it.
 */
export function withServerBounds(settings: readonly SettingOut[]): readonly SettingOut[] {
  const lease = settings.find((setting) => setting.key === LEASE_DEFAULT_KEY);
  if (!lease) return settings;
  return settings.map((setting) =>
    setting.key === SERVER_SECONDS_KEY && setting.min === null && setting.max === null
      ? { ...setting, min: lease.min, max: lease.max }
      : setting,
  );
}

/** The value a draft stands for, or the i18n key of its problem. */
export function fromDraft(
  setting: Pick<SettingOut, 'key' | 'type' | 'min' | 'max'>,
  draft: Draft,
): Parsed {
  const { key, type } = setting;
  if (key === WORK_HOURS_KEY && isWorkWeek(draft)) return workHoursOf(draft);
  if (key === HOLIDAYS_KEY && Array.isArray(draft)) {
    const days = (draft as string[]).map((item) => item.trim());
    if (days.some((day) => !validHoliday(day)))
      return { error: 'settings.application.errors.holiday' };
    return { value: [...new Set(days)].sort() };
  }
  if (key === TOTP_ROLES_KEY && Array.isArray(draft)) {
    const roles = draft as string[];
    if (TOTP_MANDATORY_ROLES.some((role) => !roles.includes(role)))
      return { error: 'settings.application.errors.totpRoles' };
    return { value: [...roles] };
  }
  if (key === SERVER_SECONDS_KEY && Array.isArray(draft)) {
    const rows = draft as ServerSeconds[];
    const value: Record<string, number> = {};
    for (const row of rows) {
      const server = row.server.trim();
      const seconds = parseInteger(row.seconds);
      if (!server) return { error: 'settings.application.errors.serverName' };
      const low = setting.min ?? SERVER_SECONDS_MIN;
      if (seconds === null || integerError(row.seconds, low, setting.max))
        return setting.max === null
          ? { error: 'settings.application.errors.serverSecondsMin', params: { min: low } }
          : {
              error: 'settings.application.errors.serverSeconds',
              params: { min: low, max: setting.max },
            };
      value[server] = seconds;
    }
    return { value };
  }
  if (key === EGRESSES_KEY && Array.isArray(draft)) {
    const result = valueFromRows(draft as EgressRow[]);
    return result.refusal
      ? { error: `network.egress.codes.${result.refusal.code}`, params: result.refusal.facts }
      : { value: result.value };
  }
  switch (type) {
    case 'boolean':
      return { value: draft === true };
    case 'integer': {
      const text = draftText(draft);
      const problem = integerError(text, setting.min ?? 0, setting.max);
      return problem ? { error: problem } : { value: parseInteger(text) ?? 0 };
    }
    case 'string':
      return { value: draftText(draft).trim() };
    case 'string_list': {
      const items = stringListItems(draftText(draft));
      if (EMAIL_LISTS.has(key) && items.some((item) => !EMAIL_PATTERN.test(item)))
        return { error: 'validation.email' };
      return { value: items };
    }
    case 'integer_list': {
      const items = stringListItems(draftText(draft));
      const numbers = items.map(parseInteger);
      if (numbers.some((item) => item === null || item < 0))
        return { error: 'settings.application.errors.integerList' };
      return { value: numbers as number[] };
    }
    case 'object':
      try {
        const value = JSON.parse(draftText(draft)) as unknown;
        if (typeof value !== 'object' || value === null || Array.isArray(value))
          return { error: 'settings.application.errors.object' };
        return { value: value as Record<string, unknown> };
      } catch {
        return { error: 'settings.application.errors.object' };
      }
  }
}

/** A work day's hours, checked as the server checks them (`_work_window`, `_work_breaks`). */
function checkDay(row: WorkDayDraft, day: number): Parsed | DayHours {
  const params = { day };
  if (!HHMM.test(row.start) || !HHMM.test(row.end))
    return { error: 'settings.application.errors.workTime', params };
  if (row.start >= row.end) return { error: 'settings.application.errors.workOrder', params };
  if (row.breaks.length > WORK_BREAKS_MAX)
    return {
      error: 'settings.application.errors.breakCount',
      params: { ...params, max: WORK_BREAKS_MAX },
    };
  const breaks: WorkBreak[] = [];
  for (const item of row.breaks) {
    if (!HHMM.test(item.start) || !HHMM.test(item.end))
      return { error: 'settings.application.errors.breakTime', params };
    if (minutesOf(item.end) - minutesOf(item.start) < WORK_BREAK_MIN_MINUTES)
      return {
        error: 'settings.application.errors.breakShort',
        params: { ...params, min: WORK_BREAK_MIN_MINUTES },
      };
    if (item.start < row.start || item.end > row.end)
      return { error: 'settings.application.errors.breakOutside', params };
    breaks.push({ start: item.start, end: item.end });
  }
  breaks.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
  for (let index = 1; index < breaks.length; index += 1)
    if ((breaks[index]?.start ?? '') < (breaks[index - 1]?.end ?? ''))
      return { error: 'settings.application.errors.breakOverlap', params };
  return breaks.length > 0
    ? { start: row.start, end: row.end, breaks }
    : { start: row.start, end: row.end };
}

/**
 * The stored value of the editor's week (contract W5 §6 K1). The general hours are those most
 * work days share (on a tie, the earliest day's); a work day with other hours gets a `by_day`
 * entry, and a day whose hours equal the general ones gets none. Empty `breaks` and `by_day` are
 * left out, as the server stores them.
 */
export function workHoursOf(draft: WorkWeek): Parsed {
  const days: number[] = [];
  const hours = new Map<number, DayHours>();
  for (const [index, row] of draft.week.entries()) {
    if (!row.work) continue;
    const day = index + 1;
    const checked = checkDay(row, day);
    if ('error' in checked && checked.error !== undefined) return checked;
    days.push(day);
    hours.set(day, checked as DayHours);
  }
  if (days.length === 0) return { error: 'settings.application.errors.workDays' };
  const counts = new Map<string, { count: number; hours: DayHours }>();
  for (const day of days) {
    const own = hours.get(day) as DayHours;
    const key = canonical(own);
    const seen = counts.get(key);
    counts.set(key, { count: (seen?.count ?? 0) + 1, hours: own });
  }
  let general: DayHours | undefined;
  let best = 0;
  for (const { count, hours: candidate } of counts.values())
    if (count > best) {
      best = count;
      general = candidate;
    }
  const shared = general as DayHours;
  const value: WorkHours = { days, start: shared.start, end: shared.end };
  if (shared.breaks) value.breaks = shared.breaks;
  const byDay: Record<string, DayHours> = {};
  for (const day of days) {
    const own = hours.get(day) as DayHours;
    if (canonical(own) !== canonical(shared)) byDay[String(day)] = own;
  }
  if (Object.keys(byDay).length > 0) value.by_day = byDay;
  return { value: value as unknown as SettingValue };
}

/** JSON with sorted object keys (two values are the same setting value). */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (typeof value === 'object' && value !== null)
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`)
      .join(',')}}`;
  return JSON.stringify(value);
}

export function sameValue(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b);
}

function hoursText(hours: DayHours): string {
  const pauses = (hours.breaks ?? []).map((item) => `${item.start}–${item.end}`);
  return pauses.length > 0
    ? `${hours.start}–${hours.end} (${pauses.join(', ')})`
    : `${hours.start}–${hours.end}`;
}

/** `1,2,3,4,5 08:00–18:00 (12:00–13:00); 6: 09:00–13:00` (the breaks in brackets). */
function workHoursText(value: WorkHours): string {
  const general = `${value.days.join(',')} ${hoursText(value)}`;
  const own = Object.entries(value.by_day ?? {}).map(
    ([day, hours]) => `${day}: ${hoursText(hours)}`,
  );
  return [general, ...own].join('; ');
}

/** A value as one line of text (the default, the old value of a conflict). */
export function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.join(', ');
  if (isWorkHours(value)) return workHoursText(value);
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value);
    return entries.length === 0 ? '—' : entries.map(([k, v]) => `${k}: ${String(v)}`).join(', ');
  }
  if (typeof value === 'boolean') return value ? '✓' : '✗';
  if (value === '' || value === null || value === undefined) return '—';
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—';
}
