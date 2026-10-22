/**
 * The mocked usage endpoints of the E2E tests (`/api/v1/usage/*`, group 2 contract section 2),
 * built on the answers captured from the real API (`samples/usage.json`, see `samples.ts`).
 *
 * Two sources of figures under the same rules (permissions, the KVKK notice, the period checks,
 * pseudonyms, search, sorting and paging, the exports' sheets and file names, the refusals — all
 * as in `api/src/arcilogger_api/routers/usage.py` and `arcilogger_worker/usage/query.py`):
 *
 * - **The captured day** (`CAPTURE_DAY`, 12.10.2026, every hour): the captured answers. What the
 *   capture has no answer for is derived from the captured details of that day's five subjects —
 *   a department's sites and categories, the users of a site or a category (their figures on it;
 *   the off-hours part estimated from the hourly top domain), filtered, searched and paged lists,
 *   pseudonyms of devices. The company view of that day is captured for work and off hours too.
 * - **Every other period** (and the other views of the captured day for work or off hours): a
 *   generated, deterministic dataset of the same company — the captured persons, devices and
 *   sites plus more of them — of hourly per-domain records for the 60 days up to "today"
 *   (13.10.2026, traffic until 14:35), from which every answer is computed like the API does.
 *   The captured day is a generated stand-in there, so a longer period that contains it does not
 *   add up to the captured day. The capture's "today" and its week answer are not served: their
 *   clock (07:00) is not the E2E browser clock (15:00).
 *
 * Access: `/company` (and `view=company` exports) `dashboard.usage`, `usage.person` or
 * `usage.anonymous`; everything else `usage.person` or `usage.anonymous` and the accepted KVKK
 * notice; rebuilds `settings.manage`; `/pseudonyms` `users.manage`. Names by default; with
 * `pseudonymize` a viewer holding `usage.anonymous` but not `usage.person` gets pseudonyms
 * (`Kullanıcı-D364`, keys `p:…` / `d:…` accepted back, a real key answers 404 — the captured
 * tokens where the capture has them), with `keyMissing` 409 `pseudonym_key_missing` instead.
 */

import { createHash } from 'node:crypto';

import type {
  AppRow,
  BlockedRow,
  CategoryDetail,
  CategoryList,
  CategoryRow,
  CompanyUsage,
  DepartmentRow,
  DeviceUsage,
  HeatCell,
  HostnameRow,
  HourPoint,
  Metrics,
  PseudonymRow,
  RebuildJob,
  SiteDetail,
  SiteHost,
  SitePage,
  SiteRow,
  SubjectDetail,
  SubjectHour,
  SubjectKind,
  SubjectRef,
  SubjectRow,
  TimelineSpan,
  TopCategory,
  TopSite,
  UrlRow,
  UsageHours,
  UsagePeriod,
} from '../src/lib/api-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { copy, SAMPLE_TODAY, USAGE } from './samples';

/** What the usage mock needs from the main mock. */
export interface UsageHost extends MockHost {
  privacyRequired(account: MockUser): boolean;
}

export interface UsageOptions {
  /** `usage.pseudonymize_anonymous_role`: viewers with only `usage.anonymous` get pseudonyms. */
  pseudonymize?: boolean;
  /** The secret `usage.pseudonym_key` is not stored (409 `pseudonym_key_missing`). */
  keyMissing?: boolean;
  /** No traffic at all (a fresh installation). */
  empty?: boolean;
  /** The next rebuild job fails. */
  rebuildFails?: boolean;
}

export const NOTICE_KEY = USAGE.company.notice_key;
/** The site day of the captured answers. */
export const CAPTURE_DAY = USAGE.company.period.from;
/** Days of generated data up to "today". */
const HISTORY_DAYS = 60;
const HISTORY_FIRST = addDays(SAMPLE_TODAY, 1 - HISTORY_DAYS);
/** The generated rollups processed traffic until 14:35 site time today. */
const LAST_HOUR_TODAY = 14;
const LAST_MINUTE_TODAY = 35;
export const COMPUTED_UNTIL = instant(SAMPLE_TODAY, LAST_HOUR_TODAY, LAST_MINUTE_TODAY);
/** Largest page of `/usage/subjects` and `/usage/sites` (`MAX_LIMIT` of `routers/usage.py`). */
const PAGE_MAX = 100_000;
/** Detail caps of `query.py`: users of a site/category, host names, a category's sites. */
const USERS_MAX = 200;
const HOSTNAMES_MAX = 50;
const CATEGORY_SITES_MAX = 100;
const MAX_OFFSET = 1_000_000;
const MAX_DAYS = 400;
const UNKNOWN = 'Bilinmiyor';
/** UTF-8 byte order mark of the CSV exports (Turkish Excel). */
const BOM = String.fromCharCode(0xfeff);
const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

const SUBJECT_SORTS = [
  'active_seconds',
  'online_seconds',
  'download_bytes',
  'upload_bytes',
  'blocked',
  'label',
] as const;
type SubjectSort = (typeof SUBJECT_SORTS)[number];
const SITE_SORTS = [
  'seconds',
  'raw_seconds',
  'download_bytes',
  'upload_bytes',
  'sessions',
  'users',
  'domain',
] as const;
type SiteSort = (typeof SITE_SORTS)[number];
const VIEWS = [
  'company',
  'subjects',
  'subject',
  'sites',
  'site',
  'categories',
  'category',
] as const;
type View = (typeof VIEWS)[number];

// -- time and text --------------------------------------------------------------------------------

function addDays(day: string, count: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + count * 86_400_000).toISOString().slice(0, 10);
}

function weekday(day: string): number {
  const value = new Date(`${day}T00:00:00Z`).getUTCDay();
  return value === 0 ? 7 : value;
}

/** Work hours of the default setting: Monday–Friday 08:00–18:00 site time. */
function isWorkHour(day: string, hour: number): boolean {
  return weekday(day) <= 5 && hour >= 8 && hour < 18;
}

function iso(time: number): string {
  return new Date(time).toISOString().replace('.000Z', 'Z');
}

/** An instant of site time (UTC+03:00) as the API writes it (UTC). */
function instant(day: string, hour: number, minute = 0, second = 0): string {
  return iso(Date.parse(`${day}T00:00:00+03:00`) + ((hour * 60 + minute) * 60 + second) * 1000);
}

/** The site day and hour of an instant of the API. */
function siteHour(time: string): { day: string; hour: number } {
  const local = new Date(Date.parse(time) + 3 * 3_600_000).toISOString();
  return { day: local.slice(0, 10), hour: Number(local.slice(11, 13)) };
}

/** Every hour of the period's site days (the API's `Period.hour_starts`). */
function hourStarts(period: UsagePeriod): string[] {
  const found: string[] = [];
  for (let day = period.from; day <= period.to; day = addDays(day, 1))
    for (let hour = 0; hour < 24; hour += 1) found.push(instant(day, hour));
  return found;
}

function inPeriod(day: string, hour: number, period: UsagePeriod): boolean {
  if (day < period.from || day > period.to) return false;
  if (period.hours === 'work') return isWorkHour(day, hour);
  if (period.hours === 'off') return !isWorkHour(day, hour);
  return true;
}

const FOLD_FROM = 'ABCDEFGHIJKLMNOPQRSTUVWXYZİıŞĞÜÖÇÂÎÛ';
const FOLD_TO = 'abcdefghijklmnopqrstuvwxyziişğüöçâîû';

/** The API's search folding (`arcilogger_api.textfold`): Turkish-aware lower case only. */
function fold(text: string): string {
  let out = '';
  for (const char of text) {
    const at = FOLD_FROM.indexOf(char);
    out += at < 0 ? char : (FOLD_TO[at] ?? char);
  }
  return out;
}

function share(seconds: number, total: number): number {
  return total > 0 ? Math.round((seconds / total) * 10_000) / 10_000 : 0;
}

