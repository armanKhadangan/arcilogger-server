import { expect, test, type Page } from '@playwright/test';

import type { DriveInfo } from '../src/lib/system-types';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { mockW4, type W4Options } from './mock-w4';
import { SAMPLE_TODAY, SYSTEM, SYSTEM_DISK, SYSTEM_HEALTH } from './samples';

/*
 * Sistem (`/system`, Wave 4 contract 2.8.2, §3 W4-F) on the answers captured from the real API
 * (`samples/system.json`, `system-disk.json`, `system-health.json` through `mock-w4.ts`, a
 * Windows host with the data drive `D:` and the system drive `C:`): the blocks in the contract's
 * order, the processor's first reading, the drives with their forecast sentences, the tables of
 * tasks, jobs and clock samples, the versions and how to upgrade; "Yenile" and the 30 s refresh;
 * a failed `GET /system/health` shown in the Saat block only (agreed on #24). The whole page in
 * light and dark is scanned in `w4-a11y.spec.ts`; here the error state is.
 */

const BLOCKS_TR = [
  'Kaynak kullanımı',
  'Diskler',
  'Veritabanı',
  'Toplayıcı',
  'İçe aktarma',
  'Hizmetler',
  'Zamanlanmış görevler',
  'Worker işleri',
  'Saat',
  'Sürüm ve güncelleme',
];

const BLOCKS_EN = [
  'Resource use',
  'Disks',
  'Database',
  'Collector',
  'Import',
  'Services',
  'Scheduled tasks',
  'Worker jobs',
  'Clock',
  'Version and upgrades',
];

/** Contract 2.8.2, the upgrade text. */
const UPGRADE_TR =
  "Yükseltme için yeni çevrimdışı paketi sunucuya kopyalayıp KURULUM.md bölüm 13'teki " +
  'adımları izleyin. ARCILOGGER güncelleme indirmez.';
const UPGRADE_EN =
  'To upgrade, copy the new offline bundle to the server and follow INSTALL.md section 13. ' +
  'ARCILOGGER downloads no updates.';

const TASK_STATES: Record<string, string> = {
  ok: 'Zamanında',
  late: 'Gecikti',
  missing: 'Kanıt yok',
  not_used: 'Kullanılmıyor',
  not_observable: 'İzlenemiyor',
};

const JOB_STATES: Record<string, string> = {
  ok: 'Tamam',
  failing: 'Hata veriyor',
  skipped: 'Atlandı',
  running: 'Çalışıyor',
  never: 'Hiç çalışmadı',
};

/** The real API's 503 (`api/src/arcilogger_api/errors.py`). */
const UNAVAILABLE = {
  error: {
    code: 'unavailable',
    message_tr: 'Hizmet şu anda kullanılamıyor (veritabanı?).',
    message_en: 'The service is unavailable right now (database?).',
  },
};

/** `2026-11-23` → `23.11.2026`. */
function dayText(day: string): string {
  return day.split('-').reverse().join('.');
}

/** Whole days from the site day of the summary's measurement to `day` (the "~N gün"). */
function daysUntil(day: string): number {
  const measured = SYSTEM.summary.checked_at.slice(0, 10);
  return Math.round(
    (Date.parse(`${day}T00:00:00Z`) - Date.parse(`${measured}T00:00:00Z`)) / 86_400_000,
  );
}

function drive(path: string, drives: DriveInfo[] = SYSTEM.summary.drives): DriveInfo {
  const found = drives.find((item) => item.path === path);
  if (!found) throw new Error(`the capture has no drive ${path}`);
  return found;
}

async function open(page: Page, options: W4Options = {}) {
  const api = await mockApi(page, { signedInAs: 'tolga' });
  const w4 = await mockW4(page, api, options);
  await page.goto('/system');
  await expect(page.getByRole('heading', { level: 1, name: 'Sistem' })).toBeVisible();
  await expect(block(page, 'clock')).toBeVisible();
  return { api, w4 };
}

function block(page: Page, id: string) {
  return page.getByTestId(`system-block-${id}`);
}

