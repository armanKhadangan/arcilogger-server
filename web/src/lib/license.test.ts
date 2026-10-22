/**
 * The licence helpers (licensing contract 8.1, 8.3): module answers, banner selection for every
 * state and role, their order, the base-only and clock-inconsistent banners, the exact texts with
 * the `{{count}}` plurals, and the request-code formatting.
 */

import type { LicenseEventOut, LicenseOut, LicenseTimeSource } from '@/lib/api-types';
import i18n from '@/lib/i18n';
import { LICENSE_DOCUMENT_NAMES, type LicenseDocumentName } from '@/test/license-samples';
import { LICENSE_REQUEST_CODE, LICENSE_SERVER_ID, licenseSample } from '@/test/samples';

import type { Lang } from './format';
import {
  bannersFor,
  bannerText,
  CORE_MODULE,
  historySummary,
  isBaseOnly,
  LICENSE_MAX_BYTES,
  LICENSE_STATES,
  licenseDayText,
  licenseFileProblem,
  MAX_BANNERS,
  moduleEnabled,
  moduleName,
  moduleOf,
  moduleShownState,
  REQUEST_CODE_FILE,
  formatRequestCode,
  requestCodeFileText,
  stateTone,
  timeSourceText,
} from './license';

const tr = i18n.getFixedT('tr');
const en = i18n.getFixedT('en');

function texts(status: LicenseOut, isAdmin: boolean, lang: Lang): string[] {
  return bannersFor(status, { isAdmin }).map((item) =>
    bannerText(item, lang === 'en' ? en : tr, lang),
  );
}

function ids(name: LicenseDocumentName, isAdmin: boolean): string[] {
  return bannersFor(licenseSample(name), { isAdmin }).map((item) => item.id);
}

describe('moduleEnabled', () => {
  it('is true while the licence is loading or could not be read (the API enforces)', () => {
    expect(moduleEnabled(undefined, 'usage_analytics')).toBe(true);
    expect(moduleEnabled(null, 'bulk_import')).toBe(true);
  });

  it('follows the API: every module in the trial, 5651 Temel only when unlicensed', () => {
    const trial = licenseSample('trial');
    const unlicensed = licenseSample('trial_expired');
    for (const key of ['usage_analytics', 'usage_reports', 'bulk_import']) {
      expect(moduleEnabled(trial, key)).toBe(true);
      expect(moduleEnabled(unlicensed, key)).toBe(false);
    }
    expect(moduleEnabled(unlicensed, CORE_MODULE)).toBe(true);
  });

  it('5651 Temel is enabled in every state; a key the answer does not list is off', () => {
    for (const name of LICENSE_DOCUMENT_NAMES) {
      const status = licenseSample(name);
      expect(moduleEnabled(status, CORE_MODULE), name).toBe(true);
      expect(moduleEnabled(status, 'rule_engine'), name).toBe(false);
    }
  });

  it('a licence listing reports and analytics enables them, not the CSV import', () => {
    const status = licenseSample('licensed');
    expect(moduleEnabled(status, 'usage_analytics')).toBe(true);
    expect(moduleEnabled(status, 'usage_reports')).toBe(true);
    expect(moduleEnabled(status, 'bulk_import')).toBe(false);
  });

  it('names a module in the interface language, its key when unknown', () => {
    const status = licenseSample('trial');
    expect(moduleName(moduleOf(status, 'bulk_import'), 'bulk_import', 'tr')).toBe(
      'Toplu içe aktarma (CSV)',
    );
    expect(moduleName(moduleOf(status, 'bulk_import'), 'bulk_import', 'en')).toBe(
      'Bulk import (CSV)',
    );
    expect(moduleName(moduleOf(status, 'nope'), 'nope', 'tr')).toBe('nope');
  });

  it('shows a module as on, off (licensed but off) or not licensed', () => {
    const expired = licenseSample('expired');
    expect(expired.modules.map(moduleShownState)).toEqual(['on', 'off', 'off', 'notLicensed']);
    expect(licenseSample('trial').modules.map(moduleShownState)).toEqual(['on', 'on', 'on', 'on']);
    expect(licenseSample('trial_expired').modules.map(moduleShownState)).toEqual([
      'on',
      'notLicensed',
      'notLicensed',
      'notLicensed',
    ]);
  });
});

