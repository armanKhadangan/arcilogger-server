import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import type { LicenseDocumentName } from '../src/test/license-samples';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { REFUSED_FILES } from './mock-license';
import { LICENSE } from './samples';

/*
 * The licence in the browser (licensing contract 8): the banners of every captured state for an
 * administrator and another user, locked module pages and menu marks without requests to the
 * locked API, the Panel's locked usage card, upload and removal with re-authentication, every
 * refusal text, the request code, the users limit and the accessibility of the new pages. The
 * mock serves the real API's answers (`samples/license.json`, captured by
 * `web/scripts/capture_api_samples.py`).
 */

const banners = (page: Page) => page.getByTestId('license-banners');
const REQUEST_CODE = LICENSE.trial.details?.request_code ?? '';
const SERVER_ID = LICENSE.trial.details?.server_id ?? '';

function gatedCalls(api: MockApi): string[] {
  return api.calls
    .filter((call) => /^\/(usage|reports|imports)(\/|$)/.test(call.path))
    .map((call) => `${call.method} ${call.path}`);
}

async function upload(page: Page, name: string) {
  await page.getByLabel('Lisans dosyası (.lic)').setInputFiles({
    name,
    mimeType: 'application/json',
    buffer: Buffer.from('{"payload": {}, "signature": {}}\n'),
  });
  await page.getByRole('button', { name: 'Yükle', exact: true }).click();
}

const PAGE_LINK = ' Lisans sayfası';
const CONTACT = ' Yöneticinize başvurun.';
const TRIAL_EXPIRED =
  'Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor.';
const INVALID =
  'Lisans dosyası doğrulanamadı: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor.';
const CLOCK =
  'Deneme başlangıcı sistem saatinden ileride: deneme süresi kullanılamıyor. Deneme lisansı için arman@khadangan.com ile iletişime geçin.';
const VERSION =
  'Bu sürüm (0.1.0, 01.10.2026) bakım süresinden (30.09.2026) sonra yayımlandı: ücretli modüller kapalı. Bakımı yenileyip yeni lisans dosyasını yükleyin. 5651 işlevleri çalışmaya devam ediyor.';
const MAINTENANCE_ENDED =
  'Bakım süresi doldu (30.09.2026): yeni sürümler ve destek için bakımı yenileyin. Kurulu sürüm çalışmaya devam eder.';
// The mock has four active users (the limit without a verified licence is three).
const USERS_OVER_3 = 'Kullanıcı sayısı lisans sınırını aşıyor (4/3): yeni kullanıcı eklenemez.';

/**
 * The banners every captured state shows (at most two; `alert` for danger, else `status`):
 * [administrator (tolga), another user (ayse.yilmaz, auditor)], each `[test id, role, text]`
 * (the HR role, whose only page is the Panel, is covered below).
 */
