import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import {
  ARCHIVE_ERRORS,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_SEARCH_ARCHIVED,
  ARCHIVE_SEARCH_REQUIRED,
  mockArchive,
  type ArchiveOptions,
} from './mock-archive';
import { mockApi, type MockApi } from './mock-api';
import { mockSearch } from './mock-search';
import { SAMPLE_TODAY } from './samples';

/**
 * "Günler mühürlü arşivde" (Wave 5 contract §5, G7 to G10) on the answers of `mock-archive.ts`
 * (W5-G's captured cases): a lookup and a search whose days are outside the database window get
 * the card instead of a failure; "Arşivden yükle" asks after the confirmation, the card follows the
 * reload (the first read is the user's, the polls are background requests) and, once it is
 * ready, the lookup or search runs again — once, with the same body. A refusal stays in the dialog
 * in the API's words, a failed reload has the web's text and is offered again; axe in light and
 * dark with the dialog open and with the progress.
 */

/** The browser's clock at 15:00 site time on the sample day (`useSampleClock`). */
const SAMPLE_NOW = new Date(`${SAMPLE_TODAY}T15:00:00+03:00`);

/** The days of the captured 409 and `archived` range, as the card shows them. */
const DAYS = '04.10.2026 – 05.10.2026';
/** The confirmation after a 409: it names the captured `keep_days` (7, C23). */
const DIALOG_TEXT =
  `${DAYS} günlerinin kayıtları mühürlü arşivden veritabanına yüklenir ve 7 gün boyunca ` +
  'veritabanında kalır; sonra yeniden kaldırılır. Bu, o günlerin kişisel verilerini de ' +
  'veritabanına geri getirir. İstek denetim kaydına yazılır.';
/** The confirmation for a search page's `archived` days, which give no keep time: the setting. */
const DIALOG_SETTING_TEXT =
  `${DAYS} günlerinin kayıtları mühürlü arşivden veritabanına yüklenir ve db.reload_keep_days ` +
  'ayarının süresi boyunca (varsayılan 7 gün) orada kalır; sonra yeniden kaldırılır. Bu, o ' +
  'günlerin kişisel verilerini de veritabanına geri getirir. İstek denetim kaydına yazılır.';
/** `archive_file_unreadable` of a failed reload: the web's text (the API sends none). */
const UNREADABLE =
  'Yükleme yapılamadı: bir günün mühürlü dosyası okunamadı veya mühür kaydıyla eşleşmiyor. ' +
  'Sistem yöneticisine bildirin; arşiv doğrulamasını çalıştırın.';
/** The request body of the captured days (the card sends no `lookup_id`: G7 has none yet). */
const RELOAD_DAYS = { first_day: '2026-10-04', last_day: '2026-10-05' };

const card = (page: Page) => page.getByRole('region', { name: 'Günler mühürlü arşivde' });
const dialog = (page: Page) => page.getByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });

/** The lookup page with a request whose records are of 05.10.2026 (outside the window). */
async function openLookup(page: Page, options: ArchiveOptions = {}) {
  await page.clock.setFixedTime(SAMPLE_NOW);
  const api = await mockApi(page, { signedInAs: 'tolga' });
  const archive = await mockArchive(page, api, { lookup: true, ...options });
  await page.goto('/lookup');
  await page.getByLabel('Makam').fill('İzmir Cumhuriyet Başsavcılığı');
  await page.getByLabel('Dosya / evrak no').fill('2026/48213 Soruşturma');
  await page.getByLabel('Talep tarihi').fill('10.10.2026');
  await page.getByLabel('Gerekçe').fill('5651 kapsamında IP bilgisi talebi');
  await page.getByLabel('Genel / WAN IP').fill('100.64.12.9');
  await page.getByLabel('Tarih', { exact: true }).fill('05.10.2026');
  await page.getByLabel('Saat', { exact: true }).fill('11:23:41');
  await page.getByRole('button', { name: 'Sorgula' }).click();
  await expect(card(page)).toBeVisible();
  return { api, archive };
}

/** "Arşivden yükle" (or "Yeniden dene") and "Yükle" in the confirmation, which says `text`. */
async function reload(page: Page, button = 'Arşivden yükle', text = DIALOG_TEXT) {
  await card(page).getByRole('button', { name: button }).click();
  await expect(dialog(page)).toContainText(text);
  await dialog(page).getByRole('button', { name: 'Yükle', exact: true }).click();
}

