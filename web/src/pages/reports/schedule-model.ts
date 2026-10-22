/**
 * Scheduled reports (spec 10.5, 12.3 item 12; group contract section 4): the schedule form, its
 * checks, the bodies sent to the API, and when a schedule runs next and which period that run
 * covers (no components here; unit-tested in `schedule-model.test.ts`).
 *
 * Times are site times (Europe/Istanbul, UTC+03:00 all year: `lib/site-day.ts`), whatever the
 * browser's time zone. The server computes `next_run_at` itself; the form's preview uses the same
 * rules so the user sees what a change means before saving it:
 *
 * - `end_of_day` runs every day at `time` and covers 00:00 → `time` of that day (so never at
 *   00:00: that period would be empty, the API refuses it);
 * - `full_day` runs every day at `time` and covers the previous day;
 * - `weekly` runs on `weekday` (1 = Monday) at `time` and covers the previous ISO week;
 * - `monthly` runs on `monthday` (1 … 28) at `time` and covers the previous month.
 */

import type {
  ReportFormats,
  ReportKind,
  RunRequest,
  ScheduleCreate,
  ScheduleOut,
  ScheduleUpdate,
} from '@/lib/api-types';
import { parseDay, parseTime } from '@/lib/dates';
import { codePointLength, type FieldErrors } from '@/lib/validation';

import { addRecipients } from './recipients-model';

export const REPORT_KINDS: readonly ReportKind[] = ['end_of_day', 'full_day', 'weekly', 'monthly'];

/** The seeded schedules' times (contract section 4), used when the kind of a form changes. */
export const KIND_DEFAULTS: Readonly<
  Record<ReportKind, { time: string; weekday: number | null; monthday: number | null }>
> = {
  end_of_day: { time: '18:30', weekday: null, monthday: null },
  full_day: { time: '07:00', weekday: null, monthday: null },
  weekly: { time: '07:15', weekday: 1, monthday: null },
  monthly: { time: '07:30', weekday: null, monthday: 1 },
};

export const NAME_MAX = 200;
export const MONTHDAY_MAX = 28;
const SITE_OFFSET_MS = 3 * 3_600_000;
const DAY_MS = 86_400_000;
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;

export function isReportKind(value: string | null | undefined): value is ReportKind {
  return (REPORT_KINDS as readonly (string | null | undefined)[]).includes(value);
}

// -- form -----------------------------------------------------------------------------------------

export interface ScheduleFormValues {
  name: string;
  kind: ReportKind;
  enabled: boolean;
  /** As typed, e.g. `7:15`; checked by {@link parseClock}. */
  time: string;
  /** `1` … `7` (weekly). */
  weekday: string;
  /** `1` … `28` (monthly). */
  monthday: string;
  recipients: string[];
  /** Text still in the recipients input (not yet a chip). */
  pending: string;
  formats: ReportFormats;
  personLevel: boolean;
  pseudonymize: boolean;
}

export type ScheduleField = 'name' | 'time' | 'weekday' | 'monthday' | 'recipients' | 'formats';

/** A new schedule: end of day, person-level detail on, pseudonyms off (owner decision). */
export function scheduleFormValues(schedule: ScheduleOut | null): ScheduleFormValues {
  if (!schedule) {
    const defaults = KIND_DEFAULTS.end_of_day;
    return {
      name: '',
      kind: 'end_of_day',
      enabled: true,
      time: defaults.time,
      weekday: '1',
      monthday: '1',
      recipients: [],
      pending: '',
      formats: { email_html: true, pdf: true, xlsx: false },
      personLevel: true,
      pseudonymize: false,
    };
  }
  return {
    name: schedule.name,
    kind: schedule.kind,
    enabled: schedule.enabled,
    time: schedule.time,
    weekday: String(schedule.weekday ?? 1),
    monthday: String(schedule.monthday ?? 1),
    recipients: [...schedule.recipients],
    pending: '',
    formats: { ...schedule.formats },
    personLevel: schedule.person_level,
    pseudonymize: schedule.pseudonymize,
  };
}

