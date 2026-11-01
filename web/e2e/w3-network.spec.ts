import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { mockNetwork, type NetworkMock } from './mock-network';
import { SAMPLE_TODAY, SETTINGS } from './samples';

/**
 * Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi, the internet exits (Wave 3
 * contract 2.13, ADR-0016): the exit editor with its rules on the field and its warnings, the
 * server's refusal at the editor level, "Kayıtlarda görülen NAT adresleri" with "Çıkış olarak
 * ekle" and "srcmac için ekle", and Ayarlar → Kaynaklar's FortiGate hint and supported set-ups.
 * The settings, the observed addresses and the exit refusal are the API's captured answers
 * (`mock-admin.ts`, `mock-network.ts`): the stored exits are the upgrade's converted `wan`
 * (ISP NAT, NAT address 100.64.12.9, no interfaces), which claims the first observed address.
 */

/** The captured `nat.egresses`: the exit `wan` the upgrade converted. */
const CONVERTED = SETTINGS.list.groups
  .flatMap((group) => group.settings)
  .find((item) => item.key === 'nat.egresses')?.value;
const WAN = {
  label: 'FortiGate WAN adresi (yükseltmede aktarıldı)',
  mode: 'isp_nat',
  public: [],
  public_dynamic: false,
  transips: ['100.64.12.9'],
  interfaces: [],
  port_preservation: 'unknown',
};

/** A stored exit the editor cannot read (an unknown mode, written around the rules). */
const UNREADABLE = { misafir: { mode: 'notlogged', public: ['198.51.100.90'] } };

async function open(
  page: Page,
  egresses?: Record<string, unknown>,
): Promise<{ api: MockApi; network: NetworkMock }> {
  const api = await mockApi(page, { signedInAs: 'tolga' });
  if (egresses) {
    const stored = api.admin.settings
      .flatMap((item) => item.settings)
      .find((item) => item.key === 'nat.egresses');
    if (!stored) throw new Error('no nat.egresses setting');
    Object.assign(stored, { value: egresses, is_default: false, updated_by: 'tolga' });
  }
  const network = await mockNetwork(page, api);
  await page.goto('/settings/application?group=lookup');
  await expect(page.getByTestId('egress-editor')).toBeVisible();
  return { api, network };
}

function observedRow(page: Page, transip: string) {
  return page
    .getByRole('table', { name: 'FortiGate oturum kayıtlarında görülen kaynak NAT adresleri' })
    .getByRole('row')
    .filter({ has: page.getByRole('rowheader', { name: transip, exact: true }) });
}

function exit(page: Page, name: string) {
  return page.getByRole('group', { name: `Çıkış ${name}` });
}

function patches(api: MockApi) {
  return api.callsTo('PATCH', '/settings');
}

