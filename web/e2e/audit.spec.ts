import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { AUDIT_VECTORS } from './generated';
import { mockApi } from './mock-api';

const ENTRIES = AUDIT_VECTORS.entries;

test.describe('Denetim Kaydı', () => {
  test.beforeEach(async ({ page }) => {
    // The golden entries were written on 12.10.2026; "Bugün" must mean that day.
    await page.clock.setFixedTime(new Date('2026-10-12T15:00:00+03:00'));
  });

  test('verifies the chain, loads pages on demand and filters on the server', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/audit');
    await expect(page.getByText('Zincir doğrulandı · 140 kayıt')).toBeVisible();
    await expect(page.getByText('100 satır')).toBeVisible();
    await page.getByRole('button', { name: 'Daha fazla yükle' }).click();
    await expect(page.getByText('140 satır')).toBeVisible();
    await expect(page.getByText('Kaydın başına ulaşıldı.')).toBeVisible();
    const pages = api.callsTo('GET', '/audit');
    expect(pages.map((call) => call.query.get('cursor'))).toEqual([null, '41']);

    // Only the page's own (empty) range: no top-bar picker suggesting a "today" filter.
    await expect(page.getByRole('button', { name: /^Tarih aralığı: / })).toHaveCount(1);
    await page.getByRole('button', { name: 'Tarih aralığı: Tarih aralığı seçin' }).click();
    await page.getByRole('button', { name: 'Bugün' }).click();
    await page.getByLabel('Kullanıcı', { exact: true }).fill('ayse.yilmaz');
    await page.getByLabel('İşlem (önek)').fill('auth.');
    await page.getByRole('button', { name: 'Filtrele' }).click();
    const expected = ENTRIES.filter(
      (entry) => entry.api.actor === 'ayse.yilmaz' && entry.api.action.startsWith('auth.'),
    ).length;
    await expect(page.getByText(`${expected} satır`)).toBeVisible();
    const query = api.callsTo('GET', '/audit').at(-1)?.query;
    expect(Object.fromEntries(query ?? [])).toEqual({
      from: '2026-10-12T00:00:00+03:00',
      to: '2026-10-13T00:00:00+03:00',
      actor: 'ayse.yilmaz',
      action: 'auth.',
      limit: '100',
    });
    const table = page.getByRole('table', { name: 'Denetim kaydı, en yeni kayıt önce' });
    await expect(table.getByRole('row').nth(1)).toContainText('Çıkış');

    // Back to the unfiltered list: both pages loaded before come from the cache.
    await page.getByRole('button', { name: 'Temizle' }).click();
    await expect(page.getByText('140 satır')).toBeVisible();
    await expect(page.getByLabel('Kullanıcı', { exact: true })).toHaveValue('');
  });

  test('the detail drawer shows the canonical entry and recomputes its hash', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/audit');
    await page.getByLabel('İşlem (önek)').fill('settings.');
    await page.getByRole('button', { name: 'Filtrele' }).click();
    await expect(page.getByText('1 satır')).toBeVisible();
    await page.getByRole('row', { name: /Ayar değiştirildi/ }).click();
    const drawer = page.getByRole('dialog', { name: 'Ayar değiştirildi' });
    await expect(drawer.getByText('Kayıt #8')).toBeVisible();
    await expect(
      drawer.getByText('Özet bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
    ).toBeVisible();
    const canonical = drawer.locator('pre').filter({ hasText: '"v":1' });
    await expect(canonical).toContainText('"time":"2026-10-12T05:15:59.055433+00:00"');
    await expect(canonical).toContainText('"😀":"smile","｡":"halfwidth"');
    await expect(drawer.getByText('Önceki kaydın özeti', { exact: true })).toBeVisible();
    await expect(drawer.getByText('Bu kaydın özeti', { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(page.getByRole('row', { name: /Ayar değiştirildi/ })).toBeFocused();
  });

  test('a reader who sees pseudonyms gets no hashes: the table and the drawer say so', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    // GET /audit for the auditor while usage.pseudonymize_anonymous_role is on.
    api.entries.splice(
      0,
      api.entries.length,
      ...api.entries.map((entry) => ({ ...entry, prev_hash: null, hash: null })),
    );
    await page.goto('/audit');
    await expect(page.getByText('Zincir doğrulandı · 140 kayıt')).toBeVisible();
    await page.getByLabel('İşlem (önek)').fill('settings.');
    await page.getByRole('button', { name: 'Filtrele' }).click();
    await expect(page.getByText('1 satır')).toBeVisible();
    const row = page.getByRole('row', { name: /Ayar değiştirildi/ });
    await expect(row.getByRole('cell').last()).toHaveText('—');
    await row.click();
    const drawer = page.getByRole('dialog', { name: 'Ayar değiştirildi' });
    await expect(
      drawer.getByText('Takma ad görenlere özetler gösterilmez: zincir sunucuda doğrulanır'),
    ).toBeVisible();
    await expect(drawer.getByText('Bu kaydın özeti', { exact: true })).toHaveCount(0);
    await expect(drawer.getByText('Önceki kaydın özeti', { exact: true })).toHaveCount(0);
  });

  test('the drawer opens from the keyboard and gives the focus back to its row', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/audit');
    await expect(page.getByText('100 satır')).toBeVisible();
    const table = page.getByRole('table', { name: 'Denetim kaydı, en yeni kayıt önce' });
    const row = table.getByRole('row').nth(2);
    await row.focus();
    await page.keyboard.press('Enter');
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(row).toBeFocused();
    // The close button too, and with the mouse.
    await row.click();
    await drawer.getByRole('button', { name: 'Kapat' }).click();
    await expect(drawer).toBeHidden();
    await expect(row).toBeFocused();
  });

  test('a broken chain is reported loudly and can be checked again', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', chainBroken: true });
    await page.goto('/audit');
    await expect(page.getByText('Zincir bozuk · ilk hatalı kayıt #37')).toBeVisible();
    await expect(page.getByText('Denetim kaydı zinciri doğrulanamadı')).toBeVisible();
    await expect(page.getByText('entry 37: hash does not match its content')).toBeVisible();
    api.chainBroken = false;
    await page.getByRole('button', { name: 'Yeniden doğrula' }).click();
    await expect(page.getByText('Zincir doğrulandı · 140 kayıt')).toBeVisible();
    const checks = api.callsTo('GET', '/audit/verify');
    expect(checks).toHaveLength(2);
    expect(checks.every((call) => !call.background)).toBe(true);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`audit page and drawer have no accessibility violations — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga', chainBroken: theme === 'dark' });
    await page.goto('/audit');
    await expect(page.getByText('100 satır')).toBeVisible();
    await expectNoA11yViolations(page);
    // A hovered and keyboard-focused row (bg-subtle): its secondary text must keep 4.5:1.
    const row = page.getByRole('table').getByRole('row').nth(1);
    await row.hover();
    await row.focus();
    await expectNoA11yViolations(page);
    await row.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByText(/eşleşiyor/)).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