function hex(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

function sum<T>(items: readonly T[], value: (item: T) => number): number {
  return items.reduce((total, item) => total + value(item), 0);
}

function byText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

// -- metrics --------------------------------------------------------------------------------------

function emptyMetrics(): Metrics {
  return {
    online_seconds: 0,
    active_seconds: 0,
    download_bytes: 0,
    upload_bytes: 0,
    sessions: 0,
    blocked: 0,
    off_hours_active_seconds: 0,
    first_activity: null,
    last_activity: null,
  };
}

/** `query.add_metrics`: sums, the earliest first and the latest last activity. */
function addMetrics(total: Metrics, other: Metrics): Metrics {
  total.online_seconds += other.online_seconds;
  total.active_seconds += other.active_seconds;
  total.download_bytes += other.download_bytes;
  total.upload_bytes += other.upload_bytes;
  total.sessions += other.sessions;
  total.blocked += other.blocked;
  total.off_hours_active_seconds += other.off_hours_active_seconds;
  if (
    other.first_activity &&
    (!total.first_activity || Date.parse(other.first_activity) < Date.parse(total.first_activity))
  )
    total.first_activity = other.first_activity;
  if (
    other.last_activity &&
    (!total.last_activity || Date.parse(other.last_activity) > Date.parse(total.last_activity))
  )
    total.last_activity = other.last_activity;
  return total;
}

function sortSites(rows: SiteRow[]): SiteRow[] {
  return rows.sort(
    (a, b) =>
      b.seconds - a.seconds || b.download_bytes - a.download_bytes || byText(a.domain, b.domain),
  );
}

/** `query.category_rows`: categories of site rows (share of the same scope's time). */
function categoryRows(
  sites: readonly SiteRow[],
  users: ReadonlyMap<string, number>,
): CategoryRow[] {
  const total = sum(sites, (row) => row.seconds);
  const grouped = new Map<string, CategoryRow>();
  for (const row of sites) {
    const name = row.category ?? UNKNOWN;
    const entry = grouped.get(name) ?? {
      category: name,
      seconds: 0,
      share: 0,
      download_bytes: 0,
      upload_bytes: 0,
      users: 0,
    };
    entry.seconds += row.seconds;
    entry.download_bytes += row.download_bytes;
    entry.upload_bytes += row.upload_bytes;
    grouped.set(name, entry);
  }
  return [...grouped.values()]
    .map((entry) => ({
      ...entry,
      share: share(entry.seconds, total),
      users: users.get(entry.category) ?? 0,
    }))
    .sort(
      (a, b) =>
        b.seconds - a.seconds ||
        b.download_bytes - a.download_bytes ||
        byText(a.category, b.category),
    );
}

/** `top_sites` (≤ 10) and `top_categories` (≤ 5) of a subject from its sites with time. */
function tops(
  sites: readonly [string, number][],
  categoryOf: (domain: string) => string | null,
): { top_sites: TopSite[]; top_categories: TopCategory[] } {
  const sorted = sites
    .filter(([, seconds]) => seconds > 0)
    .sort((a, b) => b[1] - a[1] || byText(a[0], b[0]));
  const byCategory = new Map<string, number>();
  for (const [domain, seconds] of sorted) {
    const name = categoryOf(domain) ?? UNKNOWN;
    byCategory.set(name, (byCategory.get(name) ?? 0) + seconds);
  }
  return {
    top_sites: sorted
      .slice(0, 10)
      .map(([domain, seconds]) => ({ domain, seconds: Math.round(seconds) })),
    top_categories: [...byCategory.entries()]
      .sort((a, b) => b[1] - a[1] || byText(a[0], b[0]))
      .slice(0, 5)
      .map(([category, seconds]) => ({ category, seconds: Math.round(seconds) })),
  };
}

// -- subjects, pseudonyms -------------------------------------------------------------------------

/** What the API knows about a subject (`query.SubjectInfo`). */
interface Info {
  key: string;
  kind: SubjectKind;
  label: string;
  mac: string | null;
  ip: string | null;
  hostname: string | null;
  department: string | null;
  source: 'person' | 'vlan' | null;
}

interface Stats {
  info: Info;
  metrics: Metrics;
}

function realRef(info: Info): SubjectRef {
  return {
    kind: info.kind,
    key: info.key,
    label: info.label,
    pseudonymous: false,
    mac: info.mac,
    ip: info.ip,
    hostname: info.hostname,
  };
}

/** Pseudonym tokens of the capture (real key → `p:…` / `d:…`). */
const TOKENS = new Map<string, string>();
for (const row of USAGE.viewer_subjects_pseudonymous.items) {
  const real = USAGE.pseudonyms.find((item) => item.pseudonym === row.subject.label);
  if (real) TOKENS.set(real.subject.key, row.subject.key);
}
USAGE.subject_person.devices.forEach((device, index) => {
  const token = USAGE.viewer_subject_pseudonymous.devices[index]?.key;
  if (token) TOKENS.set(device.key, token);
});
/** Pseudonym labels of the capture without a captured token (their first four hex digits). */
const LABEL_HEADS = new Map(
  USAGE.pseudonyms.map((row) => [row.subject.key, row.pseudonym.slice(-4).toLowerCase()]),
);

/** The opaque key a pseudonymous viewer sees (`pseudonyms.pseudonym`: an HMAC there). */
function tokenOf(key: string): string {
  const known = TOKENS.get(key);
  if (known) return known;
  const digest = hex(`arcilogger-e2e-pseudonym:${key}`);
  const head = LABEL_HEADS.get(key) ?? digest.slice(0, 4);
  return `${key.startsWith('person:') ? 'p' : 'd'}:${head}${digest.slice(4, 16)}`;
}

function pseudonymOf(ref: SubjectRef): SubjectRef {
  const token = tokenOf(ref.key);
  const prefix = ref.kind === 'person' ? 'Kullanıcı' : 'Cihaz';
  return {
    kind: ref.kind,
    key: token,
    label: `${prefix}-${token.slice(2, 6).toUpperCase()}`,
    pseudonymous: true,
    mac: null,
    ip: null,
    hostname: null,
  };
}

/** Department rows of subjects (`query.department_rows`). */
function departmentRows(stats: readonly Stats[]): DepartmentRow[] {
  const grouped = new Map<string, DepartmentRow>();
  for (const item of stats) {
    const name = item.info.department ?? UNKNOWN;
    const entry = grouped.get(name) ?? {
      department: name,
      source: 'vlan',
      active_users: 0,
      metrics: emptyMetrics(),
    };
    if (item.info.source === 'person') entry.source = 'person';
    if (item.metrics.active_seconds > 0) entry.active_users += 1;
    addMetrics(entry.metrics, item.metrics);
    grouped.set(name, entry);
  }
  return [...grouped.values()].sort(
    (a, b) =>
      b.metrics.active_seconds - a.metrics.active_seconds || byText(a.department, b.department),
  );
}

// -- the sources ----------------------------------------------------------------------------------

/** One request's period and department filter. */
interface Scope {
  period: UsagePeriod;
  department: string | null;
  /** The subjects of the department (`null`: everyone). */
  keys: ReadonlySet<string> | null;
}

type Bare<T> = Omit<T, 'period' | 'estimated' | 'computed_until' | 'notice_key'>;

/** Where the figures come from: the captured day or the generated dataset. */
interface Source {
  computedUntil(period: UsagePeriod): string | null;
  /** Every subject with traffic in the period (real keys). */
  stats(period: UsagePeriod): Map<string, Stats>;
  company(period: UsagePeriod): Bare<CompanyUsage>;
  subjectRows(period: UsagePeriod, keys: readonly string[]): SubjectRow[];
  subjectDetail(period: UsagePeriod, key: string): Bare<SubjectDetail>;
  sites(scope: Scope): SiteRow[];
  /** `capped`: the detail answer's caps (the exports pass `false`: every row). */
  siteDetail(scope: Scope, domain: string, capped: boolean): Bare<SiteDetail> | null;
  categories(scope: Scope): CategoryRow[];
  categoryDetail(scope: Scope, category: string, capped: boolean): Bare<CategoryDetail> | null;
}

/** The users of a site or category: their figures there only (`query._restricted_users`). */
interface Restricted {
  key: string;
  domain: string;
  seconds: number;
  raw: number;
  down: number;
  up: number;
  sessions: number;
  off: number;
}

/**
 * The rows (at most `limit`, `null`: all) and how many there are. Online time is the raw time of
 * one site (`rawIsOnline`) and 0 for a category; no blocked attempts, no first/last activity.
 */
function restrictedUsers(
  items: readonly Restricted[],
  stats: ReadonlyMap<string, Stats>,
  categoryOf: (domain: string) => string | null,
  { rawIsOnline, limit }: { rawIsOnline: boolean; limit: number | null },
): { users: SubjectRow[]; total: number } {
  const per = new Map<string, { metrics: Metrics; sites: Map<string, number> }>();
  for (const item of items) {
    if (!stats.get(item.key)) continue;
    const entry = per.get(item.key) ?? {
      metrics: emptyMetrics(),
      sites: new Map<string, number>(),
    };
    if (rawIsOnline) entry.metrics.online_seconds += item.raw;
    entry.metrics.active_seconds += item.seconds;
    entry.metrics.download_bytes += item.down;
    entry.metrics.upload_bytes += item.up;
    entry.metrics.sessions += item.sessions;
    entry.metrics.off_hours_active_seconds += item.off;
    entry.sites.set(item.domain, (entry.sites.get(item.domain) ?? 0) + item.seconds);
    per.set(item.key, entry);
  }
  const sorted = [...per.entries()]
    .map(([key, entry]) => {
      entry.metrics.active_seconds = Math.round(entry.metrics.active_seconds);
      entry.metrics.off_hours_active_seconds = Math.round(entry.metrics.off_hours_active_seconds);
      return { key, entry };
    })
    .sort(
      (a, b) =>
        b.entry.metrics.active_seconds - a.entry.metrics.active_seconds ||
        b.entry.metrics.download_bytes - a.entry.metrics.download_bytes ||
        byText(a.key, b.key),
    );
  const users = (limit === null ? sorted : sorted.slice(0, limit)).flatMap(({ key, entry }) => {
    const info = stats.get(key)?.info;
    if (!info) return [];
    return [
      {
        subject: realRef(info),
        department: info.department,
        metrics: entry.metrics,
        ...tops([...entry.sites.entries()], categoryOf),
      },
    ];
  });
  return { users, total: sorted.length };
}

/** The department filters of the captured requests (`web/scripts/capture_api_samples.py`). */
const CAPTURED_FILTERS = {
  sites: 'Finans',
  site: 'Finans',
  categories: 'Finans',
  category: 'Planlama',
} as const;

/** The captured day: the captured answers, and what follows from its subjects' details. */
class CapturedDay implements Source {
  private readonly rows: SubjectRow[] = [
    ...USAGE.subjects_persons.items,
    ...USAGE.subjects_devices.items,
  ];
  private readonly details = new Map<string, SubjectDetail>(
    [
      USAGE.subject_person,
      USAGE.subject_reassigned_before,
      USAGE.subject_reassigned_after,
      USAGE.subject_device,
      USAGE.subject_ip,
    ].map((detail) => [detail.subject.key, detail]),
  );
  private readonly categoryOfDomain = new Map(
    USAGE.sites.items.map((row) => [row.domain, row.category]),
  );

  computedUntil(): string | null {
    return USAGE.company.computed_until;
  }

  stats(): Map<string, Stats> {
    return new Map(
      this.rows.map((row) => [
        row.subject.key,
        {
          info: {
            key: row.subject.key,
            kind: row.subject.kind,
            label: row.subject.label,
            mac: row.subject.mac ?? null,
            ip: row.subject.ip ?? null,
            hostname: row.subject.hostname ?? null,
            department: row.department,
            source:
              row.department === null ? null : row.subject.kind === 'person' ? 'person' : 'vlan',
          },
          metrics: row.metrics,
        },
      ]),
    );
  }

  company(period: UsagePeriod): Bare<CompanyUsage> {
    const sample =
      period.hours === 'work'
        ? USAGE.company_work_hours
        : period.hours === 'off'
          ? USAGE.company_off_hours
          : USAGE.company;
    return copy(sample);
  }

  subjectRows(_period: UsagePeriod, keys: readonly string[]): SubjectRow[] {
    return keys.flatMap((key) => {
      const row = this.rows.find((item) => item.subject.key === key);
      return row ? [copy(row)] : [];
    });
  }

  subjectDetail(_period: UsagePeriod, key: string): Bare<SubjectDetail> {
    const detail = this.details.get(key);
    if (!detail) throw new Error(`no captured detail of ${key}`);
    return copy(detail);
  }

  private scoped(scope: Scope): SubjectDetail[] {
    return [...this.details.values()].filter(
      (detail) => scope.keys?.has(detail.subject.key) ?? true,
    );
  }

  private categoryOf(domain: string): string | null {
    return this.categoryOfDomain.get(domain) ?? null;
  }

  /** The scope's site rows, summed from its subjects' details. */
  derivedSites(scope: Scope): SiteRow[] {
    const grouped = new Map<string, SiteRow>();
    for (const detail of this.scoped(scope)) {
      for (const row of detail.sites) {
        const entry = grouped.get(row.domain) ?? {
          ...row,
          seconds: 0,
          raw_seconds: 0,
          download_bytes: 0,
          upload_bytes: 0,
          sessions: 0,
          users: 0,
          background: true,
        };
        entry.seconds += row.seconds;
        entry.raw_seconds += row.raw_seconds;
        entry.download_bytes += row.download_bytes;
        entry.upload_bytes += row.upload_bytes;
        entry.sessions += row.sessions;
        entry.users += 1;
        entry.background = entry.background && row.background;
        grouped.set(row.domain, entry);
      }
    }
    const rows = [...grouped.values()];
    const total = sum(rows, (row) => row.seconds);
    return sortSites(rows.map((row) => ({ ...row, share: share(row.seconds, total) })));
  }

  sites(scope: Scope): SiteRow[] {
    if (scope.department === null) return copy(USAGE.sites.items);
    if (scope.department === CAPTURED_FILTERS.sites)
      return copy(USAGE.sites_department_filter.items);
    return this.derivedSites(scope);
  }

  /** The scope's users of `domains`, with their (estimated) off-hours part. */
  private restricted(scope: Scope, domains: ReadonlySet<string>): Restricted[] {
    return this.scoped(scope).flatMap((detail) => {
      const offHours = new Map<string, number>();
      for (const hour of detail.hourly) {
        const local = siteHour(hour.hour);
        if (hour.top_domain && domains.has(hour.top_domain) && !isWorkHour(local.day, local.hour))
          offHours.set(hour.top_domain, (offHours.get(hour.top_domain) ?? 0) + hour.active_seconds);
      }
      return detail.sites
        .filter((row) => domains.has(row.domain))
        .map((row) => ({
          key: detail.subject.key,
          domain: row.domain,
          seconds: row.seconds,
          raw: row.raw_seconds,
          down: row.download_bytes,
          up: row.upload_bytes,
          sessions: row.sessions,
          off: Math.min(row.seconds, offHours.get(row.domain) ?? 0),
        }));
    });
  }

  siteDetail(scope: Scope, domain: string, capped: boolean): Bare<SiteDetail> | null {
    // The captured answers are under every cap.
    if (domain === USAGE.site.domain) {
      if (scope.department === null) return copy(USAGE.site);
      if (scope.department === CAPTURED_FILTERS.site) return copy(USAGE.site_department_filter);
    }
    return this.derivedSiteDetail(scope, domain, capped);
  }

  /** A site's detail from the scope's subject details. */
  derivedSiteDetail(scope: Scope, domain: string, capped: boolean): Bare<SiteDetail> | null {
    const totals = this.derivedSites(scope).find((row) => row.domain === domain);
    if (!totals) return null;
    const active = sum(this.scoped(scope), (detail) => detail.metrics.active_seconds);
    const hosts = new Map<string, SiteHost>();
    for (const detail of this.scoped(scope))
      for (const row of detail.hostnames.filter((item) => item.domain === domain)) {
        const entry = hosts.get(row.hostname) ?? {
          hostname: row.hostname,
          seconds: 0,
          bytes: 0,
          sessions: 0,
        };
        entry.seconds += row.seconds;
        entry.bytes += row.bytes;
        entry.sessions += row.sessions;
        hosts.set(row.hostname, entry);
      }
    const hostnames = [...hosts.values()].sort(
      (a, b) => b.seconds - a.seconds || b.bytes - a.bytes || byText(a.hostname, b.hostname),
    );
    const { users, total } = restrictedUsers(
      this.restricted(scope, new Set([domain])),
      this.stats(),
      (item) => this.categoryOf(item),
      { rawIsOnline: true, limit: capped ? USERS_MAX : null },
    );
    return {
      domain,
      category: this.categoryOf(domain),
      totals: { ...totals, share: share(totals.seconds, active) },
      hostnames: capped ? hostnames.slice(0, HOSTNAMES_MAX) : hostnames,
      users,
      users_total: total,
    };
  }

  private categoryUsers(scope: Scope): Map<string, number> {
    const users = new Map<string, number>();
    for (const detail of this.scoped(scope)) {
      const names = new Set(detail.sites.map((row) => row.category ?? UNKNOWN));
      for (const name of names) users.set(name, (users.get(name) ?? 0) + 1);
    }
    return users;
  }

  categories(scope: Scope): CategoryRow[] {
    if (scope.department === null) return copy(USAGE.categories.items);
    if (scope.department === CAPTURED_FILTERS.categories)
      return copy(USAGE.categories_department_filter.items);
    return this.derivedCategories(scope);
  }

  derivedCategories(scope: Scope): CategoryRow[] {
    return categoryRows(this.derivedSites(scope), this.categoryUsers(scope));
  }

  categoryDetail(scope: Scope, category: string, capped: boolean): Bare<CategoryDetail> | null {
    // The captured answers are under every cap.
    if (scope.department === null && category === USAGE.category.category)
      return copy(USAGE.category);
    if (scope.department === null && category === USAGE.category_unknown.category)
      return copy(USAGE.category_unknown);
    if (
      scope.department === CAPTURED_FILTERS.category &&
      category === USAGE.category_department_filter.category
    )
      return copy(USAGE.category_department_filter);
    return this.derivedCategoryDetail(scope, category, capped);
  }

  /** A category's detail from the scope's subject details. */
  derivedCategoryDetail(
    scope: Scope,
    category: string,
    capped: boolean,
  ): Bare<CategoryDetail> | null {
    const totals = this.derivedCategories(scope).find((row) => row.category === category);
    if (!totals) return null;
    const sites = this.derivedSites(scope).filter((row) => (row.category ?? UNKNOWN) === category);
    const { users, total } = restrictedUsers(
      this.restricted(scope, new Set(sites.map((row) => row.domain))),
      this.stats(),
      () => category,
      { rawIsOnline: false, limit: capped ? USERS_MAX : null },
    );
    return {
      category,
      totals,
      sites: capped ? sites.slice(0, CATEGORY_SITES_MAX) : sites,
      sites_total: sites.length,
      users,
      users_total: total,
    };
  }
}

// -- the generated dataset ------------------------------------------------------------------------

interface SiteDef {
  domain: string;
  kind: SiteRow['kind'];
  category: string | null;
  /** Background traffic (updates, telemetry): online time, never active time. */
  background: boolean;
  hosts: string[];
  /** FortiGate application control's name, when it logs one. */
  app: string | null;
  appcat: string | null;
  /** Downloaded bytes per active second (per minute online for background traffic). */
  rate: number;
  /** Paths the web filter logs (none: no URL rows). */
  paths: string[];
}

const SITES: SiteDef[] = [
  {
    domain: 'portal.example',
    kind: 'domain',
    category: 'İş Uygulamaları',
    background: false,
    hosts: ['portal.example', 'api.portal.example'],
    app: 'HTTPS.BROWSER',
    appcat: 'Web.Client',
    rate: 1_300,
    paths: ['/fatura', '/cari-hesap', '/raporlar/aylik'],
  },
  {
    domain: 'bulut.example',
    kind: 'domain',
    category: 'Bulut Depolama',
    background: false,
    hosts: ['bulut.example', 'dosya.bulut.example'],
    app: 'SSL_TLSv1.3.PQC',
    appcat: 'Network.Service',
    rate: 9_000,
    paths: ['/paylasim/teklifler', '/klasor/2026'],
  },
  {
    domain: 'video.example',
    kind: 'domain',
    category: 'Video',
    background: false,
    hosts: ['www.video.example', 'cdn.video.example'],
    app: 'SSL_TLSv1.3.PQC',
    appcat: 'Network.Service',
    rate: 90_000,
    paths: ['/izle?v=42', '/izle?v=7', '/ara?q=hava+durumu'],
  },
  {
    domain: 'haber.example',
    kind: 'domain',
    category: 'Haber',
    background: false,
    hosts: ['haber.example', 'ekonomi.haber.example'],
    app: null,
    appcat: null,
    rate: 8_000,
    paths: ['/gundem', '/ekonomi/doviz'],
  },
  {
    domain: 'muzik.example',
    kind: 'domain',
    category: 'Müzik',
    background: false,
    hosts: ['www.muzik.example'],
    app: 'SSL_TLSv1.3.PQC',
    appcat: 'Network.Service',
    rate: 5_000,
    paths: [],
  },
  {
    domain: 'posta.example',
    kind: 'domain',
    category: null,
    background: false,
    hosts: ['mail.posta.example'],
    app: null,
    appcat: null,
    rate: 4_000,
    paths: ['/gelen', '/gonderilen'],
  },
  {
    domain: '203.0.113.110',
    kind: 'ip',
    category: null,
    background: false,
    hosts: ['203.0.113.110'],
    app: null,
    appcat: null,
    rate: 6_000,
    paths: [],
  },
  {
    domain: 'sosyal.example',
    kind: 'domain',
    category: 'Sosyal Medya',
    background: false,
    hosts: ['www.sosyal.example', 'i.sosyal.example'],
    app: 'SSL_TLSv1.3.PQC',
    appcat: 'Network.Service',
    rate: 40_000,
    paths: ['/kesfet', '/kisa-videolar'],
  },
  {
    domain: 'alisveris.example',
    kind: 'domain',
    category: 'Alışveriş',
    background: false,
    hosts: ['www.alisveris.example'],
    app: null,
    appcat: null,
    rate: 15_000,
    paths: ['/ara?q=is+ayakkabisi', '/sepet'],
  },
  {
    domain: 'banka.example',
    kind: 'domain',
    category: 'Finans ve Bankacılık',
    background: false,
    hosts: ['sube.banka.example'],
    app: null,
    appcat: null,
    rate: 5_000,
    paths: ['/giris', '/hesaplar'],
  },
  {
    domain: 'kamu.example',
    kind: 'domain',
    category: 'Kamu Kurumları',
    background: false,
    hosts: ['giris.kamu.example', 'www.kamu.example'],
    app: null,
    appcat: null,
    rate: 4_000,
    paths: ['/e-beyanname', '/sgk-hizmet-dokumu'],
  },
  {
    domain: 'mesaj.example',
    kind: 'domain',
    category: 'Anlık Mesajlaşma',
    background: false,
    hosts: ['web.mesaj.example', 'medya.mesaj.example'],
    app: 'SSL_TLSv1.3.PQC',
    appcat: 'Network.Service',
    rate: 10_000,
    paths: [],
  },
  {
    domain: 'arama.example',
    kind: 'domain',
    category: 'Arama Motorları',
    background: false,
    hosts: ['www.arama.example'],
    app: null,
    appcat: null,
    rate: 7_000,
    paths: ['/ara?q=doviz+kuru', '/ara?q=tatil+otelleri'],
  },
  {
    domain: 'windowsupdate.com',
    kind: 'domain',
    category: null,
    background: true,
    hosts: ['download.windowsupdate.com'],
    app: 'MS.Windows.Update',
    appcat: 'Update',
    rate: 300_000,
    paths: [],
  },
  {
    domain: 'Microsoft-Azure',
    kind: 'service',
    category: null,
    background: true,
    hosts: ['Microsoft-Azure'],
    app: null,
    appcat: null,
    rate: 25_000,
    paths: [],
  },
  {
    domain: '8.8.8.8',
    kind: 'ip',
    category: null,
    background: true,
    hosts: ['8.8.8.8'],
    app: null,
    appcat: null,
    rate: 2_500,
    paths: [],
  },
  {
    domain: 'reklam.example',
    kind: 'domain',
    category: null,
    background: true,
    hosts: ['tracker.reklam.example'],
    app: null,
    appcat: null,
    rate: 600,
    paths: [],
  },
];
const SITE_INDEX = new Map(SITES.map((site, index) => [site.domain, index]));
const BACKGROUND = SITES.flatMap((site, index) => (site.background ? [index] : []));

function siteDef(index: number): SiteDef {
  const found = SITES[index];
  if (!found) throw new Error(`no site ${index}`);
  return found;
}

function categoryOfDomain(domain: string): string | null {
  const index = SITE_INDEX.get(domain);
  return index === undefined ? null : siteDef(index).category;
}

interface Profile {
  /** First and last hour of the usual day (inclusive). */
  start: number;
  end: number;
  /** Share of an active hour that is used (0…1). */
  intensity: number;
  /** Probability that an hour of the usual day is active. */
  busy: number;
  /** Domains with weights. */
  sites: [string, number][];
  /** Evening hours (off hours) with their probability, e.g. a phone after work. */
  evening?: { start: number; end: number; chance: number };
  /** Weekend days are active with this probability. */
  weekend?: number;
}

interface PersonDef {
  id: number;
  name: string;
  department: string | null;
}

/**
 * Persons 1–3 as in the capture; the others only in the generated days. The departments are the
 * three of the example site (Finans, Planlama, İdari İşler).
 */
const PERSONS: PersonDef[] = [
  { id: 1, name: 'Ayşe Yılmaz', department: 'Finans' },
  { id: 2, name: 'Burak Demir', department: 'Planlama' },
  { id: 3, name: 'Can Öztürk', department: 'Finans' },
  { id: 4, name: 'Ahmet Kara', department: 'İdari İşler' },
  { id: 5, name: 'Zeynep Arslan', department: 'Planlama' },
  { id: 6, name: 'Mustafa Şahin', department: 'Planlama' },
  { id: 7, name: 'Elif Kaya', department: 'Finans' },
  { id: 8, name: 'Hakan Yıldız', department: 'İdari İşler' },
  { id: 9, name: 'Selin Aydın', department: 'Planlama' },
  { id: 10, name: 'Emre Koç', department: 'İdari İşler' },
];

interface DeviceDef {
  /** `null`: an address without a MAC (an `ip:` subject). */
  mac: string | null;
  ip: string;
  name: string | null;
  hostname: string | null;
  /** Who it is assigned to from a site day on (`null`: nobody). */
  owners: readonly { from: string; person: number | null }[];
  /** The department of its VLAN. */
  vlanDepartment: string | null;
  profile: Profile;
}

const ALWAYS = '2000-01-01';
const OFFICE: Profile['sites'] = [
  ['portal.example', 6],
  ['posta.example', 3],
  ['arama.example', 3],
  ['bulut.example', 2],
];
const PHONE: Profile = {
  start: 8,
  end: 18,
  intensity: 0.15,
  busy: 0.35,
  sites: [
    ['mesaj.example', 5],
    ['sosyal.example', 3],
    ['arama.example', 1],
  ],
  evening: { start: 19, end: 23, chance: 0.4 },
  weekend: 0.6,
};

function owned(person: number | null): DeviceDef['owners'] {
  return [{ from: ALWAYS, person }];
}

const DEVICES: DeviceDef[] = [
  {
    mac: '3c:52:82:00:10:01',
    ip: '172.20.43.37',
    name: "Ayşe'nin bilgisayarı",
    hostname: 'PC-01',
    owners: owned(1),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 8,
      end: 17,
      intensity: 0.6,
      busy: 0.8,
      sites: [
        ...OFFICE,
        ['banka.example', 3],
        ['kamu.example', 2],
        ['haber.example', 2],
        ['video.example', 1],
      ],
      evening: { start: 19, end: 20, chance: 0.15 },
    },
  },
  {
    // Burak Demir's until the capture's "today", then Can Öztürk's (the capture reassigned it).
    mac: '3c:52:82:00:10:03',
    ip: '172.20.42.82',
    name: 'Planlama bilgisayarı',
    hostname: 'PC-03',
    owners: [
      { from: ALWAYS, person: 2 },
      { from: SAMPLE_TODAY, person: 3 },
    ],
    vlanDepartment: 'Planlama',
    profile: {
      start: 8,
      end: 18,
      intensity: 0.65,
      busy: 0.85,
      sites: [
        ['portal.example', 5],
        ['bulut.example', 3],
        ['arama.example', 2],
        ['video.example', 1],
      ],
    },
  },
  {
    mac: '3c:52:82:00:10:02',
    ip: '172.20.43.81',
    name: null,
    hostname: 'PC-02',
    owners: owned(null),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 9,
      end: 16,
      intensity: 0.3,
      busy: 0.4,
      sites: [
        ['bulut.example', 4],
        ['muzik.example', 2],
        ['portal.example', 1],
      ],
      evening: { start: 20, end: 23, chance: 0.2 },
    },
  },
  {
    mac: null,
    ip: '172.20.41.77',
    name: null,
    hostname: null,
    owners: owned(null),
    vlanDepartment: 'Finans',
    profile: {
      start: 9,
      end: 15,
      intensity: 0.2,
      busy: 0.3,
      sites: [
        ['203.0.113.110', 3],
        ['arama.example', 1],
      ],
    },
  },
  {
    mac: '02:00:5e:00:53:5d',
    ip: '172.20.60.115',
    name: "Can'ın telefonu",
    hostname: 'Telefon-03',
    owners: owned(3),
    vlanDepartment: null,
    profile: PHONE,
  },
  {
    mac: '3c:52:82:00:00:11',
    ip: '172.20.43.11',
    name: null,
    hostname: 'IDR-PC-11',
    owners: owned(4),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 8,
      end: 18,
      intensity: 0.6,
      busy: 0.8,
      sites: [...OFFICE, ['haber.example', 3], ['banka.example', 2], ['kamu.example', 1]],
      evening: { start: 19, end: 21, chance: 0.15 },
    },
  },
  {
    mac: 'a2:41:00:00:00:31',
    ip: '172.20.60.131',
    name: 'Galaxy S24',
    hostname: null,
    owners: owned(4),
    vlanDepartment: null,
    profile: PHONE,
  },
  {
    // A laptop on the staff Wi-Fi (no department of its own: its owner's counts).
    mac: 'a4:83:e7:00:00:13',
    ip: '172.20.60.83',
    name: 'NB-03',
    hostname: 'NB-03',
    owners: owned(5),
    vlanDepartment: null,
    profile: {
      start: 9,
      end: 18,
      intensity: 0.65,
      busy: 0.8,
      sites: [
        ['sosyal.example', 3],
        ['portal.example', 4],
        ['alisveris.example', 2],
        ['arama.example', 2],
      ],
    },
  },
  {
    mac: '3c:52:82:00:00:04',
    ip: '172.20.42.84',
    name: 'PLN-PC-04',
    hostname: 'PLN-PC-04',
    owners: owned(6),
    vlanDepartment: 'Planlama',
    profile: {
      start: 8,
      end: 19,
      intensity: 0.75,
      busy: 0.85,
      sites: [
        ['bulut.example', 4],
        ['arama.example', 3],
        ['portal.example', 3],
        ['video.example', 3],
        ['203.0.113.110', 1],
      ],
    },
  },
  {
    mac: 'f0:1f:af:00:00:02',
    ip: '172.20.41.22',
    name: 'FIN-PC-02',
    hostname: 'FIN-PC-02',
    owners: owned(7),
    vlanDepartment: 'Finans',
    profile: {
      start: 8,
      end: 17,
      intensity: 0.6,
      busy: 0.8,
      sites: [
        ['portal.example', 5],
        ['banka.example', 3],
        ['kamu.example', 3],
        ['haber.example', 2],
        ['posta.example', 2],
      ],
    },
  },
  {
    mac: '00:15:5d:00:00:23',
    ip: '172.20.43.23',
    name: 'IDR-PC-03',
    hostname: 'IDR-PC-03',
    owners: owned(8),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 8,
      end: 17,
      intensity: 0.4,
      busy: 0.55,
      sites: [
        ['video.example', 4],
        ['alisveris.example', 3],
        ['muzik.example', 1],
        ['arama.example', 1],
      ],
      evening: { start: 18, end: 22, chance: 0.35 },
      weekend: 0.3,
    },
  },
  {
    mac: '02:00:5e:00:53:59',
    ip: '172.20.60.41',
    name: 'Telefon (Selin)',
    hostname: null,
    owners: owned(9),
    vlanDepartment: null,
    profile: { ...PHONE, intensity: 0.2 },
  },
  {
    mac: 'a4:83:e7:00:00:21',
    ip: '172.20.42.21',
    name: 'PLN-PC-01',
    hostname: 'PLN-PC-01',
    owners: owned(9),
    vlanDepartment: 'Planlama',
    profile: {
      start: 9,
      end: 17,
      intensity: 0.55,
      busy: 0.75,
      sites: [
        ['portal.example', 4],
        ['sosyal.example', 3],
        ['alisveris.example', 1],
        ['arama.example', 2],
      ],
    },
  },
  {
    mac: '00:0c:29:00:00:12',
    ip: '172.20.43.82',
    name: null,
    hostname: 'PC-12',
    owners: owned(10),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 8,
      end: 17,
      intensity: 0.5,
      busy: 0.7,
      sites: [...OFFICE, ['haber.example', 2]],
    },
  },
  // Devices assigned to nobody: the VLAN's department (spec 9.2), "Bilinmiyor" without one.
  {
    mac: '3c:52:82:00:00:05',
    ip: '172.20.42.25',
    name: 'PLN-PC-05',
    hostname: 'PLN-PC-05',
    owners: owned(null),
    vlanDepartment: 'Planlama',
    profile: {
      start: 9,
      end: 16,
      intensity: 0.4,
      busy: 0.5,
      sites: [
        ['arama.example', 3],
        ['bulut.example', 2],
        ['video.example', 1],
      ],
    },
  },
  {
    mac: '00:0c:29:00:00:31',
    ip: '172.20.43.31',
    name: 'TOPLANTI-PC',
    hostname: 'TOPLANTI-PC',
    owners: owned(null),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 10,
      end: 16,
      intensity: 0.3,
      busy: 0.3,
      sites: [
        ['portal.example', 2],
        ['video.example', 1],
      ],
    },
  },
  {
    mac: null,
    ip: '172.20.43.10',
    name: null,
    hostname: null,
    owners: owned(null),
    vlanDepartment: 'İdari İşler',
    profile: {
      start: 9,
      end: 15,
      intensity: 0.2,
      busy: 0.3,
      sites: [
        ['203.0.113.110', 2],
        ['arama.example', 1],
      ],
    },
  },
  {
    // A small server (VLAN 20 "Sunucular" has no department).
    mac: 'd8:3a:dd:00:00:70',
    ip: '172.20.20.70',
    name: null,
    hostname: 'raspberrypi',
    owners: owned(null),
    vlanDepartment: null,
    profile: {
      start: 0,
      end: 23,
      intensity: 0.05,
      busy: 0.25,
      sites: [['bulut.example', 1]],
      weekend: 1,
    },
  },
];

