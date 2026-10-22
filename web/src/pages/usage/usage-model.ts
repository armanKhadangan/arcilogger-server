/**
 * State of the "Kullanım" page that lives in the address bar — tab, period, hours filter and the
 * selected department, person/device, site or category — and the site-day arithmetic of the
 * period presets (no components here; unit-tested in `usage-model.test.ts`).
 *
 * Days are site days (Europe/Istanbul) written `YYYY-MM-DD`, as the API takes them. A drill-down
 * (department → person → site …) is a new history entry, so the browser's Back button returns to
 * the list; changing the period or the hours keeps the selection. The address never holds a
 * table's search text (a person's name must not end up in the browser history or the web
 * server's access log); a selected subject is its key (`person:12`, `device:aa:bb:…`).
 */

import type { TFunction } from 'i18next';

import type { QueryValue } from '@/lib/api';
import type { UsageHours, UsageParams } from '@/lib/api-types';
import { siteToday } from '@/lib/display';
import { parseDay } from '@/lib/dates';

export const USAGE_TABS = [
  'company',
  'departments',
  'persons',
  'devices',
  'sites',
  'categories',
] as const;
export type UsageTab = (typeof USAGE_TABS)[number];

export const HOURS_FILTERS: readonly UsageHours[] = ['all', 'work', 'off'];

export interface DayRange {
  from: string;
  to: string;
}

export interface UsageView extends DayRange {
  tab: UsageTab;
  hours: UsageHours;
  /** Departman tab: the department shown. */
  department: string | null;
  /** Kişi / Cihaz tab: the subject key shown. */
  subject: string | null;
  /**
   * Site tab: the site shown, spelled as the API names it — the API matches it exactly, and a
   * FortiGuard service (`Microsoft-Azure`) is not lower case like a domain.
   */
  site: string | null;
  /** Kategori tab: the category shown. */
  category: string | null;
}

/** Query parameter names in the page address. */
const KEYS = {
  tab: 'tab',
  from: 'from',
  to: 'to',
  hours: 'hours',
  department: 'department',
  subject: 'subject',
  site: 'site',
  category: 'category',
} as const;

/** Longest department, domain or category name taken from the address. */
const MAX_NAME = 200;
/** Control, format (bidi overrides, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
/** `person:12`, `device:aa:bb:cc:dd:ee:ff`, `ip:172.31.254.5`, or a pseudonym's `p:…` / `d:…`. */
const SUBJECT_KEY = /^(?:person|device|ip|p|d):[^\s\p{Cc}\p{Cf}\p{Cs}]{1,200}$/u;

// -- site days ------------------------------------------------------------------------------------

function utc(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}

function iso(time: number): string {
  return new Date(time).toISOString().slice(0, 10);
}

/** `day` plus `count` days (negative: earlier). */
export function addDays(day: string, count: number): string {
  return iso(utc(day) + count * 86_400_000);
}

/** ISO weekday of a site day: 1 = Monday … 7 = Sunday. */
export function weekday(day: string): number {
  const value = new Date(utc(day)).getUTCDay();
  return value === 0 ? 7 : value;
}

/** Number of days from `from` to `to`, both included (≥ 1 for an ordered range). */
export function dayCount(range: DayRange): number {
  return Math.round((utc(range.to) - utc(range.from)) / 86_400_000) + 1;
}

/** Every day of the range, oldest first. */
export function daysOf(range: DayRange): string[] {
  const count = Math.max(0, dayCount(range));
  return Array.from({ length: count }, (_, index) => addDays(range.from, index));
}

export function isSingleDay(range: DayRange): boolean {
  return range.from === range.to;
}

/** The first day of the month of `day`. */
export function monthStart(day: string): string {
  return `${day.slice(0, 8)}01`;
}

/** The last day of the month of `day`. */
export function monthEnd(day: string): string {
  return addDays(monthStart(addDays(monthStart(day), 32)), -1);
}

/** Today in the site's time zone, whatever the browser's time zone is. */
export function today(now: Date = new Date()): string {
  return siteToday(now);
}

// -- presets --------------------------------------------------------------------------------------

export const RANGE_PRESETS = [
  'today',
  'yesterday',
  'thisWeek',
  'lastWeek',
  'thisMonth',
  'lastMonth',
] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];

