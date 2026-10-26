import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Saklama, Replika and Yedekleme (contract 2.17): the retention floor, the preview of
 * the days to delete with the number typed to confirm, the held change with its countdown and
 * "İptal et" (also after the page was left), the replica with its connection test and the
 * password rebinding, and the backup retention. The API is `mock-admin.ts`.
 */

async function applied(page: Page) {
  await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
    'Uygulandı',
    { timeout: 10_000 },
  );
}

test.describe('Ayarlar → Saklama', () => {
  test('switching retention on: the floor, the preview and the number typed', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings');
    await page.getByTestId('settings-retention').getByRole('link').click();
    await expect(page.getByTestId('retention-current')).toHaveText(
      'Saklama kapalı: hiçbir gün silinmez.',
    );
    await page.getByLabel('Süresi dolan günleri sil').click();
    await expect(page.getByTestId('retention-deletes')).toBeVisible();
    const days = page.getByLabel('Saklama süresi (gün)');
    await days.fill('700');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('En az 730 olmalı.')).toBeVisible();
    await expect(days).toBeFocused();
    expect(api.callsTo('POST', '/admin/config/retention/check')).toHaveLength(0);

    await days.fill('760');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    const preview = review.getByTestId('retention-preview');
    await expect(preview).toContainText('gün silinecek');
    await expect(preview).toContainText('İlk silme:');
    const count = api.admin.config().sections.retention.preview.days_to_delete;
    expect(count).toBeGreaterThan(0);
    const confirm = review.getByRole('button', { name: 'Onayla ve kaydet' });
    await expect(confirm).toBeDisabled();
    const typed = review.getByLabel(/Onaylamak için silinecek gün sayısını yazın/);
    await typed.fill(String(count + 1));
    await expect(confirm).toBeDisabled();
    await typed.fill(String(count));
    await confirm.click();
    await confirmReauth(page);
    await applied(page);
    expect(api.callsTo('PUT', '/admin/config/retention').at(-1)?.body).toEqual({
      etag: expect.stringMatching(/^retention:/) as unknown,
      enabled: true,
      days: 760,
      confirm_delete_days: count,
    });
    await expect(page.getByTestId('retention-current')).toContainText(
      'Saklama açık: 760 günden eski günler silinir.',
    );
  });

  test('a held change: countdown, still shown after leaving, cancelled', async ({ page }) => {
    const api = await mockApi(page, {
      signedInAs: 'tolga',
      staleReauth: true,
      admin: { holdHours: 24 },
    });
    await page.goto('/settings/retention');
    await page.getByLabel('Süresi dolan günleri sil').click();
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByTestId('review-held')).toBeVisible();
    const count = Number(
      /(\d+)$/.exec(
        (await review.getByText(/Onaylamak için silinecek gün sayısını yazın/).textContent()) ?? '',
      )?.[1],
    );
    await review.getByLabel(/Onaylamak için silinecek gün sayısını yazın/).fill(String(count));
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Bekletiliyor', {
      timeout: 10_000,
    });
    await expect(progress.getByTestId('hold-countdown')).toContainText('kalan');
    expect(api.admin.sections.retention.enabled).toBe(false);

    await page.getByRole('link', { name: 'Ayarlar' }).first().click();
    await page.getByTestId('settings-retention').getByRole('link').click();
    const held = page.getByTestId('held-change');
    await expect(held.getByTestId('request-state')).toHaveText('Bekletiliyor');
    await held.getByRole('button', { name: 'İptal et' }).click();
    await expect(held).toHaveCount(0, { timeout: 10_000 });
    expect(
      api.calls.some(
        (call) => call.method === 'DELETE' && call.path.startsWith('/admin/requests/'),
      ),
    ).toBe(true);
    expect(api.admin.sections.retention.enabled).toBe(false);
  });
});