describe('bannersFor — every state and role', () => {
  // [fixture, administrator's banners, other users' banners]
  const table: [LicenseDocumentName, string[], string[]][] = [
    ['trial', ['trial'], ['trial']],
    ['trial_last_day', ['trial'], ['trial']],
    ['trial_expired', ['trialExpired'], ['trialExpired']],
    ['trial_clock_inconsistent', ['trialClockInconsistent'], ['trialClockInconsistent']],
    ['licensed', [], []],
    ['licensed_limits_exceeded', ['usersExceeded', 'sourcesExceeded'], []],
    ['maintenance_ends_soon', ['maintenanceEndsSoon'], []],
    ['maintenance_expired', ['maintenanceExpired'], []],
    // The captured site has 4 active users: without a verified licence the limit is 3 (3.2).
    ['invalid', ['invalid', 'usersExceeded'], ['invalid']],
    ['revoked', ['invalid', 'usersExceeded'], ['invalid']],
    ['wrong_server', ['wrongServer'], ['wrongServer']],
    ['expired', ['expired'], ['expired']],
    ['version_not_covered', ['versionNotCovered', 'maintenanceExpired'], ['versionNotCovered']],
    ['version_not_covered_base_only', ['baseOnly', 'maintenanceExpired'], []],
  ];

  it.each(table)('%s', (name, admin, others) => {
    expect(ids(name, true)).toEqual(admin);
    expect(ids(name, false)).toEqual(others);
  });

  it('covers every state', () => {
    const covered = new Set(table.map(([name]) => licenseSample(name).status));
    expect([...covered].sort()).toEqual([...LICENSE_STATES].sort());
  });

  it('danger for an unverifiable licence or another server, warning for locks, info else', () => {
    const tone = (name: LicenseDocumentName) =>
      bannersFor(licenseSample(name), { isAdmin: true }).map((item) => item.tone);
    expect(tone('invalid')).toEqual(['danger', 'warning']);
    expect(tone('wrong_server')).toEqual(['danger']);
    expect(tone('expired')).toEqual(['warning']);
    expect(tone('trial_expired')).toEqual(['warning']);
    expect(tone('trial')).toEqual(['info']);
    expect(tone('licensed_limits_exceeded')).toEqual(['warning', 'warning']);
    expect(tone('maintenance_ends_soon')).toEqual(['info']);
    expect(tone('version_not_covered_base_only')).toEqual(['info', 'info']);
  });

  it('orders state, limits, maintenance; the shell shows the first two', () => {
    const status = licenseSample('version_not_covered');
    status.limits = licenseSample('licensed_limits_exceeded').limits;
    const banners = bannersFor(status, { isAdmin: true });
    expect(banners.map((item) => [item.order, item.id])).toEqual([
      [1, 'versionNotCovered'],
      [2, 'usersExceeded'],
      [2, 'sourcesExceeded'],
      [3, 'maintenanceExpired'],
    ]);
    expect(MAX_BANNERS).toBe(2);
    // Users see the state only.
    expect(bannersFor(status, { isAdmin: false }).map((item) => item.id)).toEqual([
      'versionNotCovered',
    ]);
  });

  it('limit banners in the unlicensed state too (users max 3 stays a limit)', () => {
    const status = licenseSample('trial_expired');
    status.limits.users = { max: 3, used: 5, unconfigured: 0, exceeded: true, full: true };
    expect(texts(status, true, 'tr')).toEqual([
      'Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor.',
      'Kullanıcı sayısı lisans sınırını aşıyor (5/3): yeni kullanıcı eklenemez.',
    ]);
  });

  it('no limit banner without a maximum (trial: unlimited)', () => {
    const status = licenseSample('trial');
    status.limits.users = { max: null, used: 900, unconfigured: 0, exceeded: false, full: false };
    expect(ids('trial', true)).toEqual(['trial']);
  });

  it('maintenance "ends soon" gives way to "ended"', () => {
    const status = licenseSample('maintenance_expired');
    if (status.maintenance) status.maintenance.ends_soon = true;
    expect(bannersFor(status, { isAdmin: true }).map((item) => item.id)).toEqual([
      'maintenanceExpired',
    ]);
  });
});

