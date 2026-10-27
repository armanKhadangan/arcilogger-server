import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { mockSources } from './mock-sources';
import { RAW, SAMPLE_TODAY } from './samples';

/**
 * Kaynaklar (contract 2.16 and 2.22.4, `sources.read`): every source with its state, networks
 * with their members under them, unknown senders, the drawer's volume, latest lines (audited,
 * only with a raw-line permission and only when the tab opens), gaps and parse failures,
 * "Kaynak olarak tanımla" (Ayarlar → Kaynaklar opens filled in) and the licence strip. The API is
 * `mock-sources.ts` on top of `mock-api.ts`.
 */

async function open(page: Page, options: Parameters<typeof mockApi>[1] = {}): Promise<MockApi> {
  const api = await mockApi(page, { signedInAs: 'tolga', ...options });
  await mockSources(page, api);
  return api;
}

function table(page: Page) {
  return page.getByRole('table', { name: 'Log kaynaklarının durumu' });
}

function row(page: Page, name: string) {
  return table(page)
    .getByRole('row')
    .filter({ has: page.getByTitle(name, { exact: true }) });
}

async function names(page: Page): Promise<string[]> {
  const cells = table(page).locator('tbody tr td:first-child span[title]');
  return cells.evaluateAll((items) => items.map((item) => item.getAttribute('title') ?? ''));
}

