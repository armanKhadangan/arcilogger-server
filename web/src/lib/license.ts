/**
 * The licence as the web app shows it (licensing contract section 8): which modules are on, which
 * banners to show, how the request code is copied. Pure functions, unit-tested in
 * `license.test.ts`; the data is `GET /license` (`useLicense()`).
 *
 * The server decides everything: a locked module's API routes answer 403 `module_not_licensed`
 * whatever the browser shows. The web app only follows the state: it marks locked menu entries,
 * shows a locked page instead of running a module's queries, and explains the state in banners.
 * 5651 functions (collection, sealing, lookups, evidence packages, alerts, audit) never depend on
 * anything here.
 */

import type { TFunction } from 'i18next';

import type {
  LicenseEventOut,
  LicenseModuleOut,
  LicenseState,
  LicenseOut,
  LicenseTimeOut,
} from './api-types';
import { formatDay } from './display';
import { formatNumber, type Lang } from './format';

/** "5651 Temel": in every licence and every state. */
export const CORE_MODULE = 'core_5651';

/** The registry keys this version of the web app gates (`arcilogger_core.license.modules`). */
export type ModuleKey = 'core_5651' | 'usage_analytics' | 'usage_reports' | 'bulk_import';

/** Every state, in the order of the contract (3.2). */
export const LICENSE_STATES: readonly LicenseState[] = [
  'trial',
  'trial_expired',
  'licensed',
  'invalid',
  'wrong_server',
  'expired',
  'version_not_covered',
];

/** The licence page (administrators: `settings.manage`). */
export const LICENSE_PAGE = '/settings/license';
/** Holders of this permission see the licence page, its details and the admin banners. */
export const LICENSE_ADMIN_PERMISSION = 'settings.manage';
/** A `.lic` file is at most 64 KiB (the API refuses larger ones with `license_too_large`). */
export const LICENSE_MAX_BYTES = 65_536;
/** The shell shows at most this many banners (the first ones of {@link bannersFor}). */
export const MAX_BANNERS = 2;
/** "İndir (.txt)": the request code on one line plus CRLF. */
export const REQUEST_CODE_FILE = 'arcilogger-istek-kodu.txt';

/**
 * Whether `key` is enabled. `true` while the licence is loading or could not be read: this is for
 * menu marks and texts only, never a reason to run a module's queries (the API enforces anyway).
 */
export function moduleEnabled(status: LicenseOut | null | undefined, key: string): boolean {
  if (!status || key === CORE_MODULE) return true;
  return status.modules.some((module) => module.key === key && module.enabled);
}

/** The registry entry of `key` in the answer. */
export function moduleOf(
  status: LicenseOut | null | undefined,
  key: string,
): LicenseModuleOut | undefined {
  return status?.modules.find((module) => module.key === key);
}

/** A module's name as the API sends it, in `lang` (its key when the answer lacks it). */
export function moduleName(module: LicenseModuleOut | undefined, key: string, lang: Lang): string {
  if (!module) return key;
  return lang === 'en' ? module.name_en : module.name_tr;
}

/** How the module table shows a module: on, licensed but off (state, `requires`), not licensed. */
export function moduleShownState(module: LicenseModuleOut): 'on' | 'off' | 'notLicensed' {
  if (module.enabled) return 'on';
  return module.licensed ? 'off' : 'notLicensed';
}

/**
 * The verified licence lists no paid module of the registry (only `core_5651`, or only keys this
 * version does not know): its state then turns nothing off.
 */
export function isBaseOnly(status: LicenseOut): boolean {
  return !status.modules.some((module) => !module.always && module.licensed);
}

export type LicenseTone = 'info' | 'success' | 'warning' | 'danger';

/** Tone of the state badge. */
export function stateTone(state: LicenseState): LicenseTone {
  switch (state) {
    case 'licensed':
      return 'success';
    case 'trial':
      return 'info';
    case 'invalid':
    case 'wrong_server':
      return 'danger';
    default:
      return 'warning';
  }
}