type Shown = [string, 'alert' | 'status', string][];
const BANNERS: [LicenseDocumentName, Shown, Shown][] = [
  [
    'trial',
    [['trial', 'status', `Deneme sürümü — 6 gün kaldı.${PAGE_LINK}`]],
    [['trial', 'status', `Deneme sürümü — 6 gün kaldı.${CONTACT}`]],
  ],
  [
    'trial_last_day',
    [['trial', 'status', `Deneme sürümü — 1 gün kaldı.${PAGE_LINK}`]],
    [['trial', 'status', `Deneme sürümü — 1 gün kaldı.${CONTACT}`]],
  ],
  [
    'trial_expired',
    [
      ['trialExpired', 'status', `${TRIAL_EXPIRED}${PAGE_LINK}`],
      ['usersExceeded', 'status', `${USERS_OVER_3}${PAGE_LINK}`],
    ],
    [['trialExpired', 'status', `${TRIAL_EXPIRED}${CONTACT}`]],
  ],
  [
    'trial_clock_inconsistent',
    [
      ['trialClockInconsistent', 'status', `${CLOCK}${PAGE_LINK}`],
      ['usersExceeded', 'status', `${USERS_OVER_3}${PAGE_LINK}`],
    ],
    [['trialClockInconsistent', 'status', `${CLOCK}${CONTACT}`]],
  ],
  ['licensed', [], []],
  [
    'licensed_limits_exceeded',
    [
      ['usersExceeded', 'status', `${USERS_OVER_3}${PAGE_LINK}`],
      [
        'sourcesExceeded',
        'status',
        `Log kaynağı sayısı lisans sınırını aşıyor (4/3; 2 yapılandırılmamış). Kayıtlar toplanmaya ve mühürlenmeye devam ediyor.${PAGE_LINK}`,
      ],
    ],
    [],
  ],
  [
    'maintenance_ends_soon',
    [
      [
        'maintenanceEndsSoon',
        'status',
        `Bakım süresi 12 gün sonra bitiyor (25.10.2026).${PAGE_LINK}`,
      ],
    ],
    [],
  ],
  [
    'maintenance_expired',
    [
      [
        'maintenanceExpired',
        'status',
        `Bakım süresi doldu (05.10.2026): yeni sürümler ve destek için bakımı yenileyin. Kurulu sürüm çalışmaya devam eder.${PAGE_LINK}`,
      ],
    ],
    [],
  ],
  [
    'invalid',
    [
      ['invalid', 'alert', `${INVALID}${PAGE_LINK}`],
      ['usersExceeded', 'status', `${USERS_OVER_3}${PAGE_LINK}`],
    ],
    [['invalid', 'alert', `${INVALID}${CONTACT}`]],
  ],
  [
    'revoked',
    [
      ['invalid', 'alert', `${INVALID}${PAGE_LINK}`],
      ['usersExceeded', 'status', `${USERS_OVER_3}${PAGE_LINK}`],
    ],
    [['invalid', 'alert', `${INVALID}${CONTACT}`]],
  ],
  [
    'wrong_server',
    [
      [
        'wrongServer',
        'alert',
        `Lisans bu sunucu için değil (sunucu kimliği ${SERVER_ID}): ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.${PAGE_LINK}`,
      ],
    ],
    // Users get no details, hence no server id.
    [
      [
        'wrongServer',
        'alert',
        `Lisans bu sunucu için değil: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.${CONTACT}`,
      ],
    ],
  ],
  [
    'expired',
    [
      [
        'expired',
        'status',
        `Lisansın süresi 10.10.2026 tarihinde doldu: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.${PAGE_LINK}`,
      ],
    ],
    [
      [
        'expired',
        'status',
        `Lisansın süresi 10.10.2026 tarihinde doldu: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor.${CONTACT}`,
      ],
    ],
  ],
  [
    'version_not_covered',
    [
      ['versionNotCovered', 'status', `${VERSION}${PAGE_LINK}`],
      ['maintenanceExpired', 'status', `${MAINTENANCE_ENDED}${PAGE_LINK}`],
    ],
    [['versionNotCovered', 'status', `${VERSION}${CONTACT}`]],
  ],
  [
    'version_not_covered_base_only',
    // Nothing paid is off: one info line for administrators, nothing for users.
    [
      [
        'baseOnly',
        'status',
        `Lisans durumu: Sürüm bakım kapsamında değil — kapanan ücretli modül yok.${PAGE_LINK}`,
      ],
      ['maintenanceExpired', 'status', `${MAINTENANCE_ENDED}${PAGE_LINK}`],
    ],
    [],
  ],
];

