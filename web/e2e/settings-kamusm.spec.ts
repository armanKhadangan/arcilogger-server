import { createHash } from 'node:crypto';

import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Kamu SM (contract 2.17): the wizard with the state of each step, the write-only
 * credentials (never pre-filled) with the re-authentication, the Zamane client (a known release
 * and one held for the console's approval), the root certificates and the legal roots, the
 * stamp test, the switch to production and back, the credit threshold, the server's refusal of
 * a change that would stop a working sealer, a held client on a later visit, and a sealing cause
 * that no step shows. The API is `mock-admin.ts`.
 */

const PEM = [
  '-----BEGIN CERTIFICATE-----',
  'Subject: CN=Kamu SM Kök Sertifika Hizmet Sağlayıcısı - Sürüm 3 (örnek)',
  'MIIBfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKE',
  '-----END CERTIFICATE-----',
  '',
].join('\n');

function steps(page: Page) {
  return page.getByRole('navigation', { name: 'Kurulum adımları' });
}

async function openStep(page: Page, name: RegExp) {
  await steps(page).getByRole('link', { name }).click();
}

async function upload(page: Page, label: string, name: string, content: string) {
  await page.getByLabel(label).setInputFiles({
    name,
    mimeType: 'application/octet-stream',
    buffer: Buffer.from(content),
  });
  await page.getByRole('button', { name: 'Dosyayı gönder' }).click();
}

async function applied(page: Page) {
  await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
    'Uygulandı',
    { timeout: 10_000 },
  );
}

