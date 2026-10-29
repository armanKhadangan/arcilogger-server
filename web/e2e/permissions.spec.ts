import { expect, test } from '@playwright/test';

import { usePreferences } from './fixtures';
import { mockApi } from './mock-api';
import { mockNetwork } from './mock-network';

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
  ]) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
  }
  expect(api.calls.filter((call) => call.path.startsWith('/admin'))).toEqual([]);
  expect(api.calls.filter((call) => call.path.startsWith('/settings'))).toEqual([]);
});

for (const [username, allowed] of [
  ['tolga', true],
  ['ayse.yilmaz', false],
  ['mehmet.kaya', false],
  ['zeynep', false],
] as const) {
  test(`what the FortiGate's records show is for administrators only — ${username}`, async ({
    page,
  }) => {
    // `GET /settings/fortigate/observed` (the exit editor's "Kayıtlarda görülen NAT adresleri")
    // needs settings.manage, as the whole settings router does (docs/roles.md). User management
    // opens Ayarlar, not this: every other role is given it here, and still finds the page closed
    // and the API refusing.
    const api = await mockApi(page, {
      signedInAs: username,
      grant: { auditor: ['users.manage'], hr: ['users.manage'], viewer: ['users.manage'] },
    });
    await mockNetwork(page, api);
    await page.goto('/settings/application?group=lookup');
    if (allowed) {
      await expect(page.getByTestId('fortigate-observed')).toBeVisible();
      await expect(
        page.getByRole('table', {
          name: 'FortiGate oturum kayıtlarında görülen kaynak NAT adresleri',
        }),
      ).toBeVisible();
      expect(api.callsTo('GET', '/settings/fortigate/observed')[0]?.query.get('hours')).toBe('24');
    } else {
      await expect(
        page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
      ).toBeVisible();
      await expect(page.getByTestId('fortigate-observed')).toHaveCount(0);
      // Nothing was even asked of the API for that page.
      expect(api.callsTo('GET', '/settings/fortigate/observed')).toEqual([]);
    }
    // Asked directly, the API answers the administrator and refuses everyone else.
    const answer = await page.evaluate(async () => {
      const response = await fetch('/api/v1/settings/fortigate/observed?hours=24');
      const body = (await response.json()) as { error?: { code: string }; nat?: unknown };
      return { status: response.status, code: body.error?.code ?? null, nat: 'nat' in body };
    });
    expect(answer).toEqual(
      allowed
        ? { status: 200, code: null, nat: true }
        : { status: 403, code: 'forbidden', nat: false },
    );
  });
}
