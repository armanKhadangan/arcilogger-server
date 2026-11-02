import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import {
  LEGAL_MIXED,
  LEGAL_OK,
  LEGAL_PROVIDER_NOT_LEGAL,
  LEGAL_REASONS,
  LEGAL_WARNING_ONLY,
  legalStatusWith,
  mockLegal,
} from './mock-legal';
import { mockW4 } from './mock-w4';
import { SAMPLE_TODAY } from './samples';

/**
 * The legal-validity banner (Wave 5 contract 2.3.2, 3 W5-D) on the hand-built answers of
 * `mock-legal.ts`: the danger banner of the mock provider on every page the auditor opens (also
 * a refused page and an unknown path, which are drawn in the same shell), the 24-hour pause of a
 * warning across a reload, "ve N uyarı daha", the 5-minute refresh in the background, and axe in
 * light and dark on a danger and a warning banner with the list open.
 */

/** The auditor's pages (the menu of `permissions.spec.ts`) and their headings. */
const AUDITOR_PAGES: [string, string][] = [
  ['/', 'Panel'],
  ['/live', 'Canlı Akış'],
  ['/search', 'Arama'],
  ['/lookup', '5651 Sorgu'],
  ['/usage', 'Kullanım'],
  ['/sealing', 'Mühürleme'],
  ['/evidence', 'Kanıt Paketleri'],
  ['/compliance', 'Uyum Durumu'],
  ['/audit', 'Denetim Kaydı'],
];

const HIDE_KEY = 'arci.legalBanner.hidden';
const HOUR = 60 * 60_000;

const banner = (page: Page) => page.getByTestId('legal-banner');

/** The browser's clock as `useSampleClock` fixes it (15:00 at the site on the sample day). */
const SAMPLE_NOW = Date.parse(`${SAMPLE_TODAY}T15:00:00+03:00`);

/** Store a pause of the banner in the browser before the app starts. */
async function preloadHide(page: Page, hide: { until: number; codes: string[] }): Promise<void> {
  await page.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value);
    },
    [HIDE_KEY, JSON.stringify(hide)] as const,
  );
}

test.describe('legal-validity banner: the mock provider on every page', () => {
  async function asAuditor(page: Page) {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await mockW4(page, api);
    await mockLegal(page, api, { status: LEGAL_PROVIDER_NOT_LEGAL });
    return api;
  }

  async function expectDanger(page: Page) {
    const shown = banner(page).getByTestId('legal-banner-provider_not_legal');
    await expect(shown).toBeVisible();
    await expect(shown).toHaveAttribute('role', 'alert');
    await expect(shown).toContainText(LEGAL_REASONS.provider_not_legal.message_tr);
    await expect(shown).toContainText('(ZAMAN-DAMGASI.md bölüm 5.2)');
    // Danger cannot be closed; the auditor may not open Ayarlar → Zaman damgası, so no link.
    await expect(shown.getByRole('button')).toHaveCount(0);
    await expect(shown.getByRole('link')).toHaveCount(0);
  }

  for (const [path, heading] of AUDITOR_PAGES) {
    test(`${path} (${heading})`, async ({ page }) => {
      await asAuditor(page);
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
      await expectDanger(page);
    });
  }

  test('a page the auditor may not open, and an unknown path', async ({ page }) => {
    await asAuditor(page);
    await page.goto('/sources');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
    await expectDanger(page);
    await page.goto('/does-not-exist');
    await expect(page.getByText('Sayfa bulunamadı')).toBeVisible();
    await expectDanger(page);
  });

  test('the administrator gets the link to Ayarlar → Zaman damgası', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockLegal(page, api, { status: LEGAL_PROVIDER_NOT_LEGAL });
    await page.goto('/usage');
    const link = banner(page).getByRole('link', { name: 'Ayarlar → Zaman damgası' });
    await expect(link).toHaveAttribute('href', '/settings/kamusm');
    await link.click();
    await expect(page).toHaveURL(/\/settings\/kamusm$/);
    await expect(banner(page).getByTestId('legal-banner-provider_not_legal')).toBeVisible();
  });

  test('Kamu SM production with nothing to report: no banner', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await mockLegal(page, api, { status: LEGAL_OK });
    await page.goto('/usage');
    await expect(page.getByRole('heading', { level: 1, name: 'Kullanım' })).toBeVisible();
    await expect.poll(() => api.callsTo('GET', '/legal-status').length).toBeGreaterThan(0);
    await expect(banner(page)).toHaveCount(0);
  });
});

