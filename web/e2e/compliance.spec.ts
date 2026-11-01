import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import type { ComplianceCard, ComplianceTone } from '../src/lib/compliance-types';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { COMPLIANCE_PDF, mockW4, type W4Options } from './mock-w4';
import { COMPLIANCE, SAMPLE_TODAY } from './samples';

/*
 * Uyum Durumu (`/compliance`, Wave 4 contract 2.8.1, §3 W4-F) on the answers captured from the
 * real API (`samples/compliance.json` through `mock-w4.ts`): the ten cards as the server words
 * them, the report download, the configuration backup confirmation and its refusal, the refresh
 * rules of Kaynaklar, the English switch and the auditor of D15 (cards and report, nothing that
 * changes state). The whole page in light and dark is scanned in `w4-a11y.spec.ts`; here the
 * error states are.
 */

const TONE_TR: Record<ComplianceTone, string> = {
  ok: 'Tamam',
  warning: 'Dikkat',
  error: 'Sorun',
  neutral: 'Bilgi yok',
};

/** The pages a card links to: menu name and path (`card.page`). */
const PAGES: Record<string, [string, string]> = {
  sources: ['Kaynaklar', '/sources'],
  sealing: ['Mühürleme', '/sealing'],
  system: ['Sistem', '/system'],
  search: ['Arama', '/search'],
  settings: ['Ayarlar', '/settings'],
};

/** Contract 2.8.5, `compliance.confirmBackupBody`. */
const CONFIRM_BODY =
  'arcilogger.toml, secret.key ve Caddy CA klasörünün (özel anahtarıyla) şifreli, çevrim dışı ' +
  've yedeklerden ayrı saklanan bir ortama kopyalandığını onaylıyorsunuz (secrets.json isteğe ' +
  'bağlı; YEDEKLEME.md bölüm 3). Onay, yapılandırmanın şimdiki SHA-256 değeriyle denetim ' +
  'kaydına yazılır.';

/** The real API's 503 (`api/src/arcilogger_api/errors.py`). */
const UNAVAILABLE = {
  error: {
    code: 'unavailable',
    message_tr: 'Hizmet şu anda kullanılamıyor (veritabanı?).',
    message_en: 'The service is unavailable right now (database?).',
  },
};

const ADMIN_CARDS = COMPLIANCE.admin.cards;

function backupCard(cards: ComplianceCard[]): ComplianceCard {
  const card = cards.find((item) => item.id === 'backup');
  if (!card) throw new Error('the capture has no backup card');
  return card;
}

/** The file name the report is offered under (the capture's `Content-Disposition`). */
function reportName(): string {
  const disposition = COMPLIANCE.report_pdf.headers['content-disposition'] ?? '';
  const name = /filename="([^"]+)"/.exec(disposition)?.[1];
  if (!name) throw new Error('report_pdf sample without a file name');
  return name;
}

async function open(page: Page, username = 'tolga', options: W4Options = {}) {
  const api = await mockApi(page, { signedInAs: username });
  const w4 = await mockW4(page, api, options);
  await page.goto('/compliance');
  await expect(page.getByRole('heading', { level: 1, name: 'Uyum Durumu' })).toBeVisible();
  await expect(cards(page)).toHaveCount(10);
  return { api, w4 };
}

function cards(page: Page) {
  return page.getByRole('list', { name: 'Denetimler' }).getByRole('group');
}

function card(page: Page, id: string) {
  return page.getByTestId(`compliance-card-${id}`);
}

