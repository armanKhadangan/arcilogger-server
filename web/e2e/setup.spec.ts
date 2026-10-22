import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, submitCredentials, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

const ADMIN_PASSWORD = 'mavi bisiklet kırk iki limon';

test.describe('first-run setup', () => {
  test('creates the first administrator with the console token, then TOTP at sign-in', async ({
    page,
  }) => {
    const api = await mockApi(page, { needsSetup: true });
    await page.goto('/login');
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByRole('heading', { level: 1, name: 'İlk kurulum' })).toBeVisible();
    // The full path: the programs are not on PATH on the Windows server (docs/KURULUM.md §9).
    await expect(
      page.getByText(
        '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\arcilogger-api.exe" setup-token --config "C:\\ProgramData\\ARCILOGGER\\arcilogger.toml"',
        { exact: true },
      ),
    ).toBeVisible();

    // Browser-side checks first.
    await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
    await expect(page.getByText('Bu alan zorunludur.').first()).toBeVisible();
    expect(api.callsTo('POST', '/auth/setup/admin')).toHaveLength(0);

    await page.getByLabel('Kurulum anahtarı').fill('yanlis-anahtar');
    await page.getByLabel('Kullanıcı adı').fill('Tolga');
    await page.getByLabel('Ad soyad').fill('Tolga Yılmaz');
    await page.getByLabel('Yeni parola', { exact: true }).fill(ADMIN_PASSWORD);
    await page.getByLabel('Yeni parola (tekrar)').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
    await expect(page.getByText(/Kurulum anahtarı geçersiz veya süresi dolmuş/)).toBeVisible();
    await expect(page.getByLabel('Kurulum anahtarı')).toHaveAttribute('aria-invalid', 'true');

    await page.getByLabel('Kurulum anahtarı').fill(api.setupToken);
    await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
    await expect(page).toHaveURL(/\/login\?setup=done$/);
    await expect(page.getByText(/Yönetici hesabı oluşturuldu/)).toBeVisible();
    expect(api.callsTo('POST', '/auth/setup/admin').at(-1)?.body).toEqual({
      token: api.setupToken,
      username: 'tolga',
      display_name: 'Tolga Yılmaz',
      password: ADMIN_PASSWORD,
    });

    // The setup page is closed now; the new administrator enrols TOTP at the first sign-in.
    await page.goto('/setup');
    await expect(page).toHaveURL(/\/login$/);
    await submitCredentials(page, 'tolga', ADMIN_PASSWORD);
    await expect(
      page.getByRole('heading', { level: 1, name: 'İki adımlı doğrulamayı kurun' }),
    ).toBeVisible();
  });

  test('keeps the "created" notice on a slow PC (CPU throttled)', async ({ page }) => {
    // Regression: the redirect with the notice used to race with the setup page's own
    // "setup is done → /login" redirect and lost on slow machines (8 of 8 runs at 4x).
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
    const api = await mockApi(page, { needsSetup: true });
    await page.goto('/setup');
    await page.getByLabel('Kurulum anahtarı').fill(api.setupToken);
    await page.getByLabel('Kullanıcı adı').fill('tolga');
    await page.getByLabel('Ad soyad').fill('Tolga Yılmaz');
    await page.getByLabel('Yeni parola', { exact: true }).fill(ADMIN_PASSWORD);
    await page.getByLabel('Yeni parola (tekrar)').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
    await expect(page.getByText(/Yönetici hesabı oluşturuldu/)).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/login\?setup=done$/);
    expect(api.callsTo('POST', '/auth/setup/admin')).toHaveLength(1);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`setup page has no accessibility violations — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { needsSetup: true });
    await page.goto('/setup');
    await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
    await expect(page.getByText('Bu alan zorunludur.').first()).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