test.describe('legal-validity banner: warnings', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('a warning is hidden for 24 hours in this browser, also after a reload', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await mockLegal(page, api, { status: LEGAL_WARNING_ONLY });
    await page.goto('/usage');
    const shown = banner(page).getByTestId('legal-banner-crl_stale');
    await expect(shown).toHaveAttribute('role', 'status');
    await expect(shown).toContainText('(ZAMAN-DAMGASI.md bölüm 5.9)');
    await shown.getByRole('button', { name: '24 saat gizle' }).click();
    await expect(banner(page)).toHaveCount(0);
    await expect(
      page.getByText(
        'Uyarı bu tarayıcıda 24 saat gizlendi; yeni bir uyarı çıkarsa yeniden gösterilir.',
        { exact: true },
      ),
    ).toBeVisible();
    expect(
      JSON.parse((await page.evaluate((key) => localStorage.getItem(key), HIDE_KEY)) ?? 'null'),
    ).toEqual({ until: SAMPLE_NOW + 24 * HOUR, codes: ['crl_stale', 'renewal_due'] });

    await page.reload();
    await expect(page.getByRole('heading', { level: 1, name: 'Kullanım' })).toBeVisible();
    await expect.poll(() => api.callsTo('GET', '/legal-status').length).toBeGreaterThan(1);
    await expect(banner(page)).toHaveCount(0);
  });

  test('a pause that has ended shows the banner again', async ({ page }) => {
    await preloadHide(page, { until: SAMPLE_NOW - 1, codes: ['crl_stale', 'renewal_due'] });
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await mockLegal(page, api, { status: LEGAL_WARNING_ONLY });
    await page.goto('/usage');
    await expect(banner(page).getByTestId('legal-banner-crl_stale')).toBeVisible();
  });

  test('a new reason, or danger, ends the pause', async ({ page }) => {
    await preloadHide(page, {
      until: SAMPLE_NOW + 12 * HOUR,
      codes: ['crl_stale', 'renewal_due'],
    });
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    const legal = await mockLegal(page, api, { status: LEGAL_WARNING_ONLY });
    await page.goto('/usage');
    await expect.poll(() => api.callsTo('GET', '/legal-status').length).toBeGreaterThan(0);
    await expect(banner(page)).toHaveCount(0);

    legal.status = legalStatusWith(['crl_stale', 'renewal_due', 'renewal_uncovered']);
    await page.reload();
    await expect(banner(page).getByTestId('legal-banner-crl_stale')).toHaveAttribute(
      'role',
      'status',
    );

    legal.status = LEGAL_MIXED;
    await page.reload();
    const shown = banner(page).getByTestId('legal-banner-legal_root_missing');
    await expect(shown).toHaveAttribute('role', 'alert');
    await expect(shown.getByRole('button', { name: '24 saat gizle' })).toHaveCount(0);
  });

  test('"ve N uyarı daha" opens the other reasons', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await mockLegal(page, api, { status: LEGAL_MIXED });
    await page.goto('/usage');
    const shown = banner(page).getByTestId('legal-banner-legal_root_missing');
    const more = shown.getByRole('button', { name: 've 3 uyarı daha' });
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    const list = shown.getByRole('list', { name: 'Diğer yasal geçerlilik uyarıları' });
    await expect(list).toBeHidden();
    await more.click();
    await expect(more).toHaveAttribute('aria-expanded', 'true');
    await expect(list.getByRole('listitem')).toHaveText([
      `${LEGAL_REASONS.unstamped_days.message_tr} (ZAMAN-DAMGASI.md bölüm 5.5) Mühürleme sayfası`,
      `${LEGAL_REASONS.crl_stale.message_tr} (ZAMAN-DAMGASI.md bölüm 5.9)`,
      `${LEGAL_REASONS.renewal_due.message_tr} (ZAMAN-DAMGASI.md bölüm 5.6) Mühürleme sayfası`,
    ]);
    await list.getByRole('link', { name: 'Mühürleme sayfası' }).first().click();
    await expect(page.getByRole('heading', { level: 1, name: 'Mühürleme' })).toBeVisible();
    await more.click();
    await expect(list).toBeHidden();
  });
});

test('refreshes every 5 minutes in the background without counting as activity', async ({
  page,
}) => {
  await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
  const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
  const legal = await mockLegal(page, api, { status: LEGAL_WARNING_ONLY });
  await page.goto('/usage');
  await expect(banner(page).getByTestId('legal-banner-crl_stale')).toBeVisible();
  const before = api.callsTo('GET', '/legal-status').length;
  legal.status = LEGAL_PROVIDER_NOT_LEGAL;
  await page.clock.fastForward('05:01');
  await expect.poll(() => api.callsTo('GET', '/legal-status').length).toBeGreaterThan(before);
  await expect(banner(page).getByTestId('legal-banner-provider_not_legal')).toBeVisible();
  const calls = api.callsTo('GET', '/legal-status');
  expect(calls[0]?.background).toBe(false);
  expect(calls.at(-1)?.background).toBe(true);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`legal-validity banner accessibility — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      test.setTimeout(90_000);
      await useSampleClock(page, SAMPLE_TODAY);
      await usePreferences(page, { theme });
    });

    test('danger with the list open, as the administrator (every link)', async ({ page }) => {
      const api = await mockApi(page, { signedInAs: 'tolga' });
      await mockW4(page, api);
      await mockLegal(page, api, { status: LEGAL_MIXED });
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
      const shown = banner(page).getByTestId('legal-banner-legal_root_missing');
      await shown.getByRole('button', { name: 've 3 uyarı daha' }).click();
      await expect(shown.getByRole('listitem')).toHaveCount(3);
      await expect(shown.getByRole('link')).toHaveCount(3);
      await expectNoA11yViolations(page);
    });

    test('a warning with the list open', async ({ page }) => {
      const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
      await mockW4(page, api);
      await mockLegal(page, api, { status: LEGAL_WARNING_ONLY });
      await page.goto('/');
      await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
      const shown = banner(page).getByTestId('legal-banner-crl_stale');
      await shown.getByRole('button', { name: 've 1 uyarı daha' }).click();
      await expect(shown.getByRole('listitem')).toHaveCount(1);
      await expect(shown.getByRole('button', { name: '24 saat gizle' })).toBeVisible();
      await expectNoA11yViolations(page);
    });
  });
}
