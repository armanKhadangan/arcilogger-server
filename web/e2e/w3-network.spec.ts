import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { mockNetwork, type NetworkMock } from './mock-network';
import { SAMPLE_TODAY } from './samples';

/**
 * Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi, the internet exits (Wave 3
 * contract 2.13, ADR-0016): the exit editor with its rules on the field and its warnings, the
 * server's refusal at the editor level, "Kayıtlarda görülen NAT adresleri" with "Çıkış olarak
 * ekle" and "srcmac için ekle", and Ayarlar → Kaynaklar's FortiGate hint and supported set-ups.
 * The settings are the captured ones (`mock-admin.ts`); the observed addresses and the exit
 * refusal are HAND MOCKS (`mock-network.ts`) until the server side captures them.
 */

async function open(page: Page): Promise<{ api: MockApi; network: NetworkMock }> {
  const api = await mockApi(page, { signedInAs: 'tolga' });
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
    const { api } = await open(page);
    const editor = page.getByTestId('egress-editor');
    await expect(editor).toContainText('Tanımlı çıkış yok: tek bir çift NAT çıkışı varsayılır.');
    const call = api.callsTo('GET', '/settings/fortigate/observed')[0];
    expect(call?.query.get('hours')).toBe('24');

    // A public address prefills Direct with it and the row's dstintf.
    await observedRow(page, '203.0.113.200')
      .getByRole('button', { name: 'Çıkış olarak ekle: 203.0.113.200' })
      .click();
    const hat1 = exit(page, 'hat-1');
    await expect(hat1.getByLabel('Ad', { exact: true })).toBeFocused();
    await expect(hat1.getByRole('combobox', { name: 'NAT türü' })).toHaveText(
      "Doğrudan (genel IP FortiGate'te)",
    );
    await expect(hat1.getByLabel('Genel adresler')).toHaveValue('203.0.113.200');
    await expect(hat1.getByLabel('Arayüzler (dstintf)')).toHaveValue('wan1');
    await hat1.getByLabel('Açıklama').fill('Ana hat');

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
          old: {},
          value: {
            'hat-1': {
              label: 'Ana hat',
              mode: 'direct',
              public: ['203.0.113.200'],
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
    await expect(observedRow(page, '100.64.13.9').getByRole('cell').nth(4)).toHaveText('hat-2');
    await expect(observedRow(page, '172.20.99.1').getByRole('cell').nth(4)).toHaveText('nat-1');
    // A public address on hat-1's line is offered to hat-1 first.
    await expect(observedRow(page, '198.51.100.20').getByRole('button')).toHaveText([
      'hat-1 çıkışının genel adreslerine ekle',
      'Çıkış olarak ekle',
    ]);
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

    network.refuseNext = 'setting_egress_overlap';
    await page.getByTestId('settings-group-lookup').getByRole('button', { name: 'Kaydet' }).click();
    const field = page.getByTestId('setting-nat.egresses');
    await expect(
      field.getByText(/^Bir genel adres ya da NAT adresi iki çıkışta birden olamaz/),
    ).toContainText('(setting_egress_overlap)');
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
}
