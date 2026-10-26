import { expect, test } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { CSRF_TOKEN, mockApi } from './mock-api';

/**
 * Ayarlar and Ayarlar → Genel (contract 2.17): the sections, the administration service's chip,
 * the site name through the whole request (check, confirmation, the request's states until
 * "Uygulandı"), the refusals and failures a section shows, and the read-only page while the
 * service is not running. The API is `mock-admin.ts`, starting from the installation the capture
 * made (`samples/admin.json`: no local time source yet).
 */

test.describe('Ayarlar', () => {
  test('the sections with the administration service chip; later sections stay listed', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await expect(page.getByRole('heading', { level: 1, name: 'Ayarlar' })).toBeVisible();
    await expect(page.getByTestId('agent-status')).toHaveText('Yönetim hizmeti: çalışıyor');
    // The installation's setup problems, as the API words them (`setup_problems`).
    await expect(page.getByTestId('config-problems')).toContainText(
      'clock.ntp_server — Yerel saat kaynağı (clock.ntp_server) ayarlanmamış',
    );
    for (const [key, href] of [
      ['general', '/settings/general'],
      ['application', '/settings/application'],
      ['kamusm', '/settings/kamusm'],
      ['sources', '/settings/sources'],
      ['email', '/settings/email'],
      ['replica', '/settings/replica'],
      ['retention', '/settings/retention'],
      ['backup', '/settings/backup'],
      ['history', '/settings/history'],
    ] as const)
      await expect(page.getByTestId(`settings-${key}`).getByRole('link')).toHaveAttribute(
        'href',
        href,
      );
    await expect(page.getByText('Güvenlik kuralları', { exact: true })).toBeVisible();
  });

  test('the chip says when the service is late or not running', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'stale' } });
    await page.goto('/settings');
    await expect(page.getByTestId('agent-status')).toHaveText('Yönetim hizmeti: gecikiyor');
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'missing' } });
    await page.goto('/settings');
    await expect(page.getByTestId('agent-status')).toHaveText('Yönetim hizmeti: çalışmıyor');
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'disabled' } });
    await page.goto('/settings');
    await expect(page.getByTestId('agent-status')).toHaveText('Yönetim hizmeti: kapalı');
  });
});

