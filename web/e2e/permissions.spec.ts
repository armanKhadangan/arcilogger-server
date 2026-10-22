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
    await expect(page.getByText('Bu modül M6 aşamasında geliştirilecek.')).toBeVisible();
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
    '/audit',
    '/settings/users',
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
      ].some((prefix) => call.path.startsWith(prefix)),
    ),
  ).toEqual([]);
});

test('the report viewer cannot open the 5651, evidence or sealing pages either', async ({
  page,
}) => {
  await mockApi(page, { signedInAs: 'zeynep' });
  for (const path of ['/lookup/1', '/evidence?lookup=1', '/sealing?day=2026-10-04']) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  }
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
