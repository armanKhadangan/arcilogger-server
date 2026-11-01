import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { mockW4 } from './mock-w4';
import { ADMIN, SAMPLE_TODAY } from './samples';

/**
 * WCAG 2.1 AA scans of the Wave 4 pages (contract §3 W4-F), light and dark, on the captured
 * answers (`mock-w4.ts`): Uyum Durumu as the administrator (the ten cards, then the confirmation
 * dialog open) and as the auditor (cards without the confirmation and the wizard link), Sistem
 * with every block loaded, the Panel with the disk card, and the first-run wizard's summary as the
 * administrator. The wizard's own spec scans every step of a fresh installation; this one scans
 * the summary of the captured installation, with its configuration problem and the link to Uyum
 * Durumu. The error states are scanned in `compliance.spec.ts` and `system.spec.ts`.
 */
for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`accessibility of the Wave 4 pages — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      test.setTimeout(90_000);
      await useSampleClock(page, SAMPLE_TODAY);
      await usePreferences(page, { theme });
    });

    test('Uyum Durumu: the administrator’s cards and the confirmation dialog', async ({ page }) => {
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/compliance');
      await expect(page.getByRole('heading', { level: 1, name: 'Uyum Durumu' })).toBeVisible();
      await expect(page.getByRole('list', { name: 'Denetimler' }).getByRole('group')).toHaveCount(
        10,
      );
      await expect(page.getByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }).click();
      await expect(page.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' })).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Uyum Durumu: the auditor’s view', async ({ page }) => {
      await mockW4(page, await mockApi(page, { signedInAs: 'ayse.yilmaz' }));
      await page.goto('/compliance');
      await expect(page.getByRole('list', { name: 'Denetimler' }).getByRole('group')).toHaveCount(
        10,
      );
      await expect(page.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toHaveCount(
        0,
      );
      await expectNoA11yViolations(page);
    });

    test('Sistem: every block loaded', async ({ page }) => {
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/system');
      await expect(page.getByRole('heading', { level: 1, name: 'Sistem' })).toBeVisible();
      await expect(
        page.getByTestId('system-blocks').getByRole('heading', { level: 2 }),
      ).toHaveCount(10);
      await expect(page.getByTestId('system-clock-samples')).toBeVisible();
      await expect(page.getByTestId('system-maintenance')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Panel: the administrator’s cards with the disk card', async ({ page }) => {
      await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
      await expect(page.getByTestId('card-disk').getByRole('meter')).toHaveCount(2);
      await expect(page.getByTestId('card-usage')).toContainText('Aktif kullanıcı');
      await expectNoA11yViolations(page);
    });

    test('İlk kurulum sihirbazı: the summary of the captured installation', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/settings/setup?step=summary');
      await expect(
        page.getByRole('heading', { level: 1, name: 'İlk kurulum sihirbazı' }),
      ).toBeVisible();
      const card = page.getByTestId('wizard-step-summary');
      await expect(card.getByRole('heading', { level: 2 })).toHaveText('8. Özet');
      await expect(card.getByTestId('wizard-summary-steps').getByRole('listitem')).toHaveCount(7);
      await expect(card.getByTestId('config-problems').getByRole('listitem')).toHaveCount(
        ADMIN.config.problems.length,
      );
      await expect(card.getByRole('link', { name: 'Uyum Durumu sayfasını aç' })).toHaveAttribute(
        'href',
        '/compliance',
      );
      await expectNoA11yViolations(page);
    });
  });
}