/** The form after choosing another kind: the time follows the kind's default unless edited. */
export function withKind(values: ScheduleFormValues, kind: ReportKind): ScheduleFormValues {
  const timeWasDefault = parseClock(values.time) === KIND_DEFAULTS[values.kind].time;
  return {
    ...values,
    kind,
    time: timeWasDefault ? KIND_DEFAULTS[kind].time : values.time,
  };
}

/** `7:15`, `07.15`, `7:15:00` → `07:15` (24 hours); else `null`. */
export function parseClock(text: string): string | null {
  const parsed = parseTime(text);
  return parsed ? parsed.slice(0, 5) : null;
}

function inRange(text: string, min: number, max: number): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) return null;
  const value = Number(text.trim());
  return value >= min && value <= max ? value : null;
}

/** The recipients the form would save: the chips plus valid addresses still in the input. */
export function formRecipients(values: ScheduleFormValues): string[] {
  return values.pending.trim()
    ? addRecipients(values.recipients, values.pending).recipients
    : values.recipients;
}

/** Problems of the form as i18n keys (empty when it may be sent). */
export function validateSchedule(values: ScheduleFormValues): FieldErrors<ScheduleField> {
  const errors: FieldErrors<ScheduleField> = {};
  const name = values.name.trim();
  if (!name) errors.name = 'validation.required';
  else if (codePointLength(name) > NAME_MAX) errors.name = 'validation.tooLong';
  else if (FORBIDDEN.test(name)) errors.name = 'validation.controlCharacters';
  const time = parseClock(values.time);
  if (!values.time.trim()) errors.time = 'validation.required';
  else if (time === null) errors.time = 'reports.validation.time';
  else if (values.kind === 'end_of_day' && time === '00:00')
    errors.time = 'reports.validation.endOfDayMidnight';
  if (values.kind === 'weekly' && inRange(values.weekday, 1, 7) === null)
    errors.weekday = 'validation.required';
  if (values.kind === 'monthly' && inRange(values.monthday, 1, MONTHDAY_MAX) === null)
    errors.monthday = 'reports.validation.monthday';
  if (values.pending.trim()) {
    const added = addRecipients(values.recipients, values.pending);
    if (added.problem) errors.recipients = added.problem;
  }
  if (!errors.recipients && values.enabled && formRecipients(values).length === 0)
    errors.recipients = 'reports.validation.recipientsForEnabled';
  if (!values.formats.email_html && !values.formats.pdf && !values.formats.xlsx)
    errors.formats = 'reports.validation.formats';
  return errors;
}

function schedulePart(values: ScheduleFormValues) {
  return {
    weekday: values.kind === 'weekly' ? inRange(values.weekday, 1, 7) : null,
    monthday: values.kind === 'monthly' ? inRange(values.monthday, 1, MONTHDAY_MAX) : null,
  };
}