test.describe('licence banners of every captured state', () => {
  for (const [state, admin, others] of BANNERS) {
    for (const [who, expected] of [
      ['tolga', admin],
      ['ayse.yilmaz', others],
    ] as const) {
      test(`${state} — ${who}`, async ({ page }) => {
        const api = await mockApi(page, { signedInAs: who, license: { state } });
        await page.goto('/sealing');
        await expect(page.getByRole('heading', { level: 1, name: 'Mühürleme' })).toBeVisible();
        const licensee = api.license.current().licensee;
        // The licence has been read once the footer names its licensee (or a banner shows).
        if (licensee)
          await expect(page.getByTestId('sidebar-licensee')).toHaveText(`Lisans: ${licensee}`);
        if (expected.length === 0) {
          await expect(banners(page)).toHaveCount(0);
          return;
        }
        const shown = banners(page).getByTestId(/^license-banner-/);
        await expect(shown).toHaveCount(expected.length);
        for (const [index, [id, role, text]] of expected.entries()) {
          const item = shown.nth(index);
          await expect(item).toHaveAttribute('data-testid', `license-banner-${id}`);
          await expect(item).toHaveAttribute('role', role);
          await expect(item).toHaveText(text);
        }
        await expect(banners(page).getByRole('link')).toHaveCount(
          who === 'tolga' ? expected.length : 0,
        );
      });
    }
  }

  test('the trial banner on every page, with the licence page link for administrators', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing');
    const banner = banners(page).getByRole('status');
    await expect(banner).toHaveText('Deneme sürümü — 6 gün kaldı. Lisans sayfası');
    await banner.getByRole('link', { name: 'Lisans sayfası' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Lisans' })).toBeVisible();
  });

  test('users are told to contact their administrator', async ({ page }) => {
    await mockApi(page, { signedInAs: 'mehmet.kaya', license: { state: 'trial_expired' } });
    await page.goto('/');
    await expect(banners(page)).toHaveText(
      'Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor. Yöneticinize başvurun.',
    );
    await expect(banners(page).getByRole('link')).toHaveCount(0);
  });

  test('an unverifiable licence is an alert; limits only for administrators', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'invalid' } });
    // Four active users over the unlicensed limit of three.
    expect(api.license.current().limits.users).toMatchObject({ max: 3, used: 4 });
    await page.goto('/evidence');
    await expect(banners(page).getByRole('alert')).toContainText(
      'Lisans dosyası doğrulanamadı: ücretli modüller kapalı.',
    );
    await expect(banners(page).getByRole('status')).toHaveText(
      'Kullanıcı sayısı lisans sınırını aşıyor (4/3): yeni kullanıcı eklenemez. Lisans sayfası',
    );
  });

  test('another server: the server id; English', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga', license: { state: 'wrong_server' } });
    await page.goto('/sealing');
    await expect(banners(page).getByRole('alert')).toHaveText(
      `The licence is not for this server (server id ${SERVER_ID}): paid modules are off. The 5651 functions keep working. Licence page`,
    );
  });
});

test.describe('locked modules', () => {
  test('Kullanım and Raporlar: lock marks and locked pages, no request to the locked API', async ({
    page,
  }) => {
    await usePreferences(page, { collapsed: false });
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'trial_expired' } });
    await page.goto('/sealing');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await nav.getByRole('link', { name: 'Kullanım Lisansınızda yok' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kullanım analizi lisansınızda yok' }),
    ).toBeVisible();
    await nav.getByRole('link', { name: 'Raporlar Lisansınızda yok' }).click();
    await expect(
      page.getByRole('heading', {
        level: 1,
        name: 'Zamanlanmış kullanım raporları lisansınızda yok',
      }),
    ).toBeVisible();
    await page.getByRole('link', { name: 'Lisans sayfasına git' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Lisans' })).toBeVisible();
    expect(gatedCalls(api)).toEqual([]);
  });

  test('the collapsed rail marks a locked entry too', async ({ page }) => {
    await usePreferences(page, { collapsed: true });
    await mockApi(page, { signedInAs: 'tolga', license: { state: 'expired' } });
    await page.goto('/sealing');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await expect(nav.getByRole('link', { name: 'Kullanım (Lisansınızda yok)' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Raporlar (Lisansınızda yok)' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Mühürleme', exact: true })).toBeVisible();
  });

  test('a licence with the modules: no lock marks, the pages work', async ({ page }) => {
    await usePreferences(page, { collapsed: false });
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    await page.goto('/usage');
    await expect(page.getByRole('heading', { level: 1, name: 'Kullanım' })).toBeVisible();
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await expect(nav.getByRole('link', { name: 'Kullanım', exact: true })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Raporlar', exact: true })).toBeVisible();
    await expect.poll(() => gatedCalls(api).length).toBeGreaterThan(0);
  });

  test('the Panel of the HR role: the locked usage card, the usage never asked', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'mehmet.kaya', license: { state: 'expired' } });
    await page.goto('/');
    await expect(page.getByTestId('card-usage-locked')).toHaveText(
      'Kullanım analizi lisansınızda yok.',
    );
    await expect(page.getByTestId('card-usage')).toHaveCount(0);
    expect(gatedCalls(api)).toEqual([]);
  });

  test('the CSV import is locked, the exports stay', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    await page.goto('/devices?tab=transfer');
    await expect(page.getByTestId('import-locked')).toContainText(
      'Toplu içe aktarma (CSV) lisansınızda yok.',
    );
    await expect(page.getByRole('button', { name: 'Cihaz listesini indir' })).toBeVisible();
  });

  test('a stale page: the API refuses, the licence is read again', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    await page.goto('/reports');
    await expect(page.getByRole('heading', { level: 1, name: 'Raporlar' })).toBeVisible();
    // The licence is removed elsewhere (the CLI); the open page's next request is refused.
    api.license.set('trial_expired');
    await page.getByRole('link', { name: 'Kullanım', exact: true }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kullanım analizi lisansınızda yok' }),
    ).toBeVisible();
  });

  test('the mock refuses a locked route with the API’s captured answer', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'trial_expired' } });
    await page.goto('/sealing');
    const answer = await page.evaluate(async () => {
      const response = await fetch('/api/v1/usage/company');
      return { status: response.status, body: (await response.json()) as unknown };
    });
    expect(answer).toEqual({
      status: LICENSE.module_locked_usage.status,
      body: LICENSE.module_locked_usage.body,
    });
    expect(gatedCalls(api)).toEqual(['GET /usage/company']);
  });
});

