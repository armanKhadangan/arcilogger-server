import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockOptions } from './mock-api';
import { mockSources } from './mock-sources';
import { DASHBOARD, RAW, SAMPLE_TODAY, SOURCES } from './samples';

/** The Panel's blocks (spec 12.3 item 2) by their test ids, in page order. */
const ALL = [
  'card-ingest',
  'card-parse-failures',
  'card-seal',
  'card-credit',
  'card-clock',
  'card-usage',
  'card-alerts',
  'card-sites',
  'card-categories',
  'card-calendar',
];

/** The main mock with the parse-failure box of `mock-sources.ts` in the summary. */
async function panel(page: Page, options: MockOptions) {
  const api = await mockApi(page, options);
  await mockSources(page, api);
  return api;
}

async function shown(page: Page): Promise<string[]> {
  await expect(page.getByTestId('card-usage')).toContainText('Aktif kullanıcı');
  const found: string[] = [];
  for (const id of ALL) if ((await page.getByTestId(id).count()) > 0) found.push(id);
  return found;
}

test.describe('Panel', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('administrator: every block with its figures, each linking to its page', async ({
    page,
  }) => {
    const api = await panel(page, { signedInAs: 'tolga' });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    await expect(page.getByTestId('card-ingest')).toContainText('2/3 kaynak aktif');
    expect(await shown(page)).toEqual(ALL);

    // The captured summary: times in site time (last_seen is sent with +03:00). The capture's
    // clock runs on in real time, so the seconds are read from the sample.
    const silent = DASHBOARD.admin.ingest?.silent[0];
    const seen = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?\+03:00$/.exec(
      silent?.last_seen ?? '',
    );
    if (!silent || !seen) throw new Error('no silent source with a site-time last_seen');
    expect(silent.name).toBe('switch-3');
    await expect(page.getByTestId('silent-sources')).toContainText(
      `switch-3 — son mesaj ${seen[3]}.${seen[2]}.${seen[1]} ${seen[4]}`,
    );
    await expect(page.getByTestId('card-seal')).toContainText('Henüz mühürlenmedi');
    await expect(page.getByTestId('card-credit')).toContainText('9.612 kaldı');
    await expect(page.getByTestId('card-credit')).toContainText('~6 yıl yeter');
    await expect(page.getByTestId('card-credit')).not.toContainText('Eşiğin altında');
    await expect(page.getByTestId('card-clock')).toContainText('3,2 ms');
    await expect(page.getByTestId('card-clock')).toContainText('NTP 172.20.10.1');
    const usage = page.getByTestId('card-usage');
    // The captured company usage (usage.json → company): 5 people, 17 minutes active.
    await expect(usage).toContainText('5');
    await expect(usage).toContainText('17 dk');
    await expect(usage).toContainText('717,3 KB');
    await expect(page.getByTestId('card-sites')).toContainText('portal.example');
    await expect(page.getByTestId('card-categories')).toContainText('İş Uygulamaları');
    const alerts = page.getByTestId('card-alerts');
    await expect(alerts.getByRole('link', { name: 'Yüksek 2' })).toBeVisible();
    await expect(alerts.getByRole('link', { name: 'Orta 1' })).toBeVisible();

    // The usage figures are estimates: the info icon carries the method note.
    await expect(
      usage
        .getByRole('button', {
          name: 'Tahmini değer: bağlantı kayıtlarından hesaplanır; arka planda açık sekmeler de sayılabilir.',
        })
        .first(),
    ).toBeVisible();

    // Calendar day → that day on the sealing page.
    await page
      .getByTestId('card-calendar')
      .getByRole('link', { name: /11 Ekim 2026: Başarısız/ })
      .click();
    await expect(page).toHaveURL(/\/sealing\?month=2026-10&day=2026-10-11$/);
    await page.goBack();

    // Newest alert → the security page with its drawer open.
    await alerts.getByRole('link', { name: /switch-3 kaynağından/ }).click();
    await expect(page).toHaveURL(/\/security\?alert=4$/);
    await expect(page.getByRole('dialog', { name: /switch-3 kaynağından/ })).toBeVisible();
    expect(api.callsTo('GET', '/dashboard/summary')[0]?.background).toBe(false);
  });

  test('auditor: no alert block (no alerts.read), sealing and clock without system links', async ({
    page,
  }) => {
    await panel(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/');
    await expect(page.getByTestId('card-seal')).toBeVisible();
    expect(await shown(page)).toEqual(ALL.filter((id) => id !== 'card-alerts'));
    await expect(page.getByTestId('card-ingest').getByRole('link')).toHaveCount(0);
    await expect(page.getByTestId('card-clock').getByRole('link')).toHaveCount(0);
    await expect(
      page.getByTestId('card-seal').getByRole('link', { name: 'Mühürleme sayfasını aç' }),
    ).toBeVisible();
  });

  test('report viewer: log intake and company usage only, nothing else is an error', async ({
    page,
  }) => {
    await panel(page, { signedInAs: 'zeynep' });
    await page.goto('/');
    await expect(page.getByTestId('card-ingest')).toBeVisible();
    expect(await shown(page)).toEqual([
      'card-ingest',
      'card-parse-failures',
      'card-usage',
      'card-sites',
      'card-categories',
    ]);
    await expect(page.getByRole('alert')).toHaveCount(0);
    // Counts only for a role without raw lines and search: no example, no Arama, no Kaynaklar.
    const failures = page.getByTestId('card-parse-failures');
    await expect(failures).toContainText('fortigate.kv');
    await expect(failures.getByRole('button')).toHaveCount(0);
    await expect(failures.getByRole('link')).toHaveCount(0);
  });

  test('HR: the usage blocks only; the summary is never asked', async ({ page }) => {
    const api = await panel(page, { signedInAs: 'mehmet.kaya' });
    await page.goto('/');
    expect(await shown(page)).toEqual(['card-usage', 'card-sites', 'card-categories']);
    expect(api.callsTo('GET', '/dashboard/summary')).toHaveLength(0);
  });

  test('an acknowledged alert leaves the open counts at the next refresh', async ({ page }) => {
    const api = await panel(page, { signedInAs: 'tolga' });
    await page.goto('/');
    const alerts = page.getByTestId('card-alerts');
    await expect(alerts.getByRole('link', { name: 'Yüksek 2' })).toBeVisible();
    const alert = api.alerts.alerts.get(4);
    if (!alert) throw new Error('sample alert 4 missing');
    alert.state = 'acknowledged';
    await page.getByRole('button', { name: 'Yenile' }).click();
    await expect(alerts.getByRole('link', { name: 'Yüksek 1' })).toBeVisible();
  });
});

