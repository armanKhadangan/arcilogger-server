import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → E-posta (contract 2.17): the SMTP server with its write-only password (never sent to
 * another server), the trial e-mail before enabling or moving it, the CA upload, "Test e-postası
 * gönder", and the `alerts.email_*` database settings on the same page. The API is
 * `mock-admin.ts`.
 */

const CA = [
  '-----BEGIN CERTIFICATE-----',
  'Subject: CN=Örnek İç CA (örnek)',
  'MIIBfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKE',
  '-----END CERTIFICATE-----',
  '',
].join('\n');

async function applied(page: Page) {
  await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
    'Uygulandı',
    { timeout: 10_000 },
  );
}

test.describe('Ayarlar → E-posta', () => {
  test('the SMTP section and the alert e-mail settings on one page', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByTestId('settings-email').getByRole('link').click();
    await expect(page.getByRole('heading', { level: 1, name: 'E-posta' })).toBeVisible();
    const smtp = page.getByTestId('settings-smtp');
    await expect(smtp.getByLabel('SMTP sunucusu')).toHaveValue('smtp.ornek.example');
    await expect(smtp.getByLabel('Bağlantı noktası')).toHaveValue('587');
    await expect(smtp).toContainText('Kayıtlı');
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(page.getByTestId('smtp-ca')).toContainText('Windows sertifika deposu');
    const alerts = page.getByTestId('settings-group-email');
    await expect(alerts).toContainText('Uyarı e-postası alıcıları');
    await expect(alerts).not.toContainText('Uyarı kuralları');
  });

  test('a new server needs the password again and a trial e-mail; then it is applied', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/email');
    const smtp = page.getByTestId('settings-smtp');
    await smtp.getByLabel('SMTP sunucusu').fill('smtp2.ornek.example');
    await page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }).click();
    await expect(smtp).toContainText('kayıtlı parola yeni sunucuya gönderilmez');
    await expect(smtp).toContainText('Deneme e-postası için bir alıcı seçin.');
    expect(api.callsTo('POST', '/admin/config/smtp/check')).toHaveLength(0);

    await smtp.getByRole('button', { name: 'Değiştir' }).click();
    await smtp.getByLabel('Parola', { exact: true }).fill('yeni-smtp-degeri'); // gitleaks:allow — fake test value
    await smtp.getByLabel('Deneme e-postasının alıcısı').click();
    await page.getByRole('option', { name: 'tolga@ornek.com.tr' }).click();
    await page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /SMTP sunucusu/ })).toContainText(
      'smtp.ornek.examplesmtp2.ornek.example',
    );
    // The stored password is overwritten: not "kayıtlı → kayıtlı".
    await expect(review.getByRole('row', { name: /SMTP parolası/ })).toContainText(
      'kayıtlıyenisiyle değiştirilecek',
    );
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    expect(api.callsTo('PUT', '/admin/config/smtp').at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^smtp:[0-9a-f]{64}$/) as unknown,
      enabled: true,
      host: 'smtp2.ornek.example',
      port: 587,
      security: 'starttls',
      username: 'arcilogger',
      from_address: 'arcilogger@ornek.example',
      timeout_seconds: 30,
      password: 'yeni-smtp-degeri', // gitleaks:allow — fake test value
      test_to: 'tolga@ornek.com.tr',
      probe: true,
    });
    expect(api.admin.sections.smtp.host).toBe('smtp2.ornek.example');
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    // The password went out with the check and the change only, and nothing on the page keeps it.
    const carrying = api.calls
      .filter((call) => JSON.stringify(call.body ?? null).includes('yeni-smtp-degeri'))
      .map((call) => `${call.method} ${call.path}`);
    expect(new Set(carrying)).toEqual(
      new Set(['POST /admin/config/smtp/check', 'PUT /admin/config/smtp']),
    );
    expect(await page.content()).not.toContain('yeni-smtp-degeri');
    const stored = await page.evaluate(() =>
      JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]),
    );
    expect(stored).not.toContain('yeni-smtp-degeri');
  });

  test('a user name is never sent without TLS to another host', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/email');
    const smtp = page.getByTestId('settings-smtp');
    await smtp.getByLabel('Bağlantı güvenliği').click();
    await page.getByRole('option', { name: 'Şifresiz' }).click();
    await expect(smtp).toContainText('e-postalar ağda açık metin gider');
    await page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }).click();
    await expect(smtp).toContainText(
      'Kullanıcı adı şifresiz bağlantıyla yalnızca bu sunucunun kendisine gönderilebilir.',
    );
  });

  test('switching e-mail off warns and asks for the password', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/email');
    await page.getByLabel('E-posta gönderimi açık').click();
    await expect(page.getByTestId('settings-smtp')).toContainText(
      'uyarılar artık e-postayla bildirilmez',
    );
    await page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    expect(api.admin.sections.smtp.enabled).toBe(false);
    expect(api.callsTo('PUT', '/admin/config/smtp').at(-1)?.body).not.toHaveProperty('test_to');
  });

  test('"Test e-postası gönder" reports the answer of the server', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/email');
    const card = page.getByTestId('settings-test-email');
    const send = card.getByRole('button', { name: 'Test e-postası gönder' });
    await expect(send).toBeDisabled();
    await card.getByLabel('Alıcı').click();
    await page.getByRole('option', { name: 'tolga@ornek.com.tr' }).click();
    await send.click();
    await expect(card.getByTestId('test-email-result')).toHaveText('Test e-postası gönderildi.', {
      timeout: 10_000,
    });
    expect(api.callsTo('POST', '/admin/actions/test-email')[0]?.body).toEqual({
      to: 'tolga@ornek.com.tr',
    });
    api.admin.nextAction = 'failed';
    await send.click();
    await expect(card.getByTestId('test-email-result')).toContainText(
      'Test e-postası gönderilemedi: 535 5.7.8',
      { timeout: 10_000 },
    );
  });

  test('"Test e-postası gönder" goes through the worker: it works without the administration service', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'missing' } });
    await page.goto('/settings/email');
    await expect(page.getByTestId('agent-unavailable')).toBeVisible();
    await expect(
      page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }),
    ).toBeDisabled();
    const card = page.getByTestId('settings-test-email');
    await card.getByLabel('Alıcı').click();
    await page.getByRole('option', { name: 'tolga@ornek.com.tr' }).click();
    await card.getByRole('button', { name: 'Test e-postası gönder' }).click();
    await expect(card.getByTestId('test-email-result')).toHaveText('Test e-postası gönderildi.', {
      timeout: 10_000,
    });
    expect(api.callsTo('POST', '/admin/actions/test-email')).toHaveLength(1);
  });

  test('an internal CA is uploaded, used, and given up again', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/email');
    const card = page.getByTestId('settings-smtp-ca');
    await card.getByLabel('CA dosyası').setInputFiles({
      name: 'ic-ca.pem',
      mimeType: 'application/x-pem-file',
      buffer: Buffer.from(CA),
    });
    await card.getByRole('button', { name: 'Dosyayı gönder' }).click();
    const preview = card.getByTestId('smtp-ca-preview');
    await expect(preview).toContainText('CN=Örnek İç CA (örnek)');
    await preview.getByRole('button', { name: "Bu CA'yı kullan" }).click();
    await confirmReauth(page);
    await expect(card.getByTestId('request-state')).toHaveText('Uygulandı', { timeout: 10_000 });
    await expect(page.getByTestId('smtp-ca')).toContainText('Yüklenen CA kullanılıyor');
    expect(api.callsTo('POST', '/admin/uploads')[0]?.query.get('kind')).toBe('smtp_ca');

    await card.getByRole('button', { name: 'Windows sertifika deposuna dön' }).click();
    await expect(page.getByTestId('smtp-ca')).toContainText('Windows sertifika deposu', {
      timeout: 10_000,
    });
    expect(api.callsTo('DELETE', '/admin/config/smtp/ca')[0]?.query.get('etag')).toMatch(/^smtp:/);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`E-posta accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/email');
    await expect(page.getByTestId('settings-group-email')).toBeVisible();
    await expectNoA11yViolations(page);
    const smtp = page.getByTestId('settings-smtp');
    await smtp.getByLabel('SMTP sunucusu').fill('smtp_2');
    await page.getByTestId('section-actions').getByRole('button', { name: 'Kaydet' }).click();
    await expect(smtp).toContainText('Geçerli bir sunucu adı ya da IPv4 adresi girin.');
    await expectNoA11yViolations(page);
  });
}