/** The days of a preset; weeks start on Monday (Türkiye), "this …" ends today. */
export function presetRange(preset: RangePreset, todayDay: string): DayRange {
  const monday = addDays(todayDay, 1 - weekday(todayDay));
  const firstOfMonth = monthStart(todayDay);
  switch (preset) {
    case 'today':
      return { from: todayDay, to: todayDay };
    case 'yesterday': {
      const day = addDays(todayDay, -1);
      return { from: day, to: day };
    }
    case 'thisWeek':
      return { from: monday, to: todayDay };
    case 'lastWeek':
      return { from: addDays(monday, -7), to: addDays(monday, -1) };
    case 'thisMonth':
      return { from: firstOfMonth, to: todayDay };
    case 'lastMonth': {
      const last = addDays(firstOfMonth, -1);
      return { from: monthStart(last), to: last };
    }
  }
}

/** The preset `range` is (the first match), `null` for a custom range. */
export function presetOf(range: DayRange, todayDay: string): RangePreset | null {
  return (
    RANGE_PRESETS.find((preset) => {
      const days = presetRange(preset, todayDay);
      return days.from === range.from && days.to === range.to;
    }) ?? null
  );
}

/** Ordered and not past today (a day after today has no traffic yet). */
export function clampRange(range: DayRange, todayDay: string): DayRange {
  let from = range.from <= range.to ? range.from : range.to;
  let to = range.from <= range.to ? range.to : range.from;
  if (to > todayDay) to = todayDay;
  if (from > to) from = to;
  return { from, to };
}

/**
 * The range of the same length just before (`-1`) or after (`1`) `range`; `null` when the next
 * one would begin after today. A month preset moves by calendar months.
 */
export function shiftRange(range: DayRange, direction: -1 | 1, todayDay: string): DayRange | null {
  // A whole month, or "Bu ay" (the 1st to today); a single day stays a day.
  const wholeMonth =
    range.from !== range.to &&
    range.from === monthStart(range.from) &&
    (range.to === monthEnd(range.from) ||
      (range.to === todayDay && range.to.slice(0, 7) === range.from.slice(0, 7)));
  let next: DayRange;
  if (wholeMonth) {
    const from =
      direction === -1
        ? monthStart(addDays(range.from, -1))
        : monthStart(addDays(monthEnd(range.from), 1));
    next = { from, to: monthEnd(from) };
  } else {
    const length = dayCount(range);
    next = {
      from: addDays(range.from, direction * length),
      to: addDays(range.to, direction * length),
    };
  }
  if (next.from > todayDay) return null;
  return clampRange(next, todayDay);
}

// -- the address ----------------------------------------------------------------------------------

function isTab(value: string | null): value is UsageTab {
  return (USAGE_TABS as readonly (string | null)[]).includes(value);
}

function isHours(value: string | null): value is UsageHours {
  return (HOURS_FILTERS as readonly (string | null)[]).includes(value);
}

/** A name from the address: trimmed, not empty, not too long, no control characters. */
function cleanName(value: string | null): string | null {
  const name = value?.trim() ?? '';
  if (name === '' || name.length > MAX_NAME || FORBIDDEN.test(name)) return null;
  return name;
}

export function isSubjectKey(value: string | null | undefined): value is string {
  return typeof value === 'string' && SUBJECT_KEY.test(value);
}

/** The tab a subject key belongs to: persons (`person:`, `p:`) or devices (`device:`, `ip:`, `d:`). */
export function tabOfSubject(key: string): 'persons' | 'devices' {
  return key.startsWith('person:') || key.startsWith('p:') ? 'persons' : 'devices';
}

function day(value: string | null): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  return parseDay(value);
}

const EMPTY_SELECTION = { department: null, subject: null, site: null, category: null };

/** The view in the address; anything unknown or malformed falls back to the default. */
export function viewFromSearch(params: URLSearchParams, todayDay: string): UsageView {
  const tabParam = params.get(KEYS.tab);
  let tab: UsageTab = isTab(tabParam) ? tabParam : 'company';
  const hoursParam = params.get(KEYS.hours);
  const from = day(params.get(KEYS.from));
  const to = day(params.get(KEYS.to));
  const range = clampRange({ from: from ?? to ?? todayDay, to: to ?? from ?? todayDay }, todayDay);
  const view: UsageView = {
    tab,
    ...range,
    hours: isHours(hoursParam) ? hoursParam : 'all',
    ...EMPTY_SELECTION,
  };
  const subject = params.get(KEYS.subject);
  switch (tab) {
    case 'departments':
      view.department = cleanName(params.get(KEYS.department));
      break;
    case 'persons':
    case 'devices':
      if (isSubjectKey(subject)) {
        // A device key opened on the person tab (or the other way round) opens its own tab.
        tab = tabOfSubject(subject);
        view.tab = tab;
        view.subject = subject;
      }
      break;
    case 'sites':
      view.site = cleanName(params.get(KEYS.site));
      break;
    case 'categories':
      view.category = cleanName(params.get(KEYS.category));
      break;
    case 'company':
      break;
  }
  return view;
}