describe('base-only licence', () => {
  it('lists no paid module: no state banner for users, one info line for administrators', () => {
    for (const state of ['wrong_server', 'expired', 'version_not_covered'] as const) {
      const status = licenseSample('version_not_covered_base_only');
      status.status = state;
      expect(isBaseOnly(status)).toBe(true);
      expect(bannersFor(status, { isAdmin: false }).filter((item) => item.order === 1)).toEqual([]);
      const [first] = bannersFor(status, { isAdmin: true });
      expect(first?.id).toBe('baseOnly');
      expect(first?.tone).toBe('info');
    }
  });

  it('names the state with the licence page label', () => {
    const status = licenseSample('version_not_covered_base_only');
    expect(texts(status, true, 'tr')[0]).toBe(
      'Lisans durumu: Sürüm bakım kapsamında değil — kapanan ücretli modül yok.',
    );
    expect(texts(status, true, 'en')[0]).toBe(
      'Licence state: Version not covered by maintenance — no paid module is affected.',
    );
    status.status = 'expired';
    expect(texts(status, true, 'tr')[0]).toBe(
      'Lisans durumu: Süresi doldu — kapanan ücretli modül yok.',
    );
  });

  it('counts unknown keys as no paid module; an invalid licence is never base-only quiet', () => {
    const status = licenseSample('expired');
    status.modules = status.modules.map((module) => ({
      ...module,
      licensed: module.always,
    }));
    expect(isBaseOnly(status)).toBe(true);
    expect(isBaseOnly(licenseSample('expired'))).toBe(false);
    expect(ids('invalid', false)).toEqual(['invalid']);
  });
});

describe('banner texts (contract 8.3, Turkish first)', () => {
  it.each<[LicenseDocumentName, string, string]>([
    [
      'invalid',
      'Lisans dosyası doğrulanamadı: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor.',
      'The licence file cannot be verified: paid modules are off. 5651 log collection, sealing, lookups and evidence packages keep working.',
    ],
    [
      'wrong_server',
      `Lisans bu sunucu için değil (sunucu kimliği ${LICENSE_SERVER_ID}): ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.`,
      `The licence is not for this server (server id ${LICENSE_SERVER_ID}): paid modules are off. The 5651 functions keep working.`,
    ],
    [
      'expired',
      'Lisansın süresi 10.10.2026 tarihinde doldu: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.',
      'The licence expired on 2026-10-10: paid modules are off. The 5651 functions keep working.',
    ],
    [
      'version_not_covered',
      'Bu sürüm (0.1.0, 01.10.2026) bakım süresinden (30.09.2026) sonra yayımlandı: ücretli modüller kapalı. Bakımı yenileyip yeni lisans dosyasını yükleyin. 5651 işlevleri çalışmaya devam ediyor.',
      'This version (0.1.0, 2026-10-01) was released after the maintenance period (2026-09-30): paid modules are off. Renew maintenance and upload the new licence file. The 5651 functions keep working.',
    ],
    [
      'trial_clock_inconsistent',
      'Deneme başlangıcı sistem saatinden ileride: deneme süresi kullanılamıyor. Deneme lisansı için arman@khadangan.com ile iletişime geçin.',
      'The trial start lies after the current time: the trial is unavailable. Contact arman@khadangan.com for a demo licence.',
    ],
    [
      'trial_expired',
      'Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor.',
      'The trial has ended: paid modules are off. 5651 log collection, sealing, lookups and evidence packages keep working.',
    ],
    ['trial', 'Deneme sürümü — 6 gün kaldı.', 'Trial version — 6 days left.'],
    ['trial_last_day', 'Deneme sürümü — 1 gün kaldı.', 'Trial version — 1 day left.'],
  ])('%s', (name, turkish, english) => {
    const status = licenseSample(name);
    expect(texts(status, false, 'tr')[0]).toBe(turkish);
    expect(texts(status, false, 'en')[0]).toBe(english);
  });

  it('another server without a known id (users, or the id unreadable)', () => {
    expect(texts(licenseSample('wrong_server', { viewer: true }), false, 'tr')).toEqual([
      'Lisans bu sunucu için değil: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.',
    ]);
    const unreadable = licenseSample('wrong_server');
    unreadable.problem = 'license_server_id_unavailable';
    if (unreadable.details) unreadable.details.server_id = null;
    expect(texts(unreadable, true, 'en')).toEqual([
      'The licence is not for this server: paid modules are off. The 5651 functions keep working.',
    ]);
  });

  it('limits: the unconfigured part only when there are unconfigured senders', () => {
    const status = licenseSample('licensed_limits_exceeded');
    // A licence for 3 sources and 3 users; 4 of each in use, 2 of the sources unconfigured.
    expect(texts(status, true, 'tr')).toEqual([
      'Kullanıcı sayısı lisans sınırını aşıyor (4/3): yeni kullanıcı eklenemez.',
      'Log kaynağı sayısı lisans sınırını aşıyor (4/3; 2 yapılandırılmamış). Kayıtlar toplanmaya ve mühürlenmeye devam ediyor.',
    ]);
    expect(texts(status, true, 'en')).toEqual([
      'The number of users exceeds the licence limit (4/3): no new user can be added.',
      'The number of log sources exceeds the licence limit (4/3; 2 unconfigured). Logs are still collected and sealed.',
    ]);
    status.limits.log_sources.unconfigured = 0;
    expect(texts(status, true, 'tr')[1]).toBe(
      'Log kaynağı sayısı lisans sınırını aşıyor (4/3). Kayıtlar toplanmaya ve mühürlenmeye devam ediyor.',
    );
    expect(texts(status, true, 'en')[1]).toBe(
      'The number of log sources exceeds the licence limit (4/3). Logs are still collected and sealed.',
    );
  });

  it('formats large counts per language', () => {
    const status = licenseSample('licensed_limits_exceeded');
    status.limits.users = { max: 1000, used: 1200, unconfigured: 0, exceeded: true, full: true };
    expect(texts(status, true, 'tr')[0]).toContain('(1.200/1.000)');
    expect(texts(status, true, 'en')[0]).toContain('(1,200/1,000)');
  });

  it('maintenance texts with plurals (`{{count}}`)', () => {
    expect(texts(licenseSample('maintenance_expired'), true, 'tr')).toEqual([
      'Bakım süresi doldu (05.10.2026): yeni sürümler ve destek için bakımı yenileyin. Kurulu sürüm çalışmaya devam eder.',
    ]);
    expect(texts(licenseSample('maintenance_expired'), true, 'en')).toEqual([
      'Maintenance has ended (2026-10-05): renew it for new versions and support. The installed version keeps working.',
    ]);
    const soon = licenseSample('maintenance_ends_soon');
    expect(texts(soon, true, 'tr')).toEqual(['Bakım süresi 12 gün sonra bitiyor (25.10.2026).']);
    expect(texts(soon, true, 'en')).toEqual(['Maintenance ends in 12 days (2026-10-25).']);
    if (soon.maintenance) soon.maintenance.days_left = 1;
    expect(texts(soon, true, 'tr')).toEqual(['Bakım süresi 1 gün sonra bitiyor (25.10.2026).']);
    expect(texts(soon, true, 'en')).toEqual(['Maintenance ends in 1 day (2026-10-25).']);
  });

  it('trial plurals in both languages', () => {
    const status = licenseSample('trial');
    for (const [count, turkish, english] of [
      [1, 'Deneme sürümü — 1 gün kaldı.', 'Trial version — 1 day left.'],
      [2, 'Deneme sürümü — 2 gün kaldı.', 'Trial version — 2 days left.'],
      [14, 'Deneme sürümü — 14 gün kaldı.', 'Trial version — 14 days left.'],
    ] as const) {
      if (status.trial) status.trial.days_left = count;
      expect(texts(status, false, 'tr')).toEqual([turkish]);
      expect(texts(status, false, 'en')).toEqual([english]);
    }
  });

  it('never claims legal compliance', () => {
    for (const name of LICENSE_DOCUMENT_NAMES) {
      for (const lang of ['tr', 'en'] as const) {
        for (const text of texts(licenseSample(name), true, lang)) {
          expect(text).not.toMatch(/yasal olarak uyumlu|legally compliant/i);
        }
      }
    }
  });
});

