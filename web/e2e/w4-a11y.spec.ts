import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { ADMIN, SAMPLE_TODAY } from './samples';

/**
 * WCAG 2.1 AA scans of the Wave 4 pages (contract §3 W4-F), light and dark, on the captured
 * answers: the first-run wizard's summary as the administrator. The wizard's own spec scans every
 * step of a fresh installation; this one scans the summary of the captured installation, with
 * its configuration problem and the link to Uyum Durumu.
 *
 * Uyum Durumu (administrator: cards, the confirmation dialog; auditor) and Sistem join this file
 * with their pages (W4-C part 2): see the marked places below.
 */
for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`accessibility of the Wave 4 pages — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      test.setTimeout(90_000);
      await useSampleClock(page, SAMPLE_TODAY);
      await usePreferences(page, { theme });
    });

    // Uyum Durumu (administrator, the confirmation dialog open; auditor): with W4-C part 2.

    // Sistem (administrator, every block loaded): with W4-C part 2.

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