test.describe('licence page', () => {
  test('upload after re-authentication: the state, the banner and the menu follow', async ({
    page,
  }) => {
    await usePreferences(page, { collapsed: false });
    const api = await mockApi(page, {
      signedInAs: 'tolga',
      staleReauth: true,
      license: { state: 'trial_expired' },
    });
    await page.goto('/settings/license');
    await expect(page.getByTestId('license-state')).toHaveText('Lisanssız');
    await upload(page, 'ornek.lic');
    await confirmReauth(page);
    await expect(page.getByText('Lisans dosyası yüklendi.', { exact: true })).toBeVisible();
    await expect(page.getByTestId('license-state')).toHaveText('Lisanslı');
    await expect(page.getByTestId('license-licensee').locator('dd')).toHaveText('Örnek A.Ş.');
    await expect(banners(page)).toHaveCount(0);
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await expect(nav.getByRole('link', { name: 'Kullanım', exact: true })).toBeVisible();
    await expect(page.getByTestId('sidebar-licensee')).toHaveText('Lisans: Örnek A.Ş.');
    const post = api.callsTo('POST', '/license').at(-1);
    expect(post?.contentType).toMatch(/^multipart\/form-data; boundary=/);
    expect(post?.raw).toContain('name="file"; filename="ornek.lic"');
    await expect(page.getByTestId('license-history')).toContainText('Yüklendi');
  });

  test('the same licence again is recorded as unchanged', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    await page.goto('/settings/license');
    await upload(page, 'ornek.lic');
    await expect(page.getByText('Lisans dosyası yüklendi.', { exact: true })).toBeVisible();
    const first = page.getByTestId('license-history').getByRole('row').nth(1);
    await expect(first).toContainText('Yüklendi');
    await expect(first).toContainText('aynı lisans');
  });

  for (const [name, key] of Object.entries(REFUSED_FILES)) {
    test(`refusal ${key}: the API's text`, async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/settings/license');
      await upload(page, name);
      const refusal = page.getByTestId('license-refusal');
      await expect(refusal).toHaveText(
        `Lisans dosyası reddedildi${LICENSE[key].body.error.message_tr}`,
      );
      await expect(page.getByTestId('license-state')).toHaveText('Deneme sürümü');
      const first = page.getByTestId('license-history').getByRole('row').nth(1);
      await expect(first).toContainText('Reddedildi');
      await expect(first).toContainText(LICENSE[key].body.error.code);
    });
  }

  test('a refusal in English', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/license');
    await page.getByLabel('Licence file (.lic)').setInputFiles({
      name: 'baska-sunucu.lic',
      mimeType: 'application/json',
      buffer: Buffer.from('{}\n'),
    });
    await page.getByRole('button', { name: 'Upload', exact: true }).click();
    await expect(page.getByTestId('license-refusal')).toContainText(
      LICENSE.refuse_other_server.body.error.message_en,
    );
  });

  test('removal: confirmation, re-authentication, back to unlicensed', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', staleReauth: true, license: { state: 'licensed' } });
    await page.goto('/settings/license');
    await page.getByRole('button', { name: 'Lisansı kaldır' }).click();
    const dialog = page.getByRole('dialog', { name: 'Lisans kaldırılsın mı?' });
    await dialog.getByRole('button', { name: 'Lisansı kaldır' }).click();
    await confirmReauth(page);
    await expect(page.getByText('Lisans kaldırıldı.', { exact: true })).toBeVisible();
    await expect(page.getByTestId('license-state')).toHaveText('Lisanssız');
    await expect(banners(page)).toContainText('Deneme süresi doldu');
  });

  test('the request code: copy and download (.txt with CRLF)', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/license');
    await expect(page.getByTestId('request-code')).toHaveText(REQUEST_CODE);
    await page.getByRole('button', { name: 'Kopyala', exact: true }).click();
    await expect(page.getByText('Panoya kopyalandı', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(REQUEST_CODE);
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'İndir (.txt)' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('arcilogger-istek-kodu.txt');
    expect(readFileSync(await download.path(), 'utf8')).toBe(`${REQUEST_CODE}\r\n`);
  });

  test('only administrators: the licence page is refused to other users', async ({ page }) => {
    await mockApi(page, { signedInAs: 'mehmet.kaya' });
    await page.goto('/settings/license');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  });
});