test('the live blocks refresh every 30 s in the background', async ({ page }) => {
  await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
  const api = await panel(page, { signedInAs: 'tolga' });
  await page.goto('/');
  await expect(page.getByTestId('card-ingest')).toBeVisible();
  const before = api.callsTo('GET', '/dashboard/summary').length;
  await page.clock.fastForward('00:31');
  await expect.poll(() => api.callsTo('GET', '/dashboard/summary').length).toBeGreaterThan(before);
  const calls = api.callsTo('GET', '/dashboard/summary');
  expect(calls[0]?.background).toBe(false);
  expect(calls.at(-1)?.background).toBe(true);
  // The usage figures wait for their 5 minutes.
  expect(api.callsTo('GET', '/usage/company')).toHaveLength(1);
});

test.describe('Panel — parse failures', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('the captured box of the real API', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    // `GET /dashboard/summary` as the API answered it with today's parse failures.
    await page.route('**/api/v1/dashboard/summary', (route) =>
      route.fulfill({ json: SOURCES.dashboard }),
    );
    const box = SOURCES.dashboard.parse_failures;
    if (!box) throw new Error('the capture has no parse-failure box');
    const number = (value: number) => new Intl.NumberFormat('tr-TR').format(value);
    await page.goto('/');
    const card = page.getByTestId('card-parse-failures');
    await expect(card).toContainText(`Binde ${number(box.permille ?? 0)}`);
    await expect(card).toContainText(
      `Bugün ${number(box.failures)} hata / ${number(box.lines)} satır`,
    );
    const patterns = card.getByTestId('parse-patterns').getByRole('listitem');
    await expect(patterns).toHaveCount(box.top_patterns.length);
    for (const [index, pattern] of box.top_patterns.entries()) {
      await expect(patterns.nth(index)).toContainText(pattern.pattern);
      await expect(patterns.nth(index)).toContainText(
        `${pattern.source} · ${number(pattern.count)} satır`,
      );
    }
  });

  test("today's failures, the patterns, the example line and Arama", async ({ page }) => {
    const api = await panel(page, { signedInAs: 'tolga' });
    await page.goto('/');
    const card = page.getByTestId('card-parse-failures');
    await expect(card.getByRole('heading', { name: 'Ayrıştırma hataları' })).toBeVisible();
    await expect(card).toContainText('Binde 2,5');
    await expect(card).toContainText('Bugün 31 hata / 12.400 satır');
    await expect(card.getByRole('img', { name: /Ayrıştırma hataları, son 7 gün/ })).toBeVisible();
    const patterns = card.getByTestId('parse-patterns').getByRole('listitem');
    await expect(patterns).toHaveCount(2);
    await expect(patterns.first()).toContainText('fortigate.kv');
    await expect(patterns.first()).toContainText('fortigate · 30 satır');
    // A line not in the collector's format is not in the database: its example only.
    await expect(
      card.getByRole('button', { name: 'Aramada göster: rawlog.invalid kalıbı' }),
    ).toHaveCount(0);
    await card.getByRole('button', { name: 'Örnek satır: rawlog.invalid kalıbı' }).click();
    const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    await expect(raw).toContainText(RAW.lines['core-r1|2026-10-09|4']?.sha256 ?? '-');
    expect(api.callsTo('GET', '/raw').at(-1)?.query.get('line')).toBe('4');
    await raw.getByRole('button', { name: 'Kapat' }).click();

    await card.getByRole('button', { name: 'Aramada göster: fortigate.kv kalıbı' }).click();
    await expect(page).toHaveURL(/\/search$/);
    expect(await page.evaluate(() => JSON.stringify(window.history.state))).not.toContain(
      'fortigate.kv',
    );
    await page.goBack();
    await page
      .getByTestId('card-parse-failures')
      .getByRole('link', { name: 'Kaynaklar sayfasını aç' })
      .click();
    await expect(page).toHaveURL(/\/sources$/);
  });
});