test.describe('Ayarlar → Kamu SM', () => {
  test('the steps with their state, the sealing view read-only', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByRole('link', { name: /^Kamu SM/ }).click();
    await expect(page).toHaveURL(/\/settings\/kamusm$/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kamu SM zaman damgası' }),
    ).toBeVisible();
    const nav = steps(page);
    for (const [name, state] of [
      [/^1\. Java/, 'Tamam'],
      [/^2\. Zamane istemcisi/, 'Tamam'],
      [/^3\. Kök sertifikalar/, 'Tamam'],
      [/^4\. Müşteri bilgileri/, 'Tamam'],
      [/^5\. Damga testi/, 'İsteğe bağlı'],
      [/^6\. Üretime geç/, 'İsteğe bağlı'],
      [/^7\. Kontör uyarı eşiği/, 'Tamam'],
    ] as const)
      await expect(nav.getByRole('link', { name })).toContainText(state);
    await expect(nav.getByRole('link', { name: /^1\. Java/ })).toHaveAttribute(
      'aria-current',
      'step',
    );
    const seal = page.getByTestId('seal-view');
    await expect(seal).toContainText('Mühürleme görevi geçerli ayarlarla mühürleyebilir.');
    await expect(seal.getByTestId('locked-paths')).toBeVisible();
    await expect(seal).toContainText('tsa.digest');
    await expect(page.getByTestId('tsa-provider')).toHaveCount(0);
  });

  test('credentials are write-only: never pre-filled, sent once, re-authenticated', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/kamusm?step=credentials');
    const card = page.getByTestId('kamusm-step-credentials');
    await expect(card).toContainText('Kayıtlı');
    await expect(card).toContainText('sunucu konsolu');
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(page.getByLabel('Müşteri numarası')).toHaveCount(0);

    await card.getByRole('button', { name: 'Değiştir' }).first().click();
    const customer = page.getByLabel('Müşteri numarası');
    await expect(customer).toHaveValue('');
    await expect(customer).toHaveAttribute('type', 'password');
    await customer.fill('12 34');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(
      card.getByText(
        'Müşteri numarası 1–64 karakter olmalı ve boşluk ya da denetim karakteri içermemeli.',
      ),
    ).toBeVisible();
    expect(api.callsTo('POST', '/admin/config/tsa/check')).toHaveLength(0);

    await customer.fill('100200300');
    await card.getByRole('button', { name: 'Değiştir' }).click();
    await page.getByLabel('Parola', { exact: true }).fill('yeni-gizli-deger'); // gitleaks:allow — fake test value
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    const table = review.getByRole('table', { name: 'Kaydedilecek değişiklikler' });
    // Both credentials are stored already: they are overwritten, not "kayıtlı → kayıtlı".
    await expect(table.getByRole('row', { name: /Kamu SM müşteri numarası/ })).toContainText(
      'kayıtlıyenisiyle değiştirilecek',
    );
    await expect(table.getByRole('row', { name: /Kamu SM parolası/ })).toContainText(
      'kayıtlıyenisiyle değiştirilecek',
    );
    await expect(table).not.toContainText('100200300');
    await expect(review).toContainText('Kaydederken parolanızı yeniden girmeniz istenecek.');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);

    const put = api.callsTo('PUT', '/admin/config/tsa');
    expect(put.at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^tsa:[0-9a-f]{64}$/) as unknown,
      server: 'test',
      credit_alert_threshold: 500,
      timeout_seconds: 60,
      java: 'C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe',
      customer: '100200300',
      password: 'yeni-gizli-deger', // gitleaks:allow — fake test value
      probe: true,
    });
    // Back to "Kayıtlı ✓": nothing typed stays in the page.
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(card).toContainText('web: tolga');
    // The new values went out with the check and the change only (the change twice: refused
    // once for the password confirmation), and nothing on the page keeps them.
    for (const secret of ['100200300', 'yeni-gizli-deger']) {
      const carrying = api.calls
        .filter((call) => JSON.stringify(call.body ?? null).includes(secret))
        .map((call) => `${call.method} ${call.path}`);
      expect(carrying).toEqual([
        'POST /admin/config/tsa/check',
        'PUT /admin/config/tsa',
        'PUT /admin/config/tsa',
      ]);
      expect(await page.content()).not.toContain(secret);
    }
    const stored = await page.evaluate(() =>
      JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]),
    );
    expect(stored).not.toContain('yeni-gizli-deger');
    expect(api.admin.sections.tsa.secrets['kamusm.customer']?.changed_by).toBe('web:tolga');
  });

  test('chooses another Java; the change is reviewed and re-authenticated', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/kamusm');
    await page.getByLabel('C:/Program Files/Java/jre1.8.0_421/bin/java.exe').check();
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Java/ })).toContainText('jre1.8.0_421');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    expect(api.admin.sections.tsa.java).toBe('C:/Program Files/Java/jre1.8.0_421/bin/java.exe');
    await expect(page.getByLabel('C:/Program Files/Java/jre1.8.0_421/bin/java.exe')).toBeChecked();
  });

  test('a known Zamane release is used once it is copied', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/kamusm?step=zamane');
    await expect(page.getByTestId('zamane-current')).toContainText('kurulumla gelen');
    await upload(page, 'Zamane istemcisi dosyası', 'tss-client-console-3.1.4.zip', 'known-release');
    const preview = page.getByTestId('zamane-preview');
    await expect(preview).toContainText('ZIP dosyası: tss-client-console-3.1.4.jar');
    // `release` is the client's version (`zamane_releases.ZamaneRelease.version`).
    await expect(preview).toContainText('Bilinen Kamu SM sürümü: 3.1.4.');
    await preview.getByRole('button', { name: 'Bu istemciyi kullan' }).click();
    await confirmReauth(page);
    await applied(page);
    await expect(page.getByTestId('zamane-current')).toContainText('3.1.4');
    await expect(page.getByTestId('zamane-current')).toContainText('bilinen Kamu SM sürümü');
    const stage = api.callsTo('POST', '/admin/config/tsa/zamane');
    expect(stage.at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^tsa:/) as unknown,
      upload_id: expect.stringMatching(/^[0-9a-f]{32}$/) as unknown,
    });
    const sent = api.callsTo('POST', '/admin/uploads');
    expect(sent[0]?.query.get('kind')).toBe('zamane_client');
    expect(sent[0]?.contentType).toMatch(/^multipart\/form-data/);
  });

  test('an unknown release needs the published SHA-256 and waits for the console', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    // The value the Kamu SM download page publishes for this file.
    const digest = createHash('sha256').update('unreleased build').digest('hex');
    await page.goto('/settings/kamusm?step=zamane');
    await upload(
      page,
      'Zamane istemcisi dosyası',
      'tss-client-console-9.9.jar',
      'unreleased build',
    );
    const preview = page.getByTestId('zamane-preview');
    await expect(preview).toContainText('Bilinmeyen sürüm');
    await expect(preview).toContainText('en erken 24 saat sonra');
    // The file's own hash is not on the screen to be copied into the check, nor a command.
    await expect(preview).toContainText('dosyanın değeri bu ekranda bilerek gösterilmez');
    await expect(preview).not.toContainText(digest);
    await expect(preview).not.toContainText('approve-jar');
    await expect(preview.getByRole('button', { name: 'Kopyala' })).toHaveCount(0);
    const expected = preview.getByLabel('Kamu SM sayfasındaki SHA-256');
    await expected.fill('abc');
    await preview.getByRole('button', { name: 'Bu istemciyi kullan' }).click();
    await expect(preview).toContainText('64 onaltılık karakterlik bir SHA-256 değeri girin.');
    await expected.fill('0'.repeat(64));
    await preview.getByRole('button', { name: 'Bu istemciyi kullan' }).click();
    await expect(preview).toContainText('Girilen SHA-256 dosyanın değeriyle aynı değil.');
    expect(api.callsTo('POST', '/admin/config/tsa/zamane')).toHaveLength(0);

    await expected.fill(digest.toUpperCase().replace(/(.{8})/g, '$1 '));
    await preview.getByRole('button', { name: 'Bu istemciyi kullan' }).click();
    await confirmReauth(page);
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Bekletiliyor', {
      timeout: 10_000,
    });
    const staged = page.getByTestId('zamane-staged');
    await expect(staged).toContainText(
      `& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin approve-jar ${digest}`,
    );
    await expect(staged).toContainText("Kamu SM'den indirdiğiniz istemci .jar dosyasınınki");
    expect(api.callsTo('POST', '/admin/config/tsa/zamane').at(-1)?.body).toMatchObject({
      expected_sha256: digest,
    });
    // The client in use stays until the console approves the new one.
    await expect(page.getByTestId('zamane-current')).toContainText('kurulumla gelen');
  });

  test('a held client is shown again on a later visit and cancelled from this page', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    const digest = createHash('sha256').update('unreleased build').digest('hex');
    await page.goto('/settings/kamusm?step=zamane');
    await upload(
      page,
      'Zamane istemcisi dosyası',
      'tss-client-console-9.9.jar',
      'unreleased build',
    );
    await page.getByLabel('Kamu SM sayfasındaki SHA-256').fill(digest);
    await page.getByRole('button', { name: 'Bu istemciyi kullan' }).click();
    await confirmReauth(page);
    await expect(page.getByTestId('request-state')).toHaveText('Bekletiliyor', {
      timeout: 10_000,
    });

    // Another day: the held change is listed on any step of the wizard. Time alone never makes
    // it run: it waits for the console's approval, without a countdown.
    await page.goto('/settings/kamusm?step=credit');
    const held = page.getByTestId('held-change');
    await expect(held.getByTestId('request-state')).toHaveText('Bekletiliyor');
    await expect(held.getByTestId('hold-countdown')).toContainText(
      'sunucu konsolundaki onayı bekliyor ve kendiliğinden uygulanmaz',
    );
    await expect(held.getByTestId('hold-countdown')).not.toContainText('kalan');
    await expect(page.getByTestId('request-progress')).toHaveCount(1);
    // The other steps are not kept waiting meanwhile.
    await page.getByLabel('Kontör uyarı eşiği', { exact: true }).fill('300');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page
      .getByRole('dialog', { name: 'Değişiklikleri onaylayın' })
      .getByRole('button', { name: 'Onayla ve kaydet' })
      .click();
    await expect.poll(() => api.admin.sections.tsa.credit_alert_threshold).toBe(300);
    await expect(held.getByTestId('request-state')).toHaveText('Bekletiliyor');
    // The password was confirmed a moment ago, so the cancellation is not asked again.
    await held.getByRole('button', { name: 'İptal et' }).click();
    // Cancelled: the change leaves the configuration's held list, and the client its staging.
    await expect(held).toHaveCount(0, { timeout: 10_000 });
    expect(api.admin.sections.tsa.zamane.staged).toEqual([]);
    await openStep(page, /^2\. Zamane istemcisi/);
    await expect(page.getByTestId('zamane-staged')).toHaveCount(0);
    const id = [...api.admin.requests.values()].find((item) => item.out.section === 'tsa')?.out.id;
    expect(api.callsTo('DELETE', `/admin/requests/${id ?? ''}`).length).toBeGreaterThan(0);
  });

  test('a sealing cause that no step shows is named above the steps', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.sections.tsa.seal_ready = { ok: false, causes: ['location'] };
    await page.goto('/settings/kamusm');
    const banner = page.getByTestId('seal-unplaced');
    await expect(banner).toContainText('nedeni sihirbazın adımlarından birinde değil');
    await expect(banner).toContainText(
      'Ayar dosyasındaki secrets_dir ya da data_dir bu kurulumun klasörü değil',
    );
    await expect(page.getByTestId('seal-view')).toContainText('arcilogger.toml');
  });

  test('"Yeniden dene" after a failed switch to production tries the switch again', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    api.admin.nextOutcome = 'failed';
    await page.goto('/settings/kamusm?step=production');
    await page.getByRole('button', { name: 'Üretim sunucusuna geç' }).click();
    await page
      .getByRole('dialog', { name: 'Üretim sunucusuna geçilsin mi?' })
      .getByRole('button', { name: 'Üretim sunucusuna geç' })
      .click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText(
      'Uygulanmadı, önceki ayarlar geçerli',
      { timeout: 10_000 },
    );
    await progress.getByRole('button', { name: 'Yeniden dene' }).click();
    await expect(review.getByRole('row', { name: /Zaman damgası sunucusu/ })).toContainText(
      'testproduction',
    );
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    await expect(page.getByTestId('tsa-server')).toHaveText(
      'Şu an Kamu SM üretim sunucusu kullanılıyor.',
    );
    const put = api.callsTo('PUT', '/admin/config/tsa').map((call) => call.body);
    expect(put.at(-1)).toMatchObject({ server: 'production' });
  });

  test('installs a root as a legal root and marks an installed one', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/kamusm?step=roots');
    const roots = page.getByRole('table', { name: 'Kurulu Kamu SM kök sertifikaları' });
    await expect(roots.getByRole('row')).toHaveCount(2);
    await expect(page.getByText('Henüz yasal kök işaretlenmedi', { exact: false })).toBeVisible();

    await upload(page, 'Kök sertifika dosyası', 'kamusm-kok-v3.pem', PEM);
    const preview = page.getByTestId('root-preview');
    await expect(preview).toContainText('CN=Kamu SM Kök Sertifika Hizmet Sağlayıcısı - Sürüm 3');
    // The captured inspection of a root (`samples/admin.json` `kamusm.upload_root`).
    await expect(preview).toContainText('EC P-256');
    await preview.getByLabel('Yasal kök olarak da işaretle').click();
    const install = preview.getByRole('button', { name: 'Sertifikayı kur' });
    await expect(install).toBeDisabled();
    await preview.getByLabel("Parmak izini Kamu SM'nin yayımladığı değerle karşılaştırdım").check();
    await install.click();
    await confirmReauth(page);
    await applied(page);
    await expect(roots.getByRole('row')).toHaveCount(3);
    await expect(roots.getByRole('row', { name: /Sürüm 3/ })).toContainText('Yasal kök');
    await expect(roots.getByRole('row', { name: /Sürüm 3/ }).getByRole('button')).toHaveCount(0);
    await expect(roots.getByRole('row', { name: /Sürüm 3/ })).toContainText('tarayıcıdan eklendi');
    expect(api.callsTo('POST', '/admin/config/tsa/roots').at(-1)?.body).toMatchObject({
      legal: true,
      confirmed: true,
    });

    await roots
      .getByRole('button', { name: /Kamu SM Test Kök Sertifikası.*yasal kök olarak işaretle/ })
      .click();
    const mark = page.getByRole('dialog', { name: 'Yasal kök olarak işaretlensin mi?' });
    const confirm = mark.getByRole('button', { name: 'Yasal kök olarak işaretle' });
    await expect(confirm).toBeDisabled();
    await mark.getByLabel("Parmak izini Kamu SM'nin yayımladığı değerle karşılaştırdım").check();
    await confirm.click();
    await expect(mark).toBeHidden();
    // The badge replaces the button once the request is applied.
    await expect(roots.getByRole('row', { name: /Test Kök/ }).getByRole('button')).toHaveCount(0, {
      timeout: 10_000,
    });
    await expect(roots.getByRole('row', { name: /Test Kök/ })).toContainText('Yasal kök');
    expect(api.admin.sections.tsa.roots.every((root) => root.legal)).toBe(true);
  });

  test('a certificate that is not one is refused with the API text', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/kamusm?step=roots');
    await upload(page, 'Kök sertifika dosyası', 'not-a-root.txt', 'hello');
    await expect(page.getByTestId('kamusm-step-roots').getByRole('alert')).toBeVisible();
    await expect(page.getByTestId('root-preview')).toHaveCount(0);
  });

  test('the stamp test asks first, then shows the verified stamp', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/kamusm?step=test');
    await page.getByRole('button', { name: 'Damga testi (1 kontör harcar)' }).click();
    const ask = page.getByRole('dialog', { name: 'Damga testi yapılsın mı?' });
    await expect(ask).toContainText('1 kontör harcanır');
    await ask.getByRole('button', { name: 'Testi başlat' }).click();
    const result = page.getByTestId('tsa-test-result');
    await expect(result).toContainText('Doğrulandı', { timeout: 10_000 });
    await expect(result).toContainText('CN=Kamu SM Zaman Damgası Test Sunucusu (örnek)');
    expect(api.callsTo('POST', '/admin/actions/tsa-test')[0]?.body).toEqual({
      confirm_credit: true,
    });
    await expect(steps(page).getByRole('link', { name: /^5\. Damga testi/ })).toContainText(
      'Tamam',
    );
  });

  test('switches to production and back, each time with a confirmation', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/kamusm?step=production');
    await expect(page.getByTestId('tsa-server')).toHaveText(
      'Şu an Kamu SM test sunucusu kullanılıyor.',
    );
    await page.getByRole('button', { name: 'Üretim sunucusuna geç' }).click();
    const ask = page.getByRole('dialog', { name: 'Üretim sunucusuna geçilsin mi?' });
    await ask.getByRole('button', { name: 'Üretim sunucusuna geç' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Zaman damgası sunucusu/ })).toContainText(
      'testproduction',
    );
    await expect(review).toContainText('Yasal kök parmak izi yok');
    await expect(review).toContainText('Üretim sunucusunda henüz başarılı bir damga testi yok.');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    await expect(page.getByTestId('tsa-server')).toHaveText(
      'Şu an Kamu SM üretim sunucusu kullanılıyor.',
    );

    await page.getByRole('button', { name: 'Test sunucusuna dön' }).click();
    const back = page.getByRole('dialog', { name: 'Test sunucusuna dönülsün mü?' });
    await expect(back).toContainText('Üretim damgaları durur');
    await back.getByRole('button', { name: 'Test sunucusuna dön' }).click();
    await expect(review).toContainText('Üretim zaman damgaları duracak.');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await expect(page.getByTestId('tsa-server')).toHaveText(
      'Şu an Kamu SM test sunucusu kullanılıyor.',
      { timeout: 10_000 },
    );
    const put = api.callsTo('PUT', '/admin/config/tsa');
    expect(put.at(0)?.body).toMatchObject({ server: 'production' });
    expect(put.at(-1)?.body).toMatchObject({ server: 'test', confirm: true });
  });

  test('the threshold and the time limit: checked, saved without re-authentication', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/kamusm?step=credit');
    const threshold = page.getByLabel('Kontör uyarı eşiği', { exact: true });
    await expect(threshold).toHaveValue('500');
    await threshold.fill('2000000');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('En çok 1000000 olmalı.')).toBeVisible();
    await threshold.fill('300');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Kontör uyarı eşiği/ })).toContainText('500300');
    await expect(review).not.toContainText('parolanızı yeniden');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await applied(page);
    expect(api.callsTo('POST', '/auth/reauth')).toHaveLength(0);
    expect(api.admin.sections.tsa.credit_alert_threshold).toBe(300);
    await expect(page.getByRole('button', { name: 'Kontör sorgula' })).toBeVisible();
  });

  test('first setup: a change that would stop the working sealer is refused with its causes', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', admin: { freshTsa: true } });
    await page.goto('/settings/kamusm');
    await expect(page.getByTestId('tsa-provider')).toContainText('mock');
    await expect(steps(page).getByRole('link', { name: /^1\. Java/ })).toContainText('Eksik');
    await expect(steps(page).getByRole('link', { name: /^4\. Müşteri bilgileri/ })).toContainText(
      'Eksik',
    );
    await page
      .getByLabel('C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe')
      .check();
    await openStep(page, /^4\. Müşteri bilgileri/);
    const card = page.getByTestId('kamusm-step-credentials');
    await expect(card).toContainText('Kayıtlı değil');
    await card.getByRole('button', { name: 'Gir' }).first().click();
    await page.getByLabel('Müşteri numarası').fill('100200300');
    await card.getByRole('button', { name: 'Gir' }).click();
    await page.getByLabel('Parola', { exact: true }).fill('ilk-gizli-deger'); // gitleaks:allow — fake test value
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const refusal = page.getByTestId('tsa-refusal');
    await expect(refusal).toContainText('Zamane istemcisi ayarlı değil ya da çalıştırılamıyor.');
    await expect(refusal).toContainText('Kurulu kök sertifika yok.');
    await expect(page.getByTestId('section-actions')).toContainText(
      'Bu değişiklik şu an çalışan mühürlemeyi durdururdu',
    );
    expect(api.callsTo('PUT', '/admin/config/tsa')).toHaveLength(0);

    await openStep(page, /^6\. Üretime geç/);
    await expect(page.getByRole('button', { name: 'Üretim sunucusuna geç' })).toBeDisabled();
    await expect(page.getByTestId('kamusm-step-production')).toContainText(
      'Müşteri numarası ve parola kayıtlı',
    );
  });

  test('read-only while the administration service is not running', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'missing' } });
    await page.goto('/settings/kamusm?step=credentials');
    await expect(page.getByTestId('agent-unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Değiştir' }).first()).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Kaydet' })).toBeDisabled();
    await openStep(page, /^2\. Zamane istemcisi/);
    await expect(page.getByLabel('Zamane istemcisi dosyası')).toBeDisabled();
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Kamu SM accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/kamusm');
    await expect(steps(page)).toBeVisible();
    await expectNoA11yViolations(page);
    await openStep(page, /^4\. Müşteri bilgileri/);
    await page.getByRole('button', { name: 'Değiştir' }).first().click();
    await page.getByLabel('Müşteri numarası').fill('12 34');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expectNoA11yViolations(page);
    await page.getByLabel('Müşteri numarası').fill('100200300');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' })).toBeVisible();
    await expectNoA11yViolations(page);
    await page.getByRole('button', { name: 'İptal' }).click();
    await page.getByRole('button', { name: 'Değişiklikleri bırak' }).click();
    await openStep(page, /^2\. Zamane istemcisi/);
    await upload(page, 'Zamane istemcisi dosyası', 'tss-client-console-9.9.jar', 'unreleased');
    await expect(page.getByTestId('zamane-preview')).toBeVisible();
    await expectNoA11yViolations(page);
    await openStep(page, /^3\. Kök sertifikalar/);
    await upload(page, 'Kök sertifika dosyası', 'kamusm-kok-v3.pem', PEM);
    await page.getByTestId('root-preview').getByLabel('Yasal kök olarak da işaretle').click();
    await expectNoA11yViolations(page);
    await openStep(page, /^6\. Üretime geç/);
    await page.getByRole('button', { name: 'Üretim sunucusuna geç' }).click();
    await expect(
      page.getByRole('dialog', { name: 'Üretim sunucusuna geçilsin mi?' }),
    ).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
