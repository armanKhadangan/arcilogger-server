import { readFile } from 'node:fs/promises';

import { expect, test, type Page } from '@playwright/test';

import { usePreferences, useSampleClock } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { SAMPLE_TODAY, USAGE } from './samples';

/*
 * The usage mock (`e2e/mock-usage.ts`) serves the answers captured from the real API for the
 * captured day — Monday 12.10.2026, "Dün" for the browser clock (13.10.2026 15:00): Ayşe Yılmaz
 * (Finans), Burak Demir (Planlama) and Can Öztürk (Finans), who shared the reassigned
 * "Planlama bilgisayarı", the unassigned PC-02 and the bare address 172.20.41.77 — and computes
 * every other period from a generated dataset of the same company (traffic until 14:35 today).
 */

const BOM = String.fromCharCode(0xfeff);
const DAY = 'from=2026-10-12&to=2026-10-12';

function lastQuery(api: MockApi, path: string): URLSearchParams | undefined {
  return api.callsTo('GET', path).at(-1)?.query;
}

async function openUsage(page: Page) {
  await page.goto('/');
  await page
    .getByRole('navigation', { name: 'Ana menü' })
    .getByRole('link', { name: 'Kullanım' })
    .click();
  await expect(page.getByRole('heading', { level: 1, name: 'Kullanım' })).toBeVisible();
}

