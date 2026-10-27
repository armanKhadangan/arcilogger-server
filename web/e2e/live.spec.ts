import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi, type MockOptions } from './mock-api';
import { GAP_TEXTS, liveLine, mockLive, type LiveMock } from './mock-live';
import { RAW, SAMPLE_TODAY } from './samples';

/**
 * Canlı Akış (contract 2.14.4–2.14.6 and 2.22.3, `search.read` + KVKK): the filter as one query,
 * the two-step stream (POST, then a GET read with fetch and `X-Arci-Background: 1`), pause and
 * resume with `Last-Event-ID`, reconnects, the 2,000-line list, the dropped/gap/end banners, the
 * line drawer and the highlight rules (`sessionStorage` only). The API is `mock-live.ts`, whose
 * stream is a real event stream.
 */

async function open(
  page: Page,
  options: MockOptions = {},
): Promise<{ api: MockApi; live: LiveMock }> {
  const api = await mockApi(page, { signedInAs: 'tolga', ...options });
  const live = await mockLive(page, api);
  return { api, live };
}

function list(page: Page) {
  return page.getByRole('list', { name: 'Canlı satırlar' });
}

function status(page: Page) {
  return page.getByTestId('live-status');
}

async function start(page: Page, live: LiveMock): Promise<void> {
  await page.getByRole('button', { name: 'Başlat', exact: true }).click();
  await expect(status(page)).toContainText('Bağlı');
  await expect.poll(() => live.connections()).toBe(1);
}

