import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Güvenlik kuralları (Wave 6 contract §2.3, W6-F): the table with its marks, a rule's
 * sheet, "Son 24 saatte dene", Save (only what differs from the defaults is sent), the browser's
 * checks of a list of networks, the module lock and the read-only view. The API is
 * `mock-rules.ts`, from the contract's JSON until W6-A's API is captured.
 */

async function openRule(page: Page, id: string) {
  await page.getByRole('button', { name: `${id} kuralını aç` }).click();
  const sheet = page.getByTestId('rule-sheet');
  await expect(sheet).toBeVisible();
  return sheet;
}

function row(page: Page, id: string) {
  return page.locator(`[data-testid="rule-row"][data-rule="${id}"]`);
}

test.describe('Ayarlar → Güvenlik kuralları', () => {
  test('the section, the table and its marks', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByTestId('settings-rules').getByRole('link').click();
    await expect(page).toHaveURL(/\/settings\/rules$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Güvenlik kuralları' })).toBeVisible();
    await expect(page.getByTestId('rule-row')).toHaveCount(6);
    await expect(page.getByTestId('rule-row').first()).toHaveAttribute('data-rule', 'R01');

    await expect(row(page, 'R01').getByTestId('rule-state')).toHaveText('Açık');
    await expect(row(page, 'R01').getByTestId('rule-data')).toHaveText('Veri geliyor');
    await expect(row(page, 'R01')).toContainText('Yüksek');
    await expect(row(page, 'R03')).toContainText('Sahada doğrula');
    await expect(row(page, 'R08').getByTestId('rule-state')).toHaveText('Ayar gerekli');
    await expect(row(page, 'R08').getByTestId('rule-data')).toHaveText('Hiç veri gelmedi');
    await expect(row(page, 'R13')).toContainText('Kişi düzeyi');
    await expect(row(page, 'R17').getByTestId('rule-data')).toHaveText('Son 24 saatte veri yok');
    await expect(row(page, 'R17')).toContainText('Son çalışma hatası');
    await expect(row(page, 'R19').getByTestId('rule-state')).toHaveText('Kapalı');
    await expect(page.getByTestId('rules-locked')).toHaveCount(0);
    await expect(page.getByTestId('rules-read-only')).toHaveCount(0);
  });

  test('a rule: the trial run, then Save sends only what differs', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/rules');
    const sheet = await openRule(page, 'R01');
    await expect(
      sheet.getByRole('heading', { name: /R01 · İçeriden port taraması/ }),
    ).toBeVisible();
    await expect(sheet.getByTestId('rule-starting-value')).toHaveText(
      'Başlangıç değeri; ağınıza göre ayarlayın.',
    );
    await expect(sheet.getByTestId('rule-advice')).toContainText('Cihazlar sayfasında');
    await expect(page).toHaveURL(/\?rule=R01$/);
    const save = sheet.getByRole('button', { name: 'Kaydet' });
    await expect(save).toBeDisabled();

    await sheet.getByRole('button', { name: 'Son 24 saatte dene' }).click();
    const result = sheet.getByTestId('rule-dry-run-result');
    await expect(result).toContainText('Son 24 saatte bu ayarlarla 3 uyarı açılırdı');
    await expect(result.getByRole('rowheader')).toHaveText([
      '172.20.41.17',
      '172.20.10.23',
      '172.20.62.5',
    ]);
    expect(api.callsTo('POST', '/rules/R01/dry-run').at(-1)?.body).toEqual({});

    await sheet.getByLabel('Farklı port sayısı').fill('40');
    await expect(result).toContainText('Ayarlar denemeden sonra değişti');
    await sheet.getByRole('button', { name: 'Son 24 saatte dene' }).click();
    await expect(result).toContainText('Son 24 saatte bu ayarlarla 1 uyarı açılırdı');
    await expect(result).not.toContainText('Ayarlar denemeden sonra değişti');
    expect(api.callsTo('POST', '/rules/R01/dry-run').at(-1)?.body).toEqual({
      params: { threshold_ports: 40 },
    });

    await sheet.getByLabel('Önem').click();
    await page.getByRole('option', { name: 'Kritik' }).click();
    await save.click();
    await expect(page.getByText('R01 kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PUT', '/rules/R01').at(-1)?.body).toEqual({
      severity: 'critical',
      params: { threshold_ports: 40 },
    });
    await expect(save).toBeDisabled();
    // Saved, Save is disabled: the focus stayed in the sheet. The first Escape dismisses the
    // notice (the topmost layer), the next one closes the sheet.
    await page.keyboard.press('Escape');
    await expect(page.getByText('R01 kaydedildi.', { exact: true })).toBeHidden();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();
    await expect(row(page, 'R01')).toContainText('Kritik');

    // Back to the defaults: the overrides go (an empty body).
    const again = await openRule(page, 'R01');
    await again.getByRole('button', { name: 'Varsayılanlara dön' }).click();
    await again.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('R01 kaydedildi.', { exact: true }).last()).toBeVisible();
    expect(api.callsTo('PUT', '/rules/R01').at(-1)?.body).toEqual({});
  });

  test('a list of networks is checked line by line; the rule then needs no setup', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/rules?rule=R08');
    const sheet = page.getByTestId('rule-sheet');
    await expect(sheet.getByTestId('rule-needs-setup')).toContainText('Ayar gerekli');
    const servers = sheet.getByLabel('İzin verilen DNS sunucuları');
    await servers.fill('172.20.0.10\n172.20.1.0/16');
    await sheet.getByLabel('Kural açık').click();
    await sheet.getByRole('button', { name: 'Kaydet' }).click();
    await expect(sheet.getByText('Satır 2: ağ adresi 172.20.0.0/16 olmalı.')).toBeVisible();
    await expect(servers).toBeFocused();
    expect(api.callsTo('PUT', '/rules/R08')).toHaveLength(0);

    await servers.fill('172.20.0.10\n\n198.51.100.53\n');
    await sheet.getByLabel('Pencere (saniye)').fill('30');
    await sheet.getByRole('button', { name: 'Kaydet' }).click();
    await expect(sheet.getByText('En az 60 olmalı.')).toBeVisible();
    await sheet.getByLabel('Pencere (saniye)').fill('3600');
    await sheet.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('R08 kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PUT', '/rules/R08').at(-1)?.body).toEqual({
      enabled: true,
      params: { allowed_dns_servers: ['172.20.0.10', '198.51.100.53'] },
    });
    await expect(sheet.getByTestId('rule-needs-setup')).toHaveCount(0);
    await sheet.getByRole('button', { name: 'Kapat' }).click();
    await expect(sheet).toBeHidden();
    await expect(row(page, 'R08').getByTestId('rule-state')).toHaveText('Açık');
  });

  test('a byte limit is typed in MiB', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/rules?rule=R13');
    const sheet = page.getByTestId('rule-sheet');
    const hour = sheet.getByLabel('Bir saatte gönderilen (MiB)');
    await expect(hour).toHaveValue('1024');
    await expect(sheet.getByText('MiB olarak: 1 GB. Varsayılan: 1 GB')).toBeVisible();
    await hour.fill('1536');
    await sheet.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('R13 kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PUT', '/rules/R13').at(-1)?.body).toEqual({
      params: { hour_bytes: 1_610_612_736 },
    });
  });

  test('a refused trial run says why', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', rules: { dryRun: 'busy' } });
    await page.goto('/settings/rules?rule=R01');
    const sheet = page.getByTestId('rule-sheet');
    await sheet.getByRole('button', { name: 'Son 24 saatte dene' }).click();
    await expect(sheet.getByTestId('rule-dry-run-error')).toHaveText(
      'Başka bir deneme sürüyor; bitince tekrar deneyin.',
    );
    await mockApi(page, { signedInAs: 'tolga', rules: { dryRun: 'timeout' } });
    await page.goto('/settings/rules?rule=R01');
    await page
      .getByTestId('rule-sheet')
      .getByRole('button', { name: 'Son 24 saatte dene' })
      .click();
    await expect(page.getByTestId('rule-dry-run-error')).toHaveText(
      'Deneme 30 sn içinde bitmedi; pencereyi ya da listeleri küçültün.',
    );
  });

  test('without the module the rules stay readable and nothing can be changed', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', rules: { locked: true } });
    await page.goto('/settings/rules');
    await expect(page.getByTestId('rules-locked')).toContainText(
      'Güvenlik kuralları lisansınızda açık değil',
    );
    await expect(page.getByTestId('rule-row')).toHaveCount(6);
    const sheet = await openRule(page, 'R01');
    await expect(sheet.getByLabel('Farklı port sayısı')).toBeDisabled();
    await expect(sheet.getByRole('button', { name: 'Kaydet' })).toHaveCount(0);
    await expect(sheet.getByRole('button', { name: 'Son 24 saatte dene' })).toHaveCount(0);
    expect(api.callsTo('PUT', '/rules/R01')).toHaveLength(0);
  });

  test('with alerts.read only: read-only', async ({ page }) => {
    await mockApi(page, { signedInAs: 'ayse.yilmaz', grant: { auditor: ['alerts.read'] } });
    await page.goto('/settings/rules');
    await expect(page.getByTestId('rules-read-only')).toBeVisible();
    const sheet = await openRule(page, 'R03');
    await expect(sheet.getByTestId('rule-verify')).toContainText('Sahada doğrula');
    await expect(sheet.getByLabel('Başarısız giriş sayısı')).toBeDisabled();
    await expect(sheet.getByRole('button', { name: 'Kaydet' })).toHaveCount(0);
  });

  test('without alerts.read the page is refused', async ({ page }) => {
    await mockApi(page, { signedInAs: 'zeynep' });
    await page.goto('/settings/rules');
    await expect(page.getByTestId('rule-row')).toHaveCount(0);
    await expect(page.getByRole('heading', { level: 1, name: 'Güvenlik kuralları' })).toHaveCount(
      0,
    );
  });

  test('in English', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/rules?rule=R03');
    await expect(row(page, 'R08').getByTestId('rule-state')).toHaveText('Needs setup');
    await expect(row(page, 'R03')).toContainText('Verify on site');
    const sheet = page.getByTestId('rule-sheet');
    await expect(sheet.getByTestId('rule-starting-value')).toHaveText(
      'Starting value; adjust it to your network.',
    );
    await sheet.getByRole('button', { name: 'Try on the last 24 hours' }).click();
    await expect(sheet.getByTestId('rule-dry-run-result')).toContainText(
      'With these settings 3 alerts would have opened in the last 24 hours',
    );
  });

  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`accessibility (${theme}): the table and a rule's sheet`, async ({ page }) => {
      await usePreferences(page, { theme });
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/settings/rules');
      await expect(page.getByTestId('rule-row')).toHaveCount(6);
      await expectNoA11yViolations(page);
      const sheet = await openRule(page, 'R17');
      await sheet.getByRole('button', { name: 'Son 24 saatte dene' }).click();
      await expect(sheet.getByTestId('rule-dry-run-result')).toBeVisible();
      await sheet.getByLabel('Süre', { exact: true }).fill('x');
      await sheet.getByRole('button', { name: 'Kaydet' }).click();
      await expect(sheet.getByText('Bir tam sayı girin.')).toBeVisible();
      await expectNoA11yViolations(page);
    });
  }
});
