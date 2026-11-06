import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { mockSearch } from './mock-search';
import { mockW4 } from './mock-w4';
import { SAMPLE_TODAY } from './samples';

/**
 * Getting around: the menu's groups, "Sayfaya git" (Ctrl+K), the alerts bell and "/" to the
 * global search box, with WCAG 2.1 AA scans in light and dark.
 */
test.describe('getting around', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`the menu's groups, expanded and collapsed — ${theme}`, async ({ page }) => {
      await usePreferences(page, { theme, collapsed: false });
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/');
      const nav = page.getByRole('navigation', { name: 'Ana menü' });
      await expect(nav.getByRole('list')).toHaveCount(6); // five groups and the UI kit
      await expect(nav.getByRole('list', { name: 'Kanıt' }).getByRole('link')).toHaveText([
        'Mühürleme',
        'Kanıt Paketleri',
        'Cihazlar ve Kişiler',
      ]);
      await expect(nav.getByRole('list', { name: 'Yönetim' }).getByRole('link')).toHaveText([
        'Ayarlar',
        'Sistem',
      ]);
      await expectNoA11yViolations(page);
      await page.getByRole('button', { name: 'Menüyü daralt' }).click();
      await expect(nav.getByRole('list', { name: 'İnceleme' }).getByRole('link')).toHaveCount(4);
      await expectNoA11yViolations(page);
    });

    test(`"Sayfaya git" finds a page and opens it — ${theme}`, async ({ page }) => {
      await usePreferences(page, { theme });
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
      await page.keyboard.press('Control+k');
      const palette = page.getByRole('dialog', { name: 'Sayfaya git' });
      await expect(palette).toBeVisible();
      await expect(palette.getByRole('option', { name: 'Zaman damgası' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.type('kanit pak');
      await expect(palette.getByRole('option')).toHaveText([
        'Kanıt Paketleri',
        'Kayıtlarda ara: “kanit pak”',
      ]);
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/\/evidence$/);
      await expect(page.getByRole('heading', { level: 1, name: 'Kanıt Paketleri' })).toBeVisible();
      await expect(palette).toBeHidden();
      await expect(page).toHaveTitle('Kanıt Paketleri · ARCILOGGER');
    });
  }

  test('"Sayfaya git" searches the logs for what was typed', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockSearch(page, api);
    await page.goto('/');
    await page.getByRole('button', { name: /Sayfaya git/ }).click();
    const palette = page.getByRole('dialog', { name: 'Sayfaya git' });
    await page.keyboard.type('172.20.41.105');
    await palette.getByRole('option', { name: 'Kayıtlarda ara: “172.20.41.105”' }).click();
    await expect(page).toHaveURL(/\/search/);
    await expect(page.getByRole('combobox', { name: 'Sorgu' })).toHaveValue('ip:172.20.41.105');
  });

  test('the bell counts the open alerts and opens Güvenlik', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/');
    const bell = page.getByRole('link', { name: /^Güvenlik uyarıları: \d+ açık uyarı$/ });
    await expect(bell).toBeVisible();
    await bell.click();
    await expect(page.getByRole('heading', { level: 1, name: 'Güvenlik' })).toBeVisible();
  });

  test('"/" moves to the global search box, but not while typing elsewhere', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    await page.keyboard.press('/');
    const box = page.getByRole('searchbox', { name: 'Genel arama' });
    await expect(box).toBeFocused();
    await page.keyboard.type('a/b');
    await expect(box).toHaveValue('a/b');
  });
});
