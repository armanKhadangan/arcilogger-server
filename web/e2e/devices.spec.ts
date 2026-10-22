import { readFile } from 'node:fs/promises';

import { expect, test, type Page } from '@playwright/test';

import { useSampleClock } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { DEVICES, SAMPLE_TODAY, type CsvSample } from './samples';
import type { LookupOutcome } from '../src/lib/lookup-types';

/*
 * The mocked API starts from the answers captured from the real API (`samples/devices.json`):
 * six devices — two of Selin Kaya (her PC "Planlama-PC-14" since 01.10.2026 09:00 and a phone
 * with a randomised MAC address, Can Öztürk's from 10.10. to 12.10.) and four "unknown" ones —,
 * the persons Selin Kaya (Planlama, 1001), Can Öztürk (Finans, 1002) and the inactive Deniz
 * Işık, the three departments and the VLAN plan of the example site. The browser clock and the
 * mock's "now" are 13.10.2026 15:00.
 */

/** UTF-8 byte order mark of the CSV files (Turkish Excel). */
const BOM = String.fromCharCode(0xfeff);
const UNKNOWN = DEVICES.list_unknown.items.map((device) => device.mac);

/**
 * The file name the API gave a captured export (`Content-Disposition`). Its time is the capture's
 * clock, which runs on in real time: taken from the sample, not written down here.
 */
function exportName(sample: CsvSample, prefix: string): string {
  const name = /filename="([^"]+)"/.exec(sample.headers['content-disposition'] ?? '')?.[1] ?? '';
  expect(name).toMatch(new RegExp(`^${prefix}-\\d{8}-\\d{6}\\.csv$`));
  return name;
}

/** A CSV file as the browser's file chooser hands it over. */
function csvFile(name: string, lines: string[]) {
  return {
    name,
    mimeType: 'text/csv',
    buffer: Buffer.from(`${BOM}${lines.join('\r\n')}\r\n`, 'utf8'),
  };
}

function devicesTable(page: Page) {
  return page.getByRole('table', { name: 'Ağda görülen cihazlar' });
}

/** MAC addresses in the device table, top to bottom. */
async function macs(page: Page): Promise<string[]> {
  const table = devicesTable(page);
  await expect(table.getByRole('row').nth(1)).toBeVisible();
  return table.locator('tbody tr button[id*="-open-"]').allTextContents();
}

function lastDevicesQuery(api: MockApi): URLSearchParams | undefined {
  return api.callsTo('GET', '/devices').at(-1)?.query;
}