test.describe('İnternet çıkışları (NAT)', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('the observed addresses become exits by the prefill rule, then are saved', async ({
    page,
  }) => {
    expect(CONVERTED).toEqual({ wan: WAN });
    const { api } = await open(page);
    const editor = page.getByTestId('egress-editor');
    // The converted exit alone: an ISP NAT exit without a public address; the converted warning
    // waits for a converted partner beside it (2.13).
    const wan = exit(page, 'wan');
    await expect(wan.getByRole('combobox', { name: 'NAT türü' })).toHaveText(
      "Servis sağlayıcı NAT'ı (çift NAT)",
    );
    await expect(wan.getByLabel('NAT adresleri (transip)')).toHaveValue('100.64.12.9');
    await expect(wan).toContainText('Genel adres girilmedi: bu çıkış, hiçbir çıkışa tanımlı');
    await expect(wan).not.toContainText('Yükseltmede aktarıldı');
    await expect(editor).not.toContainText('Tanımlı çıkış yok');
    const call = api.callsTo('GET', '/settings/fortigate/observed')[0];
    expect(call?.query.get('hours')).toBe('24');
    await expect(observedRow(page, '100.64.12.9').getByRole('cell').nth(4)).toHaveText('wan');
    await expect(observedRow(page, '100.64.12.9').getByRole('button')).toHaveCount(0);

    // A public address prefills Direct with it and the row's dstintf (`wan` lists no interface,
    // so there is no exit on that line to join).
    await expect(observedRow(page, '198.51.100.20').getByRole('button')).toHaveText([
      'Çıkış olarak ekle',
    ]);
    await observedRow(page, '198.51.100.20')
      .getByRole('button', { name: 'Çıkış olarak ekle: 198.51.100.20' })
      .click();
    const hat1 = exit(page, 'hat-1');
    await expect(hat1.getByLabel('Ad', { exact: true })).toBeFocused();
    await expect(hat1.getByRole('combobox', { name: 'NAT türü' })).toHaveText(
      "Doğrudan (genel IP FortiGate'te)",
    );
    await expect(hat1.getByLabel('Genel adresler')).toHaveValue('198.51.100.20');
    await expect(hat1.getByLabel('Arayüzler (dstintf)')).toHaveValue('wan1');
    await hat1.getByLabel('Açıklama').fill('Ana hat');
    await expect(editor).toContainText('Her hattın arayüzlerini (dstintf) girin;');

    // 100.64.13.9 (RFC 6598) prefills ISP NAT with no public address, and warns.
    const shared = observedRow(page, '100.64.13.9');
    await expect(
      shared.getByRole('button', { name: 'İnternet çıkışı değil olarak ekle: 100.64.13.9' }),
    ).toBeVisible();
    await shared.getByRole('button', { name: 'Çıkış olarak ekle: 100.64.13.9' }).click();
    const hat2 = exit(page, 'hat-2');
    await expect(hat2.getByRole('combobox', { name: 'NAT türü' })).toHaveText(
      "Servis sağlayıcı NAT'ı (çift NAT)",
    );
    await expect(hat2.getByLabel('NAT adresleri (transip)')).toHaveValue('100.64.13.9');
    await expect(hat2.getByLabel('Arayüzler (dstintf)')).toHaveValue('wan2');
    await expect(hat2.getByLabel('Genel adresler')).toHaveValue('');
    await expect(hat2).toContainText('Genel adres girilmedi: bu çıkış, hiçbir çıkışa tanımlı');
    await expect(shared).toContainText('Taslakta: hat-2 (kaydedilmedi)');
    await hat2.getByLabel('Genel adresler').fill('198.51.100.77');
    await hat2.getByRole('checkbox', { name: 'Genel IP değişebilir (dinamik)' }).check();
    await expect(hat2).toContainText('Genel IP değişebilir: bu çıkış, tanımlı adreslerinin');

    // The DMZ address: not an internet exit (no public addresses for that mode).
    await observedRow(page, '172.20.99.1')
      .getByRole('button', { name: 'İnternet çıkışı değil olarak ekle: 172.20.99.1' })
      .click();
    const dmz = exit(page, 'nat-1');
    await expect(dmz.getByLabel('Genel adresler')).toHaveCount(0);
    await expect(dmz.getByLabel('NAT adresleri (transip)')).toHaveValue('172.20.99.1');
    await expect(dmz.getByLabel('Arayüzler (dstintf)')).toHaveValue('dmz');

    // A NAT type outside nat.snat_trandisp is flagged.
    await expect(observedRow(page, '198.51.100.20')).toContainText('Ayarlarda yok');
    await expect(page.getByTestId('observed-unknown-trandisp')).toContainText('snat-pool');

    await page.getByTestId('settings-group-lookup').getByRole('button', { name: 'Kaydet' }).click();
    await expect.poll(() => patches(api).length).toBe(1);
    expect(patches(api)[0]?.body).toEqual({
      changes: [
        {
          key: 'nat.egresses',
          old: CONVERTED,
          value: {
            wan: WAN,
            'hat-1': {
              label: 'Ana hat',
              mode: 'direct',
              public: ['198.51.100.20'],
              public_dynamic: false,
              transips: [],
              interfaces: ['wan1'],
              port_preservation: 'unknown',
            },
            'hat-2': {
              label: '',
              mode: 'isp_nat',
              public: ['198.51.100.77'],
              public_dynamic: true,
              transips: ['100.64.13.9'],
              interfaces: ['wan2'],
              port_preservation: 'unknown',
            },
            'nat-1': {
              label: '',
              mode: 'internal',
              public: [],
              public_dynamic: false,
              transips: ['172.20.99.1'],
              interfaces: ['dmz'],
              port_preservation: 'unknown',
            },
          },
        },
      ],
    });
    // Read again after the save: the server's exits claim the addresses now.
    await expect(observedRow(page, '198.51.100.20').getByRole('cell').nth(4)).toHaveText('hat-1');
    await expect(observedRow(page, '100.64.13.9').getByRole('cell').nth(4)).toHaveText('hat-2');
    await expect(observedRow(page, '172.20.99.1').getByRole('cell').nth(4)).toHaveText('nat-1');
    await expect(observedRow(page, '100.64.12.9').getByRole('cell').nth(4)).toHaveText('wan');
    await expect(
      page.getByRole('table', {
        name: 'FortiGate oturum kayıtlarında görülen kaynak NAT adresleri',
      }),
    ).not.toContainText('Tanımsız');
  });

  test('the rules on the field, the warnings, and a refusal of the server at the editor', async ({
    page,
  }) => {
    const { api, network } = await open(page);
    await page.getByRole('button', { name: 'Çıkış ekle' }).click();
    // A row keeps its test id while its name (and so its group's name) is typed.
    const row = page.getByTestId('egress-new-1');
    await row.getByLabel('Ad', { exact: true }).fill('hat-1');
    await row.getByLabel('Genel adresler').fill('100.64.12.9');
    const refusal =
      "Genel adres özel (RFC 1918), paylaşılan (RFC 6598) ya da ayrılmış bir adres olamaz; FortiGate'in servis sağlayıcı yönlendiricisinin arkasındaki adresini servis sağlayıcı NAT'ı çıkışının NAT adresi olarak girin.";
    await expect(exit(page, 'hat-1').getByText(refusal)).toBeVisible();
    await page.getByTestId('settings-group-lookup').getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByTestId('egress-editor')).toBeFocused();
    expect(patches(api)).toHaveLength(0);

    await exit(page, 'hat-1').getByLabel('Genel adresler').fill('203.0.113.200');
    await expect(exit(page, 'hat-1').getByText(refusal)).toHaveCount(0);
    // A public address on hat-1's line is offered to hat-1 first.
    await exit(page, 'hat-1').getByLabel('Arayüzler (dstintf)').fill('wan1');
    await expect(observedRow(page, '198.51.100.20').getByRole('button')).toHaveText([
      'hat-1 çıkışının genel adreslerine ekle',
      'Çıkış olarak ekle',
    ]);
    await page.getByRole('button', { name: 'Çıkış ekle' }).click();
    const second = page.getByTestId('egress-new-2');
    await second.getByLabel('Ad', { exact: true }).fill('hat-2');
    await second.getByRole('combobox', { name: 'NAT türü' }).click();
    await page.getByRole('option', { name: "Servis sağlayıcı NAT'ı (çift NAT)" }).click();
    await second.getByLabel('NAT adresleri (transip)').fill('198.51.100.10');
    await expect(exit(page, 'hat-2')).toContainText(
      'NAT adresi genel bir adres: FortiGate bu adresi kendisi tutuyorsa hat doğrudandır',
    );
    await expect(page.getByTestId('egress-editor')).toContainText(
      'Her hattın arayüzlerini (dstintf) girin;',
    );

    // The browser's mirror accepts the draft; the server refuses it (its captured 422), and the
    // refusal is shown at the editor with its code.
    network.refuseNext = true;
    await page.getByTestId('settings-group-lookup').getByRole('button', { name: 'Kaydet' }).click();
    await expect.poll(() => patches(api).length).toBe(1);
    const field = page.getByTestId('setting-nat.egresses');
    await expect(field.getByText(/^Genel adres özel \(RFC 1918\)/)).toContainText(
      '(setting_egress_public_private)',
    );
    await expect(exit(page, 'hat-1').getByText(refusal)).toHaveCount(0);
  });

  test('the observed addresses could not be summarised in time: said, then read again', async ({
    page,
  }) => {
    await useSampleClock(page, SAMPLE_TODAY);
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const network = await mockNetwork(page, api);
    network.failNext = 'fortigate_observed_timeout';
    await page.goto('/settings/application?group=lookup');
    const panel = page.getByTestId('fortigate-observed');
    await expect(panel).toContainText('Kayıtlarda görülen NAT adresleri okunamadı');
    await expect(panel).toContainText(
      'FortiGate kayıtları 5 saniyede özetlenemedi; biraz sonra yeniden deneyin.',
    );
    await expect(page.getByTestId('egress-editor')).toBeVisible();
    await panel.getByRole('button', { name: 'Yenile' }).click();
    await expect(observedRow(page, '100.64.13.9')).toBeVisible();
    expect(api.callsTo('GET', '/settings/fortigate/observed')).toHaveLength(2);
  });

  test('"srcmac için ekle" fills lookup.srcmac_interfaces; not behind a router', async ({
    page,
  }) => {
    const { api } = await open(page);
    const table = page.getByRole('table', {
      name: 'Oturum kayıtlarında srcmac taşıyan FortiGate arayüzleri',
    });
    await expect(table.getByRole('button', { name: 'srcmac için ekle: vlan60' })).toBeDisabled();
    await table.getByRole('button', { name: 'srcmac için ekle: vlan42' }).click();
    await expect(
      page.getByTestId('setting-lookup.srcmac_interfaces').getByRole('textbox'),
    ).toHaveValue('vlan42');
    await page.getByTestId('settings-group-lookup').getByRole('button', { name: 'Kaydet' }).click();
    await expect.poll(() => patches(api).length).toBe(1);
    expect(patches(api)[0]?.body).toEqual({
      changes: [{ key: 'lookup.srcmac_interfaces', old: [], value: ['vlan42'] }],
    });
    // Read again after the save: the server lists vlan42 as used.
    await expect(table.getByRole('row').filter({ hasText: 'vlan42' })).toContainText(
      'srcmac kullanılıyor',
    );
  });

  test('the FortiGate DHCP settings: the traffic warning, the rebuild hint', async ({ page }) => {
    await open(page);
    const lookup = page.getByTestId('settings-group-lookup');
    // The logid map's own help says it (the captured settings); the message map gets the hint.
    await expect(lookup.getByTestId('setting-dhcp.fortigate_logids')).toContainText(
      'Değişiklik geçmiş günlere kiralar yeniden oluşturulunca uygulanır.',
    );
    await expect(lookup.getByTestId('setting-dhcp.fortigate_logids')).not.toContainText(
      'Değişiklik geçmiş günlere ancak kiralar',
    );
    await expect(lookup.getByTestId('setting-dhcp.fortigate_dhcp_msgs')).toContainText(
      'Değişiklik geçmiş günlere ancak kiralar yeniden oluşturulunca uygulanır',
    );
    await expect(lookup.getByTestId('traffic-extends-warning')).toHaveCount(0);
    await lookup.getByTestId('setting-dhcp.fortigate_traffic_extends').getByRole('switch').click();
    await expect(lookup.getByTestId('traffic-extends-warning')).toContainText(
      'Kapalı (önerilen): FortiGate kirası yalnızca DHCP kayıtlarıyla',
    );
  });

  test('a stored exit the editor cannot read is kept as stored; another key is saved alone', async ({
    page,
  }) => {
    const { api } = await open(page, UNREADABLE);
    const kept = exit(page, 'misafir');
    await expect(kept).toContainText(
      'Bu çıkış kaydedildiği biçimiyle düzenleyicide gösterilemiyor',
    );
    await expect(kept).toContainText('NAT türü direct, isp_nat, not_logged ya da internal olmalı.');
    await expect(kept.getByRole('combobox')).toHaveCount(0);
    await expect(page.getByTestId('fortigate-observed')).toContainText(
      'Kayıtlı çıkış tanımı okunamıyor; çıkış sütunu boş.',
    );
    const lookup = page.getByTestId('settings-group-lookup');
    await expect(lookup.getByRole('button', { name: 'Kaydet' })).toBeDisabled();
    await lookup.getByTestId('setting-dhcp.fortigate_traffic_extends').getByRole('switch').click();
    await lookup.getByRole('button', { name: 'Kaydet' }).click();
    await expect.poll(() => patches(api).length).toBe(1);
    expect(patches(api)[0]?.body).toEqual({
      changes: [{ key: 'dhcp.fortigate_traffic_extends', old: false, value: true }],
    });
  });

  test('Ayarlar → Kaynaklar: the supported set-ups and the FortiGate hint', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/sources');
    await expect(page.getByTestId('sources-fortigate-hint')).toHaveText(
      "FortiGate'in DHCP sunucusunu kullanıyorsanız sistem olay kayıtları syslog'a gönderilmelidir (DEVICE-SETUP.md bölüm 2.4).",
    );
    const note = page.getByTestId('supported-topologies');
    await note.getByRole('button', { name: 'Desteklenen ağ yapıları' }).click();
    await expect(note.getByRole('listitem')).toHaveCount(7);
    await page.getByRole('button', { name: 'Kaynak ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await dialog.getByRole('combobox', { name: 'Tür' }).click();
    await page.getByRole('option', { name: 'FortiGate' }).click();
    await expect(dialog.getByTestId('source-fortigate-hint')).toBeVisible();
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`internet exits accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await open(page);
    await observedRow(page, '100.64.13.9')
      .getByRole('button', { name: 'Çıkış olarak ekle: 100.64.13.9' })
      .click();
    await page.getByRole('button', { name: 'Çıkış ekle' }).click();
    await page
      .getByRole('group', { name: 'Çıkış adsız çıkış' })
      .getByLabel('Ad', { exact: true })
      .fill('Hat 3');
    await expect(page.getByTestId('egress-editor')).toContainText(
      'Çıkış adı küçük harf, rakam ve tire içermeli',
    );
    await expect(page.getByTestId('fortigate-observed')).toContainText('Ayarlarda yok');
    await expectNoA11yViolations(page);
  });

  test(`a stored exit the editor cannot read, accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await open(page, UNREADABLE);
    await expect(exit(page, 'misafir')).toContainText('Kayıtlı değer');
    await expect(page.getByTestId('fortigate-observed')).toContainText(
      'Kayıtlı çıkış tanımı okunamıyor',
    );
    await expectNoA11yViolations(page);
  });
}
