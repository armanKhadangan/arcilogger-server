import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { SAMPLE_TODAY } from './samples';

/*
 * Canlı Durum (contract W5 §6, M1–M3, N6, P3): the internet lines over SNMP on top, "Şu an"
 * below: who sends and receives data in the last 1, 5 or 15 minutes. The lines are W5-N's
 * captured `activity-wan.json`; "Şu an" is built from the captured people until W5-M's answers
 * are captured (`mock-activity.ts`).
 */

function lastQuery(api: MockApi, path: string): URLSearchParams | undefined {
  return api.callsTo('GET', path).at(-1)?.query;
}

async function openActivity(page: Page) {
  await page.goto('/');
  await page
    .getByRole('navigation', { name: 'Ana menü' })
    .getByRole('link', { name: 'Canlı Durum' })
    .click();
  await expect(page.getByRole('heading', { level: 1, name: 'Canlı Durum' })).toBeVisible();
}

test.describe('Canlı Durum', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('the lines, the window and the people sending and receiving now', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await openActivity(page);

    // The captured lines: `wan1` with its rate and share of a known capacity, `wan2` not
    // answering, `ether1` with SNMP off.
    const lines = page.getByRole('region', { name: 'İnternet hattı' });
    await expect(lines.getByRole('heading', { name: 'wan1' })).toBeVisible();
    await expect(lines.getByRole('heading', { name: 'wan2' })).toBeVisible();
    await expect(lines.getByRole('heading', { name: 'ether1' })).toBeVisible();
    await expect(lines).toContainText('kapasitenin %');
    expect(lastQuery(api, '/activity/wan')?.get('range')).toBe('60m');

    // "Şu an": five minutes by default, the busiest first.
    const now = page.getByRole('region', { name: 'Şu an' });
    const window = now.getByRole('group', { name: 'Zaman aralığı' });
    await expect(window.getByRole('button', { name: 'Son 5 dk' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(lastQuery(api, '/activity/devices')?.get('window')).toBe('5');
    const table = now.getByRole('table', { name: 'Şu an veri gönderip alanlar' });
    await expect(table.getByRole('row')).not.toHaveCount(1);
    await expect(now.getByTestId('activity-note')).toContainText(
      'Uzun bağlantılar FortiGate ara kayıtları geldikçe görünür.',
    );

    await window.getByRole('button', { name: 'Son 1 dk' }).click();
    await expect(page).toHaveURL(/\/activity\?window=1$/);
    await expect.poll(() => lastQuery(api, '/activity/devices')?.get('window')).toBe('1');
    await page.reload();
    await expect(
      page.getByRole('group', { name: 'Zaman aralığı' }).getByRole('button', { name: 'Son 1 dk' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  test('a line that does not answer, one with SNMP off, and no line at all', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/activity');
    const lines = page.getByRole('region', { name: 'İnternet hattı' });
    await expect(lines.getByText(/^Cihaz yanıt vermiyor/)).toBeVisible();
    await expect(lines.getByText(/^SNMP kapalı/)).toBeVisible();
    await expect(lines.getByText('Henüz ölçüm yok.').first()).toBeVisible();

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await mockApi(page, { signedInAs: 'tolga', activity: { noLines: true } });
    await page.goto('/activity');
    await expect(page.getByText('İnternet hattı ölçülmüyor')).toBeVisible();
    await expect(page.getByRole('link', { name: "Kaynaklar'da SNMP'yi açın" })).toHaveAttribute(
      'href',
      '/settings/sources',
    );
  });

  test('each part only for those who may see it', async ({ page }) => {
    // İK (`usage.person`, no `dashboard.view`): "Şu an" only, no line speed.
    const api = await mockApi(page, { signedInAs: 'mehmet.kaya' });
    await page.goto('/activity');
    await expect(page.getByRole('region', { name: 'Şu an' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'İnternet hattı' })).toHaveCount(0);
    expect(api.callsTo('GET', '/activity/wan')).toHaveLength(0);

    // The usage module off: the lines stay (base product), "Şu an" says why it is missing.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    const locked = await mockApi(page, {
      signedInAs: 'tolga',
      license: { state: 'trial_expired' },
    });
    await page.goto('/activity');
    await expect(page.getByRole('region', { name: 'İnternet hattı' })).toBeVisible();
    await expect(page.getByText('Kullanım analizi modülü kapalı')).toBeVisible();
    expect(locked.callsTo('GET', '/activity/devices')).toHaveLength(0);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Canlı Durum accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/activity');
    await expect(
      page.getByRole('table', { name: 'Şu an veri gönderip alanlar' }).getByRole('row').nth(1),
    ).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