export type BannerTone = 'info' | 'warning' | 'danger';

export type BannerId =
  | 'invalid'
  | 'wrongServer'
  | 'expired'
  | 'versionNotCovered'
  | 'trialClockInconsistent'
  | 'trialExpired'
  | 'trial'
  | 'baseOnly'
  | 'usersExceeded'
  | 'sourcesExceeded'
  | 'maintenanceExpired'
  | 'maintenanceEndsSoon';

/** One banner of the shell (contract 8.3); its text is {@link bannerText}. */
export interface LicenseBanner {
  id: BannerId;
  /** 1: the state, 2: limits, 3: maintenance. */
  order: 1 | 2 | 3;
  tone: BannerTone;
  /** i18n key (under `license.banner.`). */
  key: string;
  /** Numbers and texts of the message; `count` selects the plural form. */
  values: Record<string, string | number>;
  /** `YYYY-MM-DD` days of the message, written per language by {@link bannerText}. */
  dates: Record<string, string>;
  /** `{{state}}`: the state label of the licence page. */
  stateLabel?: LicenseState;
}

function banner(
  id: BannerId,
  order: LicenseBanner['order'],
  tone: BannerTone,
  values: LicenseBanner['values'] = {},
  dates: LicenseBanner['dates'] = {},
): LicenseBanner {
  return { id, order, tone, key: `license.banner.${id}`, values, dates };
}

/** The state's own banner (order 1), `null` when there is none for this user. */
function stateBanner(status: LicenseOut, isAdmin: boolean): LicenseBanner | null {
  const state = status.status;
  if (
    (state === 'wrong_server' || state === 'expired' || state === 'version_not_covered') &&
    isBaseOnly(status)
  ) {
    // Nothing paid is affected: no banner for users, one info line for administrators.
    return isAdmin ? { ...banner('baseOnly', 1, 'info'), stateLabel: state } : null;
  }
  switch (state) {
    case 'invalid':
      return banner('invalid', 1, 'danger');
    case 'wrong_server': {
      const serverId = status.details?.server_id ?? null;
      // Without `details` (users) or an unreadable id, the sentence goes without the id.
      return serverId
        ? banner('wrongServer', 1, 'danger', { serverId })
        : { ...banner('wrongServer', 1, 'danger'), key: 'license.banner.wrongServerNoId' };
    }
    case 'expired':
      return banner('expired', 1, 'warning', {}, { date: status.valid_until ?? '' });
    case 'version_not_covered':
      return banner(
        'versionNotCovered',
        1,
        'warning',
        { version: status.product.version },
        { releaseDate: status.product.release_date, until: status.maintenance?.until ?? '' },
      );
    case 'trial_expired':
      return status.problem === 'trial_clock_inconsistent'
        ? banner('trialClockInconsistent', 1, 'warning')
        : banner('trialExpired', 1, 'warning');
    case 'trial':
      return banner('trial', 1, 'info', { count: status.trial?.days_left ?? 0 });
    case 'licensed':
      return null;
  }
}

/**
 * Every banner for this user, in display order (the shell shows the first {@link MAX_BANNERS}).
 * Administrators (`settings.manage`) also get the limit and maintenance banners.
 */