type BlockedTarget = Omit<BlockedRow, 'time'>;

const BLOCKED_TARGETS: BlockedTarget[] = [
  {
    domain: 'bahis.example',
    hostname: 'bahis.example',
    category: 'Kumar',
    action: 'blocked',
    source: 'web',
    url: '/canli',
    app: null,
  },
  {
    domain: 'bahis.example',
    hostname: 'bahis.example',
    category: 'Kumar',
    action: 'block',
    source: 'dns',
    url: null,
    app: null,
  },
  {
    domain: null,
    hostname: null,
    category: 'P2P',
    action: 'block',
    source: 'app',
    url: null,
    app: 'BitTorrent',
  },
  {
    domain: 'vekil.example',
    hostname: 'www.vekil.example',
    category: 'Vekil Sunucu',
    action: 'blocked',
    source: 'web',
    url: '/gizli-gezinti',
    app: null,
  },
];

/** One device's seconds on one domain in one hour of one site day. */
interface UsageRecord {
  device: number;
  subject: string;
  day: string;
  hour: number;
  site: number;
  seconds: number;
  raw: number;
  down: number;
  up: number;
  sessions: number;
  /** The active minutes of this domain in the hour (none for background traffic). */
  start: number;
  minutes: number;
}

/** One device-hour: online and active seconds, the active minutes' place in the hour. */
interface DeviceHour {
  device: number;
  subject: string;
  day: string;
  hour: number;
  online: number;
  active: number;
  offset: number;
}