test.describe('Cihazlar ve Kişiler', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('lists the devices, filters them on the server and keeps the view in the address', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/');
    await page
      .getByRole('navigation', { name: 'Ana menü' })
      .getByRole('link', { name: 'Cihazlar ve Kişiler' })
      .click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Cihazlar ve Kişiler' }),
    ).toBeVisible();
    expect(await macs(page)).toHaveLength(DEVICES.list.items.length);
    await expect(page.getByText('1–6 / 6 cihaz')).toBeVisible();
    // Randomised MAC addresses are marked (spec 9.2).
    const phone = devicesTable(page).getByRole('row').filter({ hasText: '02:00:5e:00:53:64' });
    await expect(phone.getByText('Rastgele', { exact: true })).toBeVisible();
    await expect(phone.getByText('Selin Kaya')).toBeVisible();

    await page.getByRole('button', { name: 'Bilinmeyen cihazlar' }).click();
    await expect(page).toHaveURL(/\/devices\?unknown=true$/);
    await expect.poll(() => lastDevicesQuery(api)?.get('unknown')).toBe('true');
    // No current owner and not infrastructure.
    await expect.poll(async () => (await macs(page)).sort()).toEqual([...UNKNOWN].sort());

    // The search text goes to the API (host names in any case), never into the address.
    const search = page.getByLabel('Ara', { exact: true });
    await search.fill('pln-pc-05');
    await expect.poll(() => lastDevicesQuery(api)?.get('q')).toBe('pln-pc-05');
    await expect.poll(() => macs(page)).toEqual(['3c:52:82:00:00:05']);
    expect(page.url()).not.toContain('pln');
    await search.fill('');

    await page.getByLabel('Tür').click();
    await page.getByRole('option', { name: 'Yazıcı' }).click();
    await expect(page).toHaveURL(/type=printer/);
    await expect.poll(() => macs(page)).toEqual(['00:0c:29:00:00:99']);

    // Reload: the filters come back from the address (the search box starts empty).
    await page.reload();
    await expect(page.getByRole('button', { name: 'Bilinmeyen cihazlar' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect.poll(() => macs(page)).toEqual(['00:0c:29:00:00:99']);

    await page.getByRole('button', { name: 'Filtreleri temizle' }).first().click();
    await expect(page).toHaveURL(/\/devices$/);
    await expect.poll(async () => (await macs(page)).length).toBe(6);

    // A MAC address is found without its separators too.
    await search.fill('00005e005357');
    await expect.poll(() => macs(page)).toEqual(['00:00:5e:00:53:57']);
    await search.fill('');

    // Sorting is done by the server (the whole list, not the page on screen).
    await devicesTable(page).getByRole('button', { name: 'MAC', exact: true }).click();
    await expect.poll(() => lastDevicesQuery(api)?.get('sort')).toBe('mac');
    expect(lastDevicesQuery(api)?.get('order')).toBe('asc');
    await expect(page).toHaveURL(/sort=mac&order=asc/);
    await expect.poll(async () => (await macs(page))[0]).toBe('00:00:5e:00:53:57');
  });

  test('assigns several devices to one person and removes an assignment', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?unknown=true');
    const table = devicesTable(page);
    await table.getByRole('checkbox', { name: 'Seç: 3c:52:82:00:00:05' }).check();
    await table.getByRole('checkbox', { name: 'Seç: a4:83:e7:00:00:44' }).check();
    const bar = page.getByTestId('bulk-bar');
    await expect(bar).toContainText('2 cihaz seçildi');
    await bar.getByRole('button', { name: 'Kişiye ata' }).click();

    const dialog = page.getByRole('dialog', { name: 'Kişiye ata' });
    await expect(dialog.getByText('3c:52:82:00:00:05')).toBeVisible();
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    await expect(dialog.getByText('Bir kişi seçin.')).toBeVisible();

    // The person picker searches on the server and offers active persons only.
    await dialog.getByRole('combobox', { name: 'Kişi' }).click();
    await page.getByPlaceholder('Kişi ara…').fill('can');
    await expect
      .poll(() => api.callsTo('GET', '/persons').at(-1)?.query.toString())
      .toBe('q=can&active=true&limit=20');
    await page.getByRole('option', { name: /Can Öztürk/ }).click();
    await expect(dialog.getByRole('combobox', { name: 'Kişi' })).toHaveText(/Can Öztürk/);

    // A start before now is allowed; one in the future is refused before sending.
    await dialog.getByLabel('Belirli bir zamandan itibaren').check();
    const day = dialog.getByLabel('Gün', { exact: true });
    await day.fill('14.10.2026');
    await dialog.getByLabel('Saat', { exact: true }).fill('08:00');
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    await expect(day).toHaveAttribute('aria-invalid', 'true');
    await expect(day).toBeFocused();
    expect(api.callsTo('POST', '/devices/assign')).toEqual([]);
    await day.fill('12.10.2026');
    await dialog.getByLabel('Not', { exact: true }).fill('Zimmet formu 2026/88');
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByText('2 cihaz Can Öztürk adlı kişiye atandı.', { exact: true }),
    ).toBeVisible();
    expect(api.callsTo('POST', '/devices/assign')[0]?.body).toEqual({
      macs: ['3c:52:82:00:00:05', 'a4:83:e7:00:00:44'],
      person_id: 2,
      valid_from: '2026-10-12T08:00:00+03:00',
      note: 'Zimmet formu 2026/88',
    });
    // Assigned: no longer "unknown".
    await expect(table.getByRole('row').filter({ hasText: '3c:52:82:00:00:05' })).toHaveCount(0);
    expect(api.devices.devices.get('3c:52:82:00:00:05')?.person?.name).toBe('Can Öztürk');

    // The drawer shows the new assignment; "Atamayı kaldır" closes it (the row stays).
    await page.goto('/devices');
    await table.getByRole('button', { name: '3c:52:82:00:00:05' }).click();
    const drawer = page.getByRole('dialog', { name: '3c:52:82:00:00:05' });
    await expect(drawer.getByText(/Zimmet formu 2026\/88/)).toBeVisible();
    await expect(drawer.getByText('12.10.2026 08:00:00 – hâlâ geçerli')).toBeVisible();
    await expect(drawer.getByText('Finans (kişinin departmanı)')).toBeVisible();
    await drawer.getByRole('button', { name: 'Atamayı kaldır' }).click();
    const remove = page.getByRole('dialog', { name: 'Atamayı kaldır' });
    // No note there: it is kept on a new assignment, and removing one opens none.
    await expect(remove.getByLabel('Not', { exact: true })).toHaveCount(0);
    await remove.getByRole('button', { name: 'Atamayı kaldır' }).click();
    await expect(remove).toBeHidden();
    expect(api.callsTo('POST', '/devices/assign')[1]?.body).toEqual({
      macs: ['3c:52:82:00:00:05'],
      person_id: null,
    });
    await expect(drawer.getByText('12.10.2026 08:00:00 – 13.10.2026 15:00:00')).toBeVisible();
    // Without a person the device takes its VLAN's department again.
    await expect(drawer.getByText("Planlama (VLAN'ın departmanı)")).toBeVisible();
  });

  test('shows the server’s refusals clearly and changes nothing', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    // 3c:52:82:00:00:05 belonged to Can Öztürk from 05.10. to 11.10.2026 09:00.
    api.devices.assignments.unshift({
      id: 90,
      mac: '3c:52:82:00:00:05',
      person: { id: 2, name: 'Can Öztürk' },
      valid_from: '2026-10-05T06:00:00Z',
      valid_to: '2026-10-11T06:00:00Z',
      assigned_by: 'tolga',
      note: null,
      backfilled: false,
    });
    await page.goto('/devices');
    const table = devicesTable(page);
    const bar = page.getByTestId('bulk-bar');
    const dialog = page.getByRole('dialog', { name: 'Kişiye ata' });

    // Before the device's current assignment (01.10.2026 09:00): refused in the browser.
    await table.getByRole('checkbox', { name: 'Seç: 00:00:5e:00:53:57' }).check();
    await bar.getByRole('button', { name: 'Kişiye ata' }).click();
    await dialog.getByRole('combobox', { name: 'Kişi' }).click();
    await page.getByRole('option', { name: /Can Öztürk/ }).click();
    await dialog.getByLabel('Belirli bir zamandan itibaren').check();
    await expect(
      dialog.getByText(/mevcut atamasının başlangıcından \(01\.10\.2026 09:00:00\)/),
    ).toBeVisible();
    await dialog.getByLabel('Gün', { exact: true }).fill('20.09.2026');
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    await expect(
      dialog.getByText(
        'Başlangıç, seçili bir cihazın mevcut atamasının başlangıcından önce olamaz.',
      ),
    ).toBeVisible();
    expect(api.callsTo('POST', '/devices/assign')).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await table.getByRole('checkbox', { name: 'Seç: 00:00:5e:00:53:57' }).uncheck();

    // Before the end of its last assignment: the list does not know it, the server refuses.
    await table.getByRole('checkbox', { name: 'Seç: 3c:52:82:00:00:05' }).check();
    await bar.getByRole('button', { name: 'Kişiye ata' }).click();
    await dialog.getByRole('combobox', { name: 'Kişi' }).click();
    await page.getByRole('option', { name: /Selin Kaya/ }).click();
    await dialog.getByLabel('Belirli bir zamandan itibaren').check();
    await dialog.getByLabel('Gün', { exact: true }).fill('10.10.2026');
    await dialog.getByLabel('Saat', { exact: true }).fill('09:00');
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    const alert = dialog.getByRole('alert').filter({ hasText: 'Atama yapılamadı' });
    await expect(alert).toContainText(
      '3c:52:82:00:00:05 cihazının önceki atamasından (11.10.2026 09:00:00) önce olamaz',
    );
    await expect(alert).toContainText('atama geçmişi yalnızca eklenerek tutulur');
    await expect(alert).toContainText('hiçbir cihaz değiştirilmedi');
    await expect(dialog).toBeVisible();

    // Another administrator deactivated the person meanwhile.
    const selin = api.devices.persons.get(1);
    if (selin) selin.active = false;
    await dialog.getByLabel('Şimdi').check();
    await dialog.getByRole('button', { name: 'Ata', exact: true }).click();
    await expect(
      dialog.getByText(DEVICES.errors.person_inactive?.body.error.message_tr ?? '-'),
    ).toBeVisible();
    await expect(dialog).toBeVisible();
    expect(api.callsTo('POST', '/devices/assign')).toHaveLength(2);
    expect(api.devices.devices.get('3c:52:82:00:00:05')?.person).toBeNull();
  });

  test('device drawer: randomised MAC warning, history, leases and editing', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices');
    await devicesTable(page).getByRole('button', { name: '02:00:5e:00:53:64' }).click();
    // The captured phone has no name: the drawer is titled with its MAC address.
    const drawer = page.getByRole('dialog', { name: '02:00:5e:00:53:64' });
    await expect(drawer.getByTestId('randomized-notice')).toContainText(
      'aynı telefon listede birden fazla cihaz olarak görünebilir',
    );
    await expect(drawer.getByText('Planlama (kişinin departmanı)')).toBeVisible();
    // The history, newest first: Selin Kaya now, Can Öztürk before; it is append-only.
    await expect(drawer.getByText('12.10.2026 09:00:00 – hâlâ geçerli')).toBeVisible();
    await expect(drawer.getByText('10.10.2026 10:00:00 – 12.10.2026 09:00:00')).toBeVisible();
    await expect(drawer.getByText(/Zimmet formu 2026\/21/)).toBeVisible();
    await expect(drawer.getByText(/yalnızca eklenerek tutulur/)).toBeVisible();
    await expect(drawer.getByRole('table', { name: 'Son kira kayıtları' })).toContainText(
      '172.20.60.142',
    );

    // The API's limits are checked before sending.
    const name = drawer.getByLabel('Cihaz adı');
    await name.fill('x'.repeat(201));
    await drawer.getByRole('button', { name: 'Kaydet' }).click();
    await expect(drawer.getByText('En fazla 200 karakter.')).toBeVisible();
    await expect(name).toBeFocused();
    expect(api.callsTo('PATCH', '/devices/02%3A00%3A5e%3A00%3A53%3A64')).toEqual([]);

    await name.fill('Selin’in iş telefonu');
    await drawer.getByLabel('Etiketler').fill('planlama, telefon');
    await drawer.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('Cihaz kaydedildi.', { exact: true })).toBeVisible();
    // The MAC address is sent URL-encoded (`:` → `%3A`).
    expect(api.callsTo('PATCH', '/devices/02%3A00%3A5e%3A00%3A53%3A64')[0]?.body).toEqual({
      name: 'Selin’in iş telefonu',
      tags: ['planlama', 'telefon'],
    });
    await expect(page.getByRole('dialog', { name: 'Selin’in iş telefonu' })).toBeVisible();
  });

  test('adds a person; deactivating keeps the history', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=persons');
    const table = page.getByRole('table', { name: 'Kişiler' });
    await expect(table.getByRole('row')).toHaveCount(3); // header + 2 active persons
    await page.getByRole('button', { name: 'Kişi ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kişi ekle' });
    await dialog.getByRole('button', { name: 'Kişiyi ekle' }).click();
    await expect(dialog.getByText('Bu alan zorunludur.')).toBeVisible();
    await dialog.getByLabel('Ad soyad').fill('Burak Çelik');
    await dialog.getByLabel('Departman').click();
    await page.getByRole('option', { name: 'İdari İşler' }).click();
    await dialog.getByLabel('Sicil no').fill('1002');
    await dialog.getByRole('button', { name: 'Kişiyi ekle' }).click();
    // Employee number of Can Öztürk: the server's refusal, shown in the dialog.
    await expect(dialog.getByText('Bu sicil numarası (1002) başka bir kişiye ait.')).toBeVisible();
    await dialog.getByLabel('Sicil no').fill('2002');
    await dialog.getByRole('button', { name: 'Kişiyi ekle' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('Burak Çelik eklendi.', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/persons').at(-1)?.body).toEqual({
      name: 'Burak Çelik',
      department_id: 3,
      employee_no: '2002',
    });
    await expect(table.getByRole('row').filter({ hasText: 'Burak Çelik' })).toBeVisible();

    // The search matches the job title too.
    await page.getByLabel('Ara', { exact: true }).fill('müdür');
    await expect.poll(() => api.callsTo('GET', '/persons').at(-1)?.query.get('q')).toBe('müdür');
    await expect(table.getByRole('row')).toHaveCount(2);
    await expect(table).toContainText('Can Öztürk');
    await page.getByLabel('Ara', { exact: true }).fill('');

    await page.getByRole('button', { name: 'İşlemler: Can Öztürk' }).click();
    await page.getByRole('menuitem', { name: 'Pasifleştir' }).click();
    const confirm = page.getByRole('dialog', { name: 'Kişiyi pasifleştir' });
    await expect(confirm).toContainText('silinmez: atama geçmişi');
    await expect(confirm).toContainText('Pasif bir kişiye cihaz atanamaz.');
    await confirm.getByRole('button', { name: 'Pasifleştir' }).click();
    await expect(confirm).toBeHidden();
    expect(api.callsTo('PATCH', '/persons/2')[0]?.body).toEqual({ active: false });
    await expect(table.getByRole('row').filter({ hasText: 'Can Öztürk' })).toHaveCount(0);

    // Inactive persons stay listed under "Pasif", with their history.
    await page.getByLabel('Durum').click();
    await page.getByRole('option', { name: 'Pasif' }).click();
    await expect(table.getByRole('row')).toHaveCount(3); // Can Öztürk, Deniz Işık
    await table.getByRole('button', { name: 'Can Öztürk', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: 'Can Öztürk' });
    await expect(drawer.getByText('Bu kişi pasif')).toBeVisible();
    await expect(drawer.getByText(/MAC 02:00:5e:00:53:64/)).toBeVisible();
    await expect(drawer.getByText('10.10.2026 10:00:00 – 12.10.2026 09:00:00')).toBeVisible();
  });

  test('a person with a randomised MAC address: one drawer, every device', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=persons');
    await page
      .getByRole('table', { name: 'Kişiler' })
      .getByRole('button', { name: 'Selin Kaya', exact: true })
      .click();
    const drawer = page.getByRole('dialog', { name: 'Selin Kaya' });
    await expect(drawer.getByText('Şu anki cihazları (2)')).toBeVisible();
    await expect(drawer.getByTestId('randomized-notice')).toContainText(
      '1 cihazın MAC adresi rastgele',
    );
    await expect(drawer.getByText('02:00:5e:00:53:64').first()).toBeVisible();
    await expect(drawer.getByText('00:00:5e:00:53:57').first()).toBeVisible();
  });

  test('departments and VLANs: add a department, map VLANs', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=departments');
    await page.getByRole('button', { name: 'Departman ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Departman ekle' });
    // Names are unique regardless of case and Turkish letters (the server's refusal).
    await dialog.getByLabel('Departman adı').fill('idari işler');
    await dialog.getByRole('button', { name: 'Departmanı ekle' }).click();
    await expect(dialog.getByText('Bu adda bir departman zaten var (İDARİ İŞLER).')).toBeVisible();
    await dialog.getByLabel('Departman adı').fill('Kalite Kontrol');
    await dialog.getByRole('button', { name: 'Departmanı ekle' }).click();
    await expect(dialog).toBeHidden();
    expect(api.callsTo('POST', '/departments').at(-1)?.body).toEqual({ name: 'Kalite Kontrol' });
    await expect(
      page.getByRole('table', { name: 'Departmanlar' }).getByText('Kalite Kontrol'),
    ).toBeVisible();

    await page.getByRole('combobox', { name: 'Departman: VLAN 60' }).click();
    await page.getByRole('option', { name: 'Kalite Kontrol' }).click();
    await expect(
      page.getByText('VLAN 60 departmanı: Kalite Kontrol', { exact: true }),
    ).toBeVisible();
    expect(api.callsTo('PATCH', '/vlans')[0]?.body).toEqual({
      subnet: '172.20.60.0/24',
      department_id: 4,
    });

    // A VLAN without a department: its unassigned devices have none.
    await page.getByRole('combobox', { name: 'Departman: VLAN 41' }).click();
    await page.getByRole('option', { name: 'Departman yok' }).click();
    await expect(
      page.getByText('VLAN 41 için departman kaldırıldı.', { exact: true }),
    ).toBeVisible();
    expect(api.callsTo('PATCH', '/vlans')[1]?.body).toEqual({
      subnet: '172.20.41.0/24',
      department_id: null,
    });

    await page.getByRole('button', { name: 'Yeniden adlandır: İdari İşler' }).click();
    const rename = page.getByRole('dialog', { name: 'Departmanı yeniden adlandır' });
    await rename.getByLabel('Departman adı').fill('İdari İşler ve Arşiv');
    await rename.getByRole('button', { name: 'Kaydet' }).click();
    await expect(rename).toBeHidden();
    expect(api.callsTo('PATCH', '/departments/3')[0]?.body).toEqual({
      name: 'İdari İşler ve Arşiv',
    });

    await page.getByRole('tab', { name: 'Cihazlar' }).click();
    await devicesTable(page).getByRole('button', { name: '00:15:5d:00:00:20' }).click();
    await expect(
      page
        .getByRole('dialog', { name: '00:15:5d:00:00:20' })
        .getByText("Yok — Finans VLAN'ı bir departmana bağlı değil"),
    ).toBeVisible();
  });

  test('VLANs: add with checks and refusals, edit, move to another network, delete', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=departments');
    const vlanTable = page.getByRole('table', { name: "VLAN'lar" });
    await expect(vlanTable).toBeVisible();
    await page.getByRole('button', { name: 'VLAN ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'VLAN ekle' });
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(dialog.getByText('Bu alan zorunludur.')).toHaveCount(2);
    await expect(dialog.getByLabel('Ağ', { exact: true })).toBeFocused();
    expect(api.callsTo('POST', '/vlans')).toEqual([]);

    // The browser checks the network first; the server refuses what is taken.
    await dialog.getByLabel('Ağ', { exact: true }).fill('172.20.70.8/24');
    await dialog.getByLabel('Ad', { exact: true }).fill('Laboratuvar');
    await dialog.getByLabel('VLAN numarası').fill('5000');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(
      dialog.getByText('Ana bilgisayar bitleri sıfır olmalı: ağ adresi 172.20.70.0/24.'),
    ).toBeVisible();
    await expect(
      dialog.getByText('VLAN numarası 1 ile 4094 arasında bir tam sayıdır (ya da boş kalır).'),
    ).toBeVisible();
    await dialog.getByLabel('Ağ', { exact: true }).fill('172.20.43.0/24');
    await dialog.getByLabel('VLAN numarası').fill('70');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(dialog.getByText('172.20.43.0/24 ağı zaten tanımlı.')).toBeVisible();
    await dialog.getByLabel('Ağ', { exact: true }).fill('172.20.70.0/24');
    await dialog.getByLabel('VLAN numarası').fill('43');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(
      dialog.getByText('VLAN numarası 43 başka bir ağda kullanılıyor (172.20.43.0/24).'),
    ).toBeVisible();
    await dialog.getByLabel('VLAN numarası').fill('70');
    await dialog.getByLabel('Amaç').fill('Test bench PCs');
    await dialog.getByLabel('DHCP sunucusu').fill('dhcp70');
    await dialog.getByLabel('Departman', { exact: true }).click();
    await page.getByRole('option', { name: 'Planlama' }).click();
    await dialog.getByLabel('Misafir ağı').check();
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('VLAN 70 eklendi.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'VLAN ekle' })).toBeFocused();
    expect(api.callsTo('POST', '/vlans').at(-1)?.body).toEqual({
      subnet: '172.20.70.0/24',
      vlan_id: 70,
      name_tr: 'Laboratuvar',
      purpose: 'Test bench PCs',
      dhcp_server: 'dhcp70',
      department_id: 2,
      guest: true,
    });
    const row = vlanTable.getByRole('row', { name: /172\.20\.70\.0\/24/ });
    await expect(row).toContainText('Laboratuvar');
    await expect(row).toContainText('Misafir');
    await expect(row).toContainText('dhcp70');

    // Edit: only what changed is sent; a corrected network keeps everything else.
    await page.getByRole('button', { name: 'İşlemler: VLAN 70' }).click();
    await page.getByRole('menuitem', { name: 'Düzenle' }).click();
    const edit = page.getByRole('dialog', { name: "VLAN'ı düzenle" });
    await expect(edit.getByLabel('Ağ', { exact: true })).toHaveValue('172.20.70.0/24');
    await expect(edit.getByLabel('DHCP sunucusu')).toHaveValue('dhcp70');
    await edit.getByLabel('Ağ', { exact: true }).fill('172.20.71.0/24');
    await edit.getByLabel('Ad', { exact: true }).fill('Ar-Ge Laboratuvarı');
    await edit.getByLabel('Misafir ağı').uncheck();
    await edit.getByRole('button', { name: 'Kaydet' }).click();
    await expect(edit).toBeHidden();
    await expect(page.getByText('VLAN 70 kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PATCH', '/vlans').at(-1)?.body).toEqual({
      subnet: '172.20.70.0/24',
      new_subnet: '172.20.71.0/24',
      name_tr: 'Ar-Ge Laboratuvarı',
      guest: false,
    });
    await expect(vlanTable.getByRole('row', { name: /172\.20\.71\.0\/24/ })).toContainText(
      'Ar-Ge Laboratuvarı',
    );
    await expect(vlanTable.getByText('172.20.70.0/24')).toHaveCount(0);
    // The moved VLAN is a new row: the focus goes to its menu button (WCAG 2.4.3).
    await expect(page.getByRole('button', { name: 'İşlemler: VLAN 70' })).toBeFocused();

    // A rename keeps the row: the focus comes back to the same menu button.
    await page.getByRole('button', { name: 'İşlemler: VLAN 70' }).click();
    await page.getByRole('menuitem', { name: 'Düzenle' }).click();
    await edit.getByLabel('Amaç').fill('Ar-Ge test PCs');
    await edit.getByRole('button', { name: 'Kaydet' }).click();
    await expect(edit).toBeHidden();
    expect(api.callsTo('PATCH', '/vlans').at(-1)?.body).toEqual({
      subnet: '172.20.71.0/24',
      purpose: 'Ar-Ge test PCs',
    });
    await expect(page.getByRole('button', { name: 'İşlemler: VLAN 70' })).toBeFocused();

    // Delete, after a confirmation that says what it changes and what it does not.
    await page.getByRole('button', { name: 'İşlemler: VLAN 70' }).click();
    await page.getByRole('menuitem', { name: 'Sil' }).click();
    const confirm = page.getByRole('dialog', { name: "VLAN'ı sil" });
    await expect(confirm).toContainText(
      'VLAN 70 (172.20.71.0/24, Ar-Ge Laboratuvarı) silinsin mi?',
    );
    await expect(confirm).toContainText('kaydedilmiş 5651 sonuçları değişmez');
    await expect(confirm.getByRole('button', { name: 'İptal' })).toBeFocused();
    await confirm.getByRole('button', { name: 'Sil', exact: true }).click();
    await expect(confirm).toBeHidden();
    await expect(page.getByText('VLAN 70 silindi.', { exact: true })).toBeVisible();
    expect(api.callsTo('DELETE', '/vlans').map((call) => call.query.get('subnet'))).toEqual([
      '172.20.71.0/24',
    ]);
    await expect(vlanTable.getByText('Ar-Ge Laboratuvarı')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'İşlemler: VLAN 70' })).toHaveCount(0);
  });

  test('VLANs: a deleted VLAN leaves its devices without VLAN name and department', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices');
    const unknownPc = devicesTable(page).getByRole('row', { name: /00:15:5d:00:00:20/ });
    await expect(unknownPc).toContainText('VLAN 41 · Finans');
    await expect(unknownPc).toContainText("Finans (VLAN'dan)");
    await page.getByRole('tab', { name: "Departmanlar ve VLAN'lar" }).click();
    await page.getByRole('button', { name: 'İşlemler: VLAN 41' }).click();
    await page.getByRole('menuitem', { name: 'Sil' }).click();
    await page
      .getByRole('dialog', { name: "VLAN'ı sil" })
      .getByRole('button', { name: 'Sil', exact: true })
      .click();
    await expect(page.getByText('VLAN 41 silindi.', { exact: true })).toBeVisible();
    // The row and its menu button are gone: the focus goes to "VLAN ekle".
    await expect(page.getByRole('button', { name: 'VLAN ekle' })).toBeFocused();
    await page.getByRole('tab', { name: 'Cihazlar' }).click();
    await expect(unknownPc).toContainText('VLAN 41 · —');
    await expect(unknownPc).not.toContainText("(VLAN'dan)");
  });

  test('VLANs: a new installation starts without any and asks for the network', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.devices.vlans.splice(0);
    await page.goto('/devices?tab=departments');
    await expect(page.getByText('Henüz VLAN yok: ağınızı ekleyin')).toBeVisible();
    await expect(page.getByRole('table', { name: "VLAN'lar" })).toHaveCount(0);
    await page.getByRole('button', { name: 'VLAN ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'VLAN ekle' });
    await dialog.getByLabel('Ağ', { exact: true }).fill('::ffff:10.0.20.0/120');
    await dialog.getByLabel('Ad', { exact: true }).fill('Ofis');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(
      dialog.getByText('Bu, IPv6 biçiminde yazılmış bir IPv4 ağı; IPv4 ağını girin: 10.0.20.0/24.'),
    ).toBeVisible();
    await dialog.getByLabel('Ağ', { exact: true }).fill('10.0.20.0');
    await dialog.getByLabel('Ad', { exact: true }).fill('Ofis');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(dialog.getByText('Önek uzunluğunu da yazın, ör. 10.0.20.0/24.')).toBeVisible();
    await dialog.getByLabel('Ağ', { exact: true }).fill('10.0.20.0/24');
    await dialog.getByRole('button', { name: "VLAN'ı ekle" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText('10.0.20.0/24 eklendi.', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/vlans')[0]?.body).toEqual({
      subnet: '10.0.20.0/24',
      name_tr: 'Ofis',
    });
    await expect(page.getByRole('table', { name: "VLAN'lar" })).toContainText('Ofis');
    await expect(page.getByText('Henüz VLAN yok: ağınızı ekleyin')).toHaveCount(0);
  });

  test('imports persons: template, dry run with a refused line, fixed file, apply', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=transfer');

    const template = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Şablonu indir' }).click();
    const saved = await template;
    expect(saved.suggestedFilename()).toBe('arcilogger-kisiler-sablon.csv');
    const text = await readFile(await saved.path(), 'utf8');
    expect(text).toBe(DEVICES.template_persons.text);
    expect(text.startsWith(`${BOM}name;department;title;email;employee_no;active;notes\r\n`)).toBe(
      true,
    );

    const header = 'name;department;title;email;employee_no;active;notes';
    await page
      .getByLabel('CSV dosyası')
      .setInputFiles(
        csvFile('kisiler.csv', [
          header,
          'Burak Çelik;İdari İşler;Bakım Teknisyeni;;2002;evet;',
          'Can Öztürk;Finans;Mali Analist;can.ozturk@example.com;1002;evet;',
          ';Finans;;;;evet;',
        ]),
      );
    await page.getByRole('button', { name: 'Kontrol et (deneme)' }).click();
    const result = page.getByTestId('import-result');
    await expect(result.getByText('1 satır kabul edilmedi')).toBeVisible();
    await expect(result.getByRole('table', { name: 'Kabul edilmeyen satırlar' })).toContainText(
      'name boş olamaz.',
    );
    await expect(result.getByRole('button', { name: 'Uygula' })).toBeDisabled();
    await expect(result.getByText('Önce hatalı satırları düzeltin.')).toBeVisible();

    // Comma-separated works as well.
    await page
      .getByLabel('CSV dosyası')
      .setInputFiles(
        csvFile('kisiler.csv', [
          header.replace(/;/g, ','),
          'Burak Çelik,İdari İşler,Bakım Teknisyeni,,2002,evet,',
          'Can Öztürk,Finans,Mali Analist,can.ozturk@example.com,1002,evet,',
        ]),
      );
    await expect(result).toHaveCount(0);
    await page.getByRole('button', { name: 'Kontrol et (deneme)' }).click();
    await expect(result.getByText('Bu bir denemedir: henüz hiçbir şey yazılmadı.')).toBeVisible();
    const counts = result.getByTestId('import-counts');
    await expect(counts).toContainText('Eklenecek1');
    await expect(counts).toContainText('Güncellenecek1');
    const preview = result.getByRole('table', { name: 'Değişecek kayıtlar (önizleme)' });
    await expect(preview.getByRole('row')).toHaveCount(3);
    await expect(preview.getByRole('columnheader').first()).toHaveText('Satır');
    // Can Öztürk: only the job title changes.
    await expect(preview.getByRole('row').filter({ hasText: 'Can Öztürk' })).toContainText(
      'GüncellemeCan Öztürk1002Unvan',
    );
    expect(api.devices.persons.size).toBe(3); // a dry run writes nothing

    await result.getByRole('button', { name: 'Uygula' }).click();
    const confirm = page.getByRole('dialog', { name: 'İçe aktarmayı uygula' });
    await expect(confirm).toContainText('1 kayıt eklenecek, 1 kayıt güncellenecek');
    await confirm.getByRole('button', { name: 'Uygula' }).click();
    await expect(page.getByRole('heading', { name: 'İçe aktarma tamamlandı' })).toBeVisible();
    await expect(result.getByTestId('import-counts')).toContainText('Eklendi1');

    const imports = api.callsTo('POST', '/imports/persons');
    expect(imports.map((call) => call.query.get('dry_run'))).toEqual(['true', 'true', 'false']);
    expect(imports.at(-1)?.raw).toContain('filename="kisiler.csv"');
    expect(api.devices.persons.size).toBe(4);
    expect(api.devices.persons.get(2)?.title).toBe('Mali Analist');

    await page.getByRole('tab', { name: 'Kişiler' }).click();
    await expect(page.getByRole('table', { name: 'Kişiler' })).toContainText('Burak Çelik');
  });

  test('imports assignments: an apply the data no longer allows writes nothing', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=transfer');
    await page.getByLabel('Cihaz atamaları').check();
    await page
      .getByLabel('CSV dosyası')
      .setInputFiles(
        csvFile('atamalar.csv', [
          'mac;employee_no;person_name;valid_from;note',
          '3c:52:82:00:00:05;1002;;12.10.2026 09:00;Zimmet formu 2026/90',
        ]),
      );
    await page.getByRole('button', { name: 'Kontrol et (deneme)' }).click();
    const result = page.getByTestId('import-result');
    await expect(result.getByTestId('import-counts')).toContainText('Eklenecek1');
    const preview = result.getByRole('table', { name: 'Değişecek kayıtlar (önizleme)' });
    await expect(preview.getByRole('row').nth(1)).toContainText(
      '2Atama3c:52:82:00:00:05Can Öztürk12.10.2026 09:00:00',
    );

    // Can Öztürk is deactivated before the import is applied.
    const can = api.devices.persons.get(2);
    if (can) can.active = false;
    await result.getByRole('button', { name: 'Uygula' }).click();
    await page
      .getByRole('dialog', { name: 'İçe aktarmayı uygula' })
      .getByRole('button', { name: 'Uygula' })
      .click();
    await expect(page.getByRole('heading', { name: 'İçe aktarma uygulanmadı' })).toBeVisible();
    await expect(result.getByText(/hiçbir şey yazılmadı/)).toBeVisible();
    await expect(result.getByRole('table', { name: 'Kabul edilmeyen satırlar' })).toContainText(
      "'Can Öztürk' pasif; pasif bir kişiye cihaz atanamaz.",
    );
    await expect(result.getByRole('button', { name: 'Uygula' })).toBeDisabled();
    expect(api.devices.devices.get('3c:52:82:00:00:05')?.person).toBeNull();
  });

  test('exports the lists as CSV files for Excel, filtered as shown', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices?tab=transfer');
    let download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Cihaz listesini indir' }).click();
    let saved = await download;
    expect(saved.suggestedFilename()).toBe(exportName(DEVICES.devices_csv, 'arcilogger-cihazlar'));
    const text = await readFile(await saved.path(), 'utf8');
    expect(text.startsWith(`${BOM}mac;name;type;vendor;`)).toBe(true);
    expect(text).toContain('00:00:5e:00:53:57;');
    expect(api.callsTo('GET', '/devices/export.csv')[0]?.query.toString()).toBe('');

    // The list tabs export what they show: every page of the filtered list.
    await page.goto('/devices?unknown=true');
    await expect.poll(async () => (await macs(page)).length).toBe(UNKNOWN.length);
    download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'CSV indir' }).click();
    await download;
    expect(api.callsTo('GET', '/devices/export.csv')[1]?.query.toString()).toBe(
      'unknown=true&sort=last_seen&order=desc',
    );

    await page.goto('/devices?tab=persons');
    await page.getByLabel('Durum').click();
    await page.getByRole('option', { name: 'Pasif' }).click();
    await expect(page.getByRole('table', { name: 'Kişiler' }).getByRole('row')).toHaveCount(2);
    download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'CSV indir' }).click();
    saved = await download;
    expect(saved.suggestedFilename()).toBe(exportName(DEVICES.persons_csv, 'arcilogger-kisiler'));
    expect(api.callsTo('GET', '/persons/export.csv')[0]?.query.toString()).toBe('active=false');
  });

  test('the tabs follow the keyboard and the address', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/devices');
    const tab = page.getByRole('tab', { name: 'Cihazlar' });
    await tab.focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'Kişiler' })).toBeFocused();
    await expect(page.getByRole('tab', { name: 'Kişiler' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page).toHaveURL(/\?tab=persons$/);
    await page.keyboard.press('End');
    await expect(page).toHaveURL(/\?tab=transfer$/);
    await page.goto('/devices?tab=departments');
    await expect(page.getByRole('table', { name: "VLAN'lar" })).toBeVisible();
  });
});