test.describe('Ayarlar → Genel', () => {
  test('changes the site name: check, confirmation, the request until it is applied', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/general');
    await expect(page.getByRole('heading', { level: 1, name: 'Genel' })).toBeVisible();
    const name = page.getByLabel('Kuruluş adı');
    await expect(name).toHaveValue('Örnek A.Ş.');
    const save = page.getByRole('button', { name: 'Kaydet' });
    await expect(save).toBeDisabled();

    await name.fill('   ');
    await save.click();
    await expect(page.getByText('Kuruluş adı boş olamaz.')).toBeVisible();
    await expect(name).toBeFocused();
    expect(api.callsTo('POST', '/admin/config/general/check')).toHaveLength(0);

    await name.fill('Örnek Holding A.Ş.');
    await save.click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    const table = review.getByRole('table', { name: 'Kaydedilecek değişiklikler' });
    await expect(table.getByRole('row', { name: /Kuruluş adı/ })).toContainText(
      'Örnek A.Ş.Örnek Holding A.Ş.',
    );
    await expect(review.getByText('Web hizmeti yeni ayarı yükler')).toBeVisible();
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await expect(review).toBeHidden();

    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Uygulandı', {
      timeout: 10_000,
    });
    await expect(progress).toContainText(/İstek no: [0-9a-f]{8}/);
    await expect(
      progress.getByRole('list', { name: 'Yeni ayarı yükleyen hizmetler' }),
    ).toContainText('ArciApi');
    await expect(page.getByText('Uygulandı', { exact: true }).last()).toBeVisible();
    await expect(name).toHaveValue('Örnek Holding A.Ş.');
    await expect(save).toBeDisabled();

    const check = api.callsTo('POST', '/admin/config/general/check');
    const put = api.callsTo('PUT', '/admin/config/general');
    expect(put).toHaveLength(1);
    expect(put[0]?.csrf).toBe(CSRF_TOKEN);
    expect(put[0]?.body).toEqual({
      etag: expect.stringMatching(/^general:[0-9a-f]{64}$/) as unknown,
      site_name: 'Örnek Holding A.Ş.',
      clock: {
        ntp_server: '',
        fallback_server: 'time.ume.tubitak.gov.tr',
        alert_offset_ms: 1000,
      },
      probe: true,
    });
    expect(check.at(-1)?.body).toEqual(put[0]?.body);
    const polls = api.calls.filter((call) => call.path.startsWith('/admin/requests/'));
    expect(polls.every((call) => call.background)).toBe(true);
    expect(api.admin.sections.general.site_name).toBe('Örnek Holding A.Ş.');
  });

  test('read-only while the administration service is not running, with the console path', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'missing' } });
    await page.goto('/settings/general');
    const banner = page.getByTestId('agent-unavailable');
    await expect(banner).toContainText('Yönetim hizmeti çalışmıyor');
    await expect(banner).toContainText('C:\\ProgramData\\ARCILOGGER\\arcilogger.toml');
    await expect(page.getByLabel('Kuruluş adı')).toBeDisabled();
    await expect(page.getByLabel('Kuruluş adı')).toHaveValue('Örnek A.Ş.');
    await expect(page.getByRole('button', { name: 'Kaydet' })).toBeDisabled();
    await expect(page.getByTestId('section-actions')).toHaveAttribute(
      'data-section-state',
      'agent_unavailable',
    );
    expect(api.callsTo('PUT', '/admin/config/general')).toHaveLength(0);

    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'disabled' } });
    await page.goto('/settings/general');
    await expect(page.getByTestId('agent-unavailable')).toContainText(
      'Tarayıcıdan sunucu yönetimi kapalı',
    );
    await expect(page.getByLabel('Kuruluş adı')).toBeDisabled();
  });

  test('a late service still takes the change, with a warning', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'stale' } });
    await page.goto('/settings/general');
    await expect(page.getByTestId('agent-stale')).toHaveText(
      'Yönetim hizmeti geç yanıt veriyor; değişiklik gecikebilir.',
    );
    await page.getByLabel('Kuruluş adı').fill('Örnek Lojistik A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Uygulandı', {
      timeout: 10_000,
    });
    await expect(progress.getByRole('list', { name: 'Uyarılar' })).toContainText(
      'Yönetim hizmeti geç yanıt veriyor',
    );
  });

  test('changed elsewhere meanwhile: the conflict is shown and the page reloads the values', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' })).toBeVisible();
    // Someone saves another name on the server while the dialog is open.
    api.admin.sections.general.site_name = 'Örnek Grup A.Ş.';
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const conflict = page.getByTestId('section-conflict');
    await expect(conflict).toContainText(
      'Bu ayarlar siz düzenlerken başka bir yerden değiştirildi; sayfayı yenileyip tekrar deneyin.',
    );
    await expect(page.getByTestId('section-actions')).toHaveAttribute(
      'data-section-state',
      'conflict',
    );
    await conflict.getByRole('button', { name: 'Sayfayı yenile' }).click();
    await expect(page.getByLabel('Kuruluş adı')).toHaveValue('Örnek Grup A.Ş.');
    await expect(page.getByTestId('section-conflict')).toHaveCount(0);
  });

  test('a failed change: the previous settings stay, "Yeniden dene" sends it again', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.nextOutcome = 'failed';
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText(
      'Uygulanmadı, önceki ayarlar geçerli',
      { timeout: 10_000 },
    );
    await expect(progress).toContainText(
      'Yazılan ayarlar doğrulanamadı; önceki ayarlar geri yüklendi.',
    );
    await expect(progress).toContainText('Yazılan değişiklik geri alındı.');
    expect(api.admin.sections.general.site_name).toBe('Örnek A.Ş.');
    await expect(page.getByLabel('Kuruluş adı')).toHaveValue('Örnek Holding A.Ş.');

    await progress.getByRole('button', { name: 'Yeniden dene' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
      'Uygulandı',
      { timeout: 10_000 },
    );
    expect(api.admin.sections.general.site_name).toBe('Örnek Holding A.Ş.');
  });

  test('manual action needed: the red banner names the version and the steps', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.nextOutcome = 'needs_attention';
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Elle müdahale gerekiyor', {
      timeout: 10_000,
    });
    await expect(progress.getByRole('alert')).toContainText(
      'Değişiklik geri alınamadı; elle müdahale gerekiyor.',
    );
    await expect(progress).toContainText('python -I -m arcilogger_core.admin versions restore 17');
  });

  test('partly applied: the service that has not taken it yet is named', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.admin.nextOutcome = 'partially_active';
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Kısmen uygulandı', {
      timeout: 10_000,
    });
    // The API reports the reloads in the order collector, ingest, worker, web service.
    await expect(progress).toContainText('ArciApi yeni ayarı henüz almadı.');
  });

  test('the time sources: checked, re-authenticated, then the system task updates Windows Time', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/general');
    const clock = page.getByTestId('settings-clock');
    await expect(clock.getByTestId('measured-offset')).toContainText('Son ölçülen sapma: 3,2 ms');
    // The locked port is named with its reason; the API does not send its value.
    await expect(clock.getByTestId('locked-value')).toContainText('clock.ntp_port (uzman ayarı)');
    await expect(clock.getByTestId('locked-value')).not.toContainText('123');
    const ntp = page.getByLabel('Yerel saat kaynağı');
    const fallback = page.getByLabel('Yedek saat sunucusu');
    const save = page.getByRole('button', { name: 'Kaydet' });
    await expect(ntp).toHaveValue('');
    await ntp.fill('ntp_sunucu');
    await save.click();
    await expect(clock).toContainText('Geçerli bir sunucu adı ya da IPv4 adresi girin.');
    await expect(ntp).toBeFocused();
    await ntp.fill('');
    await fallback.fill('');
    await save.click();
    await expect(clock).toContainText('En az bir saat sunucusu gerekir');
    await fallback.fill('time.ume.tubitak.gov.tr');
    await expect(clock).toContainText('Yerel saat kaynağı boş');
    await page.getByLabel('Saat sapması uyarı eşiği (ms)').fill('70000');
    await save.click();
    await expect(clock).toContainText('En çok 60000 olmalı.');
    expect(api.callsTo('POST', '/admin/config/general/check')).toHaveLength(0);

    await page.getByLabel('Saat sapması uyarı eşiği (ms)').fill('1500');
    await ntp.fill('172.20.10.2');
    await expect(clock).toContainText(
      'Saat ayarlarının değişikliği için parolanız yeniden sorulur.',
    );
    await save.click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Yerel saat kaynağı/ })).toContainText(
      '—172.20.10.2',
    );
    await expect(review).toContainText('Windows saat hizmeti güncellenir');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
      'Uygulandı',
      { timeout: 10_000 },
    );
    expect(api.admin.sections.general.clock).toEqual({
      ntp_server: '172.20.10.2',
      fallback_server: 'time.ume.tubitak.gov.tr',
      alert_offset_ms: 1500,
    });
    // A local time source now: no setup problem is left.
    expect(api.admin.config().problems).toEqual([]);
    expect(api.callsTo('PUT', '/admin/config/general').at(-1)?.body).toMatchObject({
      probe: true,
    });
  });

  test('the new time server could not be reached: nothing changed', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    api.admin.nextOutcome = 'probe_failed';
    await page.goto('/settings/general');
    await page.getByLabel('Yerel saat kaynağı').fill('172.20.10.3');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
      'Bağlantı denemesi başarısız, hiçbir şey değişmedi',
      { timeout: 10_000 },
    );
    expect(api.admin.sections.general.clock.ntp_server).toBe('');
  });

  test('leaving with unsaved changes asks first', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('link', { name: 'Ayarlar' }).first().click();
    const guard = page.getByRole('dialog', { name: 'Kaydedilmemiş değişiklikler' });
    await expect(guard).toBeVisible();
    await guard.getByRole('button', { name: 'İptal' }).click();
    await expect(page).toHaveURL(/\/settings\/general$/);
    await expect(page.getByLabel('Kuruluş adı')).toHaveValue('Örnek Holding A.Ş.');
    await page.getByRole('link', { name: 'Ayarlar' }).first().click();
    await page.getByRole('dialog').getByRole('button', { name: 'Sayfadan ayrıl' }).click();
    await expect(page).toHaveURL(/\/settings$/);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Ayarlar and Genel accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await expect(page.getByTestId('agent-status')).toBeVisible();
    await expectNoA11yViolations(page);
    await page.goto('/settings/general');
    await page.getByLabel('Kuruluş adı').fill('');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('Kuruluş adı boş olamaz.')).toBeVisible();
    await expectNoA11yViolations(page);
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' })).toBeVisible();
    await expectNoA11yViolations(page);
    api.admin.nextOutcome = 'needs_attention';
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await expect(page.getByTestId('request-state')).toHaveText('Elle müdahale gerekiyor', {
      timeout: 10_000,
    });
    await expectNoA11yViolations(page);
    await mockApi(page, { signedInAs: 'tolga', admin: { agent: 'missing' } });
    await page.goto('/settings/general');
    await expect(page.getByTestId('agent-unavailable')).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
