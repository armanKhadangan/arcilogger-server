/**
 * Kanıt Paketleri: all eight columns on screen (spec 12.3 page 10) the way Windows shows the page.
 * Playwright's headless Chromium hides scrollbars; Chrome and Edge on Windows draw classic ones that
 * take about 15 px of the table's width once the list scrolls. Hence this file starts Chromium
 * without `--hide-scrollbars` (a worker option: it must be set at the top of a file) and fills the
 * list with enough packages to scroll.
 */

import { expect, test, type Page } from '@playwright/test';

import { useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { copy, EVIDENCE, SAMPLE_TODAY } from './samples';

test.use({ launchOptions: { ignoreDefaultArgs: ['--hide-scrollbars'] } });

const READY = EVIDENCE.details['1'];
if (!READY) throw new Error('evidence samples missing');

const COLUMNS = [
  'Paket',
  'Oluşturan',
  'Oluşturma',
  'Kapsam',
  'Boyut',
  'Durum',
  'İndirme',
  'Son geçerlilik',
];

function table(page: Page) {
  return page.getByRole('table', { name: 'Kanıt paketleri, en yeni önce' });
}

/** Width the table needs vs. the width its scroll box shows (without the scrollbar). */
async function overflow(page: Page) {
  return table(page).evaluate((element) => {
    const box = element.parentElement;
    if (!box) throw new Error('no scroll box');
    return {
      scrollbar: box.offsetWidth - box.clientWidth - 2, // minus the 1 px borders
      clientWidth: box.clientWidth,
      scrollWidth: box.scrollWidth,
      verticalScroll: box.scrollHeight > box.clientHeight,
    };
  });
}

async function expectAllColumnsVisible(page: Page) {
  // Polled: right after "Menüyü daralt" the menu is still sliding in and the page still narrow.
  await expect
    .poll(async () => {
      const measured = await overflow(page);
      return measured.scrollWidth - measured.clientWidth;
    }, 'width the table needs beyond its scroll box')
    .toBeLessThanOrEqual(0);
  const measured = await overflow(page);
  expect(measured.verticalScroll, 'the list scrolls (35 packages)').toBe(true);
  expect(measured.scrollbar, 'Chromium draws a classic scrollbar').toBeGreaterThan(0);
  for (const name of COLUMNS)
    await expect(table(page).getByRole('columnheader', { name, exact: true })).toBeInViewport({
      ratio: 1,
    });
}

test('all eight columns stay on screen with a scrolling list and visible scrollbars', async ({
  page,
}) => {
  await useSampleClock(page, SAMPLE_TODAY);
  const api = await mockApi(page, { signedInAs: 'tolga' });
  for (let id = 100; id < 130; id += 1)
    api.records.packages.set(id, {
      ...copy(READY),
      id,
      package_name: `KP-20261013-07${String(id).padStart(4, '0')}-2d1a24b8`,
    });

  await page.goto('/evidence');
  await expect(
    table(page)
      .getByRole('row', { name: /KP-20261013-07/ })
      .first(),
  ).toBeVisible();
  await expect.poll(async () => (await overflow(page)).verticalScroll).toBe(true);
  await expectAllColumnsVisible(page);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole('button', { name: 'Menüyü daralt' }).click();
  await expectAllColumnsVisible(page);
});