interface BlockedRecord extends BlockedRow {
  device: number;
  subject: string;
  day: string;
  hour: number;
}

interface Dataset {
  records: UsageRecord[];
  hours: DeviceHour[];
  blocked: BlockedRecord[];
}

/** Deterministic pseudo-random numbers (mulberry32). */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(items: readonly [T, number][], roll: number): T {
  const total = items.reduce((all, [, weight]) => all + weight, 0);
  let left = roll * total;
  for (const [item, weight] of items) {
    left -= weight;
    if (left <= 0) return item;
  }
  const last = items.at(-1);
  if (!last) throw new Error('nothing to pick');
  return last[0];
}

function deviceDef(index: number): DeviceDef {
  const found = DEVICES[index];
  if (!found) throw new Error(`no device ${index}`);
  return found;
}

/** The subject a device's traffic of `day` belongs to: its person, else the device or address. */
function subjectOf(device: DeviceDef, day: string): string {
  let person: number | null = null;
  for (const owner of device.owners) if (owner.from <= day) person = owner.person;
  if (person !== null) return `person:${person}`;
  return device.mac ? `device:${device.mac}` : `ip:${device.ip}`;
}

function deviceLabel(device: DeviceDef): string {
  return device.name ?? device.hostname ?? device.mac ?? device.ip;
}

function deviceRef(device: DeviceDef): SubjectRef {
  return {
    kind: 'device',
    key: `device:${device.mac ?? ''}`,
    label: deviceLabel(device),
    pseudonymous: false,
    mac: device.mac,
    ip: device.ip,
    hostname: device.hostname,
  };
}

/** Generate the hourly records of every device for the days up to "today". */
function generate(): Dataset {
  const records: UsageRecord[] = [];
  const hours: DeviceHour[] = [];
  const blocked: BlockedRecord[] = [];
  DEVICES.forEach((device, deviceIndex) => {
    const roll = random(1009 * (deviceIndex + 1));
    const profile = device.profile;
    for (let dayIndex = 0; dayIndex < HISTORY_DAYS; dayIndex += 1) {
      const day = addDays(HISTORY_FIRST, dayIndex);
      const subject = subjectOf(device, day);
      const weekend = weekday(day) > 5;
      if (weekend && roll() > (profile.weekend ?? 0.05)) continue;
      // An occasional day off.
      if (!weekend && roll() < 0.06) continue;
      for (let hour = 0; hour < 24; hour += 1) {
        if (day === SAMPLE_TODAY && hour > LAST_HOUR_TODAY) break;
        const usual = hour >= profile.start && hour <= profile.end && hour !== 12;
        const evening =
          profile.evening && hour >= profile.evening.start && hour <= profile.evening.end;
        const chance = usual ? profile.busy : evening ? (profile.evening?.chance ?? 0) : 0;
        if (chance === 0 || roll() > chance) continue;
        const limit = day === SAMPLE_TODAY && hour === LAST_HOUR_TODAY ? LAST_MINUTE_TODAY : 58;
        const minutes = Math.max(
          1,
          Math.min(limit - 1, Math.round(60 * profile.intensity * (0.4 + roll() * 0.8))),
        );
        const offset = Math.floor(roll() * Math.max(1, limit - minutes));
        // Up to three domains share the hour's active minutes, one after the other.
        const count = 1 + Math.floor(roll() * 3);
        const chosen = new Map<number, number>();
        for (let index = 0; index < count; index += 1) {
          const at = SITE_INDEX.get(pick(profile.sites, roll())) ?? 0;
          chosen.set(at, (chosen.get(at) ?? 0) + 0.3 + roll());
        }
        const weight = [...chosen.values()].reduce((all, value) => all + value, 0);
        let left = minutes;
        let start = offset;
        const entries = [...chosen.entries()];
        entries.forEach(([at, part], index) => {
          const own =
            index === entries.length - 1
              ? left
              : Math.min(left, Math.round((minutes * part) / weight));
          left -= own;
          if (own <= 0) return;
          const seconds = own * 60;
          const down = Math.round(seconds * siteDef(at).rate * (0.6 + roll() * 0.8));
          records.push({
            device: deviceIndex,
            subject,
            day,
            hour,
            site: at,
            seconds,
            raw: Math.min(3600, 60 * Math.ceil((seconds * 1.3) / 60)),
            down,
            up: Math.round(down * (0.04 + roll() * 0.08)),
            sessions: 1 + Math.floor(seconds / 240),
            start,
            minutes: own,
          });
          start += own;
        });
        // Background traffic: online time and volume, no active time.
        let background = 0;
        if (roll() < 0.5) {
          const at = BACKGROUND[Math.floor(roll() * BACKGROUND.length)] ?? 0;
          const raw = 60 * (1 + Math.floor(roll() * 2));
          const down = Math.round(raw * siteDef(at).rate * (0.5 + roll()));
          background = raw;
          records.push({
            device: deviceIndex,
            subject,
            day,
            hour,
            site: at,
            seconds: 0,
            raw,
            down,
            up: Math.round(down * 0.01),
            sessions: 1,
            start: 0,
            minutes: 0,
          });
        }
        hours.push({
          device: deviceIndex,
          subject,
          day,
          hour,
          active: minutes * 60,
          online: Math.min(3600, minutes * 60 + background + 60 * Math.round(roll() * 10)),
          offset,
        });
        if (roll() < 0.04) {
          const target = BLOCKED_TARGETS[Math.floor(roll() * BLOCKED_TARGETS.length)];
          if (target)
            blocked.push({
              ...target,
              device: deviceIndex,
              subject,
              day,
              hour,
              time: instant(
                day,
                hour,
                offset + Math.floor(roll() * minutes),
                Math.floor(roll() * 60),
              ),
            });
        }
      }
    }
  });
  return { records, hours, blocked };
}

interface Selected {
  records: UsageRecord[];
  hours: DeviceHour[];
  blocked: BlockedRecord[];
}

/** The generated dataset; every answer computed like `arcilogger_worker.usage.query`. */
class GeneratedDays implements Source {
  private readonly data: Dataset;
  private readonly cache = new Map<string, Map<string, Stats>>();

  constructor(empty: boolean) {
    this.data = empty ? { records: [], hours: [], blocked: [] } : generate();
  }

  computedUntil(period: UsagePeriod): string | null {
    if (this.data.hours.length === 0 || period.to < HISTORY_FIRST || period.from > SAMPLE_TODAY)
      return null;
    return period.to >= SAMPLE_TODAY ? COMPUTED_UNTIL : instant(addDays(period.to, 1), 0);
  }

  private select(period: UsagePeriod, keys?: ReadonlySet<string> | null): Selected {
    const keep = (item: { subject: string; day: string; hour: number }) =>
      inPeriod(item.day, item.hour, period) && (!keys || keys.has(item.subject));
    return {
      records: this.data.records.filter(keep),
      hours: this.data.hours.filter(keep),
      blocked: this.data.blocked.filter(keep),
    };
  }

  private info(key: string): Info {
    const [kind, ...rest] = key.split(':');
    const ident = rest.join(':');
    if (kind === 'person') {
      const person = PERSONS.find((item) => item.id === Number(ident));
      const device = DEVICES.find((item) =>
        item.owners.some((owner) => owner.person === Number(ident)),
      );
      return {
        key,
        kind: 'person',
        label: person?.name ?? `#${ident}`,
        mac: null,
        ip: device?.ip ?? null,
        hostname: null,
        department: person?.department ?? device?.vlanDepartment ?? null,
        source: person?.department ? 'person' : device?.vlanDepartment ? 'vlan' : null,
      };
    }
    const device =
      kind === 'device'
        ? DEVICES.find((item) => item.mac === ident)
        : DEVICES.find((item) => item.mac === null && item.ip === ident);
    return {
      key,
      kind: kind === 'device' ? 'device' : 'ip',
      label: kind === 'device' ? (device ? deviceLabel(device) : ident) : ident,
      mac: kind === 'device' ? ident : null,
      ip: device?.ip ?? (kind === 'ip' ? ident : null),
      hostname: device?.hostname ?? null,
      department: device?.vlanDepartment ?? null,
      source: device?.vlanDepartment ? 'vlan' : null,
    };
  }

