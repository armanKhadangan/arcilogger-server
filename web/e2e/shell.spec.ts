import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { mockSearch } from './mock-search';
import { SAMPLE_TODAY } from './samples';

const PAGES: [string, string][] = [
  ['Panel', 'Panel'],
  ['Kaynaklar', 'Kaynaklar'],
  ['Canlı Akış', 'Canlı Akış'],
  ['Arama', 'Arama'],
  ['5651 Sorgu', '5651 Sorgu'],
  ['Kullanım', 'Kullanım'],
  ['Güvenlik', 'Güvenlik'],
  ['Mühürleme', 'Mühürleme'],
  ['Kanıt Paketleri', 'Kanıt Paketleri'],
  ['Cihazlar ve Kişiler', 'Cihazlar ve Kişiler'],
  ['Raporlar', 'Raporlar'],
  ['Uyum Durumu', 'Uyum Durumu'],
  ['Denetim Kaydı', 'Denetim Kaydı'],
  ['Ayarlar', 'Ayarlar'],
  ['Sistem', 'Sistem'],
];

test.describe('application shell', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
  });

  test('every menu entry opens its page', async ({ page }) => {
    await usePreferences(page, { collapsed: false });
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    for (const [link, heading] of PAGES) {
      await nav.getByRole('link', { name: link, exact: true }).click();
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    }
  });

  test('loads nothing from outside the server (closed network)', async ({ page, baseURL }) => {
    const external: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (!url.startsWith(baseURL ?? '') && !url.startsWith('data:') && !url.startsWith('blob:')) {
        external.push(url);
      }
    });
    await page.goto('/ui-kit');
    await expect(page.getByText('10.000 satır')).toBeVisible();
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(external).toEqual([]);
  });

  test('theme choice from the user menu survives a reload', async ({ page }) => {
    // No init script here: it would re-apply its preferences on reload.
    await page.goto('/');
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitemradio', { name: 'Koyu' }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.reload();
    await expect(page.locator('html')).toHaveClass(/dark/);
  });

  test('language can be switched to English', async ({ page }) => {
    await usePreferences(page, { collapsed: false });
    await page.goto('/sealing');
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitemradio', { name: 'English' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Sealing' })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  });

  test('collapsed sidebar keeps accessible names', async ({ page }) => {
    await usePreferences(page, { collapsed: true });
    await page.goto('/');
    // The menu's link (the Panel's cards link to the page as "Mühürleme sayfasını aç").
    await expect(
      page.getByRole('navigation', { name: 'Ana menü' }).getByRole('link', { name: 'Mühürleme' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Menüyü genişlet' }).click();
    await expect(page.getByRole('button', { name: 'Menüyü daralt' })).toBeVisible();
  });

  test('collapsed sidebar highlights the current page; its bar stays inside the link', async ({
    page,
  }) => {
    await usePreferences(page, { collapsed: true });
    await page.goto('/sealing');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    const active = nav.getByRole('link', { name: 'Mühürleme' });
    await expect(active).toHaveAttribute('aria-current', 'page');
    await expect(active).toHaveCSS('position', 'relative');
    await expect(active).toHaveCSS('background-color', 'rgb(19, 78, 74)'); // #134E4A
    await expect(active).toHaveCSS('justify-content', 'center');
    const link = await active.boundingBox();
    const bar = await active.getByTestId('active-bar').boundingBox();
    if (!link || !bar) throw new Error('no layout');
    expect(bar.height).toBeLessThanOrEqual(link.height);
    expect(bar.y).toBeGreaterThanOrEqual(link.y);
    const other = nav.getByRole('link', { name: 'Panel' });
    await expect(other).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(other).toHaveCSS('font-weight', '400');
  });

  test('the sidebar shows the organisation name the server is configured with', async ({
    page,
  }) => {
    await usePreferences(page, { collapsed: false });
    await page.goto('/');
    const sidebar = page.locator('aside');
    const name = sidebar.getByTestId('site-name');
    await expect(name).toHaveText('Örnek A.Ş.');
    // Under the product name.
    const product = await sidebar.getByText('ARCILOGGER', { exact: true }).boundingBox();
    const caption = await name.boundingBox();
    if (!product || !caption) throw new Error('no layout');
    expect(caption.y).toBeGreaterThanOrEqual(product.y + product.height - 1);
  });

  test('the global search box is on every page; the time range only on Arama', async ({ page }) => {
    // Admin (has search.read): the box leads to Arama, the only page with a time range.
    for (const path of ['/', '/sources', '/live', '/audit', '/lookup', '/evidence', '/sealing']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('header').getByRole('search')).toHaveCount(1);
      await expect(
        page.locator('header').getByRole('button', { name: /^Zaman aralığı/ }),
      ).toHaveCount(0);
    }
    await page.goto('/search');
    await expect(page.getByRole('heading', { level: 1, name: 'Arama' })).toBeVisible();
    await expect(
      page.locator('header').getByRole('button', { name: 'Zaman aralığı: Son 24 saat' }),
    ).toBeVisible();
    await page.goto('/search?range=7d');
    await expect(
      page.locator('header').getByRole('button', { name: 'Zaman aralığı: Son 7 gün' }),
    ).toBeVisible();
  });

  test('placeholder page has no accessibility violations (dark)', async ({ page }) => {
    await usePreferences(page, { theme: 'dark' });
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { level: 1, name: 'Uyum Durumu' })).toBeVisible();
    await expectNoA11yViolations(page);
  });
});