describe('days and the request code', () => {
  it('writes licence days as the API messages do', () => {
    expect(licenseDayText('2027-10-20', 'tr')).toBe('20.10.2027');
    expect(licenseDayText('2027-10-20', 'en')).toBe('2027-10-20');
  });

  it('copies the code as one line, exactly the server’s grouping', () => {
    expect(formatRequestCode(LICENSE_REQUEST_CODE)).toBe(LICENSE_REQUEST_CODE);
    const wrapped = `  ${LICENSE_REQUEST_CODE.slice(0, 40)}\n${LICENSE_REQUEST_CODE.slice(40, 90)} \r\n\t${LICENSE_REQUEST_CODE.slice(90)}  `;
    expect(formatRequestCode(wrapped)).toBe(LICENSE_REQUEST_CODE);
    expect(formatRequestCode(LICENSE_REQUEST_CODE)).toMatch(
      /^ARCIREQ1-[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{1,5})+$/,
    );
  });

  it('saves the code on one line plus CRLF', () => {
    const text = requestCodeFileText(`${LICENSE_REQUEST_CODE}\n`);
    expect(text).toBe(`${LICENSE_REQUEST_CODE}\r\n`);
    expect(text.split('\r\n')).toEqual([LICENSE_REQUEST_CODE, '']);
    expect(REQUEST_CODE_FILE).toBe('arcilogger-istek-kodu.txt');
  });

  it('refuses a file over 64 KiB before uploading it', () => {
    expect(LICENSE_MAX_BYTES).toBe(65_536);
    expect(licenseFileProblem({ size: 65_536 })).toBeNull();
    expect(licenseFileProblem({ size: 65_537 })).toBe('license.errors.tooLarge');
    expect(tr('license.errors.tooLarge')).toBe('Lisans dosyası çok büyük (en çok 64 KiB).');
  });
});

