import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { CSRF_TOKEN, mockApi } from './mock-api';

/**
 * Ayarlar → Uygulama ayarları (contract 2.13, 2.17): the database settings by group with the
 * server's labels (the captured `GET /settings`, `samples/settings.json`), every kind of editor
 * (number, switch, list, the work hours and the holidays), the browser's and the server's
 * checks, re-authentication, the conflict and "Varsayılana dön".
 */

function group(page: Page, id: string) {
  return page.getByTestId(`settings-group-${id}`);
}

async function openTab(page: Page, name: string) {
  await page.getByRole('tab', { name }).click();
}

test.describe('Ayarlar → Uygulama ayarları', () => {
  test('groups and settings with the labels, bounds and defaults of the API', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application');
    await expect(page.getByRole('heading', { level: 1, name: 'Uygulama ayarları' })).toBeVisible();
    await expect(page.getByRole('tab')).toHaveText([
      'Uyarılar ve e-posta',
      '5651 sorgu ve kira zaman çizelgesi',
      'Arama ve canlı akış',
      'Kullanım analizi',
      'Kullanım raporları',
      'Kanıt paketleri',
      'Oturum, parola ve gizlilik',
      'Veritabanı',
    ]);
    const alerts = group(page, 'alerts');
    await expect(alerts.getByLabel('Saatte en çok e-posta')).toHaveValue('20');
    await expect(alerts.getByTestId('setting-alerts.email_max_per_hour')).toContainText(
      '1–1000 arası.',
    );
    await expect(alerts.getByTestId('setting-alerts.email_max_per_hour')).toContainText(
      'Varsayılan: 20',
    );
    await expect(alerts.getByTestId('setting-alerts.email_recipients')).toContainText(
      'Parola onayı gerekir',
    );
    await expect(
      alerts.getByRole('switch', { name: 'Çözülen uyarıları da bildir' }),
    ).toHaveAttribute('aria-checked', 'true');

    await openTab(page, '5651 sorgu ve kira zaman çizelgesi');
    await expect(page).toHaveURL(/group=lookup/);
    const locked = group(page, 'lookup').getByTestId('setting-devices.oui_file');
    await expect(locked).toContainText(
      'Bu değer güvenlik nedeniyle yalnızca sunucu konsolundan değiştirilebilir.',
    );
    await expect(locked.getByRole('textbox')).toHaveCount(0);
  });

  test('a number is checked in the browser, then saved with the value shown before', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application');
    const alerts = group(page, 'alerts');
    const field = alerts.getByLabel('Saatte en çok e-posta');
    const save = alerts.getByRole('button', { name: 'Kaydet' });
    await expect(save).toBeDisabled();
    await field.fill('2000');
    await expect(alerts.getByText('1 değişiklik')).toBeVisible();
    await save.click();
    await expect(alerts.getByText('En çok 1000 olmalı.')).toBeVisible();
    await expect(field).toBeFocused();
    expect(api.callsTo('PATCH', '/settings')).toHaveLength(0);

    await field.fill('30');
    await save.click();
    await expect(page.getByText('1 ayar kaydedildi.', { exact: true })).toBeVisible();
    const patch = api.callsTo('PATCH', '/settings');
    expect(patch).toHaveLength(1);
    expect(patch[0]?.csrf).toBe(CSRF_TOKEN);
    expect(patch[0]?.body).toEqual({
      changes: [{ key: 'alerts.email_max_per_hour', old: 20, value: 30 }],
    });
    const setting = alerts.getByTestId('setting-alerts.email_max_per_hour');
    await expect(setting).toContainText('Son değişiklik: tolga');
    await expect(setting.getByRole('button', { name: 'Varsayılana dön' })).toBeVisible();
    await expect(save).toBeDisabled();
  });

  test('a key that needs a password confirmation asks for it once, then saves', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/application');
    const alerts = group(page, 'alerts');
    const recipients = alerts.getByLabel('Uyarı e-postası alıcıları');
    await recipients.fill('bt@ornek\nnot an address');
    await alerts.getByRole('button', { name: 'Kaydet' }).click();
    await expect(alerts.getByText('Geçerli bir e-posta adresi girin.')).toBeVisible();
    await recipients.fill('bt@ornek.example\nguvenlik@ornek.example');
    await alerts.getByRole('button', { name: 'Kaydet' }).click();
    await confirmReauth(page);
    await expect(page.getByText('1 ayar kaydedildi.', { exact: true })).toBeVisible();
    const patches = api.callsTo('PATCH', '/settings');
    expect(patches).toHaveLength(2);
    expect(patches[1]?.body).toEqual({
      changes: [
        {
          key: 'alerts.email_recipients',
          old: [],
          value: ['bt@ornek.example', 'guvenlik@ornek.example'],
        },
      ],
    });
  });

  test('work hours and holidays have their own editors', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application?group=usage');
    const usage = group(page, 'usage');
    const hours = usage.getByRole('group', { name: 'Mesai saatleri' });
    await expect(hours.getByRole('checkbox', { name: 'Cuma', exact: true })).toBeChecked();
    await expect(hours.getByRole('checkbox', { name: 'Cumartesi' })).not.toBeChecked();
    await hours.getByRole('checkbox', { name: 'Cuma', exact: true }).click();
    await hours.getByLabel('Mesai bitişi').fill('07:00');
    await usage.getByRole('button', { name: 'Kaydet' }).click();
    await expect(usage.getByText('Mesai başlangıcı bitişten önce olmalı.')).toBeVisible();
    await hours.getByLabel('Mesai bitişi').fill('17:30');

    const holidays = usage.getByRole('list', { name: 'Tatil günleri' });
    await expect(holidays.getByRole('listitem')).toHaveCount(7);
    await expect(holidays).toContainText('29 Ekim (her yıl)');
    const add = usage.getByLabel('Tatil günü ekle');
    await add.fill('2026-02-30');
    await usage.getByRole('button', { name: 'Ekle' }).click();
    await expect(usage.getByText('Tarihi AA-GG ya da YYYY-AA-GG biçiminde girin.')).toBeVisible();
    await add.fill('2026-03-20');
    await add.press('Enter');
    await expect(holidays).toContainText('20.03.2026');
    await usage.getByRole('button', { name: 'Kaldır: 19 Mayıs (her yıl)' }).click();
    await expect(holidays.getByRole('listitem')).toHaveCount(7);
    await expect(usage.getByText('2 değişiklik')).toBeVisible();

    await usage.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('2 ayar kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PATCH', '/settings').at(-1)?.body).toEqual({
      changes: [
        {
          key: 'usage.work_hours',
          old: { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' },
          value: { days: [1, 2, 3, 4], start: '08:00', end: '17:30' },
        },
        {
          key: 'usage.holidays',
          old: ['01-01', '04-23', '05-01', '05-19', '07-15', '08-30', '10-29'],
          value: ['01-01', '04-23', '05-01', '07-15', '08-30', '10-29', '2026-03-20'],
        },
      ],
    });
  });

  test("the server's refusal is shown at its setting, in Turkish", async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application?group=lookup');
    const lookup = group(page, 'lookup');
    // The internet exits through the generic JSON editor: a private address as a public one.
    await lookup
      .getByLabel('İnternet çıkışları (NAT)')
      .fill('{"hat-1": {"mode": "direct", "public": ["100.64.12.9"]}}');
    await lookup.getByRole('button', { name: 'Kaydet' }).click();
    // `validate_setting`'s text (`setting_egress_public_private`), never the value it refused.
    await expect(
      lookup
        .getByTestId('setting-nat.egresses')
        .getByText('Genel adres özel (RFC 1918), paylaşılan (RFC 6598) ya da ayrılmış', {
          exact: false,
        }),
    ).toBeVisible();
    await expect(
      lookup.getByText('Bazı ayar değerleri geçersiz; işaretli ayarları düzeltin.'),
    ).toBeVisible();
  });

  test('changed by someone else meanwhile: the conflict, then the current values', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application');
    const alerts = group(page, 'alerts');
    await alerts.getByLabel('Disk doluluk eşiği (%)').fill('85');
    const disk = api.admin.settings[0]?.settings.find(
      (item) => item.key === 'alerts.disk_used_percent',
    );
    if (!disk) throw new Error('no disk setting');
    disk.value = 90;
    await alerts.getByRole('button', { name: 'Kaydet' }).click();
    await expect(alerts.getByText('Ayar başka biri tarafından değiştirildi')).toBeVisible();
    await alerts.getByRole('button', { name: 'Güncel değerleri al' }).click();
    await expect(alerts.getByLabel('Disk doluluk eşiği (%)')).toHaveValue('90');
  });

  test('"Varsayılana dön" and drafts kept across tabs, with the unsaved-change guard', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const idle = api.admin.settings
      .find((item) => item.id === 'security')
      ?.settings.find((item) => item.key === 'auth.idle_minutes');
    if (!idle) throw new Error('no idle setting');
    Object.assign(idle, { value: 30, is_default: false, updated_by: 'ayse.yilmaz' });
    await page.goto('/settings/application?group=security');
    const security = group(page, 'security');
    await expect(security.getByLabel('Boşta kalma süresi (dakika)')).toHaveValue('30');
    await security
      .getByTestId('setting-auth.idle_minutes')
      .getByRole('button', { name: 'Varsayılana dön' })
      .click();
    await expect(security.getByLabel('Boşta kalma süresi (dakika)')).toHaveValue('15');
    expect(api.callsTo('POST', '/settings/auth.idle_minutes/reset')).toHaveLength(1);

    await security.getByLabel('En kısa parola').fill('14');
    await openTab(page, 'Veritabanı');
    await openTab(page, 'Oturum, parola ve gizlilik');
    await expect(security.getByLabel('En kısa parola')).toHaveValue('14');
    await page.getByRole('link', { name: 'Ayarlar' }).first().click();
    await expect(page.getByRole('dialog', { name: 'Kaydedilmemiş değişiklikler' })).toBeVisible();
  });

  test('"Kiraları yeniden oluştur": at most 31 past days, with its progress', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application?group=lookup');
    const rebuild = group(page, 'lookup').getByTestId('lease-rebuild');
    const from = rebuild.getByLabel('İlk gün');
    const to = rebuild.getByLabel('Son gün');
    const button = rebuild.getByRole('button', { name: 'Kiraları yeniden oluştur' });
    await from.fill(siteDay(-40));
    await to.fill(siteDay(-1));
    await button.click();
    await expect(rebuild).toContainText('En çok 31 gün seçilebilir.');
    await to.fill(siteDay(2));
    await button.click();
    await expect(rebuild).toContainText('Gelecekteki bir gün seçilemez.');
    await from.fill('32.13.2026');
    await button.click();
    await expect(rebuild).toContainText('Günü GG.AA.YYYY biçiminde girin.');
    expect(api.callsTo('POST', '/admin/actions/lease-rebuild')).toHaveLength(0);

    await from.fill(siteDay(-3));
    await to.fill(siteDay(-1));
    await button.click();
    const ask = page.getByRole('dialog', { name: 'Kiralar yeniden oluşturulsun mu?' });
    await expect(ask).toContainText(`${siteDay(-3)} – ${siteDay(-1)}`);
    await ask.getByRole('button', { name: 'Kiraları yeniden oluştur' }).click();
    await expect(rebuild.getByTestId('lease-rebuild-result')).toHaveText('3 / 3 gün tamamlandı.', {
      timeout: 10_000,
    });
    expect(api.callsTo('POST', '/admin/actions/lease-rebuild')[0]?.body).toEqual({
      from: isoDay(-3),
      to: isoDay(-1),
    });
  });
});

/** A day relative to today at the site (UTC+03:00), as `YYYY-MM-DD`. */
function isoDay(offset: number): string {
  return new Date(Date.now() + 3 * 3_600_000 + offset * 86_400_000).toISOString().slice(0, 10);
}

/** The same day as typed in the site's format (`GG.AA.YYYY`). */
function siteDay(offset: number): string {
  const [year = '', month = '', day = ''] = isoDay(offset).split('-');
  return `${day}.${month}.${year}`;
}

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Uygulama ayarları accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/application');
    const alerts = group(page, 'alerts');
    await alerts.getByLabel('Saatte en çok e-posta').fill('0');
    await alerts.getByRole('button', { name: 'Kaydet' }).click();
    await expect(alerts.getByText('En az 1 olmalı.')).toBeVisible();
    await expectNoA11yViolations(page);
    await openTab(page, 'Kullanım analizi');
    await expect(group(page, 'usage').getByRole('group', { name: 'Mesai saatleri' })).toBeVisible();
    await expectNoA11yViolations(page);
    await openTab(page, '5651 sorgu ve kira zaman çizelgesi');
    await expect(group(page, 'lookup').getByTestId('setting-devices.oui_file')).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