  private metrics(selected: Selected): Metrics {
    const metrics = emptyMetrics();
    let first: number | null = null;
    let last: number | null = null;
    for (const item of selected.hours) {
      metrics.online_seconds += item.online;
      metrics.active_seconds += item.active;
      if (!isWorkHour(item.day, item.hour)) metrics.off_hours_active_seconds += item.active;
      if (item.active === 0) continue;
      const start = Date.parse(instant(item.day, item.hour, item.offset));
      const end = start + item.active * 1000;
      if (first === null || start < first) first = start;
      if (last === null || end > last) last = end;
    }
    for (const item of selected.records) {
      metrics.download_bytes += item.down;
      metrics.upload_bytes += item.up;
      metrics.sessions += item.sessions;
    }
    metrics.blocked = selected.blocked.length;
    metrics.first_activity = first === null ? null : iso(first);
    metrics.last_activity = last === null ? null : iso(last);
    return metrics;
  }

  private group(selected: Selected): Map<string, Selected> {
    const found = new Map<string, Selected>();
    const entry = (key: string) => {
      const existing = found.get(key);
      if (existing) return existing;
      const created: Selected = { records: [], hours: [], blocked: [] };
      found.set(key, created);
      return created;
    };
    for (const item of selected.records) entry(item.subject).records.push(item);
    for (const item of selected.hours) entry(item.subject).hours.push(item);
    for (const item of selected.blocked) entry(item.subject).blocked.push(item);
    return found;
  }

  stats(period: UsagePeriod): Map<string, Stats> {
    const cacheKey = `${period.from}|${period.to}|${period.hours}`;
    const cached = this.cache.get(cacheKey);
    if (cached) return cached;
    const found = new Map<string, Stats>();
    for (const [key, own] of this.group(this.select(period)))
      found.set(key, { info: this.info(key), metrics: this.metrics(own) });
    this.cache.set(cacheKey, found);
    return found;
  }

  private siteRows(records: readonly UsageRecord[], usersOf = true): SiteRow[] {
    const grouped = new Map<number, UsageRecord[]>();
    for (const item of records) {
      const list = grouped.get(item.site);
      if (list) list.push(item);
      else grouped.set(item.site, [item]);
    }
    const total = sum(records, (item) => item.seconds);
    return sortSites(
      [...grouped.entries()].map(([index, items]) => {
        const def = siteDef(index);
        const seconds = sum(items, (item) => item.seconds);
        return {
          domain: def.domain,
          kind: def.kind,
          category: def.category,
          seconds,
          raw_seconds: sum(items, (item) => item.raw),
          share: share(seconds, total),
          download_bytes: sum(items, (item) => item.down),
          upload_bytes: sum(items, (item) => item.up),
          sessions: sum(items, (item) => item.sessions),
          users: usersOf ? new Set(items.map((item) => item.subject)).size : 1,
          background: def.background,
        };
      }),
    );
  }

  private categoryUsers(records: readonly UsageRecord[]): Map<string, number> {
    const sets = new Map<string, Set<string>>();
    for (const item of records) {
      const name = siteDef(item.site).category ?? UNKNOWN;
      const set = sets.get(name) ?? new Set();
      set.add(item.subject);
      sets.set(name, set);
    }
    return new Map([...sets.entries()].map(([name, set]) => [name, set.size]));
  }

  private appRows(records: readonly UsageRecord[]): AppRow[] {
    const grouped = new Map<string, AppRow>();
    for (const item of records) {
      const def = siteDef(item.site);
      if (!def.app) continue;
      const row = grouped.get(def.app) ?? {
        app: def.app,
        appcat: def.appcat,
        seconds: 0,
        bytes: 0,
      };
      row.seconds += item.seconds;
      row.bytes += item.down + item.up;
      grouped.set(def.app, row);
    }
    return [...grouped.values()].sort(
      (a, b) => b.seconds - a.seconds || b.bytes - a.bytes || byText(a.app, b.app),
    );
  }

  company(period: UsagePeriod): Bare<CompanyUsage> {
    const selected = this.select(period);
    const stats = [...this.stats(period).values()];
    const metrics = emptyMetrics();
    for (const item of stats) addMetrics(metrics, item.metrics);
    const byHour = new Map<
      string,
      { users: Set<string>; active: number; down: number; up: number }
    >();
    for (const item of selected.hours) {
      const slot = instant(item.day, item.hour);
      const entry = byHour.get(slot) ?? { users: new Set(), active: 0, down: 0, up: 0 };
      if (item.active > 0) entry.users.add(item.subject);
      entry.active += item.active;
      byHour.set(slot, entry);
    }
    for (const item of selected.records) {
      const slot = instant(item.day, item.hour);
      const entry = byHour.get(slot) ?? { users: new Set(), active: 0, down: 0, up: 0 };
      entry.down += item.down;
      entry.up += item.up;
      byHour.set(slot, entry);
    }
    const hourly: HourPoint[] = hourStarts(period).map((hour) => {
      const entry = byHour.get(hour);
      return {
        hour,
        active_users: entry?.users.size ?? 0,
        active_seconds: entry?.active ?? 0,
        download_bytes: entry?.down ?? 0,
        upload_bytes: entry?.up ?? 0,
      };
    });
    const sites = this.siteRows(selected.records);
    return {
      active_users: stats.filter((item) => item.metrics.active_seconds > 0).length,
      metrics,
      hourly,
      top_sites: sites.filter((row) => row.seconds > 0).slice(0, 10),
      top_categories: categoryRows(sites, this.categoryUsers(selected.records))
        .filter((row) => row.seconds > 0)
        .slice(0, 10),
      top_apps: this.appRows(selected.records).slice(0, 10),
      departments: departmentRows(stats),
    };
  }

  subjectRows(period: UsagePeriod, keys: readonly string[]): SubjectRow[] {
    const stats = this.stats(period);
    const grouped = this.group(this.select(period, new Set(keys)));
    return keys.flatMap((key) => {
      const item = stats.get(key);
      if (!item) return [];
      const seconds = new Map<string, number>();
      for (const record of grouped.get(key)?.records ?? []) {
        const domain = siteDef(record.site).domain;
        seconds.set(domain, (seconds.get(domain) ?? 0) + record.seconds);
      }
      return [
        {
          subject: realRef(item.info),
          department: item.info.department,
          metrics: item.metrics,
          ...tops([...seconds.entries()], categoryOfDomain),
        },
      ];
    });
  }

  subjectDetail(period: UsagePeriod, key: string): Bare<SubjectDetail> {
    const stats = this.stats(period).get(key);
    if (!stats) throw new Error(`no subject ${key}`);
    const own = this.select(period, new Set([key]));
    const records = own.records;
    const sites = this.siteRows(records, false);
    const categoryUsers = new Map(sites.map((row) => [row.category ?? UNKNOWN, 1]));

    // Host names: the first host of a domain gets most of its time.
    const hostnames = new Map<string, HostnameRow>();
    for (const item of records) {
      const def = siteDef(item.site);
      def.hosts.forEach((hostname, index) => {
        const part = def.hosts.length === 1 ? 1 : index === 0 ? 0.7 : 0.3 / (def.hosts.length - 1);
        const row = hostnames.get(hostname) ?? {
          hostname,
          domain: def.domain,
          seconds: 0,
          bytes: 0,
          sessions: 0,
        };
        row.seconds += Math.round(item.seconds * part);
        row.bytes += Math.round((item.down + item.up) * part);
        row.sessions += index === 0 ? item.sessions : Math.max(1, Math.round(item.sessions * part));
        hostnames.set(hostname, row);
      });
    }

    // URLs of the web filter log (paths of the first host), blocked ones too; newest first.
    const urls = new Map<string, UrlRow>();
    const seen = (
      row: Omit<UrlRow, 'hits' | 'first_seen' | 'last_seen'>,
      time: string,
      hits: number,
    ) => {
      const id = `${row.hostname ?? ''}${row.url}`;
      const entry = urls.get(id) ?? { ...row, hits: 0, first_seen: time, last_seen: time };
      entry.hits += hits;
      if (time < entry.first_seen) entry.first_seen = time;
      if (time > entry.last_seen) entry.last_seen = time;
      urls.set(id, entry);
    };
    for (const item of records) {
      const def = siteDef(item.site);
      if (def.paths.length === 0 || item.seconds === 0) continue;
      const path = def.paths[(item.hour + item.day.charCodeAt(9)) % def.paths.length] ?? '/';
      seen(
        { url: path, hostname: def.hosts[0] ?? def.domain, category: def.category, blocked: false },
        instant(item.day, item.hour, item.start),
        item.sessions * 3,
      );
    }
    for (const item of own.blocked)
      if (item.source === 'web' && item.url)
        seen(
          {
            url: item.url,
            hostname: item.hostname ?? null,
            category: item.category,
            blocked: true,
          },
          item.time,
          1,
        );

    // Every hour of the period with its top domain.
    const perHour = new Map<
      string,
      { online: number; active: number; down: number; up: number; top: Map<string, number> }
    >();
    const slotOf = (item: { day: string; hour: number }) => {
      const slot = instant(item.day, item.hour);
      const entry = perHour.get(slot) ?? {
        online: 0,
        active: 0,
        down: 0,
        up: 0,
        top: new Map<string, number>(),
      };
      perHour.set(slot, entry);
      return entry;
    };
    for (const item of own.hours) {
      const entry = slotOf(item);
      entry.online = Math.min(3600, entry.online + item.online);
      entry.active += item.active;
    }
    for (const item of records) {
      const entry = slotOf(item);
      entry.down += item.down;
      entry.up += item.up;
      if (item.seconds > 0) {
        const domain = siteDef(item.site).domain;
        entry.top.set(domain, (entry.top.get(domain) ?? 0) + item.seconds);
      }
    }
    const hourly: SubjectHour[] = hourStarts(period).map((hour) => {
      const entry = perHour.get(hour);
      const top = entry
        ? ([...entry.top.entries()].sort((a, b) => b[1] - a[1] || byText(a[0], b[0]))[0]?.[0] ??
          null)
        : null;
      return {
        hour,
        active_seconds: entry?.active ?? 0,
        online_seconds: entry?.online ?? 0,
        download_bytes: entry?.down ?? 0,
        upload_bytes: entry?.up ?? 0,
        top_domain: top,
      };
    });

    // The day's active minutes by category (`query._merge_timeline`).
    const timeline: TimelineSpan[] = [];
    if (period.from === period.to) {
      const start = Date.parse(instant(period.from, 0));
      const runs = records
        .filter((item) => item.minutes > 0)
        .map((item) => ({
          first: item.hour * 60 + item.start,
          end: item.hour * 60 + item.start + item.minutes,
          category: siteDef(item.site).category,
        }))
        .sort((a, b) => a.first - b.first || a.end - b.end);
      const spans: { first: number; end: number; category: string | null }[] = [];
      for (const run of runs) {
        const last = spans.at(-1);
        if (last && last.end === run.first && last.category === run.category) last.end = run.end;
        else spans.push({ ...run });
      }
      for (const span of spans)
        timeline.push({
          start: iso(start + span.first * 60_000),
          end: iso(start + span.end * 60_000),
          category: span.category,
        });
    }

    // The 30 days ending `to`, the same hours (`query.heatmap`).
    const heatFrom = addDays(period.to, -29);
    const heat = new Map<string, number>();
    for (const item of this.data.hours) {
      if (item.subject !== key || item.day < heatFrom || item.day > period.to) continue;
      if (!inPeriod(item.day, item.hour, { ...period, from: heatFrom })) continue;
      const cell = `${item.day}#${item.hour}`;
      heat.set(cell, (heat.get(cell) ?? 0) + item.active);
    }
    const heatmap: HeatCell[] = [];
    for (let day = heatFrom; day <= period.to; day = addDays(day, 1))
      for (let hour = 0; hour < 24; hour += 1)
        heatmap.push({
          day,
          hour,
          active_minutes: Math.floor((heat.get(`${day}#${hour}`) ?? 0) / 60),
        });

    // A person's devices (`query._devices`).
    const devices: SubjectRef[] = [];
    const perDevice: DeviceUsage[] = [];
    if (stats.info.kind === 'person') {
      const indexes = [...new Set([...own.hours, ...records].map((item) => item.device))].sort(
        (a, b) => byText(deviceDef(a).mac ?? '', deviceDef(b).mac ?? ''),
      );
      for (const index of indexes) {
        const mine = (item: { device: number }) => item.device === index;
        const ref = deviceRef(deviceDef(index));
        const seconds = new Map<string, number>();
        for (const item of records.filter(mine)) {
          const domain = siteDef(item.site).domain;
          seconds.set(domain, (seconds.get(domain) ?? 0) + item.seconds);
        }
        devices.push(ref);
        perDevice.push({
          device: ref,
          metrics: this.metrics({
            records: records.filter(mine),
            hours: own.hours.filter(mine),
            blocked: own.blocked.filter(mine),
          }),
          top_sites: tops([...seconds.entries()], categoryOfDomain).top_sites,
        });
      }
    }

    return {
      subject: realRef(stats.info),
      department: stats.info.department,
      devices,
      metrics: stats.metrics,
      timeline,
      sites,
      categories: categoryRows(sites, categoryUsers),
      apps: this.appRows(records),
      hostnames: [...hostnames.values()].sort(
        (a, b) => b.seconds - a.seconds || b.bytes - a.bytes || byText(a.hostname, b.hostname),
      ),
      urls: [...urls.values()]
        .sort((a, b) => byText(b.last_seen, a.last_seen) || byText(a.url, b.url))
        .slice(0, 1000),
      blocked: own.blocked
        .map(({ time, domain, category, action, source, hostname, url, app }) => ({
          time,
          domain,
          category,
          action,
          source,
          hostname: hostname ?? null,
          url: url ?? null,
          app: app ?? null,
        }))
        .sort((a, b) => byText(b.time, a.time))
        .slice(0, 5000),
      hourly,
      per_device: perDevice,
      heatmap,
    };
  }

