import { expect, test } from '@playwright/test';

import { usePreferences } from './fixtures';
import { mockApi } from './mock-api';

/** Menu per role (docs/MVP-CONTRACTS.md section 6, spec 13). */
const MENUS: [string, string, string[]][] = [
  [
    'Yönetici',
    'tolga',
    [
      'Panel',
      'Kaynaklar',
      'Canlı Akış',
      'Arama',
      '5651 Sorgu',
      'Kullanım',
      'Güvenlik',
      'Mühürleme',
      'Kanıt Paketleri',
      'Cihazlar ve Kişiler',
      'Raporlar',
      'Uyum Durumu',
      'Denetim Kaydı',
      'Ayarlar',
      'Sistem',
      'Bileşen Kiti',
    ],
  ],
  [
    'Denetçi',
    'ayse.yilmaz',
    [
      'Panel',
      'Canlı Akış',
      'Arama',
      '5651 Sorgu',
      'Kullanım',
      'Mühürleme',
      'Kanıt Paketleri',
      'Uyum Durumu',
      'Denetim Kaydı',
    ],
  ],
  ['İK Raporları', 'mehmet.kaya', ['Panel', 'Kullanım']],
  ['Rapor İzleyici', 'zeynep', ['Panel', 'Kullanım']],
];

for (const [role, username, menu] of MENUS) {
  test(`menu of the role ${role}`, async ({ page }) => {
    await usePreferences(page, { collapsed: false });
    await mockApi(page, { signedInAs: username });
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await expect(nav.getByRole('link')).toHaveText(menu);
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await expect(page.getByRole('menu').getByText(role, { exact: false })).toBeVisible();
  });
}

for (const [username, uiKit] of [
  ['tolga', true],
  ['ayse.yilmaz', false],
  ['mehmet.kaya', false],
  ['zeynep', false],
] as const) {
  test(`placeholder pages point to the UI kit only when it may be opened — ${username}`, async ({
    page,
  }) => {
    // "Uyum Durumu" is still a placeholder; roles without it get it for this test.
    await mockApi(page, {
      signedInAs: username,
      grant: { auditor: ['compliance.read'], hr: ['compliance.read'], viewer: ['compliance.read'] },
    });
    await page.goto('/compliance');
    await expect(page.getByText('Bu sayfa sonraki bir sürümde eklenecek.')).toBeVisible();
    const link = page.getByRole('link', { name: 'Bileşen Kitini aç' });
    if (uiKit) {
      await link.click();
      await expect(page).toHaveURL(/\/ui-kit$/);
      await expect(page.getByRole('heading', { level: 1, name: 'Bileşen Kiti' })).toBeVisible();
    } else {
      await expect(link).toHaveCount(0);
      await expect(page.getByText(/Bileşen Kiti/)).toHaveCount(0);
    }
  });
}

test('pages outside the role stay closed when opened by URL', async ({ page }) => {
  const api = await mockApi(page, { signedInAs: 'mehmet.kaya' });
  for (const path of [
    '/sources',
    '/live',
    '/search',
    '/audit',
    '/settings/users',
    '/settings/general',
    '/settings/application',
    '/settings/kamusm',
    '/settings/sources',
    '/settings/email',
    '/settings/replica',
    '/settings/retention',
    '/settings/backup',
    '/settings/history',
    '/settings/setup',
    '/lookup',
    '/evidence',
    '/sealing',
    '/devices',
    '/ui-kit',
  ]) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  }
  // Nothing was even asked of the API for those pages.
  expect(
    api.calls.filter((call) =>
      [
        '/sources',
        '/live',
        '/search',
        '/audit',
        '/users',
        '/lookup',
        '/evidence',
        '/seals',
        '/credits',
        '/devices',
        '/persons',
        '/departments',
        '/vlans',
        '/admin',
        '/settings',
      ].some((prefix) => call.path.startsWith(prefix)),
    ),
  ).toEqual([]);
});

// Uyum Durumu: administrator and auditor; Sistem and the first-run wizard (`settings.manage`):
// administrator only (Wave 4 contract D15).
for (const [username, paths] of [
  ['ayse.yilmaz', ['/system', '/settings/setup']],
  ['mehmet.kaya', ['/compliance', '/system', '/settings/setup']],
  ['zeynep', ['/compliance', '/system', '/settings/setup']],
] as const) {
  test(`Uyum Durumu, Sistem and the first-run wizard stay closed by URL outside the role — ${username}`, async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: username });
    for (const path of paths) {
      await page.goto(path);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
      ).toBeVisible();
    }
    // Nothing was even asked of the API for those pages (the wizard reads the configuration, the
    // administration service's requests and the sources).
    expect(
      api.calls.filter((call) =>
        ['/compliance', '/system', '/admin', '/settings', '/sources'].some((prefix) =>
          call.path.startsWith(prefix),
        ),
      ),
    ).toEqual([]);
  });
}

test('the report viewer cannot open the 5651, evidence, sealing or log pages either', async ({
  page,
}) => {
  await mockApi(page, { signedInAs: 'zeynep' });
  for (const path of [
    '/lookup/1',
    '/evidence?lookup=1',
    '/sealing?day=2026-10-04',
    '/sources',
    '/live',
    '/search',
  ]) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  }
});

test('the auditor opens Arama and Canlı Akış, not Kaynaklar (administrators only)', async ({
  page,
}) => {
  const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
  for (const [path, heading] of [
    ['/search', 'Arama'],
    ['/live', 'Canlı Akış'],
    ['/sources', 'Bu sayfa için yetkiniz yok'],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  }
  expect(api.calls.filter((call) => call.path.startsWith('/sources'))).toEqual([]);
});

test('the auditor reads the audit log but cannot manage users', async ({ page }) => {
  await mockApi(page, { signedInAs: 'ayse.yilmaz' });
  await page.goto('/audit');
  await expect(page.getByRole('heading', { level: 1, name: 'Denetim Kaydı' })).toBeVisible();
  await page.goto('/settings');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
  ).toBeVisible();
});

test('browser administration is for administrators: the API refuses everyone else too', async ({
  page,
}) => {
  // A user management role without settings.manage sees the settings page without the machine
  // configuration, and the administration service's status is never asked for.
  const api = await mockApi(page, {
    signedInAs: 'ayse.yilmaz',
    grant: { auditor: ['users.manage'] },
  });
  await page.goto('/settings');
  await expect(page.getByRole('heading', { level: 1, name: 'Ayarlar' })).toBeVisible();
  await expect(page.getByRole('link', { name: /^Kullanıcılar ve Roller/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /^Genel/ })).toHaveCount(0);
  await expect(page.getByTestId('agent-status')).toHaveCount(0);
  for (const path of [
    '/settings/general',
    '/settings/application',
    '/settings/kamusm',
    '/settings/sources',
    '/settings/email',
    '/settings/replica',
    '/settings/retention',
    '/settings/backup',
    '/settings/history',
    '/settings/setup',
  ]) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  }
  expect(api.calls.filter((call) => call.path.startsWith('/admin'))).toEqual([]);
  expect(api.calls.filter((call) => call.path.startsWith('/settings'))).toEqual([]);
});