/** `POST /reports/schedules` (a valid form). */
export function scheduleCreateBody(values: ScheduleFormValues): ScheduleCreate {
  const { weekday, monthday } = schedulePart(values);
  return {
    name: values.name.trim(),
    kind: values.kind,
    enabled: values.enabled,
    time: parseClock(values.time) ?? values.time,
    ...(weekday === null ? {} : { weekday }),
    ...(monthday === null ? {} : { monthday }),
    recipients: formRecipients(values),
    formats: { ...values.formats },
    person_level: values.personLevel,
    pseudonymize: values.pseudonymize,
  };
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/**
 * `PATCH /reports/schedules/{id}`: only what changed. The kind travels with its day fields
 * (`weekday`/`monthday`, `null` for the kinds without one) so the server never sees a weekly
 * report without a weekday.
 */
export function scheduleChanges(schedule: ScheduleOut, values: ScheduleFormValues): ScheduleUpdate {
  const changes: ScheduleUpdate = {};
  const name = values.name.trim();
  if (name !== schedule.name) changes.name = name;
  if (values.enabled !== schedule.enabled) changes.enabled = values.enabled;
  const time = parseClock(values.time) ?? values.time;
  if (time !== schedule.time) changes.time = time;
  const { weekday, monthday } = schedulePart(values);
  if (
    values.kind !== schedule.kind ||
    weekday !== schedule.weekday ||
    monthday !== schedule.monthday
  ) {
    changes.kind = values.kind;
    changes.weekday = weekday;
    changes.monthday = monthday;
  }
  const recipients = formRecipients(values);
  if (!sameList(recipients, schedule.recipients)) changes.recipients = recipients;
  const formats = values.formats;
  if (
    formats.email_html !== schedule.formats.email_html ||
    formats.pdf !== schedule.formats.pdf ||
    formats.xlsx !== schedule.formats.xlsx
  )
    changes.formats = { ...formats };
  if (values.personLevel !== schedule.person_level) changes.person_level = values.personLevel;
  if (values.pseudonymize !== schedule.pseudonymize) changes.pseudonymize = values.pseudonymize;
  return changes;
}

// -- next run and period --------------------------------------------------------------------------

export interface ScheduleTiming {
  kind: ReportKind;
  /** `HH:MM`. */
  time: string;
  weekday: number | null;
  monthday: number | null;
}

/** The timing of a form, `null` while it is not valid. */
export function formTiming(values: ScheduleFormValues): ScheduleTiming | null {
  const time = parseClock(values.time);
  if (!time) return null;
  const { weekday, monthday } = schedulePart(values);
  if (values.kind === 'weekly' && weekday === null) return null;
  if (values.kind === 'monthly' && monthday === null) return null;
  return { kind: values.kind, time, weekday, monthday };
}

/** Midnight (site time) of the site day that contains `instant`, as an instant. */
function siteMidnight(instant: number): number {
  const wall = instant + SITE_OFFSET_MS;
  return wall - (((wall % DAY_MS) + DAY_MS) % DAY_MS) - SITE_OFFSET_MS;
}

/** Year, month (1 … 12), day and ISO weekday (1 = Monday) of a site midnight. */
function siteParts(midnight: number): {
  year: number;
  month: number;
  day: number;
  weekday: number;
} {
  const wall = new Date(midnight + SITE_OFFSET_MS);
  return {
    year: wall.getUTCFullYear(),
    month: wall.getUTCMonth() + 1,
    day: wall.getUTCDate(),
    weekday: ((wall.getUTCDay() + 6) % 7) + 1,
  };
}

/** Site midnight of a calendar date (month may overflow: `month: 0` is December before). */
function siteDate(year: number, month: number, day: number): number {
  return Date.UTC(year, month - 1, day) - SITE_OFFSET_MS;
}

function minutesOf(time: string): number {
  const [hours = 0, minutes = 0] = time.split(':').map(Number);
  return hours * 60 + minutes;
}

function runsOn(timing: ScheduleTiming, midnight: number): boolean {
  const parts = siteParts(midnight);
  if (timing.kind === 'weekly') return parts.weekday === timing.weekday;
  if (timing.kind === 'monthly') return parts.day === timing.monthday;
  return true;
}

/** The first run strictly after `now` (searches two months ahead; `null` never happens). */
export function nextRun(timing: ScheduleTiming, now: Date): Date | null {
  const start = siteMidnight(now.getTime());
  const offset = minutesOf(timing.time) * 60_000;
  for (let day = 0; day <= 62; day += 1) {
    const midnight = start + day * DAY_MS;
    const at = midnight + offset;
    if (at > now.getTime() && runsOn(timing, midnight)) return new Date(at);
  }
  return null;
}

/** The period a run at `runAt` covers: `from` inclusive, `to` exclusive (instants). */
export function coveredPeriod(kind: ReportKind, runAt: Date): { from: Date; to: Date } {
  const midnight = siteMidnight(runAt.getTime());
  const parts = siteParts(midnight);
  switch (kind) {
    case 'end_of_day':
      return { from: new Date(midnight), to: runAt };
    case 'full_day':
      return { from: new Date(midnight - DAY_MS), to: new Date(midnight) };
    case 'weekly': {
      const monday = midnight - (parts.weekday - 1) * DAY_MS;
      return { from: new Date(monday - 7 * DAY_MS), to: new Date(monday) };
    }
    case 'monthly':
      return {
        from: new Date(siteDate(parts.year, parts.month - 1, 1)),
        to: new Date(siteDate(parts.year, parts.month, 1)),
      };
  }
}

export function scheduleTiming(schedule: ScheduleOut): ScheduleTiming {
  return {
    kind: schedule.kind,
    time: schedule.time,
    weekday: schedule.weekday,
    monthday: schedule.monthday,
  };
}

/**
 * The period a run of `kind` started now covers — what the API makes of "run now" and a preview
 * without `from`/`to` (end of day: today until now; full day: yesterday; the previous week or
 * month).
 */
export function ownPeriod(kind: ReportKind, now: Date): { from: Date; to: Date } {
  return coveredPeriod(kind, now);
}

// -- run now / preview: a custom period -----------------------------------------------------------

export interface PeriodValues {
  fromDay: string;
  fromTime: string;
  toDay: string;
  toTime: string;
}

export type PeriodField = keyof PeriodValues;

export const EMPTY_PERIOD: PeriodValues = { fromDay: '', fromTime: '', toDay: '', toTime: '' };

/** `2026-10-12` + `08:00:00` → `2026-10-12T08:00:00+03:00`. */
function siteInstant(day: string, time: string): string {
  return `${day}T${time}+03:00`;
}

/** Longest custom period the API accepts (days). */
export const MAX_PERIOD_DAYS = 92;

/** Problems of a custom period (i18n keys) and, when it is valid, its bounds for the API. */
export function checkPeriod(
  values: PeriodValues,
  now: Date,
): { errors: FieldErrors<PeriodField>; from?: string; to?: string } {
  const errors: FieldErrors<PeriodField> = {};
  const fromDay = parseDay(values.fromDay);
  const toDay = parseDay(values.toDay);
  const fromTime = values.fromTime.trim() ? parseTime(values.fromTime) : '00:00:00';
  const toTime = values.toTime.trim() ? parseTime(values.toTime) : '00:00:00';
  if (!values.fromDay.trim()) errors.fromDay = 'validation.required';
  else if (!fromDay) errors.fromDay = 'reports.validation.day';
  if (!values.toDay.trim()) errors.toDay = 'validation.required';
  else if (!toDay) errors.toDay = 'reports.validation.day';
  if (!fromTime) errors.fromTime = 'reports.validation.time';
  if (!toTime) errors.toTime = 'reports.validation.time';
  if (Object.keys(errors).length > 0 || !fromDay || !toDay || !fromTime || !toTime)
    return { errors };
  const from = siteInstant(fromDay, fromTime);
  let to = siteInstant(toDay, toTime);
  if (!values.toTime.trim()) {
    // No end time: the whole last day — up to now when that day is today.
    const end = Date.parse(to) + DAY_MS;
    const minute = Math.floor(now.getTime() / 60_000) * 60_000;
    to = new Date(Math.min(end, minute)).toISOString();
  }
  if (Date.parse(to) <= Date.parse(from)) errors.toDay = 'reports.validation.periodOrder';
  else if (Date.parse(to) > now.getTime()) errors.toDay = 'reports.validation.periodFuture';
  else if (Date.parse(to) - Date.parse(from) > MAX_PERIOD_DAYS * DAY_MS)
    errors.toDay = 'reports.validation.periodTooLong';
  return Object.keys(errors).length > 0 ? { errors } : { errors, from, to };
}

/** `POST /reports/schedules/{id}/run`: the schedule's own period unless a custom one is given. */
export function runBody(send: boolean, period?: { from: string; to: string }): RunRequest {
  return period ? { from: period.from, to: period.to, send } : { send };
}
