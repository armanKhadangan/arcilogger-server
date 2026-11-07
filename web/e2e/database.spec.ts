import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { mockW4 } from './mock-w4';
import { SAMPLE_TODAY, SYSTEM_DATABASE } from './samples';

/*
 * Veritabanı (`/database`, Wave 5 addendum C, C7) on the answer captured from the real API
 * (`samples/system-database.json` through `mock-w4.ts`): the database in use, its size, daily
 * growth and the days it keeps, the 30 daily samples, the data drive with its forecast, the last
 * backup and the largest tables; the menu entry under Yönetim; administrators only.
 */

test.describe('Veritabanı', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`shows what the database is and how it grows — ${theme}`, async ({ page }) => {
      await usePreferences(page, { theme });
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/');
      const nav = page.getByRole('navigation', { name: 'Ana menü' });
      await nav.getByRole('link', { name: 'Veritabanı' }).click();
      await expect(page).toHaveURL(/\/database$/);
      await expect(page.getByRole('heading', { level: 1, name: 'Veritabanı' })).toBeVisible();
      await expect(page).toHaveTitle('Veritabanı · ARCILOGGER');

      const engine = page.getByTestId('database-engine');
      await expect(engine).toContainText('PostgreSQL 16.4');
      await expect(engine).toContainText('arcilogger');
      await expect(engine).toContainText('127.0.0.1:5432');
      await expect(engine).toContainText('7 / en çok 100');

      const size = page.getByTestId('database-size');
      await expect(size).toContainText('296,6 GB');
      await expect(size).toContainText('günde yaklaşık 175 MB');
      await expect(size).toContainText('son 30 gün');

      await expect(page.getByRole('heading', { name: 'Son 30 günün boyutu' })).toBeVisible();
      await expect(page.getByTestId('database-drive')).toContainText('D:');
      const backup = page.getByTestId('database-backup');
      await expect(backup).toContainText('Başarılı');
      await expect(backup).toContainText(SYSTEM_DATABASE.database.last_backup?.file ?? '');
      await expect(backup.getByRole('link', { name: 'Yedekleme ayarları' })).toHaveAttribute(
        'href',
        '/settings/backup',
      );
      const tables = page.getByTestId('database-table-sizes');
      await expect(tables.getByRole('rowheader')).toHaveCount(
        SYSTEM_DATABASE.database.tables.length,
      );
      await expect(tables.getByRole('rowheader').first()).toHaveText('messages');
      await expectNoA11yViolations(page);
    });
  }

  test('without daily samples yet, no backup and a failed reading', async ({ page }) => {
    const database = structuredClone(SYSTEM_DATABASE.database);
    database.history = [];
    database.growth = { state: 'insufficient', samples: 0, bytes_per_day: null };
    database.last_backup = null;
    database.connections = null;
    await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }), { database });
    await page.goto('/database');
    await expect(page.getByTestId('database-size')).toContainText(
      'Tahmin için en az 7 günlük ölçüm gerekir (0 gün var).',
    );
    await expect(page.getByTestId('database-history')).toContainText(
      'Henüz günlük ölçüm yok; ilk ölçüm bu gece alınır.',
    );
    await expect(page.getByTestId('database-backup')).toContainText('Henüz yedek alınmadı.');
    await expect(page.getByTestId('database-engine')).toContainText('—');
  });

  test('is the administrators’ only: no menu entry and no page for an auditor', async ({
    page,
  }) => {
    await mockW4(page, await mockApi(page, { signedInAs: 'ayse.yilmaz' }));
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Ana menü' });
    await expect(nav.getByRole('link', { name: 'Denetim Kaydı' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Veritabanı' })).toHaveCount(0);
  });
});