export function bannersFor(status: LicenseOut, { isAdmin }: { isAdmin: boolean }): LicenseBanner[] {
  const banners: LicenseBanner[] = [];
  const own = stateBanner(status, isAdmin);
  if (own) banners.push(own);
  if (!isAdmin) return banners;

  const { users, log_sources: sources } = status.limits;
  if (users.exceeded && users.max !== null)
    banners.push(banner('usersExceeded', 2, 'warning', { used: users.used, max: users.max }));
  if (sources.exceeded && sources.max !== null) {
    const values = { used: sources.used, max: sources.max };
    banners.push(
      sources.unconfigured > 0
        ? {
            ...banner('sourcesExceeded', 2, 'warning', {
              ...values,
              unconfigured: sources.unconfigured,
            }),
            key: 'license.banner.sourcesExceededUnconfigured',
          }
        : banner('sourcesExceeded', 2, 'warning', values),
    );
  }

  const maintenance = status.maintenance;
  if (maintenance?.expired) {
    banners.push(banner('maintenanceExpired', 3, 'info', {}, { until: maintenance.until }));
  } else if (maintenance?.ends_soon) {
    banners.push(
      banner(
        'maintenanceEndsSoon',
        3,
        'info',
        { count: maintenance.days_left },
        { until: maintenance.until },
      ),
    );
  }
  return banners;
}

/** A licence day in messages: `20.10.2027` (Turkish) or `2027-10-20` (English), as the API's. */
export function licenseDayText(day: string, lang: Lang): string {
  return lang === 'en' ? day : formatDay(day);
}

/** The banner's sentence in `lang` (`t` of that language). */
export function bannerText(item: LicenseBanner, t: TFunction, lang: Lang): string {
  const params: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(item.values)) {
    // `count` stays a number: i18next picks `_one` / `_other` with it.
    params[name] =
      typeof value === 'number' && name !== 'count' ? formatNumber(value, 0, lang) : value;
  }
  for (const [name, day] of Object.entries(item.dates)) params[name] = licenseDayText(day, lang);
  if (item.stateLabel) params.state = t(`license.page.states.${item.stateLabel}`);
  return t(item.key, params);
}

/**
 * The request code as it is copied and saved: one line, exactly the server's text (the API
 * groups it with hyphens; any whitespace a display added is removed). The parser accepts lost
 * hyphens and line breaks anyway (contract 2.2).
 */
export function formatRequestCode(code: string): string {
  return code.replace(/\s+/g, '');
}

/** Content of `arcilogger-istek-kodu.txt`: the code on one line plus CRLF. */
export function requestCodeFileText(code: string): string {
  return `${formatRequestCode(code)}\r\n`;
}

/** Client-side check before an upload: the i18n key of the problem, `null` when fine. */
export function licenseFileProblem(file: { size: number }): string | null {
  return file.size > LICENSE_MAX_BYTES ? 'license.errors.tooLarge' : null;
}

/**
 * "Zaman kaynağı": the floor of the trusted time that won (contract 3.3), with "sistem saati
 * geride" when a non-system floor is more than 5 minutes ahead of the system clock.
 */
export function timeSourceText(time: LicenseTimeOut, t: TFunction): string {
  const source = t(`license.page.about.timeSources.${time.source}`, { defaultValue: time.source });
  return time.clock_behind ? `${source} (${t('license.page.about.clockBehind')})` : source;
}

function stateName(value: unknown, t: TFunction): string {
  return typeof value === 'string'
    ? t(`license.page.states.${value}`, { defaultValue: value })
    : '—';
}

/** The "Ayrıntı" cell of a history row, from the event's details (empty when there is none). */
export function historySummary(entry: LicenseEventOut, t: TFunction): string {
  const details = entry.details;
  switch (entry.kind) {
    case 'install':
      return details.unchanged === true ? t('license.page.history.unchanged') : '';
    case 'refuse':
      return typeof details.code === 'string' ? details.code : '';
    case 'state':
      return `${stateName(details.from, t)} → ${stateName(details.to, t)}`;
    case 'limit': {
      if (typeof details.limit !== 'string') return '';
      const name = t(`license.page.limits.${details.limit}`, { defaultValue: details.limit });
      const used = typeof details.used === 'number' ? String(details.used) : '?';
      const max =
        typeof details.max === 'number' ? String(details.max) : t('license.page.limits.unlimited');
      const verdict = details.exceeded
        ? t('license.page.history.exceeded')
        : t('license.page.history.withinLimit');
      return `${name}: ${used}/${max} (${verdict})`;
    }
    default:
      return '';
  }
}
