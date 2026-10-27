import { expect, test, type Page } from '@playwright/test';

import {
  confirmReauth,
  expectNoA11yViolations,
  usePreferences,
  useSampleClock,
  type Theme,
} from './fixtures';
import { mockApi, type MockApi, type MockOptions } from './mock-api';
import { MANY_ROWS, mockSearch, type SearchMock, type SearchOptions } from './mock-search';
import { RAW, SAMPLE_TODAY, SEARCH } from './samples';

/** "Son 24 saat" at the sample clock (15:00 site time on the sample day). */
const DAY_BEFORE = (() => {
  const date = new Date(`${SAMPLE_TODAY}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
})();
const LAST_24H = {
  from: `${DAY_BEFORE}T15:00:00+03:00`,
  to: `${SAMPLE_TODAY}T15:00:00+03:00`,
};

async function openSearch(
  page: Page,
  user = 'tolga',
  options: { api?: MockOptions; search?: SearchOptions; path?: string; clock?: boolean } = {},
): Promise<{ api: MockApi; search: SearchMock }> {
  // The browser's clock at 15:00 site time on the sample day (`useSampleClock`). Not for a drag
  // across the histogram: recharts throttles mouse moves by `Date.now()`, which a fixed clock stops.
  if (options.clock !== false)
    await page.clock.setFixedTime(new Date(`${SAMPLE_TODAY}T15:00:00+03:00`));
  const api = await mockApi(page, { signedInAs: user, ...options.api });
  const search = await mockSearch(page, api, options.search);
  await page.goto(options.path ?? '/search');
  await expect(page.getByRole('heading', { level: 1, name: 'Arama' })).toBeVisible();
  return { api, search };
}

function queryBox(page: Page) {
  return page.getByRole('combobox', { name: 'Sorgu' });
}

async function runQuery(page: Page, q: string) {
  const box = queryBox(page);
  await box.fill(q);
  await box.press('Enter');
}

function results(page: Page) {
  return page.getByRole('table', { name: 'Arama sonuçları' });
}

/** Rows of the results table (its header row left out). */
function resultRows(page: Page) {
  return results(page).locator('tbody tr:not([aria-hidden])');
}

/** Whether `text` appears in the address, the tab's history state or the browser's storage. */
async function keptAnywhere(page: Page, text: string): Promise<boolean> {
  const kept = await page.evaluate(() =>
    JSON.stringify([
      window.location.href,
      window.history.state,
      Object.entries(localStorage),
      Object.entries(sessionStorage),
    ]),
  );
  return kept.includes(text) || kept.includes(encodeURIComponent(text));
}

test.describe('Arama', () => {
  test('runs a query in a request body, never in the address, history or storage', async ({
    page,
  }) => {
    const { api } = await openSearch(page);
    // Nothing is searched when the page opens.
    await expect(page.getByText('Bir sorgu yazıp “Ara”ya basın')).toBeVisible();
    expect(api.callsTo('POST', '/search')).toHaveLength(0);
    // An example only fills the box: its address is searched (and audited) only on "Ara".
    await page
      .getByRole('button', {
        name: 'Bu örneği sorguya yaz: srcip:172.20.41.105 type:web action:blocked',
      })
      .click();
    await expect(queryBox(page)).toHaveValue('srcip:172.20.41.105 type:web action:blocked');
    expect(api.callsTo('POST', '/search')).toHaveLength(0);
    await expect(
      page.getByText(
        'Bu sayfa kişisel veri gösterir; sorgular ve açılan ham satırlar denetim kaydına yazılır.',
      ),
    ).toBeVisible();

    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    expect(api.callsTo('POST', '/search').map((call) => call.body)).toEqual([
      { q: 'srcip:172.20.42.57', ...LAST_24H },
    ]);
    await expect(page.getByRole('heading', { name: 'Zaman dağılımı' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Dağılım' })).toBeVisible();
    await expect(page.getByText('4 kayıt', { exact: true })).toBeVisible();
    await expect(
      page.getByText('09.10.2026 öncesine ait kayıtlar veritabanında yok', { exact: false }),
    ).toBeVisible();
    await expect(results(page).getByText('172.20.42.57:61001 → 203.0.113.10:443')).toBeVisible();

    expect(new URL(page.url()).search).toBe('');
    expect(await keptAnywhere(page, '172.20.42.57')).toBe(false);
    for (const call of api.calls)
      expect(`${call.path}?${call.query.toString()}`, call.path).not.toContain('172.20.42');
  });

  test('says when nothing was found', async ({ page }) => {
    await openSearch(page);
    await runQuery(page, 'srcip:172.20.42.250');
    await expect(
      page.getByText('Bu aralıkta kayıt bulunamadı. Zaman aralığını genişletmeyi deneyin.'),
    ).toBeVisible();
  });

  test('underlines a problem at its characters, in Turkish and in English', async ({ page }) => {
    const { api } = await openSearch(page);
    await queryBox(page).fill('srcip:"172.20');
    const underline = page.getByTestId('query-problem');
    await expect(underline).toHaveText('"172.20');
    const turkish = 'Sorgu okunamadı: kapanmamış tırnak işareti (7. karakter).';
    await expect(page.getByText(turkish)).toBeVisible();
    await expect(queryBox(page)).toHaveAttribute('aria-invalid', 'true');
    expect(api.callsTo('POST', '/search/parse').at(-1)?.body).toEqual({ q: 'srcip:"172.20' });

    // Searching anyway: the server refuses the same span.
    await queryBox(page).press('Enter');
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(1);
    await expect(underline).toHaveText('"172.20');

    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitemradio', { name: 'English' }).click();
    await expect(
      page.getByText('The query cannot be read: a quotation mark is not closed (character 7).'),
    ).toBeVisible();
    await expect(page.getByText(turkish)).toHaveCount(0);

    const english = page.getByRole('combobox', { name: 'Query' });
    await english.fill('istemciip:172.20.42.57');
    await expect(underline).toHaveText('istemciip:172.20.42.57');
    await expect(page.getByText(/^There is no field “istemciip”/)).toBeVisible();
  });

  test('the syntax help lists the fields, marks the fast ones and fills in an example', async ({
    page,
  }) => {
    await openSearch(page);
    await page.getByRole('button', { name: 'Sorgu söz dizimi yardımı (?)' }).click();
    const help = page.getByRole('dialog', { name: 'Sorgu söz dizimi' });
    await expect(help).toBeVisible();
    await expect(help.getByRole('row', { name: /^srcip kaynakip, istemci/ })).toContainText(
      'İndeksli',
    );
    await expect(help.getByRole('row', { name: /^url/ })).toContainText('Taranır');
    await expect(help.getByText('type:dhcp', { exact: true })).toBeVisible();
    await help
      .getByRole('button', {
        name: 'Bu örneği sorguya yaz: domain:example.com -action:passthrough',
      })
      .click();
    await expect(help).toBeHidden();
    await expect(queryBox(page)).toHaveValue('domain:example.com -action:passthrough');
  });

  test('completes field names, then values the catalogue knows', async ({ page }) => {
    await openSearch(page);
    const box = queryBox(page);
    await box.fill('kay');
    const list = page.getByRole('listbox', { name: 'Öneriler' });
    await expect(list.getByRole('option')).toHaveText([
      /^kaynak:/,
      /^kaynakip:/,
      /^kaynakport:/,
      /^kaynakmac:/, // srcmac: (ADR-0016), in catalogue order
    ]);
    await box.press('ArrowDown');
    await expect(list.getByRole('option').first()).toHaveAttribute('aria-selected', 'true');
    await box.press('Enter');
    await expect(box).toHaveValue('kaynak:');
    await box.press('f');
    await expect(list.getByRole('option')).toHaveText(['fortigate']);
    await box.press('ArrowDown');
    await box.press('Tab');
    await expect(box).toHaveValue('kaynak:fortigate ');
    await box.press('Escape');
    await expect(list).toBeHidden();
  });

  test('a facet narrows the query and "Hariç tut" excludes, each a new search', async ({
    page,
  }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    const facets = page.getByRole('region', { name: 'Dağılım' });
    const kinds = facets.getByRole('region', { name: 'Tür' });
    await kinds.getByRole('button', { name: /^Web filtresi/ }).click();
    await expect(queryBox(page)).toHaveValue('srcip:172.20.42.57 type:web');
    await expect(resultRows(page)).toHaveCount(1);
    await facets
      .getByRole('region', { name: 'Tür' })
      .getByRole('button', { name: 'Hariç tut: Web filtresi' })
      .click();
    await expect(queryBox(page)).toHaveValue('srcip:172.20.42.57 -type:web');
    await expect(resultRows(page)).toHaveCount(3);
    expect(api.callsTo('POST', '/search').map((call) => (call.body as { q: string }).q)).toEqual([
      'srcip:172.20.42.57',
      'srcip:172.20.42.57 type:web',
      'srcip:172.20.42.57 -type:web',
    ]);
    // The department is the VLAN's, said so.
    await expect(facets.getByRole('region', { name: "Departman (VLAN'a göre)" })).toBeVisible();
    await expect(
      page.getByText(
        "Departman, istemci adresinin VLAN'ına tanımlı varsayılan departmandır; kişi ataması değildir.",
      ),
    ).toBeVisible();
  });

  test('dragging across the histogram searches the shorter range', async ({ page }) => {
    const { api } = await openSearch(page, 'tolga', { clock: false });
    await runQuery(page, 'srcip:172.20.42.57');
    const chart = page.getByTestId('histogram');
    await expect(chart.locator('.recharts-surface')).toBeVisible();
    const drag = async (start: number, end: number) => {
      const box = await chart.boundingBox();
      if (!box) throw new Error('no histogram');
      // The plot lies right of the 48 px value axis.
      const x = (share: number) => box.x + 48 + (box.width - 56) * share;
      const y = box.y + box.height / 2;
      await page.mouse.move(x(start), y);
      await page.mouse.down();
      await page.mouse.move(x((start + end) / 2), y, { steps: 5 });
      await page.mouse.move(x(end), y, { steps: 5 });
      await page.mouse.up();
    };
    const runs = () => api.callsTo('POST', '/search').map((call) => call.body);

    // Across every bar: the hourly buckets start before the range and end after it, and the zoom
    // is cut to the search's own range (a longer one could exceed `search.max_days`).
    await drag(0.01, 0.99);
    await expect.poll(() => runs().length).toBe(2);
    const first = runs()[0] as { from: string; to: string };
    expect(runs()[1]).toEqual({ q: 'srcip:172.20.42.57', from: first.from, to: first.to });

    await drag(0.3, 0.8);
    await expect.poll(() => runs().length).toBe(3);
    const url = new URL(page.url());
    const from = url.searchParams.get('from');
    const to = url.searchParams.get('to');
    expect(from && to && Date.parse(from) < Date.parse(to)).toBe(true);
    expect(Date.parse(from ?? '')).toBeGreaterThan(Date.parse(first.from));
    expect(Date.parse(to ?? '')).toBeLessThan(Date.parse(first.to));
    expect(runs()[2]).toEqual({ q: 'srcip:172.20.42.57', from, to });
    await expect(
      page.getByRole('button', { name: /^Zaman aralığı: \d{2}\.\d{2}\.2026/ }),
    ).toBeVisible();
    expect(await keptAnywhere(page, '172.20.42.57')).toBe(false);
  });

  test('the time range picker runs the same query on another range', async ({ page }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'type:web');
    await expect(resultRows(page)).toHaveCount(1);
    await page.getByRole('button', { name: 'Zaman aralığı: Son 24 saat' }).click();
    await page.getByRole('button', { name: 'Son 1 saat' }).click();
    await expect(page.getByRole('button', { name: 'Zaman aralığı: Son 1 saat' })).toBeVisible();
    expect(new URL(page.url()).search).toBe('?range=1h');
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(2);
    expect(api.callsTo('POST', '/search')[1]?.body).toEqual({
      q: 'type:web',
      from: `${SAMPLE_TODAY}T14:00:00+03:00`,
      to: `${SAMPLE_TODAY}T15:00:00+03:00`,
    });

    await page.getByRole('button', { name: 'Zaman aralığı: Son 1 saat' }).click();
    await page.getByRole('button', { name: 'Özel' }).click();
    await page.getByLabel('Başlangıç').fill('01.09.2026 00:00');
    await page.getByLabel('Bitiş').fill(`${SAMPLE_TODAY.split('-').reverse().join('.')} 10:00`);
    await page.getByRole('button', { name: 'Uygula' }).click();
    await expect(page.getByText('Aralık en fazla 31 gün olabilir.')).toBeVisible();
    await page.getByLabel('Başlangıç').fill(`${SAMPLE_TODAY.split('-').reverse().join('.')} 08:00`);
    await page.getByRole('button', { name: 'Uygula' }).click();
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(3);
    expect(api.callsTo('POST', '/search')[2]?.body).toEqual({
      q: 'type:web',
      from: `${SAMPLE_TODAY}T08:00:00+03:00`,
      to: `${SAMPLE_TODAY}T10:00:00+03:00`,
    });
  });

  test('"Daha fazla" reads the next pages by the search id', async ({ page }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'srcip:172.20.41.0/24');
    await expect(page.getByText('100 kayıt gösteriliyor')).toBeVisible();
    await page.getByRole('button', { name: 'Daha fazla' }).click();
    await expect(page.getByText('200 kayıt gösteriliyor')).toBeVisible();
    await page.getByRole('button', { name: 'Daha fazla' }).click();
    await expect(page.getByText(`${MANY_ROWS} kayıt gösteriliyor`)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Daha fazla' })).toHaveCount(0);
    expect(api.callsTo('POST', '/search/page').map((call) => call.body)).toEqual([
      { search_id: 'ornek-arama-01', cursor: 'rows:100' },
      { search_id: 'ornek-arama-01', cursor: 'rows:200' },
    ]);
  });

  test('a scan cut by its time budget goes on five times by itself, then waits', async ({
    page,
  }) => {
    const { api } = await openSearch(page);
    await runQuery(page, '"login failure"');
    const banner = page
      .getByRole('status')
      .filter({ hasText: 'Arama aralığın tamamını tarayamadı' });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Arama 5 kez kendiliğinden sürdü');
    expect(api.callsTo('POST', '/search/page')).toHaveLength(5);
    await expect(resultRows(page)).toHaveCount(1);
    await expect(
      page.getByText('Bu sorgu indeks kullanamıyor ve zaman dilimleri halinde taranıyor.', {
        exact: false,
      }),
    ).toBeVisible();
    // The count is "at least" while the statistics did not cover the range.
    await expect(page.getByText(/^≥ 2 kayıt/)).toBeVisible();
    await banner.getByRole('button', { name: 'Devam et' }).click();
    await expect(banner).toBeHidden();
    await expect(resultRows(page)).toHaveCount(2);
    expect(api.callsTo('POST', '/search/page')).toHaveLength(6);
    expect(api.callsTo('POST', '/search')).toHaveLength(1);
  });

  test('a row opens its fields, and its raw line with its SHA-256', async ({ page }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'type:traffic');
    await resultRows(page).first().click();
    const sheet = page.getByRole('dialog', { name: 'Kayıt ayrıntısı' });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole('definition').filter({ hasText: '203.0.113.10' })).toBeVisible();
    await expect(
      sheet.getByText('Bu görüntüleme denetim kaydına yazıldı', { exact: false }),
    ).toBeVisible();
    const event = api.callsTo('GET', '/search/event')[0];
    expect(event?.query.get('table')).toBe('fg_traffic');
    expect(event?.query.get('id')).toBe('10001');

    await sheet.getByRole('button', { name: 'Ham satır' }).click();
    const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    const line = RAW.lines['fortigate|2026-10-09|27'];
    if (!line) throw new Error('raw sample missing');
    await expect(raw.getByText(line.sha256)).toBeVisible();
    await expect(raw.getByText(line.file)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(raw).toBeHidden();
    await expect(sheet).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();

    // "Ham satır" in the row opens the raw line directly.
    await results(page).getByRole('button', { name: 'Ham satırı aç: fortigate, satır 27' }).click();
    await expect(raw).toBeVisible();
    expect(api.callsTo('GET', '/raw').at(-1)?.query.toString()).toBe(
      'source=fortigate&day=2026-10-09&line=27&offset=10260',
    );
  });

  test('saves, runs, renames and deletes a personal saved search', async ({ page }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'type:web');
    await expect(resultRows(page)).toHaveCount(1);
    const menu = page.getByRole('button', { name: 'Kayıtlı aramalar' });
    await menu.click();
    await page.getByRole('menuitem', { name: 'Bu aramayı kaydet…' }).click();
    const save = page.getByRole('dialog', { name: 'Aramayı kaydet' });
    await save.getByRole('button', { name: 'Kaydet' }).click();
    await expect(save.getByText('Bu alan zorunludur.')).toBeVisible();
    await save.getByLabel('Ad').fill('Engellenen web');
    await save.getByRole('button', { name: 'Kaydet' }).click();
    await expect(save).toBeHidden();
    expect(api.callsTo('POST', '/search/saved').map((call) => call.body)).toEqual([
      { name: 'Engellenen web', q: 'type:web', range: { preset: '24h' } },
    ]);

    // The same name again is refused.
    await menu.click();
    await page.getByRole('menuitem', { name: 'Bu aramayı kaydet…' }).click();
    await save.getByLabel('Ad').fill('engellenen WEB');
    await save.getByRole('button', { name: 'Kaydet' }).click();
    await expect(save.getByText('Bu adla kayıtlı bir aramanız zaten var.')).toBeVisible();
    await save.getByRole('button', { name: 'İptal' }).click();

    // Running it: by its id, the address names the id and the range, never the query.
    await queryBox(page).fill('');
    await menu.click();
    await page.getByRole('menuitem', { name: /^Engellenen web/ }).click();
    await expect(queryBox(page)).toHaveValue('type:web');
    expect(api.callsTo('POST', '/search').at(-1)?.body).toEqual({ saved_id: 1 });
    expect(new URL(page.url()).search).toBe('?range=24h&saved=1');

    // A reload runs it again.
    await page.reload();
    await expect(queryBox(page)).toHaveValue('type:web');
    expect(api.callsTo('POST', '/search').at(-1)?.body).toEqual({ saved_id: 1 });

    await menu.click();
    await page.getByRole('menuitem', { name: 'Kayıtlı aramaları yönet…' }).click();
    const manage = page.getByRole('dialog', { name: 'Kayıtlı aramalar' });
    await manage.getByRole('button', { name: 'Yeniden adlandır: Engellenen web' }).click();
    const rename = page.getByRole('dialog', { name: 'Kayıtlı aramayı yeniden adlandır' });
    await rename.getByLabel('Ad').fill('Web engelleri');
    await rename.getByRole('button', { name: 'Kaydet' }).click();
    await expect(manage.getByText('Web engelleri')).toBeVisible();
    expect(api.callsTo('PUT', '/search/saved/1').map((call) => call.body)).toEqual([
      { name: 'Web engelleri', q: 'type:web', range: { preset: '24h' } },
    ]);
    await manage.getByRole('button', { name: 'Sil: Web engelleri' }).click();
    const confirm = page.getByRole('dialog', { name: 'Kayıtlı aramayı sil' });
    await confirm.getByRole('button', { name: 'Sil' }).click();
    await expect(manage.getByText('Kayıtlı aramanız yok.')).toBeVisible();
    expect(api.callsTo('DELETE', '/search/saved/1')).toHaveLength(1);
  });

  test('exports after the password is confirmed and checks the file against Repr-Digest', async ({
    page,
  }) => {
    const { api } = await openSearch(page, 'tolga', { api: { staleReauth: true } });
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    await page.getByRole('button', { name: 'Dışa aktar' }).click();
    const dialog = page.getByRole('dialog', { name: 'Arama sonuçlarını dışa aktar' });
    await expect(
      dialog.getByText("Bu dosya mühürlü değildir; bütünlük için Kanıt Paketi'ni kullanın."),
    ).toBeVisible();
    await dialog.getByLabel(/^CSV/).check();
    const download = page.waitForEvent('download');
    await dialog.getByRole('button', { name: 'Dışa aktar' }).click();
    await confirmReauth(page);
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/^arama-\d{8}T\d{4}-\d{8}T\d{4}\.csv$/);
    await expect(
      dialog.getByText('İndirilen dosyanın özeti bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
    ).toBeVisible();
    const exports = api.callsTo('POST', '/search/export');
    expect(exports.map((call) => call.body)).toEqual([
      { search_id: 'ornek-arama-01', format: 'csv' },
      { search_id: 'ornek-arama-01', format: 'csv' },
    ]);
    expect(api.callsTo('POST', '/auth/reauth')).toHaveLength(1);
  });

  test('says when the exported file was cut short', async ({ page }) => {
    await openSearch(page, 'tolga', { search: { exportTruncated: 'rows' } });
    await runQuery(page, 'type:web');
    await page.getByRole('button', { name: 'Dışa aktar' }).click();
    const dialog = page.getByRole('dialog', { name: 'Arama sonuçlarını dışa aktar' });
    const download = page.waitForEvent('download');
    await dialog.getByRole('button', { name: 'Dışa aktar' }).click();
    expect((await download).suggestedFilename()).toMatch(/-kismi\.xlsx$/);
    await expect(page.getByText('Dosya kısaltıldı', { exact: true })).toBeVisible();
    await expect(
      page.getByText('Satır sınırına ulaşıldı: dosya aramanın yalnızca ilk satırlarını içerir.', {
        exact: true,
      }),
    ).toBeVisible();
  });

  test('the auditor searches but has neither export nor "Kanıt paketine aktar"', async ({
    page,
  }) => {
    await openSearch(page, 'ayse.yilmaz');
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    await expect(page.getByRole('button', { name: 'Kayıtlı aramalar' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dışa aktar' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Kanıt paketine aktar' })).toHaveCount(0);
  });

  test('"Kanıt paketine aktar" opens the evidence wizard with the search\'s lines', async ({
    page,
  }) => {
    const { api } = await openSearch(page, 'tolga', { search: { unsealedDays: ['2026-10-09'] } });
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    await page.getByRole('button', { name: 'Kanıt paketine aktar' }).click();
    const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
    await expect(wizard).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/evidence');
    expect(api.callsTo('POST', '/search/refs').map((call) => call.body)).toEqual([
      { search_id: 'ornek-arama-01', limit: 10000 },
    ]);
    await expect(wizard.getByText('Arama sonucundan 4 satır aktarıldı')).toBeVisible();
    await expect(wizard.getByText('Sorgu: srcip:172.20.42.57')).toBeVisible();
    await expect(wizard.getByText(/^Şu günler henüz mühürlenmedi: 09\.10\.2026\./)).toBeVisible();
    await expect(wizard.getByLabel('İlk gün')).toHaveValue('09.10.2026');
    await expect(wizard.getByLabel('Son gün')).toHaveValue('09.10.2026');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByLabel('Alıntılanacak satırlar')).toHaveValue(
      [
        '2026-10-09 fortigate 27',
        '2026-10-09 core-r1 7',
        '2026-10-09 fortigate 2',
        '2026-10-09 core-r1 5',
      ].join('\n'),
    );
    expect(await keptAnywhere(page, '172.20.42.57')).toBe(false);
    expect(await keptAnywhere(page, 'fortigate 27')).toBe(false);
  });

  test('nothing runs before the KVKK notice is accepted; a refused search runs after it', async ({
    page,
  }) => {
    const { api } = await openSearch(page, 'ayse.yilmaz');
    // The server asks for the notice (another tab, a new notice version).
    api.get('ayse.yilmaz').privacy_accepted = false;
    await runQuery(page, 'type:web');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    await expect(results(page)).toHaveCount(0);
    expect(api.callsTo('POST', '/search')).toHaveLength(1);
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await expect(resultRows(page)).toHaveCount(1);
    expect(api.callsTo('POST', '/search').map((call) => (call.body as { q: string }).q)).toEqual([
      'type:web',
      'type:web',
    ]);
  });

  test('a saved search in the address waits for the KVKK notice', async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    const search = await mockSearch(page, api);
    search.saved.set(1, {
      id: 1,
      owner: api.get('ayse.yilmaz').id,
      name: 'Web',
      q: 'type:web',
      range: { preset: '24h' },
      created_at: '2026-10-12T08:00:00Z',
      updated_at: '2026-10-12T08:00:00Z',
      last_run_at: null,
    });
    api.get('ayse.yilmaz').privacy_accepted = false;
    await page.goto('/search?range=24h&saved=1');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    expect(api.callsTo('POST', '/search')).toHaveLength(0);
    expect(api.callsTo('GET', '/search/saved')).toHaveLength(0);
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(resultRows(page)).toHaveCount(1);
    expect(api.callsTo('POST', '/search').map((call) => call.body)).toEqual([{ saved_id: 1 }]);
  });

  test('an expired search runs again once, silently', async ({ page }) => {
    const { api } = await openSearch(page, 'tolga', { search: { expireNext: 'stats' } });
    await runQuery(page, 'type:traffic');
    await expect(resultRows(page)).toHaveCount(1);
    await expect(page.getByRole('heading', { name: 'Zaman dağılımı' })).toBeVisible();
    const runs = api.callsTo('POST', '/search').map((call) => call.body);
    expect(runs).toHaveLength(2);
    expect(runs[1]).toEqual(runs[0]);
    expect(api.callsTo('POST', '/search/stats').map((call) => call.body)).toEqual([
      { search_id: 'ornek-arama-01' },
      { search_id: 'ornek-arama-02' },
    ]);
    await expect(page.getByText('Bu aramanın süresi doldu', { exact: false })).toHaveCount(0);
  });

  test('works on a site whose search.page_size_max is below 100', async ({ page }) => {
    // The mock refuses a `limit` above the setting, as the API does; Arama asks for none.
    const { api } = await openSearch(page, 'tolga', { search: { pageSizeMax: 50 } });
    await runQuery(page, 'srcip:172.20.41.0/24');
    await expect(page.getByText('50 kayıt gösteriliyor')).toBeVisible();
    await page.getByRole('button', { name: 'Daha fazla' }).click();
    await expect(page.getByText('100 kayıt gösteriliyor')).toBeVisible();
    expect(api.callsTo('POST', '/search/page').map((call) => call.body)).toEqual([
      { search_id: 'ornek-arama-01', cursor: 'rows:50' },
    ]);
    await expect(page.getByText('Gönderilen veriler geçersiz.')).toHaveCount(0);
  });

  test('offers only the ranges a site with a short search.max_days allows', async ({ page }) => {
    const { api } = await openSearch(page, 'tolga', {
      search: { maxDays: 7 },
      path: '/search?range=30d',
    });
    // "Son 30 gün" in the address reads as the default.
    const picker = page.getByRole('button', { name: 'Zaman aralığı: Son 24 saat' });
    await expect(picker).toBeVisible();
    await picker.click();
    await expect(page.getByRole('button', { name: 'Son 7 gün' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Son 30 gün' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Özel' }).click();
    await expect(
      page.getByText('Aralık en fazla 7 gün olabilir; ', { exact: false }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await runQuery(page, 'type:web');
    await expect(resultRows(page)).toHaveCount(1);
    expect(api.callsTo('POST', '/search').map((call) => call.body)).toEqual([
      { q: 'type:web', ...LAST_24H },
    ]);
  });

  test('asks once more when the server is busy, for the statistics and the drawer', async ({
    page,
  }) => {
    const { api, search } = await openSearch(page, 'tolga', {
      search: { refuseNext: { call: 'stats', code: 'search_busy' } },
    });
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    await expect(page.getByRole('heading', { name: 'Zaman dağılımı' })).toBeVisible();
    await expect(
      page.getByRole('region', { name: 'Dağılım' }).getByRole('region', { name: 'Tür' }),
    ).toBeVisible();
    expect(api.callsTo('POST', '/search/stats')).toHaveLength(2);
    expect(api.callsTo('POST', '/search')).toHaveLength(1);

    search.refuseNext = { call: 'event', code: 'search_busy' };
    await resultRows(page).first().click();
    const sheet = page.getByRole('dialog', { name: 'Kayıt ayrıntısı' });
    await expect(sheet.getByRole('definition').filter({ hasText: '203.0.113.10' })).toBeVisible();
    expect(api.callsTo('GET', '/search/event')).toHaveLength(2);
    await expect(
      page.getByText('Şu anda çok sayıda arama çalışıyor', { exact: false }),
    ).toHaveCount(0);
  });

  test('a scan whose continuation failed waits for "Devam et"', async ({ page }) => {
    const { api } = await openSearch(page, 'tolga', {
      search: { refuseNext: { call: 'page', code: 'search_timeout' } },
    });
    await runQuery(page, '"login failure"');
    const banner = page
      .getByRole('status')
      .filter({ hasText: 'Arama aralığın tamamını tarayamadı' });
    await expect(banner).toContainText('sonraki bölüm okunamadı');
    await expect(
      page.getByText('Arama 4 saniyede en kısa zaman dilimini (5 dakika) bile tarayamadı.', {
        exact: false,
      }),
    ).toBeVisible();
    await expect(page.getByText('Taranıyor…', { exact: false })).toHaveCount(0);
    expect(api.callsTo('POST', '/search/page')).toHaveLength(1);

    await banner.getByRole('button', { name: 'Devam et' }).click();
    // It goes on by itself again, to the end of the range.
    await expect(resultRows(page)).toHaveCount(2);
    await expect(banner).toBeHidden();
    expect(api.callsTo('POST', '/search/page')).toHaveLength(7);
    expect(api.callsTo('POST', '/search')).toHaveLength(1);
  });

  test('a facet value of several VLANs is shown, never added to the query', async ({ page }) => {
    const { api } = await openSearch(page);
    await runQuery(page, 'srcip:172.20.70.0/24');
    await expect(resultRows(page)).toHaveCount(2);
    const facets = page.getByRole('region', { name: 'Dağılım' });
    const vlans = facets.getByRole('region', { name: 'VLAN', exact: true });
    await expect(vlans.getByText('Birden çok VLAN', { exact: true })).toBeVisible();
    await expect(vlans.getByRole('button')).toHaveCount(0);
    const departments = facets.getByRole('region', { name: "Departman (VLAN'a göre)" });
    await expect(departments.getByText('Birden çok departman', { exact: true })).toBeVisible();
    await expect(departments.getByRole('button')).toHaveCount(0);
    await vlans.getByText('Birden çok VLAN', { exact: true }).click();
    await expect(queryBox(page)).toHaveValue('srcip:172.20.70.0/24');
    // The other facets still filter.
    await facets
      .getByRole('region', { name: 'Tür' })
      .getByRole('button', { name: /^Trafik/ })
      .click();
    await expect(queryBox(page)).toHaveValue('srcip:172.20.70.0/24 type:traffic');
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(2);
  });
});

test.describe('Arama — accessibility', () => {
  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`has no violations with results, help and drawers open (${theme})`, async ({ page }) => {
      await usePreferences(page, { theme });
      await openSearch(page);
      await expectNoA11yViolations(page);
      await runQuery(page, 'srcip:172.20.42.57');
      await expect(resultRows(page)).toHaveCount(4);
      await expectNoA11yViolations(page);

      await queryBox(page).fill('srcip:"172');
      await expect(page.getByTestId('query-problem')).toBeVisible();
      await expectNoA11yViolations(page);
      await queryBox(page).fill('kay');
      await expect(page.getByRole('listbox', { name: 'Öneriler' })).toBeVisible();
      await expectNoA11yViolations(page);
      await queryBox(page).press('Escape');

      await page.getByRole('button', { name: 'Tablo olarak göster' }).click();
      await expectNoA11yViolations(page);

      await page.getByRole('button', { name: 'Sorgu söz dizimi yardımı (?)' }).click();
      await expect(page.getByRole('dialog', { name: 'Sorgu söz dizimi' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await resultRows(page).first().click();
      await expect(page.getByRole('dialog', { name: 'Kayıt ayrıntısı' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page
        .getByRole('dialog', { name: 'Kayıt ayrıntısı' })
        .getByRole('button', { name: 'Ham satır' })
        .click();
      await expect(page.getByRole('dialog', { name: 'Ham kayıt satırı' })).toBeVisible();
      await expectNoA11yViolations(page);
      // One sheet at a time: the raw line, then the record drawer under it.
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog', { name: 'Ham kayıt satırı' })).toBeHidden();
      await expect(page.getByRole('dialog', { name: 'Kayıt ayrıntısı' })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog', { name: 'Kayıt ayrıntısı' })).toBeHidden();

      await page.getByRole('button', { name: 'Dışa aktar' }).click();
      await expect(
        page.getByRole('dialog', { name: 'Arama sonuçlarını dışa aktar' }),
      ).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.getByRole('button', { name: 'Zaman aralığı: Son 24 saat' }).click();
      await page.getByRole('button', { name: 'Özel' }).click();
      await expect(page.getByLabel('Başlangıç')).toBeVisible();
      await expectNoA11yViolations(page);
    });
  }
});

test.describe('Arama on the captured answers (samples/search.json)', () => {
  // The capture searched its sealed day 2026-10-09; the address sets that range.
  const DAY = SEARCH.search.typed.range;
  const path = `/search?from=${encodeURIComponent(DAY.from)}&to=${encodeURIComponent(DAY.to)}`;
  const captured: SearchOptions = { captured: true };

  test('the administrator: the captured rows, a record and its raw line', async ({ page }) => {
    const { api } = await openSearch(page, 'tolga', { search: captured, path });
    await runQuery(page, 'srcip:172.20.42.57');
    const typed = SEARCH.search.typed;
    await expect(resultRows(page)).toHaveCount(typed.items.length);
    const [body] = api
      .callsTo('POST', '/search')
      .map((call) => call.body as Record<string, string>);
    expect(Object.keys(body ?? {}).sort()).toEqual(['from', 'q', 'to']);
    expect(body?.q).toBe('srcip:172.20.42.57');
    expect(Date.parse(body?.from ?? '')).toBe(Date.parse(DAY.from));
    expect(Date.parse(body?.to ?? '')).toBe(Date.parse(DAY.to));
    await expect(
      results(page).getByText('172.20.42.57 → static.portal.example · 203.0.113.40'),
    ).toBeVisible();
    await expect(
      page.getByText('09.10.2026 öncesine ait kayıtlar veritabanında yok', { exact: false }),
    ).toBeVisible();

    // The firewall's session at 13:30 site time: the captured record (`search.event`).
    const event = SEARCH.event.traffic;
    await resultRows(page).nth(1).click();
    const sheet = page.getByRole('dialog', { name: 'Kayıt ayrıntısı' });
    await expect(sheet.getByRole('definition').filter({ hasText: '100.64.12.9' })).toBeVisible();
    const asked = api.callsTo('GET', '/search/event')[0];
    expect(asked?.query.get('table')).toBe(event.table);
    expect(asked?.query.get('id')).toBe(String(event.fields.id));
    expect(Date.parse(asked?.query.get('time') ?? '')).toBe(Date.parse(String(event.fields.time)));
    await sheet.getByRole('button', { name: 'Ham satır' }).click();
    const line = RAW.lines[`${event.raw.source}|${event.raw.day}|${event.raw.line}`];
    if (!line) throw new Error('raw sample missing');
    await expect(
      page.getByRole('dialog', { name: 'Ham kayıt satırı' }).getByText(line.sha256),
    ).toBeVisible();
  });

  test('the auditor: the captured run and its statistics, nothing to export', async ({ page }) => {
    const { api } = await openSearch(page, 'ayse.yilmaz', { search: captured, path });
    await runQuery(page, 'srcip:172.20.42.57 type:web');
    const auditor = SEARCH.search.auditor;
    await expect(resultRows(page)).toHaveCount(auditor.items.length);
    await expect(results(page).getByText('static.portal.example')).toBeVisible();
    expect(api.callsTo('POST', '/search/stats').map((call) => call.body)).toEqual([
      { search_id: auditor.search_id },
    ]);
    const facets = page.getByRole('region', { name: 'Dağılım' });
    await expect(facets.getByText('VLAN 42 (Planlama)')).toBeVisible();
    await expect(facets.getByText('Web filtresi')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dışa aktar' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Kanıt paketine aktar' })).toHaveCount(0);
  });

  test('"Daha fazla" sends the captured cursor and shows the next captured page', async ({
    page,
  }) => {
    const { api } = await openSearch(page, 'tolga', { search: captured, path });
    await page.getByRole('button', { name: 'Ara', exact: true }).click();
    const first = SEARCH.search.first_page;
    await expect(resultRows(page)).toHaveCount(first.items.length);
    await page.getByRole('button', { name: 'Daha fazla' }).click();
    await expect(resultRows(page)).toHaveCount(
      first.items.length + SEARCH.search.next_page.items.length,
    );
    expect(api.callsTo('POST', '/search/page').map((call) => call.body)).toEqual([
      { search_id: first.search_id, cursor: first.next_cursor },
    ]);
  });

  test('a refusal reads as the API words it', async ({ page }) => {
    await openSearch(page, 'tolga', { search: captured, path });
    await runQuery(page, 'srcip:172.20.42.999');
    const refused = SEARCH.errors.search_value_invalid;
    if (!refused) throw new Error('sample missing');
    await expect(page.getByText(refused.body.error.message_tr)).toBeVisible();
  });
});

/**
 * Every row shows the device's time and the receive time side by side (contract D5: a wrong
 * device clock shows as the difference), and the table does not scroll sideways at a normal
 * desktop width.
 */
for (const width of [1280, 1440, 1920]) {
  test(`the receive time is visible in every row at ${width} px`, async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
    await page.setViewportSize({ width, height: 1000 });
    await openSearch(page);
    await runQuery(page, 'srcip:172.20.42.57');
    await expect(resultRows(page)).toHaveCount(4);
    await expect(
      results(page).getByRole('columnheader', { name: 'Zaman / alınma zamanı' }),
    ).toBeVisible();
    const times = (await resultRows(page).first().innerText()).match(
      /\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}/g,
    );
    expect(times).toHaveLength(2); // the device's time and the receive time
    const overflow = await results(page).evaluate((element) => {
      const box = element.parentElement;
      return box ? box.scrollWidth - box.clientWidth : -1;
    });
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