test.describe('users limit', () => {
  test('the counter, no "add" at the limit, a re-enable refused by the API', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    api.license.status.limits.users.max = 3;
    api.get('zeynep').active = false;
    await page.goto('/settings/users');
    await expect(page.getByTestId('users-limit')).toHaveText('Kullanıcı: 3 / 3');
    await expect(page.getByRole('button', { name: 'Kullanıcı ekle' })).toBeDisabled();
    await page.getByRole('button', { name: 'İşlemler: Zeynep Demir' }).click();
    await page.getByRole('menuitem', { name: 'Etkinleştir' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kullanıcıyı etkinleştir' });
    await dialog.getByRole('button', { name: 'Etkinleştir' }).click();
    // The captured refusal of the real API (3 of 3 users).
    await expect(dialog.getByRole('alert')).toHaveText(LICENSE.user_limit.body.error.message_tr);
    expect(api.get('zeynep').active).toBe(false);
  });

  test('under the limit the counter shows and users can be added', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', license: { state: 'licensed' } });
    await page.goto('/settings/users');
    await expect(page.getByTestId('users-limit')).toHaveText('Kullanıcı: 4 / 25');
    await expect(page.getByRole('button', { name: 'Kullanıcı ekle' })).toBeEnabled();
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`licence accessibility — ${theme}`, () => {
    test('the licence page and a locked page', async ({ page }) => {
      await usePreferences(page, { theme });
      await mockApi(page, { signedInAs: 'tolga', license: { state: 'version_not_covered' } });
      await page.goto('/settings/license');
      await expect(page.getByTestId('license-state')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.goto('/usage');
      await expect(page.getByTestId('module-locked')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('the trial licence page with the request code, and the danger banner', async ({
      page,
    }) => {
      await usePreferences(page, { theme });
      const api = await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/settings/license');
      await expect(page.getByTestId('request-code')).toHaveText(REQUEST_CODE);
      await expectNoA11yViolations(page);
      api.license.set('invalid');
      await page.goto('/sealing');
      await expect(banners(page).getByRole('alert')).toBeVisible();
      await expectNoA11yViolations(page);
    });
  });
}