test.describe('Cihazlar ve Kişiler — access', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('read-only: a role with devices.read sees everything and can change nothing', async ({
    page,
  }) => {
    const api = await mockApi(page, {
      signedInAs: 'ayse.yilmaz',
      grant: { auditor: ['devices.read'] },
    });
    await page.goto('/devices');
    await expect(page.getByTestId('read-only')).toBeVisible();
    const table = devicesTable(page);
    await expect(table.getByRole('row')).toHaveCount(7);
    await expect(table.getByRole('checkbox')).toHaveCount(0);
    await table.getByRole('button', { name: '00:00:5e:00:53:57' }).click();
    const drawer = page.getByRole('dialog', { name: 'Planlama-PC-14' });
    await expect(drawer.getByText(/Zimmet formu 2026\/15/)).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Kaydet' })).toHaveCount(0);
    await expect(
      drawer.getByRole('button', { name: /Kişiye ata|Başka bir kişiye ata/ }),
    ).toHaveCount(0);
    await page.keyboard.press('Escape');

    await page.getByRole('tab', { name: 'Kişiler' }).click();
    await expect(page.getByRole('table', { name: 'Kişiler' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Kişi ekle' })).toHaveCount(0);
    await page.getByRole('tab', { name: "Departmanlar ve VLAN'lar" }).click();
    await expect(page.getByRole('table', { name: "VLAN'lar" })).toContainText('İdari İşler');
    await expect(page.getByRole('combobox', { name: /Departman: VLAN/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'VLAN ekle' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /İşlemler: VLAN/ })).toHaveCount(0);
    await page.getByRole('tab', { name: 'İçe/Dışa aktar' }).click();
    await expect(page.getByRole('button', { name: 'Kişi listesini indir' })).toBeVisible();
    await expect(page.getByText('İçe aktar (CSV)')).toHaveCount(0);
    expect(api.calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });

  test('without devices.read the page stays closed, and nothing is asked', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'mehmet.kaya' });
    await page.goto('/devices');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
    expect(
      api.calls.filter((call) => /^\/(devices|persons|departments|vlans|imports)/.test(call.path)),
    ).toEqual([]);
  });

  test('the KVKK notice comes before any person data', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.get('tolga').privacy_accepted = false;
    await page.goto('/devices');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    expect(api.callsTo('GET', '/devices')).toEqual([]);
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await expect(devicesTable(page).getByRole('row')).toHaveCount(7);
  });
});