test.describe('Ayarlar → Replika', () => {
  test('the connection test, limited to one a minute', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/replica');
    await expect(page.getByLabel('Paylaşım')).toHaveValue('\\\\NAS-01\\arcilogger-worm');
    const test = page.getByTestId('settings-replica-test');
    await test.getByRole('button', { name: 'Bağlantıyı test et' }).click();
    await expect(test.getByTestId('replica-test-result')).toContainText(
      'Replika hedefine bağlanıldı',
      { timeout: 10_000 },
    );
    await test.getByRole('button', { name: 'Bağlantıyı test et' }).click();
    await expect(test.getByRole('alert')).toContainText('saniye');
  });

  test('a new server needs the password again; the change is tried first', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/replica');
    const card = page.getByTestId('settings-replica');
    await card.getByLabel('Paylaşım').fill('C:\\yedek');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(card).toContainText('Paylaşım \\\\sunucu\\paylaşım biçiminde olmalı');
    await card.getByLabel('Paylaşım').fill('\\\\yedek-sunucu\\arcilogger-worm');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(card).toContainText('kayıtlı parola yeni hedefe gönderilmez');
    await card.getByRole('button', { name: 'Değiştir' }).click();
    await card.getByLabel('Parola', { exact: true }).fill('replika-degeri'); // gitleaks:allow — fake test value
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Replika hedefi/ })).toContainText('yedek-sunucu');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    expect(api.callsTo('PUT', '/admin/config/replica').at(-1)?.body).toMatchObject({
      target: '\\\\yedek-sunucu\\arcilogger-worm',
      password: 'replika-degeri', // gitleaks:allow — fake test value
      probe: true,
    });
    expect(api.admin.sections.replica.target).toBe('\\\\yedek-sunucu\\arcilogger-worm');
  });

  test('switching the replica off is held and can be cancelled', async ({ page }) => {
    const api = await mockApi(page, {
      signedInAs: 'tolga',
      staleReauth: true,
      admin: { holdHours: 24 },
    });
    await page.goto('/settings/replica');
    await page.getByLabel('Replika açık').click();
    await expect(page.getByTestId('replica-held')).toContainText('24 saat bekletilir');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    const progress = page.getByTestId('request-progress');
    await expect(progress.getByTestId('request-state')).toHaveText('Bekletiliyor', {
      timeout: 10_000,
    });
    await progress.getByRole('button', { name: 'İptal et' }).click();
    await expect(progress.getByTestId('request-state')).toHaveText('İptal edildi', {
      timeout: 10_000,
    });
    expect(api.admin.sections.replica.enabled).toBe(true);
  });
});

test.describe('Ayarlar → Yedekleme', () => {
  test('the backup retention: ranges, a confirmation, the backup alerts', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/backup');
    const card = page.getByTestId('settings-backup');
    await expect(card.getByLabel('Günlük yedek')).toHaveValue('14');
    await expect(card.getByTestId('locked-paths')).toContainText('backup.dir');
    await card.getByLabel('Günlük yedek').fill('0');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(card).toContainText('En az 1 olmalı.');
    await card.getByLabel('Günlük yedek').fill('7');
    await card.getByLabel('Veritabanı yedeklemesi açık').click();
    await expect(card).toContainText('veritabanının yeni yedeği alınmaz');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    const review = page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' });
    await expect(review.getByRole('row', { name: /Saklanan günlük yedek/ })).toContainText('147');
    await review.getByRole('button', { name: 'Onayla ve kaydet' }).click();
    await confirmReauth(page);
    await applied(page);
    expect(api.admin.sections.backup).toMatchObject({ enabled: false, keep_daily: 7 });
    await expect(page.getByTestId('backup-state')).toBeVisible();
    const alerts = api.callsTo('GET', '/alerts').at(-1);
    expect(alerts?.query.get('rule')).toBe('R20-backup');
    expect(alerts?.query.get('state')).toBe('open');
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Saklama, Replika, Yedekleme accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga', admin: { holdHours: 24 } });
    await page.goto('/settings/retention');
    await page.getByLabel('Süresi dolan günleri sil').click();
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByRole('dialog', { name: 'Değişiklikleri onaylayın' })).toBeVisible();
    await expectNoA11yViolations(page);
    await page.getByRole('button', { name: 'İptal' }).click();
    await page.goto('/settings/replica');
    await page.getByLabel('Paylaşım').fill('C:\\yedek');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByTestId('settings-replica')).toContainText('biçiminde olmalı');
    await expectNoA11yViolations(page);
    await page.goto('/settings/backup');
    await expect(page.getByTestId('backup-state')).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
