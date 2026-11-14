import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Webhook'lar (Wave 6 contract §2.4.4, W6-F): read-only (D4), each webhook's format,
 * host, minimum severity, rules, last delivery and 7-day counts, its deliveries, and "Test gönder"
 * followed as an administration request. The API is `mock-webhooks.ts`, from the contract's JSON
 * until W6-H's API is captured.
 */

test.describe("Ayarlar → Webhook'lar", () => {
  test('the section and the webhooks configured on the server', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByTestId('settings-webhooks').getByRole('link').click();
    await expect(page).toHaveURL(/\/settings\/webhooks$/);
    await expect(page.getByTestId('webhooks-server-only')).toContainText('[[webhooks]]');
    const rows = page.getByTestId('webhook-row');
    await expect(rows).toHaveCount(3);
    const siem = rows.filter({ has: page.locator('th', { hasText: 'siem' }) });
    await expect(siem).toContainText('Genel (imzalı JSON)');
    await expect(siem).toContainText('siem.ornek.example');
    await expect(siem).toContainText('Bütün kurallar');
    await expect(siem.getByTestId('webhook-counts')).toHaveText('12 gönderildi · 1 başarısız');
    const teams = page.locator('[data-testid="webhook-row"][data-name="teams-guvenlik"]');
    await expect(teams).toContainText('Sahada doğrula');
    await expect(teams).toContainText('Kurallar: R06, R07');
    await expect(teams).toContainText('Henüz teslim yok');
    const slack = page.locator('[data-testid="webhook-row"][data-name="slack-bt"]');
    await expect(slack).toContainText('Kapalı');
    await expect(slack.getByRole('button', { name: 'Test gönder: slack-bt' })).toBeDisabled();
    // Nothing on the page configures a webhook (D4).
    await expect(page.getByRole('textbox')).toHaveCount(0);
  });

  test("a webhook's deliveries, each linked to its alert", async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/webhooks');
    await page.getByRole('button', { name: 'Teslimler: siem' }).click();
    const sheet = page.getByTestId('webhook-deliveries');
    await expect(sheet.getByTestId('webhook-delivery')).toHaveCount(3);
    await expect(sheet).toContainText('503 Service Unavailable: upstream is restarting');
    await expect(sheet).toContainText('Sonraki deneme:');
    await expect(sheet.getByRole('link', { name: '#9001 · R01' })).toHaveAttribute(
      'href',
      '/security?alert=9001',
    );
  });

  test('"Test gönder": the request until the target answered', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/webhooks');
    await page.getByRole('button', { name: 'Test gönder: siem' }).click();
    const trial = page.getByTestId('webhook-test');
    await expect(trial).toBeFocused();
    await expect(trial.getByTestId('webhook-test-result')).toHaveText(
      'Deneme bildirimi gönderildi; hedef HTTP 204 ile yanıt verdi.',
      { timeout: 15_000 },
    );
    expect(api.callsTo('POST', '/webhooks/siem/test')).toHaveLength(1);
  });

  test('a failed test says why', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', webhooks: { test: 'failed' } });
    await page.goto('/settings/webhooks');
    await page.getByRole('button', { name: 'Test gönder: siem' }).click();
    await expect(page.getByTestId('webhook-test-result')).toHaveText(
      'Deneme bildirimi gönderilemedi: 401 Unauthorized: signature rejected',
      { timeout: 15_000 },
    );
  });

  test('none configured: where to configure one', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', webhooks: { none: true } });
    await page.goto('/settings/webhooks');
    await expect(page.getByText('Tanımlı webhook yok.')).toBeVisible();
    await expect(page.getByTestId('webhook-row')).toHaveCount(0);
  });

  test('with alerts.read only: no "Test gönder"', async ({ page }) => {
    await mockApi(page, { signedInAs: 'ayse.yilmaz', grant: { auditor: ['alerts.read'] } });
    await page.goto('/settings/webhooks');
    await expect(page.getByTestId('webhook-row')).toHaveCount(3);
    await expect(page.getByRole('button', { name: /^Test gönder/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Teslimler: siem' })).toBeEnabled();
  });

  test('in English', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/webhooks');
    await expect(page.getByTestId('webhooks-server-only')).toContainText(
      'Webhooks are configured on the server only',
    );
    await expect(
      page.locator('[data-testid="webhook-row"][data-name="teams-guvenlik"]'),
    ).toContainText('Verify on site');
  });

  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`accessibility (${theme}): the list, a test and the deliveries`, async ({ page }) => {
      await usePreferences(page, { theme });
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/settings/webhooks');
      await page.getByRole('button', { name: 'Test gönder: siem' }).click();
      await expect(page.getByTestId('webhook-test-result')).toBeVisible({ timeout: 15_000 });
      await expectNoA11yViolations(page);
      await page.getByRole('button', { name: 'Teslimler: siem' }).click();
      await expect(page.getByTestId('webhook-delivery')).toHaveCount(3);
      await expectNoA11yViolations(page);
    });
  }
});
