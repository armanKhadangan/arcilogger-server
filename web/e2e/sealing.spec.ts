import { expect, test, type Page } from '@playwright/test';

import { useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { SAMPLE_TODAY, SEALS } from './samples';

const DETAIL = SEALS.days['2026-10-04'];
const FAILED = SEALS.calendars['2026-10']?.days.find((cell) => cell.day === '2026-10-11');
const SEAL_NOW = SEALS.errors.seal_now_unavailable?.body.error;
if (!DETAIL?.token || !DETAIL.manifest || !FAILED?.message_tr || !SEAL_NOW)
  throw new Error('seal samples missing');

function day(page: Page, name: RegExp) {
  return page.getByRole('table', { name: 'Mühür takvimi' }).getByRole('button', { name });
}

test.describe('Mühürleme', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('calendar states with labels, the latest day open by default', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing');
    await expect(page.getByRole('heading', { level: 1, name: 'Mühürleme' })).toBeVisible();
    await expect(page).toHaveURL(/day=2026-10-12/);
    await expect(page.getByRole('heading', { name: '12.10.2026' })).toBeVisible();
    await expect(day(page, /^4 Ekim 2026: Mühürlendi ve doğrulandı$/)).toBeVisible();
    await expect(day(page, /^6 Ekim 2026: Geç mühürlendi — /)).toBeVisible();
    await expect(
      day(page, /^7 Ekim 2026: Mühürlendi, yakın zamanda doğrulanmadı — Son 35 günde/),
    ).toBeVisible();
    await expect(day(page, /^12 Ekim 2026: Bekliyor$/)).toHaveAttribute('aria-pressed', 'true');
    await expect(day(page, /^20 Ekim 2026/)).toBeDisabled();
    const legend = page.getByRole('list', { name: 'Renk açıklaması' });
    for (const label of ['Mühürlendi ve doğrulandı', 'Geç mühürlendi', 'Başarısız', 'Veri yok'])
      await expect(legend.getByText(label)).toBeVisible();
    // Not sealed yet: nothing to verify or download.
    await expect(page.getByRole('button', { name: 'Doğrula', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Manifesti indir' })).toBeDisabled();

    await day(page, /^11 Ekim 2026: Başarısız/).click();
    await expect(page).toHaveURL(/day=2026-10-11/);
    await expect(page.getByText(FAILED.message_tr ?? '-')).toBeVisible();
    // "Son hata" in Turkish (spec 12.4); the core's English text only as the technical detail.
    // The samples come from the mock TSA, which is never called Kamu SM.
    await expect(
      page.getByText('Zaman damgası sunucusuna ulaşılamadı.', { exact: true }),
    ).toBeVisible();
    await expect(page.getByText('Teknik ayrıntı', { exact: true })).toBeVisible();
    await expect(page.getByText('mock TSA unreachable (injected)', { exact: true })).toBeVisible();
    // The core's task state in Turkish, not "pending".
    await expect(page.getByText('Mühürlenmedi; sırada', { exact: true })).toBeVisible();
    await expect(page.getByText('pending', { exact: true })).toHaveCount(0);
  });

  test("a day's gaps and task state are shown in Turkish; the manifest text stays as a tooltip", async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing?day=2026-10-09');
    const gaps = page.getByRole('heading', { name: 'Kesintiler' }).locator('..');
    const reason = gaps.getByText('(60 dakikadan uzun süre kayıt gelmedi)').first();
    await expect(reason).toBeVisible();
    await expect(reason).toHaveAttribute('title', 'no messages > 60 min');
    await expect(gaps.getByText(/no messages/)).toHaveCount(0);
    await expect(page.getByText('Mühürlendi', { exact: true })).toBeVisible();
    await expect(page.getByText('sealed', { exact: true })).toHaveCount(0);
  });

  test('a sealed day: manifest, token, files, red banner for the mock TSA, verify, downloads', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing?day=2026-10-04');
    await expect(page.getByText('04.10.2026 günü mühürlendi ve doğrulandı.')).toBeVisible();
    await expect(page.getByTestId('not-legal-grade')).toContainText('mock');
    await expect(page.getByText(DETAIL.manifest?.sha256 ?? '-')).toBeVisible();
    await expect(page.getByText(DETAIL.token?.serial ?? '-', { exact: true })).toBeVisible();
    await expect(page.getByText('Belirteç doğrulandı')).toBeVisible();
    await expect(page.getByText('Yasal geçerli değil', { exact: true })).toBeVisible();
    const files = page.getByRole('table', { name: '04.10.2026 gününün mühürlü dosyaları' });
    await expect(files.getByRole('row')).toHaveCount(4);
    await expect(files.getByRole('rowheader', { name: /fortigate/ })).toBeVisible();
    await expect(page.getByText('Kopyalandı ve doğrulandı')).toBeVisible();

    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
    await expect(page.getByTestId('verify-result')).toContainText('04.10.2026 günü doğrulandı.');
    expect(api.callsTo('POST', '/seals/2026-10-04/verify').at(-1)?.query.get('deep')).toBe('true');
    await page.getByLabel('Derin doğrulama (dosyalar açılıp yeniden özetlenir)').uncheck();
    // Keyboard: the focus stays on "Doğrula" while it runs and afterwards (busy, not disabled).
    const verify = page.getByRole('button', { name: 'Doğrula', exact: true });
    await verify.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('verify-result')).toContainText('hızlı');
    await expect(verify).toBeFocused();
    expect(api.callsTo('POST', '/seals/2026-10-04/verify').at(-1)?.query.get('deep')).toBe('false');

    const manifestButton = page.getByRole('button', { name: 'Manifesti indir' });
    await manifestButton.focus();
    const manifest = page.waitForEvent('download');
    await page.keyboard.press('Enter');
    expect((await manifest).suggestedFilename()).toBe('manifest-2026-10-04.json');
    await expect(manifestButton).toBeFocused();
    const token = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Belirteci indir' }).click();
    expect((await token).suggestedFilename()).toBe('manifest-2026-10-04.json.zd');
  });

  test('a failed verification is shown in red with its errors', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    api.records.nextVerify = 'failed';
    await page.goto('/sealing?day=2026-10-08');
    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
    const result = page.getByTestId('verify-result');
    await expect(result).toContainText('08.10.2026 günü doğrulanamadı');
    await expect(result).toContainText('SHA-256 özeti tutmuyor (dosya değişmiş)');
    await expect(result).not.toContainText('mismatch');
  });

  test('"Şimdi mühürle" is disabled with the reason the API gives; auditors do not see it', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing?day=2026-10-12');
    const sealNow = page.getByRole('button', { name: 'Şimdi mühürle' });
    await expect(sealNow).toBeDisabled();
    await expect(page.getByText(SEAL_NOW.message_tr)).toBeVisible();
    await expect(sealNow).toHaveAccessibleDescription(SEAL_NOW.message_tr);

    await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/sealing?day=2026-10-04');
    await expect(page.getByRole('button', { name: 'Doğrula', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Şimdi mühürle' })).toHaveCount(0);
  });

  test('month navigation and the credit widget', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing');
    await expect(page.getByText('9.612', { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/Sağlayıcı: zamane/)).toBeVisible();
    await expect(page.getByRole('img', { name: /Kalan kontör/ })).toBeVisible();
    await expect(page.getByTestId('provider-not-legal')).toHaveCount(0);
    await page.getByRole('button', { name: 'Önceki ay' }).click();
    await expect(page).toHaveURL(/month=2026-09/);
    await expect(page.getByRole('heading', { name: 'Eylül 2026' })).toBeVisible();
    await expect(page.getByText('Takvimden bir gün seçin')).toBeVisible();
  });

  test('a test or mock time-stamp provider is reported with a red banner; low credit warns', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga', records: { credits: 'mock' } });
    await page.goto('/sealing');
    await expect(page.getByTestId('provider-not-legal')).toContainText(
      'Yapılandırılan zaman damgası sağlayıcısı (mock) Kamu SM üretim sunucusu değil',
    );
    await mockApi(page, { signedInAs: 'tolga', records: { credits: 'kamusm_low' } });
    await page.goto('/sealing');
    await expect(page.getByText('Eşiğin altında')).toBeVisible();
    await expect(page.getByText(/uyarı eşiğinin \(10\.000\) altında/)).toBeVisible();
  });

  test('refreshes in the background without counting as activity', async ({ page }) => {
    await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/sealing');
    await expect(page.getByRole('heading', { name: '12.10.2026' })).toBeVisible();
    const before = api.callsTo('GET', '/seals/calendar').length;
    await page.clock.fastForward('01:05');
    await expect.poll(() => api.callsTo('GET', '/seals/calendar').length).toBeGreaterThan(before);
    const calls = api.callsTo('GET', '/seals/calendar');
    expect(calls[0]?.background).toBe(false);
    expect(calls.at(-1)?.background).toBe(true);
  });
});