describe('licence page helpers', () => {
  it('tones the state badge', () => {
    expect(LICENSE_STATES.map(stateTone)).toEqual([
      'info',
      'warning',
      'success',
      'danger',
      'danger',
      'warning',
      'warning',
    ]);
  });

  it.each<[LicenseTimeSource, string, string]>([
    ['tsa', 'Kamu SM zaman damgası', 'TSA time stamp'],
    ['system', 'sistem saati', 'system clock'],
    ['vault_day', 'kasadaki son mühürlü gün', 'newest sealed day in the vault'],
    ['release_date', 'sürümün yayın tarihi', 'release date of this version'],
    ['license_issued', 'lisansın düzenlenme tarihi', 'issue date of the licence'],
  ])('names the time source %s', (source, turkish, english) => {
    const time = {
      now: '2026-10-13T07:00:00+03:00',
      source,
      tsa_time: null,
      vault_day_end: null,
      system_time: '2026-10-13T07:00:00+03:00',
      clock_behind: false,
    };
    expect(timeSourceText(time, tr)).toBe(turkish);
    expect(timeSourceText(time, en)).toBe(english);
    expect(timeSourceText({ ...time, clock_behind: true }, tr)).toBe(
      `${turkish} (sistem saati geride)`,
    );
    expect(timeSourceText({ ...time, clock_behind: true }, en)).toBe(
      `${english} (system clock is behind)`,
    );
  });

  it('summarises history events', () => {
    const entry = (kind: string, details: Record<string, unknown>): LicenseEventOut => ({
      at: '2026-10-13T07:00:00+03:00',
      // A newer server may record kinds this version does not know.
      kind: kind as LicenseEventOut['kind'],
      source: 'web',
      actor: 'admin',
      license_id: null,
      details,
    });
    expect(historySummary(entry('install', { unchanged: true }), tr)).toBe('aynı lisans');
    expect(historySummary(entry('install', { unchanged: false }), tr)).toBe('');
    expect(historySummary(entry('refuse', { code: 'license_revoked' }), tr)).toBe(
      'license_revoked',
    );
    expect(historySummary(entry('state', { from: null, to: 'trial' }), tr)).toBe(
      '— → Deneme sürümü',
    );
    expect(historySummary(entry('state', { from: 'trial', to: 'trial_expired' }), en)).toBe(
      'Trial → Unlicensed',
    );
    expect(
      historySummary(
        entry('limit', { limit: 'log_sources', exceeded: true, max: 10, used: 12 }),
        tr,
      ),
    ).toBe('Log kaynağı sayısı: 12/10 (aşıldı)');
    expect(
      historySummary(entry('limit', { limit: 'users', exceeded: false, max: 25, used: 24 }), en),
    ).toBe('Number of user accounts: 24/25 (within the limit)');
    expect(historySummary(entry('remove', {}), tr)).toBe('');
    expect(historySummary(entry('future', { x: 1 }), tr)).toBe('');
  });

  it('summarises the events as the API records them (captured history)', () => {
    const history = licenseSample('licensed_limits_exceeded').details?.history ?? [];
    expect(history.map((item) => [item.kind, historySummary(item, tr)])).toEqual([
      ['limit', 'Kullanıcı sayısı: 4/3 (aşıldı)'],
      ['limit', 'Log kaynağı sayısı: 4/3 (aşıldı)'],
      ['install', ''],
      ['state', 'Deneme sürümü → Lisanslı'],
      ['install', 'aynı lisans'],
      ['install', ''],
      ['refuse', 'license_version_not_covered'],
      ['refuse', 'license_expired'],
      ['refuse', 'license_other_server'],
      ['refuse', 'license_revoked'],
      ['refuse', 'license_signature_invalid'],
      ['refuse', 'license_unknown_key'],
      ['refuse', 'license_too_large'],
      ['refuse', 'license_malformed'],
      ['state', '— → Deneme sürümü'],
    ]);
    const later = licenseSample('maintenance_ends_soon').details?.history ?? [];
    expect(later.slice(0, 3).map((item) => historySummary(item, en))).toEqual([
      'Number of user accounts: 4/25 (within the limit)',
      'Number of log sources: 4/10 (within the limit)',
      '',
    ]);
  });
});