/** The search page on a range of `from` … `to`, running `type:web`. */
async function runSearch(
  page: Page,
  range: { from: string; to: string },
  mode: 'required' | 'archived',
) {
  await page.clock.setFixedTime(SAMPLE_NOW);
  const api = await mockApi(page, { signedInAs: 'tolga' });
  const search = await mockSearch(page, api);
  const archive = await mockArchive(page, api, { search: mode, searchMock: search });
  await page.goto(`/search?${new URLSearchParams(range).toString()}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Arama' })).toBeVisible();
  const box = page.getByRole('combobox', { name: 'Sorgu' });
  await box.fill('type:web');
  await box.press('Enter');
  await expect(card(page)).toBeVisible();
  return { api, archive };
}

function bodies(api: MockApi, path: string): unknown[] {
  return api.callsTo('POST', path).map((call) => call.body);
}

test.describe('archive reload', () => {
  test('a lookup of archived days: the card, the reload, its progress and the same lookup once more', async ({
    page,
  }) => {
    // The reload stays loading until the test lets it be ready.
    expect(ARCHIVE_LOOKUP_REQUIRED.body.error.details.keep_days).toBe(7);
    const { api, archive } = await openLookup(page, { steps: ['loading'] });
    await expect(card(page)).toContainText(ARCHIVE_LOOKUP_REQUIRED.body.error.message_tr);
    // The 409 is the card, not a failure: its sentence shows once, nothing else is red.
    await expect(page.getByText(ARCHIVE_LOOKUP_REQUIRED.body.error.message_tr)).toHaveCount(1);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(card(page).getByRole('status')).toContainText(
      ARCHIVE_LOOKUP_REQUIRED.body.error.message_tr,
    );

    // Nothing is asked for before the confirmation; "İptal" asks for nothing either.
    await card(page).getByRole('button', { name: 'Arşivden yükle' }).click();
    await expect(dialog(page)).toContainText(DIALOG_TEXT);
    await dialog(page).getByRole('button', { name: 'İptal' }).click();
    await expect(dialog(page)).toBeHidden();
    expect(api.callsTo('POST', '/archive-reloads')).toHaveLength(0);

    await reload(page);
    await expect(dialog(page)).toBeHidden();
    expect(bodies(api, '/archive-reloads')).toEqual([{ reason: 'lookup', ...RELOAD_DAYS }]);
    await expect(card(page).getByRole('button', { name: 'Arşivden yükle' })).toHaveCount(0);
    const bar = card(page).getByRole('progressbar', { name: 'Arşivden yükleme ilerlemesi' });
    await expect(bar).toHaveAttribute('aria-valuenow', '50');
    const status = card(page).getByTestId('archive-reload-status');
    await expect(status).toHaveText('Yükleniyor…');
    // The bytes beside the live region: they change with every poll.
    await expect(card(page).getByTestId('archive-reload-bytes')).toHaveText(
      '31,7 KB / 63,5 KB yüklendi (%50)',
    );
    // Polled every 2 s: the first read is the user's request, the polls background ones.
    await expect.poll(() => archive.reads(1).length).toBeGreaterThan(1);
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(1);

    // Ready: the same lookup runs again, once, and its result opens.
    archive.steps = ['ready'];
    await expect(page).toHaveURL(/\/lookup\/9$/);
    const results = page.getByRole('region', { name: 'Sorgu sonuçları' });
    await expect(results.getByRole('heading', { name: 'Sorgu #9' })).toBeVisible();
    const lookups = bodies(api, '/lookup/5651');
    expect(lookups).toHaveLength(2);
    expect(lookups[1]).toEqual(lookups[0]);
    expect((lookups[0] as { query: { at: string } }).query.at).toBe('2026-10-05T11:23:41');
    const reads = archive.reads(1).map((call) => call.background);
    expect(reads[0]).toBe(false);
    expect(reads.slice(1)).toEqual(reads.slice(1).map(() => true));
    // No poll after `ready`, no further lookup.
    await page.waitForTimeout(2_500);
    expect(archive.reads(1)).toHaveLength(reads.length);
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(2);
  });

  test('a search of archived days only (409): the card where the refusal would be, then the same search', async ({
    page,
  }) => {
    const range = { from: '2026-10-04T00:00:00+03:00', to: '2026-10-06T00:00:00+03:00' };
    expect(ARCHIVE_SEARCH_REQUIRED.body.error.details.keep_days).toBe(7);
    const { api } = await runSearch(page, range, 'required');
    const message = ARCHIVE_SEARCH_REQUIRED.body.error.message_tr;
    await expect(card(page)).toContainText(message);
    await expect(page.getByText(message)).toHaveCount(1);
    await expect(page.getByRole('table', { name: 'Arama sonuçları' })).toHaveCount(0);
    await expect(page.getByText('Bir sorgu yazıp “Ara”ya basın')).toHaveCount(0);

    await reload(page);
    expect(bodies(api, '/archive-reloads')).toEqual([{ reason: 'search', ...RELOAD_DAYS }]);
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(2);
    const runs = bodies(api, '/search');
    expect(runs[0]).toEqual({ q: 'type:web', ...range });
    expect(runs[1]).toEqual(runs[0]);
    await expect(card(page)).toHaveCount(0);
  });

  test('a search reaching before the window: its rows, the card with the archived days, then the same search', async ({
    page,
  }) => {
    const { range } = ARCHIVE_SEARCH_ARCHIVED;
    const { api } = await runSearch(page, range, 'archived');
    await expect(card(page)).toContainText(
      `Bu aramanın ${DAYS} günleri veritabanında değil; bu günlerin kayıtları mühürlü arşivde. ` +
        'Arşivden yüklerseniz arama bu günleri de kapsar.',
    );
    // The rows inside the window are shown beside the card.
    await expect(page.getByRole('heading', { name: 'Zaman dağılımı' })).toBeVisible();

    await reload(page, 'Arşivden yükle', DIALOG_SETTING_TEXT);
    expect(bodies(api, '/archive-reloads')).toEqual([{ reason: 'search', ...RELOAD_DAYS }]);
    await expect.poll(() => api.callsTo('POST', '/search').length).toBe(2);
    const runs = bodies(api, '/search');
    expect(runs[0]).toEqual({ q: 'type:web', ...range });
    expect(runs[1]).toEqual(runs[0]);
    await expect(card(page)).toHaveCount(0);
  });

  test('a refusal stays in the dialog in the API’s words; asking again works', async ({ page }) => {
    const { api } = await openLookup(page, { refuseNext: 'too_large' });
    await reload(page);
    await expect(dialog(page)).toContainText(ARCHIVE_ERRORS.too_large.body.error.message_tr);
    await expect(card(page).getByRole('progressbar')).toHaveCount(0);
    expect(api.callsTo('POST', '/archive-reloads')).toHaveLength(1);

    await dialog(page).getByRole('button', { name: 'Yükle', exact: true }).click();
    await expect(dialog(page)).toBeHidden();
    await expect(card(page).getByRole('progressbar')).toBeVisible();
    expect(api.callsTo('POST', '/archive-reloads')).toHaveLength(2);
  });

  test('a failed reload has the web’s text, no re-run, and is offered again', async ({ page }) => {
    const { api, archive } = await openLookup(page, { steps: ['loading', 'failed'] });
    await reload(page);
    await expect(card(page).getByRole('alert')).toHaveText(UNREADABLE);
    await expect(card(page).getByRole('progressbar')).toHaveCount(0);
    await page.waitForTimeout(2_500);
    expect(archive.reads(1)).toHaveLength(2);
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(1);

    // "Yeniden dene" asks for the days once more: a new reload.
    archive.steps = ['ready'];
    await reload(page, 'Yeniden dene');
    await expect(page).toHaveURL(/\/lookup\/9$/);
    expect(bodies(api, '/archive-reloads')).toEqual([
      { reason: 'lookup', ...RELOAD_DAYS },
      { reason: 'lookup', ...RELOAD_DAYS },
    ]);
    expect(archive.reads(2)).toHaveLength(1);
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(2);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`archive reload card accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    // The reload stays loading: the progress holds still for the scan.
    await openLookup(page, { steps: ['loading'] });
    await card(page).getByRole('button', { name: 'Arşivden yükle' }).click();
    await expect(dialog(page)).toContainText(DIALOG_TEXT);
    await expectNoA11yViolations(page);

    await dialog(page).getByRole('button', { name: 'Yükle', exact: true }).click();
    await expect(dialog(page)).toBeHidden();
    await expect(
      card(page).getByRole('progressbar', { name: 'Arşivden yükleme ilerlemesi' }),
    ).toHaveAttribute('aria-valuenow', '50');
    await expectNoA11yViolations(page);
  });
}
