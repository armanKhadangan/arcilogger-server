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

export interface WorkHours {
  /** ISO weekdays, 1 = Monday. */
  days: number[];
  start: string;
  end: string;
}

export interface ServerSeconds {
  server: string;
  seconds: string;
}

export type Draft = boolean | string | string[] | WorkHours | ServerSeconds[] | EgressRow[];

/** Settings with an editor of their own. */
export const WORK_HOURS_KEY = 'usage.work_hours';
export const HOLIDAYS_KEY = 'usage.holidays';
export const SERVER_SECONDS_KEY = 'lease.server_seconds';
export const SEVERITY_KEY = 'alerts.email_min_severity';
export const TOTP_ROLES_KEY = 'auth.totp_required_roles';
/** Lists of e-mail addresses. */
const EMAIL_LISTS = new Set(['alerts.email_recipients']);
/** The alert severities, lowest first (`arcilogger_db.models.ALERT_SEVERITIES`). */
export const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'] as const;
/** Spec 13: these roles must use two-factor authentication; more may be added. */
export const TOTP_MANDATORY_ROLES = ['admin', 'auditor'] as const;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
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

/** The text of a draft kept as text (numbers, lists, JSON). */
export function draftText(draft: Draft): string {
  return typeof draft === 'string' ? draft : '';
}

function listItems(text: string): string[] {
  return text
    .split(/[\n,;]/)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

/** The draft an editor starts from. */
export function toDraft(setting: Pick<SettingOut, 'key' | 'type' | 'value'>): Draft {
  const { key, type, value } = setting;
  if (key === WORK_HOURS_KEY && isWorkHours(value))
    return { days: [...value.days], start: value.start, end: value.end };
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

/** The value a draft stands for, or the i18n key of its problem. */
export function fromDraft(
  setting: Pick<SettingOut, 'key' | 'type' | 'min' | 'max'>,
  draft: Draft,
): Parsed {
  const { key, type } = setting;
  if (key === WORK_HOURS_KEY && isWorkHours(draft)) {
    if (draft.days.length === 0) return { error: 'settings.application.errors.workDays' };
    if (!HHMM.test(draft.start) || !HHMM.test(draft.end))
      return { error: 'settings.application.errors.workTime' };
    if (draft.start >= draft.end) return { error: 'settings.application.errors.workOrder' };
    return {
      value: {
        days: [...new Set(draft.days)].sort((a, b) => a - b),
        start: draft.start,
        end: draft.end,
      },
    };
  }
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
      if (seconds === null || seconds < 60)
        return { error: 'settings.application.errors.serverSeconds' };
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
      const items = listItems(draftText(draft));
      if (EMAIL_LISTS.has(key) && items.some((item) => !EMAIL_PATTERN.test(item)))
        return { error: 'validation.email' };
      return { value: items };
    }
    case 'integer_list': {
      const items = listItems(draftText(draft));
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

/** A value as one line of text (the default, the old value of a conflict). */
export function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.join(', ');
  if (isWorkHours(value)) return `${value.days.join(',')} ${value.start}–${value.end}`;
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value);
    return entries.length === 0 ? '—' : entries.map(([k, v]) => `${k}: ${String(v)}`).join(', ');
  }
  if (typeof value === 'boolean') return value ? '✓' : '✗';
  if (value === '' || value === null || value === undefined) return '—';
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—';
}