test.describe('Sistem', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('administrator: every block of the captured Windows host in the contract order', async ({
    page,
  }) => {
    const { api } = await open(page);
    await expect(
      page.getByText('Sunucunun kaynakları, hizmetleri, zamanlanmış görevleri ve saati.'),
    ).toBeVisible();
    await expect(page.getByTestId('system-blocks').getByRole('heading', { level: 2 })).toHaveText(
      BLOCKS_TR,
    );

    // Kaynak kullanımı: the processor as measured, the memory with its share.
    const host = SYSTEM.summary.host;
    expect(host.cpu_percent).not.toBeNull();
    await expect(block(page, 'resources')).toContainText(`%${String(host.cpu_percent)}`);
    await expect(block(page, 'resources')).toContainText('19 GB / 32 GB (%59)');
    await expect(page.getByTestId('system-cpu-pending')).toHaveCount(0);

    // Diskler: one meter per drive, the value as text, the forecast sentence under it.
    const data = drive('D:');
    const system = drive('C:');
    const meters = block(page, 'disks').getByRole('meter');
    await expect(meters).toHaveCount(2);
    await expect(meters.nth(0)).toHaveAccessibleName('D: sürücüsünün doluluğu');
    await expect(meters.nth(0)).toHaveAttribute('aria-valuenow', String(data.used_percent));
    await expect(meters.nth(1)).toHaveAccessibleName('C: sürücüsünün doluluğu');
    await expect(block(page, 'disks')).toContainText(`%${String(data.used_percent)} dolu`);
    await expect(block(page, 'disks')).toContainText('Veri sürücüsü');
    await expect(block(page, 'disks')).toContainText('Sistem sürücüsü');
    await expect(block(page, 'disks')).toContainText('Uyarı eşiği %80 · kritik eşik %95');
    const { warning_on: warningOn, full_on: fullOn } = data.forecast;
    expect(data.forecast.state).toBe('estimated');
    if (!warningOn || !fullOn) throw new Error('the data drive has no forecast days');
    await expect(block(page, 'disks').getByTestId('drive-forecast')).toHaveText([
      `Bu hızla %${String(data.warning_percent)} eşiğine ${dayText(warningOn)}, dolmaya ` +
        `${dayText(fullOn)} (~${String(daysUntil(fullOn))} gün).`,
      `Tahmin için en az 7 günlük ölçüm gerekiyor (${String(system.forecast.samples)}/7).`,
    ]);

    // Veritabanı: the total and the largest tables, largest first.
    await expect(block(page, 'database')).toContainText('Toplam boyut');
    const tables = page.getByTestId('system-tables');
    await expect(tables.getByRole('row')).toHaveCount(SYSTEM.summary.database.tables.length + 1);
    await expect(tables.getByRole('rowheader')).toHaveText(
      SYSTEM.summary.database.tables.map((table) => table.name),
    );

    // Hizmetler, Zamanlanmış görevler, Worker işleri: one row each, the state as text.
    const health = SYSTEM_HEALTH.health;
    await expect(page.getByTestId('system-services').getByRole('rowheader')).toHaveText([
      ...health.services.map((service) => service.name),
      'Yönetim hizmeti',
      'Sistem ayarlarının uygulanması (ApplySystem)',
    ]);
    const tasks = page.getByTestId('system-tasks');
    await expect(tasks.getByRole('rowheader')).toHaveText(health.tasks.map((task) => task.task));
    for (const task of health.tasks) {
      const row = tasks
        .getByRole('row')
        .filter({ has: page.getByRole('rowheader', { name: task.task, exact: true }) });
      await expect(row).toContainText(task.schedule_tr);
      await expect(row).toContainText(TASK_STATES[task.state] ?? task.state);
    }
    const jobs = page.getByTestId('system-jobs');
    await expect(jobs.getByRole('rowheader')).toHaveText(health.jobs.map((job) => job.job));
    for (const job of health.jobs) {
      const row = jobs
        .getByRole('row')
        .filter({ has: page.getByRole('rowheader', { name: job.job, exact: true }) });
      await expect(row).toContainText(JOB_STATES[job.state] ?? job.state);
    }

    // Saat: the configured servers, what ApplySystem applied, the samples of 24 hours.
    const time = health.time;
    const clock = block(page, 'clock');
    if (time.ntp_server) await expect(clock).toContainText(time.ntp_server);
    else await expect(clock).toContainText('Ayarlanmamış');
    await expect(clock).toContainText(time.fallback_server ?? '-');
    await expect(clock).toContainText(time.applied_peers ?? '-');
    await expect(page.getByTestId('system-clock-samples').getByRole('row')).toHaveCount(
      time.samples.length + 1,
    );
    await expect(clock).toContainText('3,2 ms');
    // The probe no server answered.
    await expect(clock.getByText('Yanıt yok', { exact: true })).toHaveCount(
      time.samples.filter((sample) => !sample.ok).length,
    );

    // Sürüm ve güncelleme: the versions and the upgrade by hand.
    const version = SYSTEM.summary.version;
    await expect(block(page, 'version')).toContainText(version.product);
    await expect(block(page, 'version')).toContainText(version.version);
    await expect(page.getByTestId('system-maintenance')).toBeVisible();
    await expect(page.getByTestId('system-upgrade')).toHaveText(UPGRADE_TR);

    // Neither request failed, and both were the user's own.
    await expect(page.getByText('Sistem durumu alınamadı.')).toHaveCount(0);
    await expect(page.getByTestId('system-health-error')).toHaveCount(0);
    for (const path of ['/system', '/system/health']) {
      expect(api.callsTo('GET', path)[0]?.background, path).toBe(false);
    }
    // The page reads the summary, not the drives alone (the Panel's request).
    expect(api.callsTo('GET', '/system/disk')).toHaveLength(0);
  });

  test('the first reading of the processor says "ölçülüyor"', async ({ page }) => {
    await open(page, { firstReading: true });
    expect(SYSTEM.summary_first_reading.host.cpu_percent).toBeNull();
    await expect(page.getByTestId('system-cpu-pending')).toHaveText('ölçülüyor');
    await expect(block(page, 'resources')).toContainText('19 GB / 32 GB (%59)');
  });

  test('a data drive that did not grow says so', async ({ page }) => {
    await open(page, { diskNotGrowing: true });
    const data = drive('D:', SYSTEM_DISK.disk_not_growing.drives);
    expect(data.forecast.state).toBe('not_growing');
    await expect(block(page, 'disks').getByRole('meter').nth(0)).toHaveAttribute(
      'aria-valuenow',
      String(data.used_percent),
    );
    await expect(block(page, 'disks').getByTestId('drive-forecast').nth(0)).toHaveText(
      'Son 30 günde disk kullanımı artmadı.',
    );
  });

  test('"Yenile" asks the summary and the health again as the user', async ({ page }) => {
    const { api } = await open(page);
    const before = ['/system', '/system/health'].map((path) => api.callsTo('GET', path).length);
    await page.getByRole('button', { name: 'Yenile' }).click();
    await expect.poll(() => api.callsTo('GET', '/system').length).toBe((before[0] ?? 0) + 1);
    await expect.poll(() => api.callsTo('GET', '/system/health').length).toBe((before[1] ?? 0) + 1);
    for (const path of ['/system', '/system/health'])
      expect(
        api.callsTo('GET', path).every((call) => !call.background),
        path,
      ).toBe(true);
  });

  test('a failed health request shows in the Saat block only; the other blocks stay', async ({
    page,
  }) => {
    const { api } = await open(page);
    let failing = true;
    await page.route('**/api/v1/system/health', (route) =>
      failing ? route.fulfill({ status: 503, json: UNAVAILABLE }) : route.fallback(),
    );
    await page.getByRole('button', { name: 'Yenile' }).click();
    const error = block(page, 'clock').getByTestId('system-health-error');
    await expect(error).toContainText(
      'Hizmetlerin, görevlerin, işlerin ve saatin durumu alınamadı.',
    );
    await expect(error).toContainText(`${UNAVAILABLE.error.message_tr} (unavailable)`);
    await expect(page.getByTestId('system-health-error')).toHaveCount(1);
    // Not the page's error; the data shown before stays, in every block.
    await expect(page.getByText('Sistem durumu alınamadı.')).toHaveCount(0);
    await expect(page.getByTestId('system-blocks').getByRole('heading', { level: 2 })).toHaveText(
      BLOCKS_TR,
    );
    await expect(block(page, 'disks').getByRole('meter')).toHaveCount(2);
    await expect(page.getByTestId('system-tasks').getByRole('rowheader')).toHaveCount(
      SYSTEM_HEALTH.health.tasks.length,
    );

    failing = false;
    const asked = api.callsTo('GET', '/system/health').length;
    await error.getByRole('button', { name: 'Tekrar dene' }).click();
    await expect(page.getByTestId('system-health-error')).toHaveCount(0);
    expect(api.callsTo('GET', '/system/health').length).toBeGreaterThan(asked);
    expect(api.callsTo('GET', '/system/health').at(-1)?.background).toBe(false);
  });

  test('a health request that fails first: the other health blocks point to the Saat block', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await mockW4(page, api);
    await page.route('**/api/v1/system/health', (route) =>
      route.fulfill({ status: 503, json: UNAVAILABLE }),
    );
    await page.goto('/system');
    await expect(block(page, 'clock').getByTestId('system-health-error')).toContainText(
      `${UNAVAILABLE.error.message_tr} (unavailable)`,
    );
    for (const id of ['services', 'tasks', 'jobs'])
      await expect(block(page, id).getByTestId('system-health-missing')).toHaveText(
        'Durum alınamadı; hata Saat bölümünde.',
      );
    await expect(block(page, 'disks').getByRole('meter')).toHaveCount(2);
    await expect(page.getByText('Sistem durumu alınamadı.')).toHaveCount(0);
  });

  test('English: the blocks, the forecast and the upgrade text', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockW4(page, await mockApi(page, { signedInAs: 'tolga' }));
    await page.goto('/system');
    await expect(page.getByRole('heading', { level: 1, name: 'System' })).toBeVisible();
    await expect(
      page.getByText("The server's resources, services, scheduled tasks and clock."),
    ).toBeVisible();
    await expect(page.getByTestId('system-blocks').getByRole('heading', { level: 2 })).toHaveText(
      BLOCKS_EN,
    );
    const data = drive('D:');
    const { warning_on: warningOn, full_on: fullOn } = data.forecast;
    if (!warningOn || !fullOn) throw new Error('the data drive has no forecast days');
    await expect(block(page, 'disks').getByTestId('drive-forecast')).toHaveText([
      `At this rate the ${String(data.warning_percent)}% mark is reached on ${dayText(warningOn)}, ` +
        `full on ${dayText(fullOn)} (~${String(daysUntil(fullOn))} days).`,
      `A forecast needs at least 7 days of samples (${String(drive('C:').forecast.samples)}/7).`,
    ]);
    await expect(page.getByTestId('system-upgrade')).toHaveText(UPGRADE_EN);
  });
});