test.describe('Kullanım', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('company → department → person → site → users, back through the history', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await openUsage(page);
    // Day default (today, processed until 14:35), every duration an estimate.
    await expect(page.getByTestId('period')).toHaveAccessibleName('Dönem: Bugün, 13.10.2026');
    await expect(page.getByTestId('data-note')).toContainText("Veriler 14:35'e kadar işlendi");
    await expect(
      page.getByRole('button', { name: /^tahmini: Tahmini değer: bağlantı kayıtlarından/ }).first(),
    ).toBeVisible();
    const kpis = page.getByRole('group', { name: 'Şirket geneli özet' });
    for (const label of [
      'Aktif kullanıcı',
      'Çevrimiçi süre',
      'Aktif kullanım süresi',
      'Mesai dışı',
      'İndirilen',
      'Yüklenen',
      'Oturum',
      'Engellenen',
    ])
      await expect(kpis.getByText(label, { exact: true })).toBeVisible();
    expect(lastQuery(api, '/usage/company')?.toString()).toBe(
      'from=2026-10-13&to=2026-10-13&hours=all',
    );

    // "Dün": the captured day, computed to its end.
    await page.getByTestId('period').click();
    await page.getByRole('button', { name: 'Dün', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/usage\\?${DAY}$`));
    await expect(page.getByTestId('data-note')).toContainText(
      'Veriler 12.10.2026 sonuna kadar işlendi',
    );
    const topSites = page.getByRole('list', { name: 'En çok vakit geçirilen siteler' });
    await expect(topSites.getByRole('listitem').first()).toContainText('portal.example');
    // Background traffic has no active time: not among the top sites.
    await expect(topSites).not.toContainText('windowsupdate.com');
    const departments = page.getByRole('table', { name: 'Departmanlar' });
    for (const name of ['Finans', 'İdari İşler', 'Planlama'])
      await expect(departments.getByRole('button', { name, exact: true })).toBeVisible();

    // Company → department (its persons, its unassigned devices and addresses).
    await departments.getByRole('button', { name: 'Finans', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=departments&${DAY}&department=Finans$`));
    await expect(page.getByRole('heading', { level: 2, name: 'Finans' })).toBeVisible();
    await expect
      .poll(() =>
        api
          .callsTo('GET', '/usage/subjects')
          .some(
            (call) =>
              call.query.get('kind') === 'person' && call.query.get('department') === 'Finans',
          ),
      )
      .toBe(true);
    expect(lastQuery(api, '/usage/sites')?.get('department')).toBe('Finans');
    await expect(page.getByRole('table', { name: 'Finans departmanının cihazları' })).toContainText(
      '172.20.41.77',
    );
    const persons = page.getByRole('table', { name: 'Finans departmanının kişileri' });
    await expect(persons.getByRole('button', { name: 'Burak Demir' })).toHaveCount(0);

    // Department → person: header, the device (its breakdown), timeline of the day, heat map.
    await persons.getByRole('button', { name: 'Can Öztürk', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=persons&${DAY}&subject=person%3A3$`));
    await expect(page.getByRole('heading', { level: 2, name: 'Can Öztürk' })).toBeVisible();
    const header = page.getByTestId('subject-header');
    await expect(header.getByRole('button', { name: 'Finans' })).toBeVisible();
    await expect(header.getByText('İlk / son etkinlik')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Günün zaman çizelgesi' })).toBeVisible();
    await expect(
      page.getByRole('list', { name: '12.10.2026 gününün aktif dakikaları' }),
    ).toBeVisible();
    await expect(
      page.getByRole('table', { name: 'Son 30 günün saatlik ısı haritası' }).getByRole('row'),
    ).toHaveCount(31);
    await header.getByRole('button', { name: 'Planlama bilgisayarı' }).click();
    await expect(page.getByRole('table', { name: 'Can Öztürk kişisinin cihazları' })).toContainText(
      '3c:52:82:00:10:03',
    );

    // Person → the site's host names and URLs (drawer) → the site → who used it.
    const sections = page.getByRole('tablist', { name: 'Ayrıntılar' });
    await sections.getByRole('tab', { name: /^Siteler/ }).click();
    await page
      .getByRole('table', { name: 'Can Öztürk kullanıcısının siteleri' })
      .getByRole('button', { name: 'portal.example', exact: true })
      .click();
    const sheet = page.getByRole('dialog', { name: 'portal.example' });
    await expect(
      sheet.getByRole('table', { name: 'portal.example ana bilgisayar adları' }),
    ).toBeVisible();
    await expect(sheet.getByText(/^Bu site için URL kaydı yok/)).toBeVisible();
    await sheet.getByRole('button', { name: 'Site ayrıntısına git' }).click();
    await expect(page).toHaveURL(new RegExp(`tab=sites&${DAY}&site=portal\\.example$`));
    await expect(page.getByRole('heading', { level: 2, name: 'portal.example' })).toBeVisible();
    const users = page.getByRole('table', { name: 'portal.example kullanıcıları' });
    for (const name of ['Burak Demir', 'Can Öztürk'])
      await expect(users.getByRole('button', { name, exact: true })).toBeVisible();

    // Back (browser) returns to the person; "Siteler" to the list of every site.
    await page.goBack();
    await expect(page.getByRole('heading', { level: 2, name: 'Can Öztürk' })).toBeVisible();
    await page.goForward();
    await page.getByRole('button', { name: 'Siteler', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/usage\\?tab=sites&${DAY}$`));
    const sites = page.getByRole('table', { name: 'Siteler' });
    await expect(sites.getByRole('row', { name: /windowsupdate\.com/ })).toContainText('arka plan');
    await expect(sites.getByRole('row', { name: /Microsoft-Azure/ })).toContainText('hizmet');

    // A FortiGuard service keeps its case in the address and the request (the API matches it
    // exactly): its background traffic and who caused it.
    await sites.getByRole('button', { name: 'Microsoft-Azure', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=sites&${DAY}&site=Microsoft-Azure$`));
    await expect(page.getByRole('heading', { level: 2, name: 'Microsoft-Azure' })).toBeVisible();
    await expect(
      page.getByRole('table', { name: 'Microsoft-Azure kullanıcıları' }).locator('tbody tr'),
    ).not.toHaveCount(0);
    expect(api.callsTo('GET', '/usage/sites/Microsoft-Azure').length).toBeGreaterThan(0);
  });

  test('"Bilinmiyor": the persons and devices of no department', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/usage?tab=departments&from=2026-10-05&to=2026-10-11');
    await page
      .getByRole('table', { name: 'Departmanlar' })
      .getByRole('button', { name: 'Bilinmiyor', exact: true })
      .click();
    await expect(page).toHaveURL(/&department=Bilinmiyor$/);
    // The API scopes the lists to the department; its subjects' own `department` is null.
    await expect
      .poll(() => lastQuery(api, '/usage/subjects')?.get('department'))
      .toBe('Bilinmiyor');
    const devices = page.getByRole('table', { name: 'Bilinmiyor departmanının cihazları' });
    await expect(devices.getByRole('button', { name: 'raspberrypi', exact: true })).toBeVisible();
    await expect(devices).not.toContainText('Bu dönemde bu departmanın cihazı için trafik yok');
  });

  test('the unknown category: "Bilinmiyor" for the API, "Unknown" on the English pages', async ({
    page,
  }) => {
    await usePreferences(page, { lang: 'en' });
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto(`/usage?tab=sites&${DAY}`);
    const sites = page.getByRole('table', { name: 'Sites' });
    await expect(sites.getByRole('button', { name: 'video.example', exact: true })).toBeVisible();

    // The filter "Unknown": the rows on screen and the same rows in the export.
    await page.getByRole('combobox', { name: 'Category' }).click();
    await page.getByRole('option', { name: 'Unknown', exact: true }).click();
    await expect(sites.getByRole('button', { name: 'posta.example', exact: true })).toBeVisible();
    await expect(sites.getByRole('button', { name: 'video.example', exact: true })).toHaveCount(0);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export: Sites' }).click();
    await page.getByRole('menuitem', { name: 'CSV (.csv)' }).click();
    const text = await readFile(await (await download).path(), 'utf8');
    expect(text).toContain('\r\nposta.example;domain;;');
    expect(api.callsTo('GET', '/usage/export.csv')[0]?.query.get('category')).toBe('Bilinmiyor');

    // A site's "Unknown" opens the category the API calls "Bilinmiyor", shown as "Unknown".
    await sites
      .getByRole('row', { name: /posta\.example/ })
      .getByRole('button', { name: 'Unknown', exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`tab=categories&${DAY}&category=Bilinmiyor$`));
    await expect
      .poll(() => api.callsTo('GET', '/usage/categories/Bilinmiyor').length)
      .toBeGreaterThan(0);
    await expect(
      page.getByRole('heading', { level: 2, name: 'Unknown', exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('list', { name: 'Summary of Unknown' })).toBeVisible();
    await expect(page.getByRole('table', { name: 'Sites of the category Unknown' })).toContainText(
      'posta.example',
    );
    await expect(page.getByRole('table', { name: 'Users of the category Unknown' })).toBeVisible();

    // The list of categories and the company's categories call it "Unknown" too.
    await page.goto(`/usage?tab=categories&${DAY}`);
    const categories = page.getByRole('table', { name: 'Categories' });
    await expect(categories.getByRole('button', { name: 'Video', exact: true })).toBeVisible();
    await expect(categories.getByRole('button', { name: 'Bilinmiyor' })).toHaveCount(0);
    await categories.getByRole('button', { name: 'Unknown', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=categories&${DAY}&category=Bilinmiyor$`));
    await page.goto(`/usage?tab=company&${DAY}`);
    const top = page.getByRole('list', { name: 'Categories' });
    await expect(top.getByRole('button', { name: 'Video', exact: true })).toBeVisible();
    await expect(top.getByRole('button', { name: 'Bilinmiyor' })).toHaveCount(0);
    await top.getByRole('button', { name: 'Unknown', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=categories&${DAY}&category=Bilinmiyor$`));
  });

  test('site → category → users; URLs and blocked attempts; a period; a device', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto(`/usage?tab=sites&${DAY}&site=video.example`);
    await page.getByRole('button', { name: 'Video', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=categories&${DAY}&category=Video$`));
    await expect(page.getByRole('table', { name: 'Video kategorisinin siteleri' })).toContainText(
      'video.example',
    );
    // The category's figures only: no online time, blocked attempts or first/last activity.
    const users = page.getByRole('table', { name: 'Video kategorisinin kullanıcıları' });
    await expect(users.getByRole('columnheader', { name: /Süre/ })).toBeVisible();
    for (const header of [/Çevrimiçi/, /Engellenen/, /İlk – son/])
      await expect(users.getByRole('columnheader', { name: header })).toHaveCount(0);
    await expect(
      page.getByText('Bu kategorideki süre ve veri, kişi veya cihaz başına (en fazla 200)'),
    ).toBeVisible();
    await users.getByRole('button', { name: 'Ayşe Yılmaz', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tab=persons&${DAY}&subject=person%3A1$`));

    // The web filter's URLs, a blocked one marked; blocked attempts of every filter.
    const sections = page.getByRole('tablist', { name: 'Ayrıntılar' });
    await sections.getByRole('tab', { name: /^URL'ler/ }).click();
    const urls = page.getByRole('table', { name: "Ayşe Yılmaz kullanıcısının URL'leri" });
    await expect(urls.locator('tbody tr')).toHaveCount(USAGE.subject_person.urls.length);
    await page.getByLabel('URL ara').fill('bahis');
    await expect(urls.locator('tbody tr').first()).toContainText('bahis.example/canli');
    await expect(urls.locator('tbody tr').first()).toContainText('engellendi');
    await page.getByLabel('URL ara').fill('olmayan-adres');
    await expect(urls).toContainText('Aramaya uyan kayıt yok.');
    await sections.getByRole('tab', { name: /^Engellenenler/ }).click();
    const blocked = page.getByRole('table', {
      name: 'Ayşe Yılmaz kullanıcısının engellenen denemeleri',
    });
    await expect(blocked).toContainText('BitTorrent');
    await expect(blocked).toContainText('Uygulama kontrolü');

    // A period (generated): the table per day with its total, no timeline.
    await page.goto('/usage?tab=persons&subject=person%3A1&from=2026-10-05&to=2026-10-11');
    await expect(page.getByTestId('period')).toHaveAccessibleName(
      'Dönem: Geçen hafta, 05.10.2026 – 11.10.2026',
    );
    await expect(page.getByRole('table', { name: 'Günlere göre' }).getByRole('row')).toHaveCount(8);
    await expect(page.getByText(/^Toplam: aktif /)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Günün zaman çizelgesi' })).toHaveCount(0);

    // Cihaz: devices assigned to nobody and bare addresses; one of them.
    await page.goto(`/usage?tab=devices&${DAY}`);
    const devices = page.getByRole('table', { name: 'Cihazlar ve IP adresleri' });
    await expect(page.getByText(/^Kimseye atanmamış cihazlar ve MAC'i bilinmeyen/)).toBeVisible();
    await expect(devices.getByRole('button', { name: '172.20.41.77' })).toBeVisible();
    await devices.getByRole('button', { name: 'PC-02', exact: true }).click();
    await expect(page).toHaveURL(
      new RegExp(`tab=devices&${DAY}&subject=device%3A3c%3A52%3A82%3A00%3A10%3A02$`),
    );
    await expect(page.getByRole('heading', { level: 2, name: 'PC-02' })).toBeVisible();
    await expect(page.getByTestId('subject-header')).toContainText('3c:52:82:00:10:02');
    await expect(page.getByTestId('subject-header')).toContainText('172.20.43.81');
  });

  test('the period and the hours go to the API and stay in the address', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/usage?tab=persons');
    const table = page.getByRole('table', { name: 'Kişiler' });
    await expect(table.getByRole('button', { name: 'Ayşe Yılmaz', exact: true })).toBeVisible();

    await page.getByTestId('period').click();
    await page.getByRole('button', { name: 'Geçen hafta' }).click();
    await expect(page).toHaveURL(/tab=persons&from=2026-10-05&to=2026-10-11$/);
    await expect.poll(() => lastQuery(api, '/usage/subjects')?.get('from')).toBe('2026-10-05');
    expect(lastQuery(api, '/usage/subjects')?.get('to')).toBe('2026-10-11');
    // The whole list in one request (limit 100 000).
    expect(lastQuery(api, '/usage/subjects')?.get('limit')).toBe('100000');
    expect(lastQuery(api, '/usage/subjects')?.get('offset')).toBe('0');

    await page
      .getByRole('group', { name: 'Saatler' })
      .getByRole('button', { name: 'Mesai dışı' })
      .click();
    await expect(page).toHaveURL(/hours=off/);
    await expect.poll(() => lastQuery(api, '/usage/subjects')?.get('hours')).toBe('off');

    await page.getByRole('button', { name: 'Önceki dönem' }).click();
    await expect(page).toHaveURL(/from=2026-09-28&to=2026-10-04&hours=off/);
    await page.reload();
    await expect(page.getByTestId('period')).toHaveAccessibleName(
      'Dönem: Özel aralık, 28.09.2026 – 04.10.2026',
    );
    await expect(
      page.getByRole('group', { name: 'Saatler' }).getByRole('button', { name: 'Mesai dışı' }),
    ).toHaveAttribute('aria-pressed', 'true');

    // The search filters in the browser like the API's `q` and never reaches the address.
    await page.getByLabel('Ara', { exact: true }).fill('yıldız');
    await expect(table.getByRole('button', { name: 'Hakan Yıldız', exact: true })).toBeVisible();
    await expect(table.getByRole('button', { name: 'Ayşe Yılmaz', exact: true })).toHaveCount(0);
    expect(page.url()).not.toContain('y%C4%B1ld%C4%B1z');

    await page.getByTestId('period').click();
    await page.getByRole('button', { name: 'Bugün' }).click();
    await expect(page).toHaveURL(/\/usage\?tab=persons&hours=off$/);

    // A day without traffic.
    await page.goto('/usage?from=2026-01-05&to=2026-01-05');
    await expect(page.getByText('Bu aralıkta trafik yok')).toBeVisible();
    await expect(page.getByTestId('data-note')).toContainText(
      'Kullanım verileri henüz hesaplanmadı',
    );
    await expect(page.getByRole('button', { name: 'Sonraki dönem' })).toBeEnabled();
    await page.goto('/usage');
    await expect(page.getByRole('button', { name: 'Sonraki dönem' })).toBeDisabled();
  });

  test('the mock reads a period like the API: `from` alone runs to today', async ({ page }) => {
    // The page always sends both days; other callers of the API may not (`period_of`).
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/usage');
    const answers = await page.evaluate(
      async (queries) => {
        const read = async (query: string) => {
          const response = await fetch(`/api/v1/usage/company?${query}`);
          const body = (await response.json()) as { period?: unknown; error?: { code: string } };
          return { status: response.status, period: body.period, code: body.error?.code };
        };
        return Promise.all(queries.map(read));
      },
      ['from=2026-10-05', 'to=2026-10-05', 'from=2026-10-20'],
    );
    expect(answers).toEqual([
      { status: 200, period: { from: '2026-10-05', to: SAMPLE_TODAY, hours: 'all' } },
      { status: 200, period: { from: '2026-10-05', to: '2026-10-05', hours: 'all' } },
      { status: 422, code: 'usage_period' },
    ]);
  });

  test('exports: the CSV of the main table, the Excel file of the whole view', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto(`/usage?tab=sites&${DAY}`);
    await expect(
      page.getByRole('table', { name: 'Siteler' }).locator('tbody tr').first(),
    ).toBeVisible();
    let download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Dışa aktar: Siteler' }).click();
    await page.getByRole('menuitem', { name: 'Excel (.xlsx)' }).click();
    let saved = await download;
    expect(saved.suggestedFilename()).toBe('arcilogger-kullanim-sites-20261012-20261012.xlsx');
    expect(api.callsTo('GET', '/usage/export.xlsx')[0]?.query.toString()).toBe(
      `view=sites&${DAY}&hours=all`,
    );
    await expect(
      page.getByText('arcilogger-kullanim-sites-20261012-20261012.xlsx indirildi.', {
        exact: true,
      }),
    ).toBeVisible();

    // The list searched: the CSV holds the same rows (the API's `q`).
    await page.goto(`/usage?tab=persons&${DAY}`);
    await page.getByLabel('Ara', { exact: true }).fill('ayşe');
    download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Dışa aktar: Kişiler' }).click();
    await page.getByRole('menuitem', { name: 'CSV (.csv)' }).click();
    saved = await download;
    expect(saved.suggestedFilename()).toBe('arcilogger-kullanim-subjects-20261012-20261012.csv');
    let text = await readFile(await saved.path(), 'utf8');
    expect(text.startsWith(`${BOM}Ad;Tür;Anahtar;MAC;IP;Departman;`)).toBe(true);
    expect(text).toContain('Ayşe Yılmaz;person;person:1;;172.20.43.37;Finans;660;360;');
    expect(text).not.toContain('Burak Demir');
    expect(api.callsTo('GET', '/usage/export.csv')[0]?.query.toString()).toBe(
      `view=subjects&${DAY}&hours=all&kind=person&q=ay%C5%9Fe`,
    );

    // One person: the CSV is the sites table; the other sections are sheets of the Excel file.
    await page.goto(`/usage?tab=persons&${DAY}&subject=person%3A1`);
    const sections = page.getByRole('tablist', { name: 'Ayrıntılar' });
    await sections.getByRole('tab', { name: /^Siteler/ }).click();
    download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Dışa aktar: Siteler' }).click();
    await page.getByRole('menuitem', { name: 'CSV (.csv)' }).click();
    saved = await download;
    text = await readFile(await saved.path(), 'utf8');
    expect(text).toBe(USAGE.export_subject_csv.text);
    expect(api.callsTo('GET', '/usage/export.csv')[1]?.query.toString()).toBe(
      `view=subject&${DAY}&hours=all&key=person%3A1`,
    );
    await sections.getByRole('tab', { name: /^URL'ler/ }).click();
    await page.getByRole('button', { name: "Dışa aktar: URL'ler" }).click();
    await expect(page.getByRole('menuitem', { name: 'Excel (.xlsx)' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'CSV (.csv)' })).toHaveCount(0);
    await expect(
      page.getByText(/^Bu tablo, görünümün Excel dosyasında ayrı bir sayfadır/),
    ).toBeVisible();
    await page.keyboard.press('Escape');

    // The company view: its CSV is the departments with the company total first.
    await page.goto(`/usage?${DAY}`);
    download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Dışa aktar: Departmanlar' }).click();
    await page.getByRole('menuitem', { name: 'CSV (.csv)' }).click();
    saved = await download;
    text = await readFile(await saved.path(), 'utf8');
    expect(text).toBe(USAGE.export_company_csv.text);
    expect(text.split('\r\n')[1]).toMatch(/^Şirket \(toplam\);;5;/);
  });

  test('"Yeniden hesapla" (administrators): checks the days and follows the job', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/usage?from=2026-10-05&to=2026-10-11');
    await page.getByRole('button', { name: 'Diğer işlemler' }).click();
    await page.getByRole('menuitem', { name: 'Yeniden hesapla…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kullanımı yeniden hesapla' });
    await expect(dialog.getByLabel('İlk gün')).toHaveValue('05.10.2026');
    await expect(dialog.getByLabel('Son gün')).toHaveValue('11.10.2026');
    await dialog.getByLabel('İlk gün').fill('14.10.2026');
    await dialog.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
    await expect(dialog.getByText('Gelecekteki bir gün seçilemez.')).toBeVisible();
    expect(api.callsTo('POST', '/usage/rebuild')).toEqual([]);
    // The API refuses days before the oldest log (they would be rewritten empty).
    await dialog.getByLabel('İlk gün').fill('01.08.2026');
    await dialog.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
    await expect(dialog.getByRole('alert')).toHaveText(
      'Veritabanında 15.08.2026 gününden önceki log yok: yeniden hesaplama en erken bu günden ' +
        'başlayabilir (daha eski günler boş hesaplanırdı; özetleri olduğu gibi korunur).',
    );
    expect(api.callsTo('POST', '/usage/rebuild')).toHaveLength(1);
    await dialog.getByLabel('İlk gün').fill('01.10.2026');
    await dialog.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
    await expect(
      dialog.getByRole('progressbar', { name: 'Yeniden hesaplama ilerlemesi' }),
    ).toBeVisible();
    expect(api.callsTo('POST', '/usage/rebuild').at(-1)?.body).toEqual({
      from: '2026-10-01',
      to: '2026-10-11',
    });
    await expect(dialog).toContainText('tolga');
    const companyCalls = api.callsTo('GET', '/usage/company').length;
    await expect(dialog.getByTestId('rebuild-status')).toHaveText(
      'Yeniden hesaplama tamamlandı; görünümler yenilendi.',
      { timeout: 20_000 },
    );
    // Every usage view is read again.
    await expect
      .poll(() => api.callsTo('GET', '/usage/company').length)
      .toBeGreaterThan(companyCalls);
    const job = USAGE.rebuild_requested.job_id;
    expect(api.callsTo('GET', `/usage/rebuild/${job}`).length).toBeGreaterThanOrEqual(4);
    await dialog.getByRole('button', { name: 'Kapat', exact: true }).first().click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('button', { name: 'Diğer işlemler' })).toBeFocused();
  });

  test('a failed rebuild is reported; other roles have no rebuild menu', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', usage: { rebuildFails: true } });
    await page.goto('/usage');
    await page.getByRole('button', { name: 'Diğer işlemler' }).click();
    await page.getByRole('menuitem', { name: 'Yeniden hesapla…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kullanımı yeniden hesapla' });
    await dialog.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
    await expect(dialog.getByTestId('rebuild-status')).toHaveText(
      'Yeniden hesaplama başarısız: database busy (injected)',
      { timeout: 20_000 },
    );

    await mockApi(page, { signedInAs: 'mehmet.kaya' });
    await page.goto('/usage');
    await expect(page.getByRole('heading', { level: 1, name: 'Kullanım' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Diğer işlemler' })).toHaveCount(0);
  });

  test('names by default; pseudonyms for anonymous viewers only with the setting', async ({
    page,
  }) => {
    // The report viewer holds only `usage.anonymous`: names by default (owner decision).
    await mockApi(page, { signedInAs: 'zeynep' });
    await page.goto(`/usage?tab=persons&${DAY}`);
    const table = page.getByRole('table', { name: 'Kişiler' });
    await expect(table.getByRole('button', { name: 'Ayşe Yılmaz', exact: true })).toBeVisible();
    await expect(table.getByText('anonim')).toHaveCount(0);

    // With the setting: the captured pseudonyms, accepted back; a real key is not found.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await mockApi(page, { signedInAs: 'zeynep', usage: { pseudonymize: true } });
    await page.goto(`/usage?tab=persons&${DAY}`);
    const pseudonym = table.getByRole('button', { name: /^Kullanıcı-D364/ });
    await expect(pseudonym).toBeVisible();
    await expect(table.getByText('Ayşe Yılmaz')).toHaveCount(0);
    await pseudonym.click();
    await expect(page).toHaveURL(/subject=p%3Ad3648b10d647ade3$/);
    await expect(page.getByRole('heading', { level: 2 }).getByText('anonim')).toBeVisible();
    const header = page.getByTestId('subject-header');
    await expect(header.getByRole('button', { name: /^Cihaz-FB1A/ })).toBeVisible();
    await expect(header).not.toContainText('Ayşe');
    await expect(header).not.toContainText('172.20.43.37');
    await page.goto(`/usage?tab=persons&${DAY}&subject=person%3A1`);
    await expect(
      page.getByText('Bu dönemde bu kişi ya da cihaz için kullanım verisi yok.'),
    ).toBeVisible();

    // The key is not stored: the API's instruction instead of the list; the company view works.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await mockApi(page, { signedInAs: 'zeynep', usage: { pseudonymize: true, keyMissing: true } });
    await page.goto(`/usage?tab=persons&${DAY}`);
    await expect(page.getByText('Kullanım verileri yüklenemedi')).toBeVisible();
    await expect(page.getByText(/usage\.pseudonym_key/)).toBeVisible();
    await page.goto(`/usage?${DAY}`);
    await expect(page.getByRole('group', { name: 'Şirket geneli özet' })).toBeVisible();
  });

  test('views with names wait for the KVKK notice; the company view does not', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'mehmet.kaya' });
    api.get('mehmet.kaya').privacy_accepted = false;
    await page.goto('/usage?tab=sites');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    expect(api.callsTo('GET', '/usage/sites')).toEqual([]);
    expect(api.callsTo('GET', '/usage/subjects')).toEqual([]);
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await expect(
      page.getByRole('table', { name: 'Siteler' }).getByRole('button', { name: 'portal.example' }),
    ).toBeVisible();
  });

  test('a role without a usage permission gets the "no permission" page', async ({ page }) => {
    await usePreferences(page, { collapsed: false });
    const api = await mockApi(page, {
      signedInAs: 'zeynep',
      revoke: { viewer: ['usage.anonymous'] },
    });
    await page.goto('/usage?tab=persons');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Ana menü' }).getByRole('link', { name: 'Kullanım' }),
    ).toHaveCount(0);
    expect(api.calls.filter((call) => call.path.startsWith('/usage'))).toEqual([]);
  });

  test('the API refuses a request: the error and "Tekrar dene"', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.failNext = { route: 'GET /usage/company', status: 422, code: 'validation_error' };
    await page.goto('/usage');
    await expect(page.getByText('Kullanım verileri yüklenemedi')).toBeVisible();
    await page.getByRole('button', { name: 'Tekrar dene' }).click();
    await expect(page.getByRole('group', { name: 'Şirket geneli özet' })).toBeVisible();
  });
});