test.describe('global search box', () => {
  for (const [username, shown] of [
    ['tolga', true],
    ['ayse.yilmaz', true],
    ['mehmet.kaya', false],
    ['zeynep', false],
  ] as const) {
    test(`only with search.read — ${username}`, async ({ page }) => {
      await mockApi(page, { signedInAs: username });
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('header').getByRole('search')).toHaveCount(shown ? 1 : 0);
    });
  }

  test('hands its term to Arama in memory: not in the address, history or storage', async ({
    page,
  }) => {
    await useSampleClock(page, SAMPLE_TODAY);
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockSearch(page, api);
    await page.goto('/audit');
    const box = page.locator('header').getByRole('searchbox', { name: 'Genel arama' });
    await box.fill('172.20.41.23');
    await box.press('Enter');
    await expect(page.getByRole('heading', { level: 1, name: 'Arama' })).toBeVisible();
    // An IP arrives as `ip:` and runs at once, in a request body.
    await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue('ip:172.20.41.23');
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(1);
    expect(api.callsTo('POST', '/search')[0]?.body).toMatchObject({ q: 'ip:172.20.41.23' });
    const url = new URL(page.url());
    expect(url.pathname + url.search + url.hash).toBe('/search');
    const kept = await page.evaluate(() =>
      JSON.stringify([
        window.history.state,
        Object.entries(localStorage),
        Object.entries(sessionStorage),
      ]),
    );
    expect(kept).not.toContain('172.20.41.23');
  });

  test('turns a MAC address into mac: and a host name into domain:, and keeps the range', async ({
    page,
  }) => {
    await useSampleClock(page, SAMPLE_TODAY);
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockSearch(page, api);
    await page.goto('/search?range=1h');
    const box = page.locator('header').getByRole('searchbox', { name: 'Genel arama' });
    await box.fill('00-00-5E-00-53-21');
    await box.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue(
      'mac:00:00:5e:00:53:21',
    );
    await box.fill('www.example.com');
    await box.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue(
      'domain:www.example.com',
    );
    await box.fill('PLN-PC14');
    await box.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue('"PLN-PC14"');
    expect(new URL(page.url()).search).toBe('?range=1h');
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(3);
  });
});

test.describe('organisation name in the sidebar', () => {
  test('a server without a name shows none and no placeholder', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', siteName: '' });
    await usePreferences(page, { collapsed: false });
    await page.goto('/');
    const sidebar = page.locator('aside');
    await expect(sidebar.getByText('ARCILOGGER', { exact: true })).toBeVisible();
    await expect(sidebar.getByTestId('site-name')).toHaveCount(0);
    await expect(sidebar).not.toContainText(/Örnek|kuruluş/i);
  });

  test('a long name is cut off inside the rail; the whole name is its tooltip', async ({
    page,
  }) => {
    const long = 'Deneme Lojistik Depolama Sanayi ve Ticaret Anonim Şirketi';
    await mockApi(page, { signedInAs: 'tolga', siteName: long });
    await usePreferences(page, { collapsed: false });
    await page.goto('/');
    const sidebar = page.locator('aside');
    const name = sidebar.getByTestId('site-name');
    await expect(name).toHaveText(long);
    await expect(name).toHaveAttribute('title', long);
    await expect(name).toHaveCSS('text-overflow', 'ellipsis');
    const caption = await name.boundingBox();
    const rail = await sidebar.boundingBox();
    if (!caption || !rail) throw new Error('no layout');
    expect(caption.x + caption.width).toBeLessThanOrEqual(rail.x + rail.width);
    expect(caption.height).toBeLessThanOrEqual(20); // one line
  });
});
