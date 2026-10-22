import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

const SECTIONS = [
  'Renkler',
  'Tipografi',
  'Düğmeler',
  'Giriş alanları',
  'Rozetler',
  'Kartlar ve KPI',
  'Grafikler',
  'Veri tablosu',
  'Sekmeler',
  'Diyalog, yan panel, ipucu, bildirim',
  'Boş durum ve iskelet',
  'Zaman çizelgesi',
  'Isı haritası',
  'Mühür takvimi',
  'Adım göstergesi',
  'Kod bloğu',
];

for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`UI kit — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await usePreferences(page, { theme, collapsed: false });
      await page.goto('/ui-kit');
    });

    test('shows every component section', async ({ page }) => {
      await expect(page.getByRole('heading', { level: 1, name: 'Bileşen Kiti' })).toBeVisible();
      for (const name of SECTIONS) {
        await expect(page.getByRole('heading', { level: 2, name, exact: true })).toBeVisible();
      }
      await expect(page.locator('html')).toHaveClass(theme === 'dark' ? /dark/ : /^(?!.*dark).*$/);
    });

    test('has no WCAG 2.1 AA violations', async ({ page }) => {
      await expect(page.getByText('10.000 satır')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('full-page screenshot', async ({ page }, testInfo) => {
      await expect(page.getByText('10.000 satır')).toBeVisible();
      await testInfo.attach(`ui-kit-${theme}.png`, {
        body: await page.screenshot({ fullPage: true }),
        contentType: 'image/png',
      });
    });
  });
}

test.describe('UI kit interactions', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await usePreferences(page, { theme: 'light', collapsed: false });
    await page.goto('/ui-kit');
  });

  test('table row opens the detail drawer with the raw line', async ({ page }) => {
    const table = page.getByRole('table', { name: 'FortiGate trafik kayıtları (örnek)' });
    await table.getByRole('row').nth(1).click();
    const drawer = page.getByRole('dialog', { name: 'Kayıt ayrıntısı' });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText('Ham kayıt (değiştirilmemiş)')).toBeVisible();
    await expect(drawer.locator('pre')).toContainText('devname="FIREWALL-01"');
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
  });

  test('CSV export downloads a semicolon-separated file with BOM', async ({ page }) => {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'CSV dışa aktar' }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^arcilogger-trafik-\d{4}-\d{2}-\d{2}\.csv$/);
    const path = await download.path();
    const { readFile } = await import('node:fs/promises');
    const text = await readFile(path, 'utf8');
    expect(text.startsWith('﻿Zaman;Kaynak IP;VLAN;Hedef;Uygulama;Eylem;Veri (bayt)\r\n')).toBe(
      true,
    );
    expect(text.split('\r\n')).toHaveLength(10_002);
  });

  test('dialog confirms and shows a toast', async ({ page }) => {
    await page.getByRole('button', { name: 'Diyalog' }).click();
    const dialog = page.getByRole('dialog', { name: '11.10.2026 gününü şimdi mühürle?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Şimdi mühürle' }).click();
    await expect(
      page.getByText('11.10.2026 günü mühürlendi ve doğrulandı.', { exact: true }),
    ).toBeVisible();
  });

  test('chart can switch to its table view', async ({ page }) => {
    await page.getByRole('button', { name: 'Tablo görünümü' }).first().click();
    await expect(page.getByRole('table', { name: 'İndirilen / yüklenen (saatlik)' })).toBeVisible();
  });

  test('theme toggle switches to dark and back', async ({ page }) => {
    const toggle = page.getByTestId('ui-kit-theme-toggle');
    await toggle.click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await toggle.click();
    await expect(page.locator('html')).not.toHaveClass(/dark/);
  });
});
