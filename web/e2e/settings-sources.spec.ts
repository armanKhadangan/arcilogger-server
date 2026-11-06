import { expect, test, type Page } from '@playwright/test';

import type { SnmpOut } from '../src/lib/settings-types';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { mockSources } from './mock-sources';

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

  test('an unknown sender handed over from Kaynaklar opens the add dialog filled in', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockSources(page, api);
    await page.goto('/sources?filter=unknown');
    await page
      .getByRole('button', { name: 'unknown-172.20.10.40 göndericisini kaynak olarak tanımla' })
      .click();
    await expect(page).toHaveURL(/\/settings\/sources$/);
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await expect(dialog.getByTestId('source-prefilled')).toHaveText(
      'Kaynaklar sayfasındaki bilinmeyen gönderici 172.20.10.40 için dolduruldu. Adı gözden geçirin; unknown-172.20.10.40 klasörü ve eski kayıtları yerinde kalır.',
    );
    await expect(dialog.getByLabel('Ad', { exact: true })).toHaveValue('kaynak-40');
    await expect(dialog.getByLabel('Adres')).toHaveValue('172.20.10.40');
    await expect(dialog.getByLabel('Tür')).toContainText('MikroTik');
    await expect(dialog.getByLabel('Beklenen sinyal aralığı (dk)')).toHaveValue('15');
    // The administrator reviews the suggested name before it is sent.
    await dialog.getByLabel('Ad', { exact: true }).fill('ap-40');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    await expect(table(page).getByRole('row', { name: /ap-40/ })).toContainText('172.20.10.40');
    expect(api.callsTo('POST', '/admin/config/sources').at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^sources:[0-9a-f]{64}$/) as unknown,
      source: {
        name: 'ap-40',
        type: 'mikrotik',
        address: '172.20.10.40',
        heartbeat_minutes: 15,
        expected: true,
        transport: 'any',
        enabled: true,
      },
    });
    // An ordinary addition: no re-authentication (Wave 1).
    expect(api.callsTo('POST', '/auth/reauth')).toHaveLength(0);
    // Taken once: the page opened again shows no dialog.
    await page.reload();
    await expect(table(page)).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Kaynak ekle' })).toHaveCount(0);
  });

  test('a hand-over while the administration service is down says why nothing opens', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'disabled' } });
    await mockSources(page, api);
    await page.goto('/sources?filter=unknown');
    await page
      .getByRole('button', { name: 'unknown-172.20.10.40 göndericisini kaynak olarak tanımla' })
      .click();
    await expect(page.getByTestId('source-prefill-blocked')).toContainText('172.20.10.40');
    await expect(page.getByRole('dialog', { name: 'Kaynak ekle' })).toHaveCount(0);
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

/** The stored `[sources.snmp]` of `fortigate` (fictional values): on, keys stored. */
const STORED_SNMP: SnmpOut = {
  enabled: true,
  port: 161,
  user: 'arcilogger',
  auth: 'sha256',
  priv: 'aes128',
  interfaces: ['wan1'],
  capacity_mbps: { wan1: { down: 100, up: 20 } },
  interval_seconds: 15,
  auth_key: 'set',
  priv_key: 'set',
};

function storeSnmp(api: MockApi, name: string) {
  const source = api.admin.sections.sources.find((item) => item.name === name);
  if (source) source.snmp = structuredClone(STORED_SNMP);
}

/** Opens `name`'s dialog and switches its SNMP on with a user, both keys and one line. */
async function switchSnmpOn(page: Page, name: string) {
  await page.getByRole('button', { name: `${name} kaynağını düzenle` }).click();
  const dialog = page.getByRole('dialog', { name: `${name} kaynağını düzenle` });
  const snmp = dialog.getByTestId('source-snmp');
  await snmp.getByRole('switch', { name: 'Hat hızını SNMPv3 ile oku' }).click();
  await snmp.getByLabel('SNMPv3 kullanıcısı').fill('arcilogger');
  await snmp
    .getByTestId('secret-source-snmp-auth_key')
    .getByRole('button', { name: 'Gir' })
    .click();
  await snmp.getByLabel('Kimlik doğrulama anahtarı').fill('örnek-anahtar-1'); // gitleaks:allow — fake test value
  await snmp
    .getByTestId('secret-source-snmp-priv_key')
    .getByRole('button', { name: 'Gir' })
    .click();
  await snmp.getByLabel('Şifreleme anahtarı').fill('örnek-anahtar-2'); // gitleaks:allow — fake test value
  await snmp.getByRole('textbox', { name: 'Arayüz 1' }).fill('wan1');
  return dialog;
}

