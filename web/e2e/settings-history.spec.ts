import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Değişiklik geçmişi (contract 2.17): the requests of the browser administration with
 * their states, the filter by kind, a request in the drawer (steps, changes, audit target), the
 * cancellation of a queued request and the banner of a configuration changed by hand.
 */

async function changeSiteName(page: Page, name: string) {
  await page.goto('/settings/general');
  await page.getByLabel('Kuruluş adı').fill(name);
  await page.getByRole('button', { name: 'Kaydet' }).click();
  await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
}

function history(page: Page) {
  return page.getByRole('table', { name: 'Değişiklik istekleri, en yeni önce' });
}

test.describe('Ayarlar → Değişiklik geçmişi', () => {
  test('lists the requests with their states; the filter asks the API by kind', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/history');
    await expect(page.getByText('Henüz istek yok')).toBeVisible();

    await changeSiteName(page, 'Örnek Holding A.Ş.');
    await expect(page.getByTestId('request-state')).toHaveText('Uygulandı', { timeout: 10_000 });
    await page.goto('/sealing?day=2026-10-11');
    await page.getByRole('button', { name: 'Şimdi mühürle' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Şimdi mühürle' }).click();
    await expect(page.getByTestId('seal-now-result')).toBeVisible({ timeout: 10_000 });

    await page.goto('/settings/history');
    const rows = history(page).getByRole('row');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(1)).toContainText('Şimdi mühürle');
    await expect(rows.nth(1)).toContainText('Tamamlandı');
    await expect(rows.nth(2)).toContainText('Genel · Yapılandırma değişikliği');
    await expect(rows.nth(2)).toContainText('Uygulandı');
    await expect(rows.nth(2)).toContainText('tolga');

    await page.getByLabel('Tür').click();
    await page.getByRole('option', { name: 'Şimdi mühürle' }).click();
    await expect(rows).toHaveCount(2);
    expect(api.callsTo('GET', '/admin/requests').at(-1)?.query.get('kind')).toBe('action.seal');
  });

  test('a request in the drawer: steps, changes and its audit log target', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await changeSiteName(page, 'Örnek Holding A.Ş.');
    await expect(page.getByTestId('request-state')).toHaveText('Uygulandı', { timeout: 10_000 });
    const id = [...api.admin.requests.keys()][0] ?? '';
    await page.goto('/settings/history');
    await history(page).getByRole('button', { name: 'Genel · Yapılandırma değişikliği' }).click();
    const drawer = page.getByRole('dialog', { name: 'Genel · Yapılandırma değişikliği' });
    await expect(drawer.getByTestId('request-state')).toHaveText('Uygulandı');
    await expect(drawer).toContainText(`İstek no: ${id.slice(0, 8)}`);
    await drawer.getByText(/^Adımlar \(\d+\)$/).click();
    await expect(drawer.getByText('Ayar dosyası yazıldı')).toBeVisible();
    await expect(
      drawer
        .getByRole('table', { name: 'İstekteki değişiklikler' })
        .getByRole('row', { name: /Kuruluş adı/ }),
    ).toContainText('Örnek A.Ş.Örnek Holding A.Ş.');
    await expect(drawer).toContainText('Önceki ayarlar 17 numaralı sürümde saklı.');
    await expect(drawer.getByText(`admin-request:${id}`)).toBeVisible();
    await expect(drawer.getByRole('link', { name: 'Denetim kaydını aç' })).toHaveAttribute(
      'href',
      '/audit',
    );
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(
      history(page).getByRole('button', { name: 'Genel · Yapılandırma değişikliği' }),
    ).toBeFocused();
  });

  test('a queued request can be cancelled; nothing changes', async ({ page }) => {
    // The administration service takes its time: the request stays queued.
    const api = await mockApi(page, { signedInAs: 'tolga', admin: { stepMs: 120_000 } });
    await changeSiteName(page, 'Örnek Holding A.Ş.');
    await expect(page.getByTestId('request-state')).toHaveText('Sırada');
    await page.goto('/settings/history');
    await history(page).getByRole('button', { name: 'Genel · Yapılandırma değişikliği' }).click();
    const drawer = page.getByRole('dialog');
    await drawer.getByRole('button', { name: 'İptal et' }).click();
    await expect(drawer.getByTestId('request-state')).toHaveText('İptal edildi', {
      timeout: 10_000,
    });
    await expect(drawer).toContainText('İptal edildi; hiçbir şey değişmedi.');
    expect(api.admin.sections.general.site_name).toBe('Örnek A.Ş.');
    expect(
      api.callsTo('DELETE', `/admin/requests/${[...api.admin.requests.keys()][0] ?? ''}`),
    ).toHaveLength(1);
  });

  test('a configuration changed by hand on the server is named', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', admin: { outside: true } });
    await page.goto('/settings/history');
    await expect(page.getByTestId('config-outside')).toContainText(
      'Ayar dosyası elle değiştirilmiş',
    );
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Değişiklik geçmişi accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga', admin: { outside: true } });
    await changeSiteName(page, 'Örnek Holding A.Ş.');
    await expect(page.getByTestId('request-state')).toHaveText('Uygulandı', { timeout: 10_000 });
    await page.goto('/settings/history');
    await expect(history(page).getByRole('row')).toHaveCount(2);
    await expectNoA11yViolations(page);
    await history(page).getByRole('button', { name: 'Genel · Yapılandırma değişikliği' }).click();
    await expect(page.getByRole('dialog').getByTestId('request-state')).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