  sites(scope: Scope): SiteRow[] {
    return this.siteRows(this.select(scope.period, scope.keys).records);
  }

  private restricted(records: readonly UsageRecord[]): Restricted[] {
    return records.map((item) => ({
      key: item.subject,
      domain: siteDef(item.site).domain,
      seconds: item.seconds,
      raw: item.raw,
      down: item.down,
      up: item.up,
      sessions: item.sessions,
      off: isWorkHour(item.day, item.hour) ? 0 : item.seconds,
    }));
  }

  private activeTotal(scope: Scope): number {
    return sum(
      [...this.stats(scope.period).values()].filter(
        (item) => scope.keys?.has(item.info.key) ?? true,
      ),
      (item) => item.metrics.active_seconds,
    );
  }

  siteDetail(scope: Scope, domain: string, capped: boolean): Bare<SiteDetail> | null {
    const index = SITE_INDEX.get(domain);
    if (index === undefined) return null;
    const records = this.select(scope.period, scope.keys).records.filter(
      (item) => item.site === index,
    );
    const totals = this.siteRows(records)[0];
    if (!totals) return null;
    const def = siteDef(index);
    const hosts = new Map<string, SiteHost>();
    for (const item of records)
      def.hosts.forEach((hostname, position) => {
        const part =
          def.hosts.length === 1 ? 1 : position === 0 ? 0.7 : 0.3 / (def.hosts.length - 1);
        const entry = hosts.get(hostname) ?? { hostname, seconds: 0, bytes: 0, sessions: 0 };
        entry.seconds += Math.round(item.seconds * part);
        entry.bytes += Math.round((item.down + item.up) * part);
        entry.sessions +=
          position === 0 ? item.sessions : Math.max(1, Math.round(item.sessions * part));
        hosts.set(hostname, entry);
      });
    const hostnames = [...hosts.values()].sort(
      (a, b) => b.seconds - a.seconds || b.bytes - a.bytes || byText(a.hostname, b.hostname),
    );
    const { users, total } = restrictedUsers(
      this.restricted(records),
      this.stats(scope.period),
      categoryOfDomain,
      { rawIsOnline: true, limit: capped ? USERS_MAX : null },
    );
    return {
      domain: def.domain,
      category: def.category,
      totals: { ...totals, share: share(totals.seconds, this.activeTotal(scope)) },
      hostnames: capped ? hostnames.slice(0, HOSTNAMES_MAX) : hostnames,
      users,
      users_total: total,
    };
  }

  categories(scope: Scope): CategoryRow[] {
    const records = this.select(scope.period, scope.keys).records;
    return categoryRows(this.siteRows(records), this.categoryUsers(records));
  }

  categoryDetail(scope: Scope, category: string, capped: boolean): Bare<CategoryDetail> | null {
    const totals = this.categories(scope).find((row) => row.category === category);
    if (!totals) return null;
    const records = this.select(scope.period, scope.keys).records.filter(
      (item) => (siteDef(item.site).category ?? UNKNOWN) === category,
    );
    const sites = this.siteRows(this.select(scope.period, scope.keys).records).filter(
      (row) => (row.category ?? UNKNOWN) === category,
    );
    const { users, total } = restrictedUsers(
      this.restricted(records),
      this.stats(scope.period),
      () => category,
      { rawIsOnline: false, limit: capped ? USERS_MAX : null },
    );
    return {
      category,
      totals,
      sites: capped ? sites.slice(0, CATEGORY_SITES_MAX) : sites,
      sites_total: sites.length,
      users,
      users_total: total,
    };
  }

  /** Every subject that ever had traffic (the pseudonym list). */
  everyone(): Stats[] {
    return [...this.stats({ from: HISTORY_FIRST, to: SAMPLE_TODAY, hours: 'all' }).values()];
  }
}

// -- exports (`usage_exports.py`) -----------------------------------------------------------------

/** A cell of an export: a value, a share (written with four decimals) or an instant. */
type Cell = string | number | boolean | null | { share: number } | { time: string | null };

interface Sheet {
  name: string;
  header: string[];
  rows: Cell[][];
}

const METRIC_HEADER = [
  'Çevrimiçi süre (sn)',
  'Aktif süre (sn, tahmini)',
  'Mesai dışı aktif (sn)',
  'İndirilen (bayt)',
  'Yüklenen (bayt)',
  'Oturum',
  'Engellenen',
  'İlk etkinlik',
  'Son etkinlik',
];
const SITE_HEADER = [
  'Alan adı',
  'Tür',
  'Kategori',
  'Süre (sn, tahmini)',
  'Ham süre (sn)',
  'Pay',
  'İndirilen (bayt)',
  'Yüklenen (bayt)',
  'Oturum',
  'Kullanıcı',
  'Arka plan',
];
const CATEGORY_HEADER = [
  'Kategori',
  'Süre (sn, tahmini)',
  'Pay',
  'İndirilen (bayt)',
  'Yüklenen (bayt)',
  'Kullanıcı',
];
const SUBJECT_HEADER = [
  'Ad',
  'Tür',
  'Anahtar',
  'MAC',
  'IP',
  'Departman',
  ...METRIC_HEADER,
  'En çok kullanılan siteler',
  'En çok kullanılan kategoriler',
];
const DEPARTMENT_HEADER = ['Departman', 'Kaynak', 'Aktif kullanıcı', ...METRIC_HEADER];
/** The users of one site or category: only the figures kept per site. */
const USER_HEADER = [
  'Ad',
  'Tür',
  'Anahtar',
  'MAC',
  'IP',
  'Departman',
  'Süre (sn, tahmini)',
  'Mesai dışı süre (sn, tahmini)',
  'İndirilen (bayt)',
  'Yüklenen (bayt)',
  'Oturum',
];
const SITE_USER_HEADER = [...USER_HEADER.slice(0, 7), 'Ham süre (sn)', ...USER_HEADER.slice(7)];
const CATEGORY_USER_HEADER = [...USER_HEADER, 'En çok kullanılan siteler'];

function metricCells(metrics: Metrics): Cell[] {
  return [
    metrics.online_seconds,
    metrics.active_seconds,
    metrics.off_hours_active_seconds,
    metrics.download_bytes,
    metrics.upload_bytes,
    metrics.sessions,
    metrics.blocked,
    { time: metrics.first_activity },
    { time: metrics.last_activity },
  ];
}

function siteCells(row: SiteRow): Cell[] {
  return [
    row.domain,
    row.kind,
    row.category,
    row.seconds,
    row.raw_seconds,
    { share: row.share },
    row.download_bytes,
    row.upload_bytes,
    row.sessions,
    row.users,
    row.background,
  ];
}

function categoryCells(row: CategoryRow): Cell[] {
  return [
    row.category,
    row.seconds,
    { share: row.share },
    row.download_bytes,
    row.upload_bytes,
    row.users,
  ];
}

function subjectCells(row: SubjectRow): Cell[] {
  return [
    row.subject.label,
    row.subject.kind,
    row.subject.key,
    row.subject.mac ?? null,
    row.subject.ip ?? null,
    row.department,
    ...metricCells(row.metrics),
    row.top_sites.map((site) => `${site.domain} (${site.seconds} sn)`).join(', '),
    row.top_categories.map((item) => `${item.category} (${item.seconds} sn)`).join(', '),
  ];
}

/** A row of `SITE_USER_HEADER` (`site`) or `CATEGORY_USER_HEADER`. */
function userCells(row: SubjectRow, site: boolean): Cell[] {
  const { subject, metrics } = row;
  // A site user's online time is the site's raw time; a category has none.
  const times = site ? [metrics.active_seconds, metrics.online_seconds] : [metrics.active_seconds];
  return [
    subject.label,
    subject.kind,
    subject.key,
    subject.mac ?? null,
    subject.ip ?? null,
    row.department,
    ...times,
    metrics.off_hours_active_seconds,
    metrics.download_bytes,
    metrics.upload_bytes,
    metrics.sessions,
    ...(site
      ? []
      : [row.top_sites.map((item) => `${item.domain} (${item.seconds} sn)`).join(', ')]),
  ];
}

function departmentCells(row: DepartmentRow): Cell[] {
  return [row.department, row.source, row.active_users, ...metricCells(row.metrics)];
}

/** Text of a cell (`csvfiles.cell` + `_csv_value`): site time, evet/hayır, `0,5833`. */
function cellText(value: Cell): string {
  if (value === null) return '';
  if (typeof value === 'boolean') return value ? 'evet' : 'hayır';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  if ('share' in value) return value.share.toFixed(4).replace('.', ',');
  if (value.time === null) return '';
  const local = new Date(Date.parse(value.time) + 3 * 3_600_000).toISOString();
  return `${local.slice(0, 19)}+03:00`;
}