test.describe('Kaynaklar', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('every source with its state; members under their network; unknown senders', async ({
    page,
  }) => {
    const api = await open(page);
    await page.goto('/');
    await page
      .getByRole('navigation', { name: 'Ana menü' })
      .getByRole('link', { name: 'Kaynaklar' })
      .click();
    await expect(page).toHaveURL(/\/sources$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Kaynaklar' })).toBeVisible();
    await expect(page.getByTestId('sources-summary')).toContainText('3/5 kaynak etkin');
    await expect(page.getByTestId('sources-summary')).toContainText('1 bilinmeyen gönderici');
    await expect
      .poll(() => names(page))
      .toEqual([
        'core-r1',
        'fortigate',
        'ap',
        'ap-172.20.10.3',
        'ap-172.20.10.4',
        'switch-3',
        'lab-fw',
        'edge-1',
        'unknown-172.20.10.40',
        'unknown-198.51.100.7',
      ]);
    const core = row(page, 'core-r1');
    await expect(core).toContainText('MikroTik');
    await expect(core).toContainText('172.20.10.1');
    await expect(core).toContainText('11.200');
    await expect(core).toContainText('%0,0');
    await expect(core).toContainText('15 dk');
    await expect(core).toContainText('Etkin');
    await expect(
      core.getByRole('img', { name: /core-r1: mesaj\/dk, son 60 dakika/ }),
    ).toBeVisible();
    await expect(row(page, 'fortigate')).toContainText('%2,5');
    await expect(row(page, 'ap-172.20.10.3').getByLabel('ap ağının üyesi')).toBeVisible();
    await expect(row(page, 'ap-172.20.10.4')).toContainText('Hiç mesaj gelmedi');
    await expect(row(page, 'switch-3')).toContainText('Sessiz (beklenmiyor)');
    await expect(row(page, 'switch-3')).toContainText('180 dk');
    await expect(row(page, 'lab-fw')).toContainText('Kapalı');
    await expect(row(page, 'unknown-172.20.10.40')).toContainText('Bilinmeyen');
    await expect(row(page, 'unknown-172.20.10.40')).toContainText('Tanımlı değil');
    await expect(row(page, 'unknown-198.51.100.7')).toContainText('→ edge-1');
    await expect(row(page, 'unknown-198.51.100.7')).toContainText('Kaynak olarak tanımlandı');
    // Only an unknown sender not configured yet can be added as a source.
    await expect(
      table(page).getByRole('button', { name: /göndericisini kaynak olarak tanımla/ }),
    ).toHaveCount(1);
    // Counts only: neither the list nor a drawer is a raw-line read.
    expect(api.callsTo('GET', '/sources')[0]?.background).toBe(false);
    await expect(page.getByTestId('licence-strip')).toHaveText(
      'Lisans: 3 / Sınırsız log kaynağı (son 30 günde gönderenler dahil)',
    );
    await expect(
      page.getByRole('link', { name: "Ayarlar → Kaynaklar'da düzenle" }),
    ).toHaveAttribute('href', '/settings/sources');
  });

  test('filters: silent and never-heard sources, unknown senders; in the address', async ({
    page,
  }) => {
    await open(page);
    await page.goto('/sources');
    await expect(table(page)).toBeVisible();
    await page
      .getByRole('group', { name: 'Kaynakları süz' })
      .getByRole('button', { name: /Sessiz/ })
      .click();
    await expect(page).toHaveURL(/\/sources\?filter=silent$/);
    await expect.poll(() => names(page)).toEqual(['ap-172.20.10.4', 'switch-3', 'edge-1']);
    await page.getByRole('button', { name: /Bilinmeyen/ }).click();
    await expect.poll(() => names(page)).toEqual(['unknown-172.20.10.40', 'unknown-198.51.100.7']);
    await page.reload();
    await expect.poll(() => names(page)).toEqual(['unknown-172.20.10.40', 'unknown-198.51.100.7']);
    await page.getByRole('button', { name: /Tümü/ }).click();
    await expect(page).toHaveURL(/\/sources$/);
    await expect.poll(() => names(page)).toHaveLength(10);
  });

  test('the overview refreshes every 30 s in the background', async ({ page }) => {
    await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
    const api = await open(page);
    await page.goto('/sources');
    await expect(table(page)).toBeVisible();
    const before = api.callsTo('GET', '/sources').length;
    await page.clock.fastForward('00:31');
    await expect.poll(() => api.callsTo('GET', '/sources').length).toBeGreaterThan(before);
    const calls = api.callsTo('GET', '/sources');
    expect(calls[0]?.background).toBe(false);
    expect(calls.at(-1)?.background).toBe(true);
  });

  test('the drawer: volume, latest lines (read when the tab opens), gaps, parse failures', async ({
    page,
  }) => {
    const api = await open(page);
    await page.goto('/sources');
    await row(page, 'core-r1').click();
    await expect(page).toHaveURL(/\/sources\?source=core-r1$/);
    const sheet = page.getByRole('dialog', { name: 'core-r1' });
    await expect(sheet.getByRole('tab', { name: 'Hacim', selected: true })).toBeVisible();
    await expect(
      sheet.getByRole('heading', { name: 'Saatlik satır sayısı, son 48 saat' }),
    ).toBeVisible();
    await expect(
      sheet
        .getByRole('table', { name: 'Son 14 günün satır ve ayrıştırma hatası sayıları' })
        .getByRole('row'),
    ).toHaveCount(15);
    // The latest lines are raw lines: not asked before their tab opens.
    expect(api.callsTo('GET', '/sources/core-r1/tail')).toHaveLength(0);

    await sheet.getByRole('tab', { name: 'Son satırlar' }).click();
    const tail = sheet.getByRole('table', { name: 'Kaynağın son satırları' });
    await expect(tail.getByRole('row')).toHaveCount(8);
    expect(api.callsTo('GET', '/sources/core-r1/tail')).toHaveLength(1);
    expect(api.callsTo('GET', '/sources/core-r1/tail')[0]?.query.get('limit')).toBe('50');
    await expect(sheet).toContainText('09.10.2026 · satır 1–7');
    await expect(sheet).toContainText('Gösterilen bayt aralığının SHA-256 özeti');
    await expect(sheet).toContainText('denetim kaydına yazıldı');
    await tail.getByRole('button', { name: '4. satırı ham haliyle aç' }).click();
    const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    await expect(raw).toContainText(RAW.lines['core-r1|2026-10-09|4']?.sha256 ?? '-');
    await raw.getByRole('button', { name: 'Kapat' }).click();

    await sheet.getByRole('tab', { name: 'Boşluklar' }).click();
    const gaps = sheet.getByRole('table', { name: 'Son 30 günün kayıt boşlukları' });
    await expect(gaps.getByRole('row')).toHaveCount(3);
    await expect(gaps).toContainText('599 dk');
    await expect(gaps).toContainText('Mühürlü günün manifestinde');

    await sheet.getByRole('tab', { name: 'Ayrıştırma hataları' }).click();
    const failures = sheet.getByRole('table', { name: 'Son 7 günün ayrıştırma hataları' });
    await expect(failures).toContainText('rawlog.invalid');
    // Not in the collector's format: no database row, so no search; its example line only.
    await expect(failures.getByRole('button', { name: 'Aramada göster' })).toHaveCount(0);
    await expect(sheet).toContainText('Arama onları bulamaz');
    await failures.getByRole('button', { name: 'Örnek satır' }).click();
    await expect(page.getByRole('dialog', { name: 'Ham kayıt satırı' })).toBeVisible();
  });

  test('a parse failure opens in Arama with its pattern, handed over in memory', async ({
    page,
  }) => {
    await open(page);
    await page.goto('/sources?source=fortigate');
    const sheet = page.getByRole('dialog', { name: 'fortigate' });
    await sheet.getByRole('tab', { name: 'Ayrıştırma hataları' }).click();
    await expect(sheet).toContainText('value was not key=value');
    await sheet.getByRole('button', { name: 'Aramada göster' }).click();
    await expect(page).toHaveURL(/\/search$/);
    // The query is in neither the address nor the history entry nor the browser's storage.
    expect(await page.evaluate(() => JSON.stringify(window.history.state))).not.toContain('error:');
    const stored = await page.evaluate(() =>
      [window.sessionStorage, window.localStorage]
        .flatMap((storage) => Object.keys(storage).map((key) => storage.getItem(key) ?? ''))
        .join('\n'),
    );
    expect(stored).not.toContain('fortigate.kv');
  });

  test('without a raw-line permission the latest lines are hidden and explained', async ({
    page,
  }) => {
    const api = await open(page, { revoke: { admin: ['search.read', 'lookup.run'] } });
    await page.goto('/sources?source=core-r1');
    const sheet = page.getByRole('dialog', { name: 'core-r1' });
    await sheet.getByRole('tab', { name: 'Son satırlar' }).click();
    await expect(sheet.getByTestId('tail-hidden')).toHaveText(
      'Ham satırları görmek için Arama ya da 5651 Sorgu yetkisi gerekir.',
    );
    expect(api.callsTo('GET', '/sources/core-r1/tail')).toHaveLength(0);
    await page.goto('/sources?source=fortigate');
    const fortigate = page.getByRole('dialog', { name: 'fortigate' });
    await fortigate.getByRole('tab', { name: 'Ayrıştırma hataları' }).click();
    // The parser's detail can echo a value of the line: the API leaves it out.
    await expect(fortigate).toContainText(
      'Hata ayrıntısı için Arama ya da 5651 Sorgu yetkisi gerekir.',
    );
    await expect(fortigate).not.toContainText('value was not key=value');
    await expect(fortigate.getByRole('button', { name: 'Örnek satır' })).toHaveCount(0);
  });

  test('the detail of a parse failure comes once the KVKK notice is accepted', async ({ page }) => {
    const api = await open(page);
    api.get('tolga').privacy_accepted = false;
    await page.goto('/sources');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    // The raw-line rule: until the notice is accepted the API leaves a pattern's error out.
    const before = await page.evaluate(async () => {
      const response = await fetch('/api/v1/sources/fortigate');
      const detail = (await response.json()) as { parse_failures: { error: string | null }[] };
      return detail.parse_failures.map((row) => row.error);
    });
    expect(before).toEqual([null]);
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await page.goto('/sources?source=fortigate');
    const sheet = page.getByRole('dialog', { name: 'fortigate' });
    await sheet.getByRole('tab', { name: 'Ayrıştırma hataları' }).click();
    await expect(sheet).toContainText('value was not key=value');
  });

  test('no file today: said so, with Arama for the source', async ({ page }) => {
    await open(page);
    await page.goto('/sources?source=switch-3');
    const sheet = page.getByRole('dialog', { name: 'switch-3' });
    await sheet.getByRole('tab', { name: 'Son satırlar' }).click();
    await expect(sheet.getByTestId('tail-no-file')).toContainText('Bugün kayıt yok');
    await sheet.getByRole('button', { name: "Bu kaynağı Arama'da aç" }).click();
    await expect(page).toHaveURL(/\/search$/);
  });

  test('"Kaynak olarak tanımla" opens Ayarlar → Kaynaklar with the sender filled in', async ({
    page,
  }) => {
    const api = await open(page);
    await page.goto('/sources');
    await row(page, 'unknown-172.20.10.40')
      .getByRole('button', { name: 'unknown-172.20.10.40 göndericisini kaynak olarak tanımla' })
      .click();
    await expect(page).toHaveURL(/\/settings\/sources$/);
    const dialog = page.getByRole('dialog', { name: 'Kaynak ekle' });
    await expect(dialog.getByLabel('Ad', { exact: true })).toHaveValue('kaynak-40');
    await expect(dialog.getByLabel('Adres')).toHaveValue('172.20.10.40');
    await expect(dialog.getByLabel('Tür')).toContainText('MikroTik');
    await expect(dialog.getByTestId('source-prefilled')).toContainText('172.20.10.40');
    // Nothing is added before the administrator reviews it.
    expect(api.callsTo('POST', '/admin/config/sources')).toHaveLength(0);
    expect(page.url()).not.toContain('172.20.10.40');
  });

  test('the licence strip says when the limit is exceeded; nothing is blocked', async ({
    page,
  }) => {
    await open(page, { license: { state: 'licensed_limits_exceeded' } });
    await page.goto('/sources');
    const strip = page.getByTestId('licence-strip');
    await expect(strip).toContainText('Lisans: 4 / 3 log kaynağı (son 30 günde gönderenler dahil)');
    await expect(strip).toContainText('Sınır aşıldı');
    await expect(strip).toContainText('kayıtları da toplanır ve mühürlenir');
    await expect(table(page)).toBeVisible();
  });

  test('the auditor has no Kaynaklar (sources.read is the administrator’s)', async ({ page }) => {
    await open(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Ana menü' }).getByRole('link', { name: 'Kaynaklar' }),
    ).toHaveCount(0);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Kaynaklar accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await open(page);
    await page.goto('/sources');
    await expect(table(page)).toBeVisible();
    await expectNoA11yViolations(page);
    await row(page, 'core-r1').click();
    const sheet = page.getByRole('dialog', { name: 'core-r1' });
    await expect(sheet.getByRole('heading', { name: 'Son 14 gün' })).toBeVisible();
    await expectNoA11yViolations(page);
    await sheet.getByRole('tab', { name: 'Son satırlar' }).click();
    await expect(sheet.getByRole('table', { name: 'Kaynağın son satırları' })).toBeVisible();
    await expectNoA11yViolations(page);
    await sheet.getByRole('tab', { name: 'Ayrıştırma hataları' }).click();
    await expect(
      sheet.getByRole('table', { name: 'Son 7 günün ayrıştırma hataları' }),
    ).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