/** Every card title on screen fits its box: it wraps instead of being cut ("Log..."). */
async function titlesFit(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const titles = Array.from(document.querySelectorAll<HTMLElement>('[data-testid^="card-"] h3'));
    return titles.flatMap((title) => {
      const parts = [title, ...Array.from(title.querySelectorAll<HTMLElement>('*'))];
      const cut = parts.some((element) => {
        const style = getComputedStyle(element);
        return (
          style.textOverflow === 'ellipsis' ||
          (element.scrollWidth > element.clientWidth && style.overflowX !== 'visible')
        );
      });
      return cut || title.scrollWidth > title.clientWidth ? [title.textContent] : [];
    });
  });
}

for (const lang of ['tr', 'en'] as const) {
  for (const width of [1280, 1440, 1920]) {
    test(`no card title is cut at ${width} px (${lang})`, async ({ page }) => {
      await usePreferences(page, { lang });
      await useSampleClock(page, SAMPLE_TODAY);
      await page.setViewportSize({ width, height: 1000 });
      await panel(page, { signedInAs: 'tolga' });
      await page.goto('/');
      await expect(page.getByTestId('card-usage')).toBeVisible();
      await expect(page.getByTestId('card-parse-failures')).toBeVisible();
      const titles = await page.locator('[data-testid^="card-"] h3').count();
      expect(titles).toBeGreaterThanOrEqual(10);
      expect(await titlesFit(page)).toEqual([]);
      // The page links sit in the card footers, not beside the titles.
      const link = page.getByTestId('card-ingest').getByRole('link', {
        name: lang === 'tr' ? 'Kaynaklar sayfasını aç' : 'Open the Sources page',
      });
      await expect(link).toBeVisible();
      const [linkBox, titleBox] = await Promise.all([
        link.boundingBox(),
        page.getByTestId('card-ingest').locator('h3').boundingBox(),
      ]);
      expect((linkBox?.y ?? 0) > (titleBox?.y ?? 0) + (titleBox?.height ?? 0)).toBe(true);
    });
  }
}

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Panel with the parse-failure card: accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await panel(page, { signedInAs: 'tolga' });
    await page.goto('/');
    await expect(page.getByTestId('card-parse-failures')).toContainText('fortigate.kv');
    await expect(page.getByTestId('card-usage')).toContainText('Aktif kullanıcı');
    await expectNoA11yViolations(page);
  });
}