test.describe('5651 Sorgu — the assignment behind the person', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  function person(api: MockApi) {
    const outcome = api.records.lookups.get(1)?.outcome as LookupOutcome | undefined;
    const found = outcome?.matches[0]?.chain?.person;
    if (!found) throw new Error('lookup 1 has no person');
    return found;
  }

  test('shows since when the device was the person’s (captured lookup)', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    await expect(page.getByTestId('person-assignment').first()).toHaveText(
      'Atama: 01.10.2026 09:00:00 itibarıyla',
    );
  });

  test('a closed assignment shows both ends', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    Object.assign(person(api), {
      assigned_from: '2026-10-01T06:00:00Z',
      assigned_to: '2026-10-12T06:00:00Z',
    });
    await page.goto('/lookup/1');
    await expect(page.getByTestId('person-assignment').first()).toHaveText(
      'Atama: 01.10.2026 09:00:00 – 12.10.2026 09:00:00',
    );
  });

  test('a lookup stored before the assignment history shows no assignment line', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const found = person(api);
    delete found.assigned_from;
    delete found.assigned_to;
    delete found.link;
    await page.goto('/lookup/1');
    await expect(page.getByTestId('chain-summary').first()).toBeVisible();
    await expect(page.getByTestId('person-assignment')).toHaveCount(0);
  });
});
