import { expect, test, type Page } from '@playwright/test';

import { useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { mockLive } from './mock-live';
import { mockSearch } from './mock-search';
import { mockSources } from './mock-sources';
import { RAW, SAMPLE_TODAY } from './samples';

/**
 * The paths that cross the Wave 2 pages (contract 4.3, integration step 4), each in one browser
 * session as a user walks it: the top bar into Arama and on to an evidence package, and an
 * unknown sender seen in Canlı Akış defined as a source in Ayarlar. Every hand-over is in memory:
 * no term or address reaches the address bar, the history or the browser's storage. Whether the
 * package verifies and the new folder streams needs the real stack (`e2e-live/smoke.spec.ts`).
 */

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

test.beforeEach(async ({ page }) => {
  await useSampleClock(page, SAMPLE_TODAY);
});

test('top bar → Arama → raw line → "Kanıt paketine aktar" → the wizard holds the lines', async ({
  page,
}) => {
  const api = await mockApi(page, { signedInAs: 'tolga' });
  await mockSearch(page, api);
  await page.goto('/');
  const box = page.locator('header').getByRole('searchbox', { name: 'Genel arama' });
  await box.fill('172.20.42.57');
  await box.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Arama' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue('ip:172.20.42.57');
  const rows = page
    .getByRole('table', { name: 'Arama sonuçları' })
    .locator('tbody tr:not([aria-hidden])');
  await expect(rows.first()).toBeVisible();
  const count = await rows.count();

  await rows.first().click();
  const sheet = page.getByRole('dialog', { name: 'Kayıt ayrıntısı' });
  await sheet.getByRole('button', { name: 'Ham satır' }).click();
  const line = RAW.lines['fortigate|2026-10-09|27'];
  if (!line) throw new Error('raw sample missing');
  const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
  await expect(raw.getByText(line.sha256)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(raw).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();

  await page.getByRole('button', { name: 'Kanıt paketine aktar' }).click();
  const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
  await expect(wizard).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/evidence');
  await expect(wizard.getByText(`Arama sonucundan ${count} satır aktarıldı`)).toBeVisible();
  await expect(wizard.getByText('Sorgu: ip:172.20.42.57')).toBeVisible();
  expect(api.callsTo('POST', '/search/refs')).toHaveLength(1);
  // The package itself is built only on the administrator's own request (re-authentication).
  expect(api.callsTo('POST', '/evidence')).toHaveLength(0);
  expect(await keptAnywhere(page, '172.20.42.57')).toBe(false);
});

test('an unknown sender seen in Canlı Akış is defined as a source from Kaynaklar', async ({
  page,
}) => {
  const api = await mockApi(page, { signedInAs: 'tolga' });
  await mockSearch(page, api);
  const live = await mockLive(page, api);
  await mockSources(page, api);
  await page.goto('/live');
  await page.getByRole('button', { name: 'Başlat', exact: true }).click();
  await expect.poll(() => live.connections()).toBe(1);
  live.send([{ source: 'unknown-172.20.10.40', sender: '172.20.10.40' }]);
  const lines = page.getByRole('list', { name: 'Canlı satırlar' }).getByRole('listitem');
  await expect(lines).toHaveCount(1);
  await expect(lines.first()).toContainText('unknown-172.20.10.40');

  // Leaving Canlı Akış closes its stream (a background request: not the user's doing).
  await page
    .getByRole('navigation', { name: 'Ana menü' })
    .getByRole('link', { name: 'Kaynaklar' })
    .click();
  await expect.poll(() => api.callsTo('DELETE', '/live/streams/akis-1').length).toBe(1);
  await page
    .getByRole('button', { name: 'unknown-172.20.10.40 göndericisini kaynak olarak tanımla' })
    .click();
  await expect(page).toHaveURL(/\/settings\/sources$/);
  const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
  await expect(dialog.getByLabel('Adres')).toHaveValue('172.20.10.40');
  await expect(dialog.getByTestId('source-prefilled')).toContainText('172.20.10.40');
  // Nothing is added before the administrator reviews it.
  expect(api.callsTo('POST', '/admin/config/sources')).toHaveLength(0);
  expect(await keptAnywhere(page, '172.20.10.40')).toBe(false);
});