test('Sistem refreshes the summary and the health every 30 s in the background', async ({
  page,
}) => {
  await page.clock.install({ time: new Date(`${SAMPLE_TODAY}T15:00:00+03:00`) });
  const { api } = await open(page);
  const before = ['/system', '/system/health'].map((path) => api.callsTo('GET', path).length);
  await page.clock.fastForward('00:31');
  await expect.poll(() => api.callsTo('GET', '/system').length).toBeGreaterThan(before[0] ?? 0);
  await expect
    .poll(() => api.callsTo('GET', '/system/health').length)
    .toBeGreaterThan(before[1] ?? 0);
  for (const path of ['/system', '/system/health']) {
    const calls = api.callsTo('GET', path);
    expect(calls[0]?.background, path).toBe(false);
    expect(calls.at(-1)?.background, path).toBe(true);
  }
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`Sistem accessibility: a failed health request — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await useSampleClock(page, SAMPLE_TODAY);
    await open(page, { firstReading: true });
    await page.route('**/api/v1/system/health', (route) =>
      route.fulfill({ status: 503, json: UNAVAILABLE }),
    );
    await page.getByRole('button', { name: 'Yenile' }).click();
    await expect(page.getByTestId('system-health-error')).toBeVisible();
    await expect(page.getByTestId('system-cpu-pending')).toBeVisible();
    await expectNoA11yViolations(page);
  });
}
