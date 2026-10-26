import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Kaynaklar (contract 2.17, `sources.manage`): the `[[sources]]` table with the last
 * record and the firewall state, a source added and changed through the check and the review,
 * the browser's and the server's refusals at their fields, switching one off (confirmation and
 * re-authentication) and removing one (re-authentication and the name typed). The API is
 * `mock-admin.ts`, with the two sources of the captured installation (`samples/admin.json`).
 */

function table(page: Page) {
  return page.getByRole('table', { name: 'Yapılandırılmış log kaynakları' });
}

async function applied(page: Page) {
  await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
    'Uygulandı',
    { timeout: 10_000 },
  );
}

test.describe('Ayarlar → Kaynaklar', () => {
  test('lists the sources with their state and firewall', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByTestId('settings-sources').getByRole('link').click();
    await expect(page).toHaveURL(/\/settings\/sources$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Kaynaklar' })).toBeVisible();
    const rows = table(page).getByRole('row');
    await expect(rows).toHaveCount(3);
    const core = table(page).getByRole('row', { name: /core-r1/ });
    await expect(core).toContainText('MikroTik');
    await expect(core).toContainText('172.20.10.1');
    await expect(core).toContainText('UDP ve TCP');
    await expect(core).toContainText('15 dk');
    await expect(core).toContainText('Açık');
    await expect(core.getByRole('switch', { name: 'core-r1 etkin' })).toBeChecked();
    await expect(page.getByTestId('sources-table')).toContainText('2 kaynak (en çok 500).');
    await expect(page.getByTestId('sources-locked')).toContainText('collector.unknown_senders');
  });

  test('adds a source: the browser checks, the review, the request', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'Kaynak ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await dialog.getByLabel('Ad', { exact: true }).fill('Erisim Noktasi');
    await dialog.getByLabel('Adres').fill('127.0.0.1');
    await dialog.getByLabel('Beklenen sinyal aralığı (dk)').fill('0');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog).toContainText(
      'Ad küçük harf ya da rakamla başlamalı; yalnızca küçük harf, rakam ve tire içermeli',
    );
    await expect(dialog).toContainText('Bu adres bir kaynak için kullanılamaz');
    await expect(dialog).toContainText('En az 1 olmalı.');
    await expect(dialog.getByLabel('Ad', { exact: true })).toBeFocused();
    await dialog.getByLabel('Ad', { exact: true }).fill('core-r1');
    await dialog.getByLabel('Adres').fill('172.20.10.0/16');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog).toContainText('Bu adda bir kaynak zaten var.');
    await expect(dialog).toContainText('Ağ adresi, ağın ilk adresi olmalı');
    expect(api.callsTo('POST', '/admin/config/sources/check')).toHaveLength(0);

    await dialog.getByLabel('Ad', { exact: true }).fill('ap-1');
    await dialog.getByLabel('Adres').fill('172.20.10.30');
    await dialog.getByLabel('Beklenen sinyal aralığı (dk)').fill('30');
    await dialog.getByLabel('Tür').click();
    await page.getByRole('option', { name: 'Genel syslog' }).click();
    await dialog.getByLabel('Taşıma').click();
    await page.getByRole('option', { name: 'UDP', exact: true }).click();
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    const changes = review.getByRole('table', { name: 'Kaydedilecek değişiklikler' });
    await expect(changes.getByRole('row', { name: /ap-1 · Adres/ })).toContainText('172.20.10.30');
    await expect(review).toContainText('Güvenlik duvarı kuralı güncellenir');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    await expect(table(page).getByRole('row', { name: /ap-1/ })).toContainText('172.20.10.30');
    expect(api.callsTo('POST', '/admin/config/sources').at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^sources:[0-9a-f]{64}$/) as unknown,
      source: {
        name: 'ap-1',
        type: 'generic',
        address: '172.20.10.30',
        heartbeat_minutes: 30,
        expected: true,
        transport: 'udp',
        enabled: true,
      },
    });
    expect(api.callsTo('POST', '/auth/reauth')).toHaveLength(0);
    // The dry run says it checks an addition (contract gap WP5-1).
    expect(api.callsTo('POST', '/admin/config/sources/check').at(-1)?.body).toMatchObject({
      operation: 'add',
      source: { name: 'ap-1' },
    });
  });

  test("the server's refusal is shown at its field", async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'Kaynak ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await dialog.getByLabel('Ad', { exact: true }).fill('server-self');
    await dialog.getByLabel('Adres').fill('172.20.10.5');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog).toContainText('Bu adres ARCILOGGER sunucusunun kendi adresi.');
    await expect(dialog.getByLabel('Adres')).toHaveAttribute('aria-invalid', 'true');
    expect(api.callsTo('POST', '/admin/config/sources/check')).toHaveLength(1);
    expect(api.callsTo('POST', '/admin/config/sources')).toHaveLength(0);
  });

  test('changes a source in place; its name cannot change', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'core-r1 kaynağını düzenle' }).click();
    const dialog = page.getByRole('dialog', { name: 'core-r1 kaynağını düzenle' });
    await expect(dialog.getByLabel('Ad', { exact: true })).toBeDisabled();
    await expect(dialog.getByLabel('Ad', { exact: true })).toHaveValue('core-r1');
    await dialog.getByLabel('Beklenen sinyal aralığı (dk)').fill('30');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /core-r1 · Sinyal aralığı/ })).toContainText(
      '1530',
    );
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    await expect(table(page).getByRole('row', { name: /core-r1/ })).toContainText('30 dk');
    const put = api.callsTo('PUT', '/admin/config/sources/core-r1');
    expect(put).toHaveLength(1);
    expect(put[0]?.body).toMatchObject({ source: { name: 'core-r1', heartbeat_minutes: 30 } });
    expect(put[0]?.body).not.toHaveProperty('operation');
    expect(api.callsTo('POST', '/admin/config/sources/check').at(-1)?.body).toMatchObject({
      operation: 'update',
      source: { name: 'core-r1' },
    });
  });

  test('switching a source off asks first and re-authenticates', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/sources');
    await page.getByRole('switch', { name: 'fortigate etkin' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review).toContainText(
      'Kapatılan kaynak, mühürlenmemiş günlerin boşluk listesinde artık beklenmez.',
    );
    await expect(review).toContainText('Kaydederken parolanızı yeniden girmeniz istenecek.');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    const row = table(page).getByRole('row', { name: /fortigate/ });
    await expect(row.getByRole('switch')).not.toBeChecked();
    await expect(row).toContainText('Kapalı');
    expect(api.admin.sections.sources.find((item) => item.name === 'fortigate')?.enabled).toBe(
      false,
    );
  });

  test('removing a source needs its name typed and a re-authentication', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'fortigate kaynağını kaldır' }).click();
    const dialog = page.getByRole('dialog', { name: 'fortigate kaldırılsın mı?' });
    await expect(dialog).toContainText('Kaydedilmiş ve mühürlenmiş kayıtları değişmez.');
    const remove = dialog.getByRole('button', { name: 'Kaynağı kaldır' });
    await expect(remove).toBeDisabled();
    await dialog.getByLabel('Onaylamak için kaynağın adını yazın: fortigate').fill('forti');
    await expect(remove).toBeDisabled();
    await dialog.getByLabel('Onaylamak için kaynağın adını yazın: fortigate').fill('fortigate');
    await remove.click();
    await confirmReauth(page);
    await applied(page);
    await expect(table(page).getByRole('row', { name: /fortigate/ })).toHaveCount(0);
    const call = api.callsTo('DELETE', '/admin/config/sources/fortigate')[0];
    expect(call?.query.get('confirm')).toBe('fortigate');
    expect(call?.query.get('etag')).toMatch(/^sources:/);
  });

  test('read-only while the administration service is not running', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'disabled' } });
    await page.goto('/settings/sources');
    await expect(page.getByTestId('agent-unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Kaynak ekle' })).toBeDisabled();
    await expect(page.getByRole('switch', { name: 'core-r1 etkin' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'core-r1 kaynağını kaldır' })).toBeDisabled();
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Kaynaklar accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await expect(table(page)).toBeVisible();
    await expectNoA11yViolations(page);
    await page.getByRole('button', { name: 'Kaynak ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog.getByLabel('Ad', { exact: true })).toBeFocused();
    await expectNoA11yViolations(page);
    await dialog.getByLabel('Ad', { exact: true }).fill('ap-1');
    await dialog.getByLabel('Adres').fill('172.20.10.30');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' })).toBeVisible();
    await expectNoA11yViolations(page);
    await page.getByRole('button', { name: 'İptal' }).click();
    await page
      .getByRole('dialog', { name: 'Kaynak ekle' })
      .getByRole('button', { name: 'İptal' })
      .click();
    await page.getByRole('button', { name: 'fortigate kaynağını kaldır' }).click();
    await expect(page.getByRole('dialog', { name: 'fortigate kaldırılsın mı?' })).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