/**
 * Kaynaklar → kaynak → SNMP (Wave 5 §6 N1, N5, P4): the section of the source dialog, its write-
 * only keys, the browser's checks, the review, the device tried on saving, and a stored table
 * kept by every other change of the source.
 */
test.describe('Ayarlar → Kaynaklar → SNMP', () => {
  test('switches SNMP on: the review, the keys sent once, the device tried', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    const dialog = await switchSnmpOn(page, 'core-r1');
    await dialog.getByRole('combobox', { name: 'Şifreleme' }).click();
    await page.getByRole('option', { name: 'AES-256' }).click();
    await dialog.getByLabel('İndirme (Mbps)').fill('100');
    await dialog.getByLabel('Yükleme (Mbps)').fill('20');
    await dialog.getByRole('button', { name: 'Hat ekle' }).click();
    await dialog.getByRole('textbox', { name: 'Arayüz 2' }).fill('ppp0');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /core-r1 · SNMP: WAN arayüzleri/ })).toContainText(
      'wan1, ppp0',
    );
    await expect(review.getByRole('row', { name: /core-r1 · SNMP: şifreleme/ })).toContainText(
      'aes256',
    );
    await expect(
      review.getByRole('row', { name: /core-r1 · SNMP kimlik doğrulama anahtarı/ }),
    ).toBeVisible();
    // The review shows no key: only that it is set.
    await expect(review).not.toContainText('örnek-anahtar');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    const progress = page.getByTestId('request-progress');
    await expect(progress).toContainText('Bağlantı');
    const put = api.callsTo('PUT', '/admin/config/sources/core-r1');
    expect(put).toHaveLength(1);
    expect(put[0]?.body).toMatchObject({
      source: {
        name: 'core-r1',
        snmp: {
          enabled: true,
          user: 'arcilogger',
          priv: 'aes256',
          interfaces: ['wan1', 'ppp0'],
          capacity_mbps: { wan1: { down: 100, up: 20 } },
          interval_seconds: 15,
        },
      },
      snmp_auth_key: 'örnek-anahtar-1',
      snmp_priv_key: 'örnek-anahtar-2',
    });
    const stored = api.admin.sections.sources.find((item) => item.name === 'core-r1')?.snmp;
    expect(stored).toMatchObject({ enabled: true, auth_key: 'set', priv_key: 'set' });
    // Opened again: the keys are stored, never shown; nothing is typed in their place.
    await page.getByRole('button', { name: 'core-r1 kaynağını düzenle' }).click();
    const again = page.getByTestId('source-snmp');
    await expect(again.getByTestId('secret-source-snmp-auth_key')).toContainText('Kayıtlı');
    await expect(again.getByRole('textbox', { name: 'Arayüz 2' })).toHaveValue('ppp0');
  });

  test("the browser's checks: user, a line, both keys, a capacity in both directions", async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'core-r1 kaynağını düzenle' }).click();
    const dialog = page.getByRole('dialog', { name: 'core-r1 kaynağını düzenle' });
    await dialog.getByRole('switch', { name: 'Hat hızını SNMPv3 ile oku' }).click();
    await dialog.getByLabel('Yükleme (Mbps)').fill('20');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog.getByLabel('SNMPv3 kullanıcısı')).toBeFocused();
    await expect(dialog.getByLabel('SNMPv3 kullanıcısı')).toHaveAttribute('aria-invalid', 'true');
    await expect(dialog).toContainText('Arayüz 1');
    await expect(dialog).toContainText('Bu alan zorunludur.');
    await expect(dialog).toContainText('İndirme ve yükleme birlikte girilir.');
    await expect(dialog.getByTestId('secret-source-snmp-auth_key')).toContainText(
      'SNMP açıkken iki anahtar da gerekli.',
    );
    await dialog
      .getByTestId('secret-source-snmp-auth_key')
      .getByRole('button', { name: 'Gir' })
      .click();
    await dialog.getByLabel('Kimlik doğrulama anahtarı').fill('kısa');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog).toContainText('Anahtar 8–64 karakter olmalı.');
    expect(api.callsTo('POST', '/admin/config/sources/check')).toHaveLength(0);
  });

  test('a network source cannot read a line', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    const dialog = await switchSnmpOn(page, 'core-r1');
    await dialog.getByLabel('Adres').fill('172.20.10.0/24');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog.getByTestId('source-snmp-error')).toHaveText(
      'Adresi bir ağ olan kaynakta SNMP açılamaz: hedef tek bir cihaz adresi olmalı.',
    );
    await expect(dialog.getByRole('switch', { name: 'Hat hızını SNMPv3 ile oku' })).toBeFocused();
    expect(api.callsTo('POST', '/admin/config/sources/check')).toHaveLength(0);
  });

  test("the server's refusal of the table is shown in the section", async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    storeSnmp(api, 'core-r1');
    await page.goto('/settings/sources');
    // Another session deleted the stored keys meanwhile: the browser still thinks they are set.
    await page.getByRole('button', { name: 'core-r1 kaynağını düzenle' }).click();
    const core = api.admin.sections.sources.find((item) => item.name === 'core-r1');
    if (core?.snmp) core.snmp.priv_key = 'unset';
    const dialog = page.getByRole('dialog', { name: 'core-r1 kaynağını düzenle' });
    await dialog.getByRole('textbox', { name: 'Arayüz 1' }).fill('wan2');
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog.getByTestId('source-snmp-error')).toHaveText(
      "SNMP'yi açmak için SNMPv3 kimlik doğrulama ve şifreleme anahtarlarının ikisi de gerekir (8-64 karakter).",
    );
    expect(api.callsTo('PUT', '/admin/config/sources/core-r1')).toHaveLength(0);
  });

  test('the device does not know the interface: nothing changed, the reason shown', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.nextOutcome = 'probe_failed';
    api.admin.nextProbeFailure = 'snmp_no_such_interface';
    await page.goto('/settings/sources');
    await switchSnmpOn(page, 'core-r1');
    await page.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText(
      'Bağlantı denemesi başarısız, hiçbir şey değişmedi',
      { timeout: 10_000 },
    );
    await expect(progress).toContainText(
      'Cihaz bu arayüz adını bildirmiyor; cihazın bildirdiği arayüzlerden WAN hattını seçin.',
    );
    // W5-N-9: what the device reports, from the captured `sources.snmp_probe_failed`.
    const list = page.getByRole('table', {
      name: 'Cihazın SNMP ile bildirdiği arayüzler: ad, durum ve hız',
    });
    await expect(list.getByRole('rowheader')).toHaveText(['wan2', 'lan', 'dmz', 'tunnel-1']);
    await expect(list.getByRole('row', { name: /dmz/ })).toContainText('kapalı');
    await expect(list.getByRole('row', { name: /wan2/ })).toContainText('1.000');
    await expect(list.getByRole('row', { name: /tunnel-1/ })).toContainText('—');
    await expectNoA11yViolations(page);
    expect(api.admin.sections.sources.find((item) => item.name === 'core-r1')?.snmp).toBeNull();
  });

  test('strict outbound mode: a new device is saved untried, with the warning', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.strictOutbound = true;
    await page.goto('/settings/sources');
    await switchSnmpOn(page, 'core-r1');
    await page.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review).toContainText('Bağlantı denemesi atlandı.');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
  });

  test("a row's switch and an edit keep the stored table (no snmp would delete it)", async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    storeSnmp(api, 'fortigate');
    await page.goto('/settings/sources');
    await page.getByRole('switch', { name: 'fortigate etkin' }).click();
    await page
      .getByRole('dialog', { name: 'Değişiklikleri onaylayın' })
      .getByRole('button', { name: 'Onayla ve kaydet' })
      .click();
    await confirmReauth(page);
    await applied(page);
    const table: Partial<SnmpOut> = { ...STORED_SNMP };
    delete table.auth_key;
    delete table.priv_key;
    expect(api.callsTo('PUT', '/admin/config/sources/fortigate').at(-1)?.body).toMatchObject({
      source: { enabled: false, snmp: table },
    });
    expect(api.callsTo('PUT', '/admin/config/sources/fortigate').at(-1)?.body).not.toHaveProperty(
      'snmp_auth_key',
    );
    expect(api.admin.sections.sources.find((item) => item.name === 'fortigate')?.snmp).toEqual(
      STORED_SNMP,
    );
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Kaynaklar → SNMP accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await page.getByRole('button', { name: 'core-r1 kaynağını düzenle' }).click();
    const dialog = page.getByRole('dialog', { name: 'core-r1 kaynağını düzenle' });
    await dialog.getByRole('switch', { name: 'Hat hızını SNMPv3 ile oku' }).click();
    await dialog.getByRole('button', { name: 'Hat ekle' }).click();
    await expect(dialog.getByTestId('source-snmp-probe')).toBeVisible();
    await expectNoA11yViolations(page);
    await dialog.getByRole('button', { name: 'Değişiklikleri gözden geçir' }).click();
    await expect(dialog.getByLabel('SNMPv3 kullanıcısı')).toBeFocused();
    await expectNoA11yViolations(page);
  });
}

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Kaynaklar: the filled-in dialog's accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockSources(page, api);
    await page.goto('/sources?filter=unknown');
    await page
      .getByRole('button', { name: 'unknown-172.20.10.40 göndericisini kaynak olarak tanımla' })
      .click();
    await expect(page.getByTestId('source-prefilled')).toBeVisible();
    await expectNoA11yViolations(page);
  });

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