/** The address of a view; defaults (company, today, all hours) are left out. */
export function viewToSearch(view: UsageView, todayDay: string): URLSearchParams {
  const next = new URLSearchParams();
  if (view.tab !== 'company') next.set(KEYS.tab, view.tab);
  if (view.from !== todayDay || view.to !== todayDay) {
    next.set(KEYS.from, view.from);
    next.set(KEYS.to, view.to);
  }
  if (view.hours !== 'all') next.set(KEYS.hours, view.hours);
  if (view.department) next.set(KEYS.department, view.department);
  if (view.subject) next.set(KEYS.subject, view.subject);
  if (view.site) next.set(KEYS.site, view.site);
  if (view.category) next.set(KEYS.category, view.category);
  return next;
}

/** Another tab, without a selection (the list of that tab); period and hours stay. */
export function withTab(view: UsageView, tab: UsageTab): UsageView {
  return { ...view, tab, ...EMPTY_SELECTION };
}

/** A drill-down target: the detail of one department, subject, site or category. */
export type DrillTarget =
  { department: string } | { subject: string } | { site: string } | { category: string };

/** The view that shows `target` (its tab, its selection); period and hours stay. */
export function drillTo(view: UsageView, target: DrillTarget): UsageView {
  const base = { ...view, ...EMPTY_SELECTION };
  if ('department' in target) return { ...base, tab: 'departments', department: target.department };
  if ('subject' in target)
    return { ...base, tab: tabOfSubject(target.subject), subject: target.subject };
  if ('site' in target) return { ...base, tab: 'sites', site: target.site };
  return { ...base, tab: 'categories', category: target.category };
}

/** True when the current tab shows a detail rather than its list. */
export function hasSelection(view: UsageView): boolean {
  return (
    view.department !== null ||
    view.subject !== null ||
    view.site !== null ||
    view.category !== null
  );
}

/**
 * The API's name of the category of domains without a FortiGuard category (`UNKNOWN_CATEGORY`
 * of `arcilogger_worker.usage.config`), whatever the UI language: a site's `category: null` is
 * this category in drill-downs, filters and exports; only the text shown is translated
 * (`categoryLabel`).
 */
export const UNKNOWN_CATEGORY = 'Bilinmiyor';

/** A site's category as the API names it. */
export function siteCategory(category: string | null): string {
  return category ?? UNKNOWN_CATEGORY;
}

/**
 * A category as the page shows it: the API's name, but the unknown category (a site's `null`,
 * `UNKNOWN_CATEGORY` in the category lists) as `usage.unknownCategory` of the UI language.
 */
export function categoryLabel(category: string | null, t: TFunction): string {
  return category === null || category === UNKNOWN_CATEGORY ? t('usage.unknownCategory') : category;
}

/** The parameters every usage endpoint takes. */
export function usageParams(view: DayRange & { hours: UsageHours }): UsageParams {
  return { from: view.from, to: view.to, hours: view.hours };
}

/** The days of "Yeniden hesapla" as typed (`13.10.2026` or `2026-10-13`). */
export interface RebuildForm {
  from: string;
  to: string;
}

/**
 * The body of `POST /usage/rebuild`, or the problems of the form as i18n keys: days in the
 * Turkish format, not after today, the last not before the first.
 */
export function rebuildRequest(
  form: RebuildForm,
  todayDay: string,
): { body: DayRange | null; errors: { from?: string; to?: string } } {
  const errors: { from?: string; to?: string } = {};
  const from = parseDay(form.from);
  const to = parseDay(form.to);
  if (!from) errors.from = 'usage.rebuild.invalidDay';
  else if (from > todayDay) errors.from = 'usage.rebuild.future';
  if (!to) errors.to = 'usage.rebuild.invalidDay';
  else if (to > todayDay) errors.to = 'usage.rebuild.future';
  if (from && to && !errors.from && !errors.to && to < from) errors.to = 'usage.rebuild.order';
  return { body: from && to && !errors.from && !errors.to ? { from, to } : null, errors };
}

/** The views of `GET /usage/export.csv|xlsx`. */
export type ExportView =
  'company' | 'subjects' | 'subject' | 'sites' | 'site' | 'categories' | 'category';

/**
 * Query of an export: the view, the period, the hours, the department (when the table is one
 * department's) and the view's own parameters (`kind`, `key`, `domain`, `category`, `q`, …).
 * Empty values are left out by the API client.
 */
export function exportParams(
  view: ExportView,
  params: UsageParams,
  extra: Record<string, QueryValue> = {},
): Record<string, QueryValue> {
  return {
    view,
    from: params.from,
    to: params.to,
    hours: params.hours,
    department: params.department,
    ...extra,
  };
}