/** A neutralised (`'` before `= + - @` TAB CR) and, where needed, quoted CSV field. */
function csvField(text: string): string {
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[;"\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** The first sheet as the API writes it: UTF-8 with BOM, `;`, CRLF. */
function csvText(sheet: Sheet): string {
  const lines = [sheet.header, ...sheet.rows.map((row) => row.map(cellText))].map((cells) =>
    cells.map(csvField).join(';'),
  );
  return `${BOM}${lines.map((line) => `${line}\r\n`).join('')}`;
}

// -- the mock -------------------------------------------------------------------------------------

type Reader = MockUser | Answer;

function isAnswer(value: Reader): value is Answer {
  return Array.isArray(value);
}

function isSheets(value: Sheet[] | Answer): value is Sheet[] {
  return value.length === 0 || typeof value[0] !== 'number';
}

/** A captured refusal of the usage API (`samples/usage.json` `errors`). */
function refusal(name: string, parameter?: string): Answer {
  const sample = USAGE.errors[name];
  if (!sample) throw new Error(`no usage error sample ${name}`);
  const body = copy(sample.body);
  if (parameter) {
    body.error.message_tr = body.error.message_tr.replace(/: \w+\.$/, `: ${parameter}.`);
    body.error.message_en = body.error.message_en.replace(/: \w+\.$/, `: ${parameter}.`);
  }
  return [sample.status, body];
}

/** 422 `usage_rebuild_no_logs` naming `first` (the captured answer names the capture's day). */
function noLogsBefore(first: string): Answer {
  const sample = USAGE.errors.usage_rebuild_no_logs;
  if (!sample) throw new Error('no usage error sample usage_rebuild_no_logs');
  const body = copy(sample.body);
  const day = first.split('-').reverse().join('.');
  body.error.message_tr = body.error.message_tr.replace(/\d{2}\.\d{2}\.\d{4}/, day);
  body.error.message_en = body.error.message_en.replace(/\d{2}\.\d{2}\.\d{4}/, day);
  return [sample.status, body];
}

interface Job {
  job: RebuildJob;
  polls: number;
  fails: boolean;
}

export class UsageMock {
  readonly options: UsageOptions;
  private readonly capturedDay = new CapturedDay();
  private readonly generated: GeneratedDays;
  /** Rebuild jobs by id. */
  readonly jobs = new Map<number, Job>();
  private nextJob = USAGE.rebuild_requested.job_id;

  constructor(options: UsageOptions = {}) {
    this.options = options;
    this.generated = new GeneratedDays(options.empty ?? false);
  }

  // -- routing ----------------------------------------------------------------------------------

  answer(call: RecordedCall, host: UsageHost): Answer | null {
    const { method, path } = call;
    if (!path.startsWith('/usage')) return null;
    let match: RegExpExecArray | null;
    if (method === 'GET' && path === '/usage/company') return this.company(call, host);
    if (method === 'GET' && path === '/usage/subjects') return this.subjects(call, host);
    if (method === 'GET' && (match = /^\/usage\/subjects\/([^/]+)$/.exec(path)))
      return this.subject(call, host, decodeURIComponent(match[1] ?? ''));
    if (method === 'GET' && path === '/usage/sites') return this.sites(call, host);
    if (method === 'GET' && (match = /^\/usage\/sites\/([^/]+)$/.exec(path)))
      return this.site(call, host, decodeURIComponent(match[1] ?? ''));
    if (method === 'GET' && path === '/usage/categories') return this.categories(call, host);
    if (method === 'GET' && (match = /^\/usage\/categories\/(.+)$/.exec(path)))
      return this.category(call, host, decodeURIComponent(match[1] ?? ''));
    if (method === 'GET' && (match = /^\/usage\/export\.(csv|xlsx)$/.exec(path)))
      return this.export(call, host, match[1] === 'xlsx' ? 'xlsx' : 'csv');
    if (method === 'POST' && path === '/usage/rebuild') return this.startRebuild(call, host);
    if (method === 'GET' && (match = /^\/usage\/rebuild\/([^/]+)$/.exec(path)))
      return this.rebuildStatus(call, host, match[1] ?? '');
    if (method === 'GET' && path === '/usage/pseudonyms') return this.pseudonyms(call, host);
    return host.error(404, 'not_found');
  }

  // -- access -----------------------------------------------------------------------------------

  /** Any of `permissions` (and the accepted KVKK notice with `notice`). */
  private reader(
    call: RecordedCall,
    host: UsageHost,
    permissions: readonly string[],
    notice: boolean,
  ): Reader {
    let denied: Answer | null = null;
    for (const permission of permissions) {
      const account = host.requirePermission(call, permission);
      if (!Array.isArray(account)) {
        if (notice && host.privacyRequired(account) && !account.privacy_accepted)
          return refusal('privacy_notice_required');
        return account;
      }
      const code = (account[1] as { error?: { code?: string } }).error?.code;
      if (account[0] !== 403 || code !== 'forbidden') return account;
      denied = account;
    }
    return denied ?? host.error(403, 'forbidden');
  }

  private companyReader(call: RecordedCall, host: UsageHost): Reader {
    return this.reader(call, host, ['dashboard.usage', 'usage.person', 'usage.anonymous'], false);
  }

  private usageReader(call: RecordedCall, host: UsageHost): Reader {
    return this.reader(call, host, ['usage.person', 'usage.anonymous'], true);
  }

  /** Pseudonyms for this viewer (`viewer_for`), or the 409 while the key is missing. */
  private pseudonymous(account: MockUser, host: UsageHost): boolean | Answer {
    const permissions = host.permissionsOf(account);
    if (permissions.includes('usage.person') || !permissions.includes('usage.anonymous'))
      return false;
    if (!this.options.pseudonymize) return false;
    if (this.options.keyMissing) return refusal('pseudonym_key_missing');
    return true;
  }

  // -- parameters -------------------------------------------------------------------------------

  private invalid(host: UsageHost, name: string, message: string): Answer {
    return host.error(422, 'validation_error', [
      { loc: ['query', name], type: 'value_error', msg: message },
    ]);
  }

  /** `period_of`: `to` defaults to today and `from` to `to`; at most 400 days. */
  private period(call: RecordedCall, host: UsageHost): UsagePeriod | Answer {
    const read = (name: string): string | null | Answer => {
      const value = call.query.get(name);
      if (value === null || value === '') return null;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`)))
        return this.invalid(host, name, 'Input should be a valid date');
      return value;
    };
    const from = read('from');
    const to = read('to');
    if (Array.isArray(from)) return from;
    if (Array.isArray(to)) return to;
    const hours = call.query.get('hours') ?? 'all';
    if (!['all', 'work', 'off'].includes(hours))
      return this.invalid(host, 'hours', "Input should be 'all', 'work' or 'off'");
    const last = to ?? SAMPLE_TODAY;
    const first = from ?? last;
    const days = (Date.parse(`${last}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`)) / 86_400_000;
    if (last < first || days >= MAX_DAYS) return refusal('usage_period');
    return { from: first, to: last, hours: hours as UsageHours };
  }

  private paging(call: RecordedCall, host: UsageHost): { limit: number; offset: number } | Answer {
    const limit = Number(call.query.get('limit') ?? 50);
    const offset = Number(call.query.get('offset') ?? 0);
    if (!Number.isInteger(limit) || limit < 1 || limit > PAGE_MAX)
      return this.invalid(host, 'limit', 'Input should be between 1 and 100000');
    if (!Number.isInteger(offset) || offset < 0 || offset > MAX_OFFSET)
      return this.invalid(host, 'offset', 'Input should be between 0 and 1000000');
    return { limit, offset };
  }

  private choice<T extends string>(
    call: RecordedCall,
    host: UsageHost,
    name: string,
    allowed: readonly T[],
    fallback: T,
  ): T | Answer {
    const value = call.query.get(name) ?? fallback;
    return (allowed as readonly string[]).includes(value)
      ? (value as T)
      : this.invalid(
          host,
          name,
          `Input should be ${allowed.map((item) => `'${item}'`).join(', ')}`,
        );
  }

  // -- sources ----------------------------------------------------------------------------------

  /** The captured day (every hour; the company view also its work and off hours). */
  private source(period: UsagePeriod, company = false): Source {
    const captured =
      !this.options.empty &&
      period.from === CAPTURE_DAY &&
      period.to === CAPTURE_DAY &&
      (period.hours === 'all' || company);
    return captured ? this.capturedDay : this.generated;
  }

  private envelope(source: Source, period: UsagePeriod) {
    return {
      period,
      estimated: true,
      computed_until: source.computedUntil(period),
      notice_key: NOTICE_KEY,
    };
  }

  private scope(source: Source, period: UsagePeriod, department: string | null): Scope {
    if (!department) return { period, department: null, keys: null };
    const keys = new Set(
      [...source.stats(period).values()]
        .filter((item) => (item.info.department ?? UNKNOWN) === department)
        .map((item) => item.info.key),
    );
    return { period, department, keys };
  }

  private present(ref: SubjectRef, pseudonymous: boolean): SubjectRef {
    return pseudonymous ? pseudonymOf(ref) : ref;
  }

  private presentRows(rows: SubjectRow[], pseudonymous: boolean): SubjectRow[] {
    return rows.map((row) => ({ ...row, subject: this.present(row.subject, pseudonymous) }));
  }

  // -- reads ------------------------------------------------------------------------------------

  /** The view of a request: period, viewer, department; or the refusal. */
  private context(
    call: RecordedCall,
    host: UsageHost,
  ):
    | { account: MockUser; period: UsagePeriod; pseudonymous: boolean; department: string | null }
    | Answer {
    const account = this.usageReader(call, host);
    if (isAnswer(account)) return account;
    const period = this.period(call, host);
    if (Array.isArray(period)) return period;
    const pseudonymous = this.pseudonymous(account, host);
    if (Array.isArray(pseudonymous)) return pseudonymous;
    return {
      account,
      period,
      pseudonymous,
      department: call.query.get('department') || null,
    };
  }

  private companyAnswer(period: UsagePeriod): CompanyUsage {
    const source = this.source(period, true);
    return { ...source.company(period), ...this.envelope(source, period) };
  }

  private company(call: RecordedCall, host: UsageHost): Answer {
    const account = this.companyReader(call, host);
    if (isAnswer(account)) return account;
    // The Panel asks without a period (the server's today); as in `mock-alerts.ts` it gets the
    // captured day, so its card shows the captured figures.
    if (!call.query.get('from') && !call.query.get('to')) return [200, copy(USAGE.company)];
    const period = this.period(call, host);
    if (Array.isArray(period)) return period;
    return [200, this.companyAnswer(period)];
  }

  /** `query.subjects_page` without the paging: every row of the filtered, sorted list. */
  private subjectList(
    call: RecordedCall,
    host: UsageHost,
    view: { period: UsagePeriod; pseudonymous: boolean; department: string | null },
    sortParam: string | null,
  ): { source: Source; rows: SubjectRow[] | DepartmentRow[] } | Answer {
    const kind = this.choice(
      call,
      host,
      'kind',
      ['person', 'device', 'department'] as const,
      'person',
    );
    if (Array.isArray(kind)) return kind;
    const sort =
      sortParam === null
        ? this.choice(call, host, 'sort', SUBJECT_SORTS, 'active_seconds')
        : (SUBJECT_SORTS as readonly string[]).includes(sortParam)
          ? (sortParam as SubjectSort)
          : 'active_seconds';
    if (Array.isArray(sort)) return sort;
    const order = this.choice(call, host, 'order', ['asc', 'desc'] as const, 'desc');
    if (Array.isArray(order)) return order;
    const q = call.query.get('q')?.trim() ?? '';
    const needle = fold(q);
    const source = this.source(view.period);
    const scope = this.scope(source, view.period, view.department);
    const stats = [...source.stats(view.period).values()].filter(
      (item) => scope.keys?.has(item.info.key) ?? true,
    );
    const desc = order === 'desc';
    if (kind === 'department') {
      const rows = departmentRows(stats).filter(
        (row) => !needle || fold(row.department).includes(needle),
      );
      if (sort === 'label')
        rows.sort((a, b) => byText(fold(a.department), fold(b.department)) * (desc ? -1 : 1));
      else rows.sort((a, b) => (a.metrics[sort] - b.metrics[sort]) * (desc ? -1 : 1));
      return { source, rows };
    }
    const kinds: readonly SubjectKind[] = kind === 'person' ? ['person'] : ['device', 'ip'];
    const labelOf = (item: Stats) =>
      fold(this.present(realRef(item.info), view.pseudonymous).label);
    const selected = stats
      .filter((item) => kinds.includes(item.info.kind))
      .filter((item) => {
        if (!needle) return true;
        const texts = view.pseudonymous
          ? [pseudonymOf(realRef(item.info)).label]
          : [item.info.label, item.info.mac ?? '', item.info.ip ?? '', item.info.hostname ?? ''];
        return texts.some((text) => fold(text).includes(needle));
      })
      .sort((a, b) => byText(labelOf(a), labelOf(b)));
    if (sort === 'label') selected.sort((a, b) => byText(labelOf(a), labelOf(b)) * (desc ? -1 : 1));
    else selected.sort((a, b) => (a.metrics[sort] - b.metrics[sort]) * (desc ? -1 : 1));
    const rows = source.subjectRows(
      view.period,
      selected.map((item) => item.info.key),
    );
    return { source, rows: this.presentRows(rows, view.pseudonymous) };
  }

  private subjects(call: RecordedCall, host: UsageHost): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    const paging = this.paging(call, host);
    if (Array.isArray(paging)) return paging;
    const list = this.subjectList(call, host, view, null);
    if (Array.isArray(list)) return list;
    const page = {
      ...this.envelope(list.source, view.period),
      items: list.rows.slice(paging.offset, paging.offset + paging.limit),
      total: list.rows.length,
      limit: paging.limit,
      offset: paging.offset,
    };
    return [200, page];
  }

  /** `query.find_subject`: the real key behind `key` (a token for pseudonymous viewers). */
  private findSubject(source: Source, period: UsagePeriod, key: string, pseudonymous: boolean) {
    const stats = source.stats(period);
    if (pseudonymous) {
      if (!/^[pd]:/.test(key)) return null;
      return [...stats.keys()].find((candidate) => tokenOf(candidate) === key) ?? null;
    }
    const real = key.startsWith('device:') ? `device:${key.slice(7).toLowerCase()}` : key;
    return stats.has(real) ? real : null;
  }

  private subjectAnswer(
    period: UsagePeriod,
    key: string,
    pseudonymous: boolean,
  ): SubjectDetail | null {
    const source = this.source(period);
    const real = this.findSubject(source, period, key, pseudonymous);
    if (real === null) return null;
    const detail = source.subjectDetail(period, real);
    return {
      ...detail,
      ...this.envelope(source, period),
      subject: this.present(detail.subject, pseudonymous),
      devices: detail.devices.map((device) => this.present(device, pseudonymous)),
      per_device: detail.per_device.map((item) => ({
        ...item,
        device: this.present(item.device, pseudonymous),
      })),
    };
  }

  private subject(call: RecordedCall, host: UsageHost, key: string): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    const detail = this.subjectAnswer(view.period, key, view.pseudonymous);
    return detail ? [200, detail] : refusal('usage_subject_not_found');
  }

  /** `query.sites_page` without the paging. */
  private siteList(
    call: RecordedCall,
    host: UsageHost,
    view: { period: UsagePeriod; department: string | null },
    sortParam: string | null,
  ): { source: Source; rows: SiteRow[] } | Answer {
    const sort =
      sortParam === null
        ? this.choice(call, host, 'sort', SITE_SORTS, 'seconds')
        : (SITE_SORTS as readonly string[]).includes(sortParam)
          ? (sortParam as SiteSort)
          : 'seconds';
    if (Array.isArray(sort)) return sort;
    const order = this.choice(call, host, 'order', ['asc', 'desc'] as const, 'desc');
    if (Array.isArray(order)) return order;
    const needle = fold(call.query.get('q')?.trim() ?? '');
    const category = call.query.get('category');
    const source = this.source(view.period);
    let rows = source.sites(this.scope(source, view.period, view.department));
    if (needle) rows = rows.filter((row) => fold(row.domain).includes(needle));
    if (category !== null) rows = rows.filter((row) => (row.category ?? UNKNOWN) === category);
    const desc = order === 'desc';
    if (sort === 'domain') rows.sort((a, b) => byText(a.domain, b.domain) * (desc ? -1 : 1));
    else if (sort !== 'seconds' || !desc) {
      rows.sort((a, b) => byText(a.domain, b.domain));
      rows.sort((a, b) => (a[sort] - b[sort]) * (desc ? -1 : 1));
    }
    return { source, rows };
  }

  private sites(call: RecordedCall, host: UsageHost): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    const paging = this.paging(call, host);
    if (Array.isArray(paging)) return paging;
    const list = this.siteList(call, host, view, null);
    if (Array.isArray(list)) return list;
    const page: SitePage = {
      ...this.envelope(list.source, view.period),
      items: list.rows.slice(paging.offset, paging.offset + paging.limit),
      total: list.rows.length,
      limit: paging.limit,
      offset: paging.offset,
    };
    return [200, page];
  }

  private siteAnswer(
    view: { period: UsagePeriod; pseudonymous: boolean; department: string | null },
    domain: string,
    capped = true,
  ): SiteDetail | null {
    const source = this.source(view.period);
    const scope = this.scope(source, view.period, view.department);
    const detail = source.siteDetail(scope, domain, capped);
    if (!detail) return null;
    return {
      ...detail,
      ...this.envelope(source, view.period),
      users: this.presentRows(detail.users, view.pseudonymous),
    };
  }

  private site(call: RecordedCall, host: UsageHost, domain: string): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    const detail = this.siteAnswer(view, domain);
    return detail ? [200, detail] : refusal('usage_site_not_found');
  }

  private categoryList(view: { period: UsagePeriod; department: string | null }): CategoryList {
    const source = this.source(view.period);
    return {
      ...this.envelope(source, view.period),
      items: source.categories(this.scope(source, view.period, view.department)),
    };
  }

  private categories(call: RecordedCall, host: UsageHost): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    return [200, this.categoryList(view)];
  }

  private categoryAnswer(
    view: { period: UsagePeriod; pseudonymous: boolean; department: string | null },
    category: string,
    capped = true,
  ): CategoryDetail | null {
    const source = this.source(view.period);
    const scope = this.scope(source, view.period, view.department);
    const detail = source.categoryDetail(scope, category, capped);
    if (!detail) return null;
    return {
      ...detail,
      ...this.envelope(source, view.period),
      users: this.presentRows(detail.users, view.pseudonymous),
    };
  }

  private category(call: RecordedCall, host: UsageHost, category: string): Answer {
    const view = this.context(call, host);
    if (Array.isArray(view)) return view;
    const detail = this.categoryAnswer(view, category);
    return detail ? [200, detail] : refusal('usage_category_not_found');
  }

  // -- exports ----------------------------------------------------------------------------------

  /** `GET /usage/export.csv|xlsx?view=…`: the view's sheets (CSV: the first one). */
  private export(call: RecordedCall, host: UsageHost, format: 'csv' | 'xlsx'): Answer {
    const account = this.companyReader(call, host);
    if (isAnswer(account)) return account;
    const period = this.period(call, host);
    if (Array.isArray(period)) return period;
    if (call.query.get('view') === null) return this.invalid(host, 'view', 'Field required');
    const view = this.choice(call, host, 'view', VIEWS, 'company');
    if (Array.isArray(view)) return view;
    let sheets: Sheet[];
    let pseudonymous = false;
    if (view === 'company') {
      sheets = this.companySheets(this.companyAnswer(period));
    } else {
      const context = this.context(call, host);
      if (Array.isArray(context)) return context;
      pseudonymous = context.pseudonymous;
      const built = this.viewSheets(call, host, view, context);
      if (!isSheets(built)) return built;
      sheets = built;
    }
    const compact = (day: string) => day.replace(/-/g, '');
    const name = `arcilogger-kullanim-${view}-${compact(period.from)}-${compact(period.to)}.${format}`;
    const headers = {
      'Content-Disposition': `attachment; filename="${name}"`,
      'Cache-Control': 'no-store',
    };
    if (format === 'xlsx') {
      // Not a real workbook: the sheet names and headers, behind the ZIP signature.
      const outline = [
        ...sheets.map((sheet) => `${sheet.name}: ${sheet.header.join(' | ')}`),
        'Yöntem',
      ];
      return [
        200,
        null,
        {
          body: Buffer.concat([
            Buffer.from('PK\u0003\u0004', 'latin1'),
            Buffer.from(outline.join('\n')),
          ]),
          contentType: XLSX_TYPE,
          headers,
        },
      ];
    }
    const main = sheets[0];
    if (!main) throw new Error('an export without sheets');
    const text = (pseudonymous ? null : this.capturedCsv(call, period)) ?? csvText(main);
    return [
      200,
      null,
      { body: Buffer.from(text, 'utf8'), contentType: 'text/csv; charset=utf-8', headers },
    ];
  }

  /** The captured file of the same request, if there is one. */
  private capturedCsv(call: RecordedCall, period: UsagePeriod): string | null {
    if (this.source(period, call.query.get('view') === 'company') !== this.capturedDay) return null;
    if (period.hours !== 'all') return null;
    const own = [...call.query.entries()]
      .filter(([name]) => !['from', 'to', 'hours'].includes(name))
      .sort((a, b) => byText(a[0], b[0]));
    const subject = USAGE.subject_person.subject.key;
    const requests: [Record<string, string>, { text: string }][] = [
      [{ view: 'company' }, USAGE.export_company_csv],
      [{ view: 'subjects', kind: 'person' }, USAGE.export_subjects_csv],
      [
        { view: 'subjects', kind: 'person', department: 'Finans' },
        USAGE.export_subjects_department_csv,
      ],
      [{ view: 'subjects', kind: 'department' }, USAGE.export_departments_csv],
      [{ view: 'subject', key: subject }, USAGE.export_subject_csv],
      [{ view: 'site', domain: USAGE.site.domain }, USAGE.export_site_csv],
      [{ view: 'category', category: USAGE.category.category }, USAGE.export_category_csv],
    ];
    for (const [params, sample] of requests) {
      const wanted = Object.entries(params).sort((a, b) => byText(a[0], b[0]));
      if (JSON.stringify(wanted) === JSON.stringify(own)) return sample.text;
    }
    return null;
  }

  private companySheets(data: CompanyUsage): Sheet[] {
    return [
      {
        name: 'Departmanlar',
        header: DEPARTMENT_HEADER,
        rows: [
          ['Şirket (toplam)', '', data.active_users, ...metricCells(data.metrics)],
          ...data.departments.map(departmentCells),
        ],
      },
      { name: 'Özet', header: ['Alan', 'Değer'], rows: [] },
      {
        name: 'Saatlik',
        header: [
          'Saat',
          'Aktif kullanıcı',
          'Aktif süre (sn)',
          'İndirilen (bayt)',
          'Yüklenen (bayt)',
        ],
        rows: [],
      },
      { name: 'Siteler', header: SITE_HEADER, rows: data.top_sites.map(siteCells) },
      {
        name: 'Kategoriler',
        header: CATEGORY_HEADER,
        rows: data.top_categories.map(categoryCells),
      },
      {
        name: 'Uygulamalar',
        header: ['Uygulama', 'Uygulama kategorisi', 'Süre (sn)', 'Bayt'],
        rows: [],
      },
    ];
  }

  private viewSheets(
    call: RecordedCall,
    host: UsageHost,
    view: Exclude<View, 'company'>,
    context: { period: UsagePeriod; pseudonymous: boolean; department: string | null },
  ): Sheet[] | Answer {
    const sort = call.query.get('sort') ?? 'active_seconds';
    const summary: Sheet = { name: 'Özet', header: ['Alan', 'Değer'], rows: [] };
    if (view === 'subjects') {
      const list = this.subjectList(call, host, context, sort);
      if (Array.isArray(list)) return list;
      if (call.query.get('kind') === 'department')
        return [
          {
            name: 'Departmanlar',
            header: DEPARTMENT_HEADER,
            rows: (list.rows as DepartmentRow[]).map(departmentCells),
          },
        ];
      return [
        {
          name: 'Kişiler ve cihazlar',
          header: SUBJECT_HEADER,
          rows: (list.rows as SubjectRow[]).map(subjectCells),
        },
      ];
    }
    if (view === 'sites') {
      const list = this.siteList(call, host, context, sort);
      if (Array.isArray(list)) return list;
      return [{ name: 'Siteler', header: SITE_HEADER, rows: list.rows.map(siteCells) }];
    }
    if (view === 'categories')
      return [
        {
          name: 'Kategoriler',
          header: CATEGORY_HEADER,
          rows: this.categoryList(context).items.map(categoryCells),
        },
      ];
    if (view === 'subject') {
      const key = call.query.get('key');
      if (!key) return refusal('usage_export_view', 'key');
      const detail = this.subjectAnswer(context.period, key, context.pseudonymous);
      if (!detail) return refusal('usage_subject_not_found');
      return [
        { name: 'Siteler', header: SITE_HEADER, rows: detail.sites.map(siteCells) },
        summary,
        {
          name: 'Kategoriler',
          header: CATEGORY_HEADER,
          rows: detail.categories.map(categoryCells),
        },
        ...[
          'Uygulamalar',
          'Ana bilgisayarlar',
          "URL'ler",
          'Engellenenler',
          'Saatlik',
          'Cihazlar',
          'Zaman çizelgesi',
        ].map((name) => ({ name, header: [], rows: [] })),
      ];
    }
    if (view === 'site') {
      const domain = call.query.get('domain');
      if (!domain) return refusal('usage_export_view', 'domain');
      const detail = this.siteAnswer(context, domain, false);
      if (!detail) return refusal('usage_site_not_found');
      return [
        {
          name: 'Kullanıcılar',
          header: SITE_USER_HEADER,
          rows: detail.users.map((row) => userCells(row, true)),
        },
        summary,
        {
          name: 'Ana bilgisayarlar',
          header: ['Ana bilgisayar', 'Süre (sn)', 'Bayt', 'Oturum'],
          rows: [],
        },
      ];
    }
    const category = call.query.get('category');
    if (!category) return refusal('usage_export_view', 'category');
    const detail = this.categoryAnswer(context, category, false);
    if (!detail) return refusal('usage_category_not_found');
    return [
      { name: 'Siteler', header: SITE_HEADER, rows: detail.sites.map(siteCells) },
      summary,
      {
        name: 'Kullanıcılar',
        header: CATEGORY_USER_HEADER,
        rows: detail.users.map((row) => userCells(row, false)),
      },
    ];
  }

  // -- rebuild ----------------------------------------------------------------------------------

  private startRebuild(call: RecordedCall, host: UsageHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as { from: string; to: string };
    const days =
      (Date.parse(`${body.to}T00:00:00Z`) - Date.parse(`${body.from}T00:00:00Z`)) / 86_400_000;
    if (!(days >= 0) || body.to > SAMPLE_TODAY || days >= MAX_DAYS)
      return refusal('usage_rebuild_range');
    // `request_rebuild`: not before the oldest log (the first generated day; none: today).
    const oldest = this.options.empty ? SAMPLE_TODAY : HISTORY_FIRST;
    if (body.from < oldest) return noLogsBefore(oldest);
    const job: RebuildJob = {
      ...copy(USAGE.rebuild_requested),
      job_id: this.nextJob,
      from: body.from,
      to: body.to,
      requested_by: account.username,
      created_at: instant(SAMPLE_TODAY, 15, 0, this.nextJob),
    };
    this.nextJob += 1;
    this.jobs.set(job.job_id, { job, polls: 0, fails: this.options.rebuildFails ?? false });
    return [202, job];
  }

  private rebuildStatus(call: RecordedCall, host: UsageHost, id: string): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    if (!/^[1-9]\d{0,17}$/.test(id))
      return host.error(422, 'validation_error', [
        { loc: ['path', 'job_id'], type: 'int_parsing', msg: 'Input should be a valid integer' },
      ]);
    const entry = this.jobs.get(Number(id));
    if (!entry) return refusal('usage_rebuild_not_found');
    const { job } = entry;
    const step = entry.polls;
    entry.polls += 1;
    const days =
      (Date.parse(`${job.to}T00:00:00Z`) - Date.parse(`${job.from}T00:00:00Z`)) / 86_400_000 + 1;
    const at = (seconds: number) => iso(Date.parse(job.created_at) + seconds * 1000);
    if (step === 0) return [200, job];
    if (step < 3) {
      const done = Math.min(days - 1, Math.ceil((days * step) / 3));
      return [
        200,
        {
          ...job,
          status: 'running',
          started_at: at(1),
          progress: Math.round((done / days) * 10_000) / 10_000,
        },
      ];
    }
    if (entry.fails)
      return [
        200,
        {
          ...job,
          status: 'failed',
          started_at: at(1),
          finished_at: at(3),
          progress: 0.5,
          error: 'database busy (injected)',
        },
      ];
    return [200, { ...job, status: 'done', started_at: at(1), finished_at: at(3), progress: 1 }];
  }

  // -- pseudonyms -------------------------------------------------------------------------------

  /** `GET /usage/pseudonyms` (administrators): who is behind each pseudonym. */
  private pseudonyms(call: RecordedCall, host: UsageHost): Answer {
    const account = this.reader(call, host, ['users.manage'], true);
    if (isAnswer(account)) return account;
    if (this.options.keyMissing) return refusal('pseudonyms_without_key');
    const needle = fold(call.query.get('q')?.trim() ?? '');
    const subjects = new Map<string, SubjectRef>();
    for (const item of this.generated.everyone()) subjects.set(item.info.key, realRef(item.info));
    for (const row of USAGE.pseudonyms) subjects.set(row.subject.key, row.subject);
    const rows: PseudonymRow[] = [...subjects.values()]
      .map((subject) => ({ pseudonym: pseudonymOf(subject).label, kind: subject.kind, subject }))
      .filter((row) => {
        if (!needle) return true;
        const texts = [
          row.pseudonym,
          tokenOf(row.subject.key),
          row.subject.label,
          row.subject.mac ?? '',
          row.subject.ip ?? '',
        ];
        return texts.some((text) => fold(text).includes(needle));
      })
      .sort((a, b) => byText(a.pseudonym, b.pseudonym) || byText(a.subject.key, b.subject.key));
    return [200, rows];
  }
}