test.describe('Canlı Akış', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('starts on Başlat with the filter as one query; every GET is a background request', async ({
    page,
  }) => {
    const { api, live } = await open(page);
    await page.goto('/');
    await page
      .getByRole('navigation', { name: 'Ana menü' })
      .getByRole('link', { name: 'Canlı Akış' })
      .click();
    await expect(page.getByRole('heading', { level: 1, name: 'Canlı Akış' })).toBeVisible();
    await expect(page.getByText('Bu sayfa kişisel veri gösterir')).toBeVisible();
    // Nothing streams before the user asks (each stream is audited with its filter).
    expect(api.callsTo('POST', '/live/streams')).toHaveLength(0);

    await page.getByRole('button', { name: 'Kaynaklar: tümü' }).click();
    await page.getByRole('menuitemcheckbox', { name: 'core-r1' }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Bilinmeyen göndericiler' }).click();
    await page
      .getByRole('group', { name: 'Kayıt türleri' })
      .getByRole('button', { name: 'DHCP' })
      .click();
    await page.getByLabel('Metin veya sorgu').fill('srcip:172.20.42.57');
    await expect(page.getByTestId('live-query')).toHaveText(
      'source:core-r1,unknown-* type:dhcp srcip:172.20.42.57',
    );
    await start(page, live);
    const post = api.callsTo('POST', '/live/streams')[0];
    expect(post?.body).toEqual({ q: 'source:core-r1,unknown-* type:dhcp srcip:172.20.42.57' });
    expect(post?.background).toBe(false);
    expect(post?.csrf).toBe('e2e-csrf-token');

    live.send(3);
    await expect(list(page).getByRole('listitem')).toHaveCount(3);
    await expect(list(page).getByRole('listitem').first()).toContainText('core-r1');
    await expect(status(page)).toContainText('3 satır gösteriliyor');
    expect(live.gets).toHaveLength(1);
    expect(live.gets.every((get) => get.background)).toBe(true);
    // The query travels in the POST body only: the stream's address holds a random id.
    expect(api.callsTo('GET', '/live/streams/akis-1')[0]?.query.toString()).toBe('');

    // Leaving the page closes the stream (not the user's doing: a background request).
    await page
      .getByRole('navigation', { name: 'Ana menü' })
      .getByRole('link', { name: 'Panel' })
      .click();
    await expect.poll(() => api.callsTo('DELETE', '/live/streams/akis-1').length).toBe(1);
    expect(api.callsTo('DELETE', '/live/streams/akis-1')[0]?.background).toBe(true);
  });

  test('a streamed body: hello, lines, dropped and end', async ({ page }) => {
    const { live } = await open(page);
    const [first, second] = [liveLine(8), liveLine(9)];
    // One whole body (route.fulfill): the stream ends with `end`, so it does not reconnect.
    await page.route('**/api/v1/live/streams/akis-1', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      live.gets.push({
        id: 'akis-1',
        lastEventId: route.request().headers()['last-event-id'] ?? null,
        background: route.request().headers()['x-arci-background'] === '1',
      });
      const frames = [
        `event: hello\ndata: ${JSON.stringify({ stream_id: 'akis-1', epoch: 'e1', notice_version: '1', folders: [], limits: { max_lines_per_second: 100, keepalive_seconds: 15, buffer_lines: 5000, max_minutes: 60 } })}\n\n`,
        `id: e1:1\nevent: line\ndata: ${JSON.stringify({ ...first, seq: 1 })}\n\n`,
        ': ka\n\n',
        `id: e1:2\nevent: line\ndata: ${JSON.stringify({ ...second, seq: 2 })}\n\n`,
        `event: dropped\ndata: ${JSON.stringify({ rate_limited: 120, slow_client: 0, parse_budget: 0, behind: 0, since: '2026-10-13T12:00:00Z', message_tr: '120 satır gösterilmedi: saniyede en fazla 100 satır gösterilir.', message_en: '120 lines were not shown: at most 100 lines a second are shown.' })}\n\n`,
        `event: end\ndata: ${JSON.stringify({ code: 'time_limit', message_tr: 'Canlı akış 60 dakika sonra durduruldu (gözetimsiz ekran). Sürdürmek için “Sürdür”e basın.', message_en: 'The live tail stopped after 60 minutes (unattended screen). Press “Resume” to continue.' })}\n\n`,
      ];
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-store',
        },
        body: frames.join(''),
      });
    });
    await page.goto('/live');
    await page.getByRole('button', { name: 'Başlat', exact: true }).click();
    await expect(list(page).getByRole('listitem')).toHaveCount(2);
    await expect(page.getByTestId('live-dropped')).toContainText(
      '120 satır gösterilmedi: saniyede en fazla 100 satır gösterilir.',
    );
    await expect(page.getByTestId('live-dropped')).toContainText('hız sınırı: 120');
    await expect(page.getByTestId('live-end')).toContainText('60 dakika sonra durduruldu');
    await expect(status(page)).toContainText('Durdu');
    await expect(status(page)).toContainText('120 satır gösterilmedi');
    await page.waitForTimeout(1_500);
    expect(live.gets).toHaveLength(1);
    expect(live.gets[0]?.background).toBe(true);
    // "Sürdür" after the time limit opens a new stream.
    await page.getByTestId('live-end').getByRole('button', { name: 'Sürdür' }).click();
    await expect.poll(() => live.streams.size).toBe(2);
  });

  test('pause stops reading; resume sends Last-Event-ID and gets the missed lines', async ({
    page,
  }) => {
    const { live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    const [, second] = live.send(2);
    await expect(list(page).getByRole('listitem')).toHaveCount(2);
    await page.getByRole('button', { name: 'Duraklat' }).click();
    await expect(status(page)).toContainText('Duraklatıldı');
    await expect.poll(() => live.connections()).toBe(0);
    live.send(3);
    await page.waitForTimeout(500);
    await expect(list(page).getByRole('listitem')).toHaveCount(2);
    await page.getByRole('button', { name: 'Sürdür' }).click();
    await expect(list(page).getByRole('listitem')).toHaveCount(5);
    await expect(status(page)).toContainText('Bağlı');
    expect(live.gets.map((get) => get.lastEventId)).toEqual([
      null,
      `e2e-epoch-1:${second?.seq ?? 0}`,
    ]);
    expect(live.gets.every((get) => get.background)).toBe(true);
  });

  test('reconnects after a dropped connection with Last-Event-ID', async ({ page }) => {
    const { api, live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    live.send(2);
    await expect(list(page).getByRole('listitem')).toHaveCount(2);
    live.drop();
    await expect(status(page)).toContainText('Yeniden bağlanıyor');
    live.send(1);
    await expect(status(page)).toContainText('Bağlı', { timeout: 5_000 });
    await expect(list(page).getByRole('listitem')).toHaveCount(3);
    expect(live.gets.at(-1)?.lastEventId).toBe('e2e-epoch-1:2');
    expect(live.gets.every((get) => get.background)).toBe(true);
    expect(api.callsTo('POST', '/live/streams')).toHaveLength(1);
  });

  test('a 404 after the API restarted opens a new stream on its own and says so', async ({
    page,
  }) => {
    const { api, live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    live.send(2);
    await expect(list(page).getByRole('listitem')).toHaveCount(2);
    live.restart();
    const gap = page.getByTestId('live-gap');
    await expect(gap).toContainText(
      "Aradaki satırlar gösterilmemiş olabilir; bu aralığı Arama'da açın.",
      { timeout: 6_000 },
    );
    await expect(status(page)).toContainText('Bağlı');
    const posts = api.callsTo('POST', '/live/streams');
    expect(posts).toHaveLength(2);
    expect(posts[1]?.body).toEqual({ q: '', backfill: 0 });
    expect(posts[1]?.background).toBe(true);
    live.send(1);
    await expect(list(page).getByRole('listitem')).toHaveCount(3);
    await gap.getByRole('button', { name: "Bu aralığı Arama'da aç" }).click();
    await expect(page).toHaveURL(/\/search$/);
  });

  test('keeps the newest 2,000 lines; scrolled up it stays and counts the new ones', async ({
    page,
  }) => {
    const { live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    live.send(2_100);
    await expect(status(page)).toContainText('2.000 satır gösteriliyor', { timeout: 10_000 });
    const scroller = page.getByTestId('live-list');
    // At the bottom: the newest line is in view.
    await expect(list(page).locator('li[data-line="fortigate/2026-10-13/2100"]')).toBeVisible();
    await scroller.evaluate((element) => {
      element.scrollTop = 0;
    });
    // The 100 oldest lines are gone.
    await expect(list(page).locator('li').first()).toHaveAttribute(
      'data-line',
      'fortigate/2026-10-13/101',
    );
    live.send(5);
    await expect(page.getByTestId('live-unseen')).toHaveText('5 yeni satır');
    await page.getByTestId('live-unseen').click();
    await expect(list(page).locator('li[data-line="fortigate/2026-10-13/2105"]')).toBeVisible();
    await expect(page.getByTestId('live-unseen')).toHaveCount(0);
  });

  test('dropped and gap banners; the gap opens its interval in Arama', async ({ page }) => {
    const { live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    live.send(1);
    live.event('dropped', {
      rate_limited: 0,
      slow_client: 0,
      parse_budget: 0,
      behind: 4_200,
      since: '2026-10-13T12:00:00Z',
      message_tr: '4200 satır gösterilmedi: dosya çok hızlı büyüdü.',
      message_en: '4200 lines were not shown: the file grew too fast.',
    });
    await expect(page.getByTestId('live-dropped')).toContainText('dosya çok hızlı büyüdü: 4.200');
    live.event('gap', {
      reason: 'resume_too_late',
      missed: 40,
      message_tr: GAP_TEXTS[0],
      message_en: GAP_TEXTS[1],
    });
    const gap = page.getByTestId('live-gap');
    await expect(gap).toContainText(GAP_TEXTS[0]);
    await gap.getByRole('button', { name: "Bu aralığı Arama'da aç" }).click();
    await expect(page).toHaveURL(/\/search$/);
    // Handed over in memory: the history entry carries no state of its own.
    expect(
      await page.evaluate(() => (window.history.state as { usr?: unknown } | null)?.usr ?? null),
    ).toBeNull();
  });

  test('end session_idle signs the viewer out like every page', async ({ page }) => {
    const { api, live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    api.session = null;
    live.end('session_idle');
    await expect(page).toHaveURL(/\/login\?next=%2Flive&reason=session_idle$/);
  });

  test('a refusal: the 429 text, a query the server cannot read', async ({ page }) => {
    const { live } = await open(page);
    live.refuseNext = { status: 429, code: 'live_stream_limit' };
    await page.goto('/live');
    await page.getByRole('button', { name: 'Başlat', exact: true }).click();
    const failure = page.getByTestId('live-failure');
    await expect(failure).toContainText(
      'Aynı anda en fazla 2 canlı akış açabilirsiniz (sunucuda toplam 4); başka bir sekmedeki akışı kapatın.',
    );
    await expect(status(page)).toContainText('Durdu');
    await page.getByLabel('Metin veya sorgu').fill('banana:1');
    await page.getByLabel('Metin veya sorgu').press('Enter');
    await expect(failure).toContainText('“banana” diye bir alan yok.');
    expect(live.gets).toHaveLength(0);
  });

  test('the line drawer: preview fields, the raw line and Arama five minutes around it', async ({
    page,
  }) => {
    const { live } = await open(page);
    await page.goto('/live');
    await start(page, live);
    live.send(2);
    await list(page).getByRole('listitem').first().getByRole('button').click();
    const sheet = page.getByRole('dialog', { name: 'Canlı satır' });
    await expect(sheet).toContainText('önizleme');
    await expect(sheet).toContainText('core-r1 · 09.10.2026 · satır 1');
    await expect(sheet).toContainText('00:00:5e:00:53:57');
    await sheet.getByRole('button', { name: 'Ham satır' }).click();
    const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    await expect(raw).toContainText(RAW.lines['core-r1|2026-10-09|1']?.sha256 ?? '-');
    await raw.getByRole('button', { name: 'Kapat' }).click();
    await sheet.getByRole('button', { name: "Arama'da aç (±5 dk)" }).click();
    await expect(page).toHaveURL(/\/search$/);
  });

  test('highlight rules: kept for the tab (a reload keeps them, a new tab has none)', async ({
    page,
    context,
  }) => {
    const { live } = await open(page);
    await page.goto('/live');
    await page.getByRole('button', { name: 'Vurgulama kuralları' }).click();
    const dialog = page.getByRole('dialog', { name: 'Vurgulama kuralları' });
    await dialog.getByRole('button', { name: 'Kural ekle' }).click();
    await dialog.getByLabel('Kural 1', { exact: true }).fill('deny');
    await dialog.getByRole('button', { name: 'Kural ekle' }).click();
    await dialog.getByLabel('Kural 2', { exact: true }).fill('source:core-r1');
    await expect(dialog).toContainText('Alan kuralı: satırın tamamı işaretlenir.');
    await dialog.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByRole('button', { name: 'Vurgulama kuralları (2)' })).toBeVisible();
    await start(page, live);
    live.send(10);
    await expect(list(page).getByRole('listitem')).toHaveCount(10);
    // Line 10 is a denied FortiGate connection; the captured lines are core-r1's.
    await expect(list(page).locator('li[data-line="fortigate/2026-10-13/10"] mark')).toHaveText(
      'deny',
    );
    await expect(
      list(page).locator('li[data-line="core-r1/2026-10-09/1"] button[data-highlight="red"]'),
    ).toHaveCount(1);
    const stored = await page.evaluate(() => ({
      session: window.sessionStorage.getItem('arci.live.highlights'),
      local: Object.keys(window.localStorage).filter((key) => key.includes('highlight')),
    }));
    expect(JSON.parse(stored.session ?? '[]')).toEqual([
      { match: 'deny', color: 'yellow' },
      { match: 'source:core-r1', color: 'red' },
    ]);
    expect(stored.local).toEqual([]);

    await page.reload();
    await expect(page.getByRole('button', { name: 'Vurgulama kuralları (2)' })).toBeVisible();

    const other = await context.newPage();
    await mockLive(other, await mockApi(other, { signedInAs: 'tolga' }));
    await other.goto('/live');
    await expect(
      other.getByRole('button', { name: 'Vurgulama kuralları', exact: true }),
    ).toBeVisible();
  });

  test('the auditor may follow the stream too', async ({ page }) => {
    const { live } = await open(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/live');
    await start(page, live);
    live.send(1);
    await expect(list(page).getByRole('listitem')).toHaveCount(1);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Canlı Akış accessibility — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await page.addInitScript(() => {
      window.sessionStorage.setItem(
        'arci.live.highlights',
        JSON.stringify([
          { match: 'deny', color: 'yellow' },
          { match: 'source:core-r1', color: 'teal' },
          { match: 'action:accept', color: 'sky' },
        ]),
      );
    });
    const { live } = await open(page);
    await page.goto('/live');
    await expect(page.getByRole('heading', { level: 1, name: 'Canlı Akış' })).toBeVisible();
    await expectNoA11yViolations(page);
    await start(page, live);
    live.send(12);
    await expect(list(page).getByRole('listitem')).toHaveCount(12);
    await expectNoA11yViolations(page);
    await list(page).getByRole('listitem').first().getByRole('button').click();
    await expect(page.getByRole('dialog', { name: 'Canlı satır' })).toBeVisible();
    await expectNoA11yViolations(page);
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: /Vurgulama kuralları/ }).click();
    await expect(page.getByRole('dialog', { name: 'Vurgulama kuralları' })).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