test.describe('Uyum Durumu', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('administrator: the ten captured cards with badge, reason, document and links', async ({
    page,
  }) => {
    const { api } = await open(page);
    await expect(
      page.getByText('Kayıt toplama, mühürleme ve yedeklemenin teknik denetimleri.'),
    ).toBeVisible();
    await expect(page.getByTestId('compliance-notice')).toHaveText(COMPLIANCE.admin.notice_tr);
    const counts = COMPLIANCE.admin.counts;
    const summary = page.getByTestId('compliance-summary');
    await expect(summary).toContainText(`10 denetimden ${String(counts.ok)} tanesi tamam`);
    await expect(summary).toContainText(`Sorun: ${String(counts.error)}`);
    await expect(summary).toContainText(`Dikkat: ${String(counts.warning)}`);
    await expect(summary).not.toContainText('Bilgi yok');

    await expect(cards(page).getByRole('heading', { level: 2 })).toHaveText(
      ADMIN_CARDS.map((item) => `${String(item.number)}. ${item.title_tr}`),
    );
    for (const item of ADMIN_CARDS) {
      const shown = card(page, item.id);
      await expect(shown).toContainText(TONE_TR[item.tone]);
      await expect(shown).toContainText(item.reason_tr);
      await expect(shown).toContainText(`Belge: ${item.doc_tr}`);
      for (const fact of item.facts) {
        await expect(shown).toContainText(fact.label_tr);
        await expect(shown).toContainText(fact.value_tr);
      }
      const verify = shown.getByTestId('verify-on-site');
      if (item.verify_on_site_tr)
        await expect(verify).toHaveText(`Sahada doğrulayın: ${item.verify_on_site_tr}`);
      else await expect(verify).toHaveCount(0);
      const [name, path] = PAGES[item.page] ?? ['', ''];
      await expect(shown.getByRole('link', { name: `${name} sayfasını aç` })).toHaveAttribute(
        'href',
        path,
      );
      // One open alert opens in Güvenlik, several open the list.
      const alerts = shown.getByRole('link', { name: /^Açık uyarı: / });
      if (item.alerts.length === 0) await expect(alerts).toHaveCount(0);
      else {
        await expect(alerts).toHaveText(`Açık uyarı: ${String(item.alerts.length)}`);
        await expect(alerts).toHaveAttribute(
          'href',
          item.alerts.length === 1 ? `/security?alert=${String(item.alerts[0]?.id)}` : '/security',
        );
      }
    }
    // Card 7 carries the `R20-filter` alert of the silent content filter.
    expect(ADMIN_CARDS.find((item) => item.id === 'content_filter')?.alerts[0]?.rule).toBe(
      'R20-filter',
    );

    // The confirmation is on card 10 only; the wizard link while a card is not ok.
    await expect(page.getByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toHaveCount(1);
    await expect(
      card(page, 'backup').getByRole('button', { name: 'Yapılandırma yedeğini aldım' }),
    ).toBeVisible();
    await page.getByRole('link', { name: 'İlk kurulum sihirbazını aç' }).click();
    await expect(page).toHaveURL(/\/settings\/setup$/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'İlk kurulum sihirbazı' }),
    ).toBeVisible();

    // Viewing is the user's own request, and nothing was posted.
    expect(api.callsTo('GET', '/compliance')[0]?.background).toBe(false);
    expect(api.calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });

  test('"Uyum Durumu Raporu (PDF)" downloads the report under the captured name', async ({
    page,
  }) => {
    const { api, w4 } = await open(page);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe(reportName());
    // The bytes the mock served; their `X-Arci-Sha256` matched, so no error is shown.
    expect(readFileSync(await file.path()).equals(COMPLIANCE_PDF)).toBe(true);
    expect(w4.reports).toBe(1);
    await expect(page.getByTestId('compliance-export-error')).toHaveCount(0);
    expect(api.callsTo('GET', '/compliance/report.pdf')).toHaveLength(1);
    expect(api.callsTo('GET', '/compliance/report.pdf')[0]?.background).toBe(false);
  });

  test('"Yapılandırma yedeğini aldım": confirmed once, card 10 turns ok', async ({ page }) => {
    const { api, w4 } = await open(page);
    const before = backupCard(ADMIN_CARDS);
    const after = backupCard(COMPLIANCE.after_backup.cards);
    expect(before.tone).toBe('warning');
    expect(after.tone).toBe('ok');
    await expect(card(page, 'backup')).toContainText(before.reason_tr);

    await card(page, 'backup').getByRole('button', { name: 'Yapılandırma yedeğini aldım' }).click();
    const dialog = page.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' });
    await expect(dialog).toContainText(CONFIRM_BODY);
    // Nothing is sent before the user confirms.
    expect(api.callsTo('POST', '/compliance/config-backup')).toHaveLength(0);
    await dialog.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByText('Yapılandırma kopyasının onayı denetim kaydına yazıldı.', { exact: true }),
    ).toBeVisible();

    const posts = api.callsTo('POST', '/compliance/config-backup');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.csrf).toBeTruthy();
    expect(w4.confirmations).toBe(1);
    await expect(card(page, 'backup')).toContainText('Tamam');
    await expect(card(page, 'backup')).toContainText(after.reason_tr);
    await expect(page.getByTestId('compliance-summary')).toContainText(
      `10 denetimden ${String(COMPLIANCE.after_backup.counts.ok)} tanesi tamam`,
    );
  });

  test('an unreadable configuration file: the 409 in the dialog, card 10 unchanged', async ({
    page,
  }) => {
    const { api, w4 } = await open(page, 'tolga', { configUnreadable: true });
    const refusal = COMPLIANCE.errors.config_backup_unreadable;
    if (!refusal) throw new Error('the capture has no config_backup_unreadable refusal');
    expect(refusal.status).toBe(409);

    await card(page, 'backup').getByRole('button', { name: 'Yapılandırma yedeğini aldım' }).click();
    const dialog = page.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' });
    await dialog.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(dialog.getByRole('alert')).toHaveText(
      `${refusal.body.error.message_tr} (config_backup_unreadable)`,
    );
    await expect(dialog).toBeVisible();
    expect(api.callsTo('POST', '/compliance/config-backup')).toHaveLength(1);
    expect(w4.confirmations).toBe(0);
    await expectNoA11yViolations(page);

    await dialog.getByRole('button', { name: 'İptal' }).click();
    await expect(dialog).toBeHidden();
    const before = backupCard(ADMIN_CARDS);
    await expect(card(page, 'backup')).toContainText(TONE_TR[before.tone]);
    await expect(card(page, 'backup')).toContainText(before.reason_tr);
  });

  test('"Yenile" asks as the user; a failed refresh keeps the cards and offers "Tekrar dene"', async ({
    page,
  }) => {
    const { api } = await open(page);
    let failing = false;
    await page.route('**/api/v1/compliance', (route) =>
      failing ? route.fulfill({ status: 503, json: UNAVAILABLE }) : route.fallback(),
    );
    const before = api.callsTo('GET', '/compliance').length;
    await page.getByRole('button', { name: 'Yenile' }).click();
    await expect.poll(() => api.callsTo('GET', '/compliance').length).toBe(before + 1);
    expect(api.callsTo('GET', '/compliance').at(-1)?.background).toBe(false);

    failing = true;
    await page.getByRole('button', { name: 'Yenile' }).click();
    const alert = page.getByRole('alert').filter({ hasText: 'Denetim sonuçları okunamadı' });
    await expect(alert).toContainText(UNAVAILABLE.error.message_tr);
    // The cards shown before stay under the error.
    await expect(cards(page)).toHaveCount(10);
    await expect(card(page, 'sources')).toContainText(ADMIN_CARDS[0]?.reason_tr ?? '-');

    failing = false;
    await alert.getByRole('button', { name: 'Tekrar dene' }).click();
    await expect(alert).toBeHidden();
    await expect(cards(page)).toHaveCount(10);
  });

  test('English: the server’s English texts and the fixed labels', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
    await page.goto('/compliance');
    await expect(page.getByRole('heading', { level: 1, name: 'Compliance' })).toBeVisible();
    await expect(
      page.getByText('Technical checks of log collection, sealing and backups.'),
    ).toBeVisible();
    await expect(page.getByTestId('compliance-notice')).toHaveText(COMPLIANCE.admin.notice_en);
    await expect(page.getByTestId('compliance-summary')).toContainText(
      `${String(COMPLIANCE.admin.counts.ok)} of 10 checks OK`,
    );
    const tones: Record<ComplianceTone, string> = {
      ok: 'OK',
      warning: 'Attention',
      error: 'Problem',
      neutral: 'No data',
    };
    const list = page.getByRole('list', { name: 'Checks' });
    await expect(list.getByRole('heading', { level: 2 })).toHaveText(
      ADMIN_CARDS.map((item) => `${String(item.number)}. ${item.title_en}`),
    );
    for (const item of ADMIN_CARDS) {
      const shown = card(page, item.id);
      await expect(shown).toContainText(tones[item.tone]);
      await expect(shown).toContainText(item.reason_en);
      await expect(shown).toContainText(`Document: ${item.doc_en}`);
      if (item.verify_on_site_en)
        await expect(shown.getByTestId('verify-on-site')).toHaveText(
          `Verify on site: ${item.verify_on_site_en}`,
        );
    }
    await expect(
      page.getByRole('button', { name: 'Compliance status report (PDF)' }),
    ).toBeVisible();
    await expect(
      card(page, 'backup').getByRole('button', { name: 'I have copied the configuration' }),
    ).toBeVisible();
    await expect(page.getByRole('link', { name: 'Open the first-run wizard' })).toHaveAttribute(
      'href',
      '/settings/setup',
    );
  });

  test('the auditor: cards and the report; no confirmation, no wizard, only allowed links', async ({
    page,
  }) => {
    const { api, w4 } = await open(page, 'ayse.yilmaz');
    await expect(page.getByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toHaveCount(0);
    for (const item of ADMIN_CARDS) {
      const shown = card(page, item.id);
      await expect(shown).toContainText(item.reason_tr);
      const [name, path] = PAGES[item.page] ?? ['', ''];
      // Mühürleme and Arama stay; Kaynaklar, Ayarlar and Sistem are the administrators'.
      const kept = item.page === 'sealing' || item.page === 'search';
      const link = shown.getByRole('link', { name: `${name} sayfasını aç` });
      if (kept) await expect(link).toHaveAttribute('href', path);
      else await expect(link).toHaveCount(0);
      // The open alerts are counted, without a link to Güvenlik (`alerts.read`).
      if (item.alerts.length > 0)
        await expect(shown).toContainText(`Açık uyarı: ${String(item.alerts.length)}`);
      await expect(shown.getByRole('link', { name: /^Açık uyarı: / })).toHaveCount(0);
    }

    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }).click();
    expect((await download).suggestedFilename()).toBe(reportName());
    expect(w4.reports).toBe(1);
    await expect(page.getByTestId('compliance-export-error')).toHaveCount(0);
    expect(api.calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });
});

test('Uyum Durumu refreshes every 30 s in the background', async ({ page }) => {
  await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
  const { api } = await open(page);
  const before = api.callsTo('GET', '/compliance').length;
  await page.clock.fastForward('00:31');
  await expect.poll(() => api.callsTo('GET', '/compliance').length).toBeGreaterThan(before);
  const calls = api.callsTo('GET', '/compliance');
  expect(calls[0]?.background).toBe(false);
  expect(calls.at(-1)?.background).toBe(true);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Uyum Durumu accessibility: a failed refresh and a failed export — ${theme}`, async ({
    page,
  }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await open(page);
    await page.route('**/api/v1/compliance**', (route) =>
      route.fulfill({ status: 503, json: UNAVAILABLE }),
    );
    await page.getByRole('button', { name: 'Yenile' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Denetim sonuçları okunamadı' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }).click();
    await expect(page.getByTestId('compliance-export-error')).toHaveText(
      `${UNAVAILABLE.error.message_tr} (unavailable)`,
    );
    await expect(cards(page)).toHaveCount(10);
    await expectNoA11yViolations(page);
  });
}
