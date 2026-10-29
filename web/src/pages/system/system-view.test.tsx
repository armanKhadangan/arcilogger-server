import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ApiError } from '@/lib/api';
import type { LicenseOut } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import type { SystemHealth } from '@/lib/system-health-types';
import type { SystemSummary } from '@/lib/system-types';
import { makeMe } from '@/test/api-mock';
import { need } from '@/test/samples';
import {
  collectorInfo,
  databaseInfo,
  driveInfo,
  hostInfo,
  ingestInfo,
  systemHealth,
  systemLicense,
  systemSummary,
  W4_NOW,
} from '@/test/w4-fixtures';

import { SystemView, type SystemViewProps } from './system-view';

/*
 * Sistem (Wave 4 contract 2.8.2): the view of the page, given the answers of `GET /system`,
 * `GET /system/health` and `GET /license` as props (W4-F fetches them).
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

const UPGRADE_TR =
  "Yükseltme için yeni çevrimdışı paketi sunucuya kopyalayıp KURULUM.md bölüm 13'teki adımları izleyin. ARCILOGGER güncelleme indirmez.";
const UPGRADE_EN =
  'To upgrade, copy the new offline bundle to the server and follow INSTALL.md section 13. ARCILOGGER downloads no updates.';

const IPV4 = /\b\d{1,3}(?:\.\d{1,3}){3}\b/g;

function renderView(props: Partial<SystemViewProps> = {}) {
  const onRefresh = vi.fn();
  const view = render(
    <SystemView
      summary={systemSummary()}
      health={systemHealth()}
      license={systemLicense()}
      error={null}
      loading={false}
      updatedAt={Date.parse(W4_NOW)}
      onRefresh={onRefresh}
      {...props}
    />,
  );
  return { ...view, onRefresh };
}

function withSummary(change: (summary: SystemSummary) => void): SystemSummary {
  const summary = systemSummary();
  change(summary);
  return summary;
}

const block = (id: string) => screen.getByTestId(`system-block-${id}`);

afterEach(() => {
  act(() => {
    setLanguage('tr');
  });
});

describe('SystemView', () => {
  it('shows the page header, the update time and the blocks in the order of the contract', async () => {
    const { onRefresh } = renderView();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Sistem');
    expect(
      screen.getByText('Sunucunun kaynakları, hizmetleri, zamanlanmış görevleri ve saati.'),
    ).toBeInTheDocument();
    const updated = screen.getByText('Güncellendi 07:00:00');
    expect(updated).toHaveAttribute('aria-live', 'polite');
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(BLOCKS_TR);
    await userEvent.click(screen.getByRole('button', { name: 'Yenile' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows CPU, memory and uptime, "ölçülüyor" for the first reading and "okunamıyor" where none is read', () => {
    const { unmount } = renderView();
    const measured = block('resources');
    expect(measured).toHaveTextContent('İşlemci%23');
    expect(measured).toHaveTextContent('Bellek9 GB / 16 GB (%56)');
    expect(measured).toHaveTextContent('Çalışma süresi10 gün 3 sa');
    unmount();

    renderView({ summary: withSummary((summary) => (summary.host = hostInfo('first_reading'))) });
    expect(screen.getByTestId('system-cpu-pending')).toHaveTextContent('ölçülüyor');
    expect(block('resources')).toHaveTextContent('Bellek9 GB / 16 GB (%56)');
    act(() => {
      setLanguage('en');
    });
    expect(screen.getByTestId('system-cpu-pending')).toHaveTextContent('being measured');
  });

  it('says "bu platformda okunamıyor" for every host value the platform does not give', () => {
    renderView({ summary: withSummary((summary) => (summary.host = hostInfo('unreadable'))) });
    const resources = block('resources');
    expect(within(resources).getAllByText('bu platformda okunamıyor')).toHaveLength(3);
    expect(resources).not.toHaveTextContent('ölçülüyor');
  });

  it('shows a meter per drive with its value as text and the forecast under it', () => {
    renderView();
    const disks = block('disks');
    const [data, system] = within(disks).getAllByRole('meter');
    expect(data).toHaveAccessibleName('D: sürücüsünün doluluğu');
    expect(data).toHaveAttribute('aria-valuenow', '54');
    expect(data).toHaveAttribute('aria-valuemin', '0');
    expect(data).toHaveAttribute('aria-valuemax', '100');
    expect(data).toHaveAttribute(
      'aria-valuetext',
      expect.stringMatching(/^%54 dolu \(1 TB \/ 1,8 TB/),
    );
    expect(system).toHaveAttribute('aria-valuenow', '84');
    // The level is a badge of icon and text, and the percentage is written beside the meter.
    const meters = within(disks).getAllByTestId('drive-meter');
    expect(meters[0]).toHaveTextContent('%54 dolu');
    expect(meters[0]).toHaveTextContent('Olağan');
    expect(meters[1]).toHaveTextContent('Uyarı eşiğinin üstünde');
    expect(meters[1]).toHaveTextContent('Uyarı eşiği %80 · kritik eşik %95');
    for (const meter of meters)
      expect(meter.querySelector('span[class*="bg-"] > svg[aria-hidden="true"]')).not.toBeNull();
    expect(
      within(disks)
        .getAllByTestId('drive-forecast')
        .map((line) => line.textContent),
    ).toEqual([
      'Bu hızla %80 eşiğine 30.12.2026, dolmaya 02.03.2027 (~140 gün).',
      'Tahmin için en az 7 günlük ölçüm gerekiyor (4/7).',
    ]);
  });

  it('draws both marks when the warning mark is the critical one', () => {
    // `alerts.disk_used_percent` may be 95, the critical mark: two marks, no duplicate key.
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderView({
      summary: withSummary(
        (summary) => (summary.drives = [{ ...driveInfo('data'), warning_percent: 95 }]),
      ),
    });
    expect(screen.getByRole('meter').querySelectorAll('span[aria-hidden="true"]')).toHaveLength(2);
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });

  it('says when the disk use did not grow', () => {
    renderView({
      summary: withSummary(
        (summary) => (summary.drives = [driveInfo('data', { forecast: 'not_growing' })]),
      ),
    });
    expect(screen.getByTestId('drive-forecast')).toHaveTextContent(
      'Son 30 günde disk kullanımı artmadı.',
    );
  });

  it('shows the database size and its largest tables, or that it could not be measured', () => {
    const { unmount } = renderView();
    const database = block('database');
    expect(database).toHaveTextContent('Toplam boyut384 GB');
    expect(within(database).getAllByRole('row')).toHaveLength(11);
    unmount();
    renderView({
      summary: withSummary((summary) => (summary.database = databaseInfo('unmeasured'))),
    });
    expect(block('database')).toHaveTextContent('Boyut ölçülemedi.');
    expect(within(block('database')).queryByRole('table')).toBeNull();
  });

  it('counts unknown senders and never shows a sender address, a person or a host name', () => {
    renderView();
    const collector = block('collector');
    expect(within(collector).getByTestId('system-unknown-senders')).toHaveTextContent(
      '2 gönderici, 37 mesaj, 6,4 KB',
    );
    expect(collector).toHaveTextContent('Adresleri burada gösterilmez');
    // Configured sources by their configured name only.
    expect(
      within(within(collector).getByTestId('system-collector-sources'))
        .getAllByRole('row')
        .slice(1)
        .map((row) => row.firstChild?.textContent),
    ).toEqual(['fortigate', 'core-r1']);
    expect(block('ingest')).toHaveTextContent('Tanımlı olmayan kaynak dosyası1');
    for (const id of [
      'resources',
      'disks',
      'database',
      'collector',
      'ingest',
      'services',
      'tasks',
      'jobs',
      'version',
    ])
      expect(block(id).textContent.match(IPV4), id).toBeNull();
    // The only addresses on the page are the configured time servers (contract 0.1 item 4).
    const page = document.body.textContent;
    expect(new Set(page.match(IPV4))).toEqual(new Set(['172.20.0.1']));
    expect(page).not.toMatch(/unknown-/);
    expect(page).not.toContain(makeMe('admin').user.display_name);
    expect(page).not.toContain('Örnek A.Ş.'); // the licensee is not shown either
    expect(page).not.toMatch(/@/);
  });

  it('notes an old or absent status file beside its badge', () => {
    renderView({
      summary: withSummary((summary) => {
        summary.collector = collectorInfo('stale');
        summary.ingest = ingestInfo('missing');
      }),
    });
    const collector = block('collector');
    expect(collector).toHaveTextContent('Güncel değil');
    expect(within(collector).getByTestId('system-collector-stale')).toHaveTextContent(
      'Durum dosyası 2 dakikadan eski',
    );
    const ingest = block('ingest');
    expect(ingest).toHaveTextContent('Durum dosyası yok');
    expect(within(ingest).getByTestId('system-ingest-missing')).toBeInTheDocument();
    expect(ingest).toHaveTextContent('İçe aktarma henüz tanımlı bir kaynağı işlemedi.');
    expect(ingest).toHaveTextContent('Veritabanı bağlantısıBilinmiyor');
  });

  it('shows the services, the agent and ApplySystem, the tasks and the jobs with badges of icon and text', () => {
    const health: SystemHealth = systemHealth();
    health.apply_system = {
      state: 'failed',
      at: '2026-10-13T06:40:11+03:00',
      code: 'system_sync_failed',
    };
    renderView({ health });
    const services = within(block('services')).getAllByRole('row').slice(1);
    expect(services.map((row) => row.textContent)).toEqual([
      'ArciCollectorÇalışıyor13.10.2026 06:59:55',
      'ArciIngestGüncel değil13.10.2026 06:50:40',
      'ArciWorkerDurum dosyası yok—',
      'ArciApiÇalışıyor13.10.2026 07:00:00',
      'Yönetim hizmetiÇalışıyor13.10.2026 06:59:40',
      'Sistem ayarlarının uygulanması (ApplySystem)BaşarısızSonuç kodu: system_sync_failed13.10.2026 06:40:11',
    ]);
    // The first cell names the row: a row header, so each cell is read with its row.
    expect(within(need(services[0], 'service row')).getByRole('rowheader')).toHaveTextContent(
      'ArciCollector',
    );
    const tasks = within(block('tasks')).getAllByRole('row').slice(1);
    expect(tasks).toHaveLength(11);
    expect(tasks[4]).toHaveTextContent("ReplicaSaatte bir, 00:40'tanGecikti");
    expect(tasks[5]).toHaveTextContent('RetentionHer gün 02:00Kullanılmıyor—');
    expect(tasks[9]).toHaveTextContent('İzlenemiyor');
    const jobs = within(block('jobs')).getAllByRole('row').slice(1);
    expect(jobs[3]).toHaveTextContent('alerts.notifyHer 1 dakikada bir—Hata veriyor');
    expect(jobs[3]).toHaveTextContent('30,0 sn');
    expect(jobs[5]).toHaveTextContent(
      'reports.run_dueHer 1 dakikada birusage_reportsHiç çalışmadı',
    );
    // Every state is an icon and a text.
    for (const id of ['services', 'tasks', 'jobs', 'clock'])
      for (const badge of block(id).querySelectorAll('span.rounded-md')) {
        expect(badge.querySelector('svg[aria-hidden="true"]'), id).not.toBeNull();
        expect(badge.textContent.trim(), id).not.toBe('');
      }
  });

  it('shows the time servers, the applied peers and the samples of the last 24 hours', () => {
    renderView();
    const clock = block('clock');
    expect(clock).toHaveTextContent('Saat sunucusu172.20.0.1');
    expect(clock).toHaveTextContent('Yedek saat sunucusutime.ume.tubitak.gov.tr');
    expect(clock).toHaveTextContent('Uyarı sınırı1.000 ms');
    expect(clock).toHaveTextContent('172.20.0.1,0x8 time.ume.tubitak.gov.tr,0xA');
    const samples = within(within(clock).getByTestId('system-clock-samples')).getAllByRole('row');
    expect(samples.slice(1).map((row) => row.textContent)).toEqual([
      '13.10.2026 06:05:02172.20.0.1Sınır içinde12,4 ms0,8 ms2',
      '13.10.2026 05:05:01172.20.0.1Sınır içinde-8,1 ms0,9 ms2',
      '13.10.2026 04:05:03172.20.0.1Yanıt yok———',
      '13.10.2026 03:05:01time.ume.tubitak.gov.trSınır içinde21,7 ms14,2 ms1',
    ]);
  });

  it('shows the versions, the licence maintenance and the upgrade text; it links and downloads nothing', () => {
    const { unmount } = renderView();
    const version = block('version');
    expect(version).toHaveTextContent('Sürüm0.1.0');
    expect(version).toHaveTextContent('Yayın tarihi01.10.2026');
    const maintenance = within(version).getByTestId('system-maintenance');
    expect(maintenance).toHaveTextContent('Geçerli');
    expect(maintenance).toHaveTextContent('20.10.2027 (372 gün kaldı)');
    expect(maintenance).toHaveTextContent('Kurulu sürüm bakım süresi içinde yayımlandı.');
    expect(within(version).getByTestId('system-upgrade')).toHaveTextContent(UPGRADE_TR);
    expect(within(version).queryAllByRole('link')).toEqual([]);
    expect(within(version).queryAllByRole('button')).toEqual([]);
    unmount();

    const states: [Parameters<typeof systemLicense>[0], string][] = [
      ['maintenance_ends_soon', 'Yakında bitiyor'],
      ['maintenance_expired', 'Süresi doldu'],
      ['trial', 'Bakım bilgisi yok'],
    ];
    for (const [name, text] of states) {
      const view = renderView({ license: systemLicense(name) });
      expect(screen.getByTestId('system-maintenance')).toHaveTextContent(text);
      view.unmount();
    }
    renderView({ license: undefined as LicenseOut | undefined });
    expect(screen.queryByTestId('system-maintenance')).toBeNull();
    expect(screen.getByTestId('system-upgrade')).toHaveTextContent(UPGRADE_TR);
  });

  it('switches to English, server texts included', () => {
    renderView();
    act(() => {
      setLanguage('en');
    });
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('System');
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
    ).toEqual([
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
    ]);
    expect(screen.getByText('Updated 07:00:00')).toBeInTheDocument();
    expect(block('resources')).toHaveTextContent('Processor23%');
    expect(screen.getAllByTestId('drive-forecast')[0]).toHaveTextContent(
      'At this rate the 80% mark is reached on 30.12.2026, full on 02.03.2027 (~140 days).',
    );
    expect(block('collector')).toHaveTextContent('2 senders, 37 messages, 6.4 KB');
    expect(within(block('tasks')).getAllByRole('row')[1]).toHaveTextContent(
      'SealEvery 5 minutes from 00:10On time',
    );
    expect(within(block('jobs')).getAllByRole('row')[1]).toHaveTextContent('Daily after 00:15');
    // The release day is written as the maintenance day beside it, as on the licence page.
    expect(block('version')).toHaveTextContent('Release date2026-10-01');
    expect(screen.getByTestId('system-upgrade')).toHaveTextContent(UPGRADE_EN);
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
  });

  it('keeps the data shown when a refresh fails, and offers to try again', async () => {
    const error = new ApiError({
      status: 503,
      code: 'database_unavailable',
      messageTr: 'Veritabanına ulaşılamıyor.',
      messageEn: 'The database cannot be reached.',
    });
    const { onRefresh } = renderView({ error });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Sistem durumu alınamadı.');
    expect(alert).toHaveTextContent('Veritabanına ulaşılamıyor.');
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(BLOCKS_TR.length);
    expect(screen.getAllByTestId('drive-forecast')).toHaveLength(2);
    await userEvent.click(within(alert).getByRole('button', { name: 'Tekrar dene' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows a loading state first, then each block as its answer arrives', () => {
    const { rerender } = renderView({
      summary: undefined,
      health: undefined,
      loading: true,
      updatedAt: 0,
    });
    expect(screen.getByRole('status', { name: 'Yükleniyor…' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect(screen.queryByText(/Güncellendi/)).toBeNull();
    rerender(
      <SystemView
        summary={systemSummary()}
        health={undefined}
        license={systemLicense()}
        error={null}
        loading
        updatedAt={Date.parse(W4_NOW)}
        onRefresh={() => undefined}
      />,
    );
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
    ).toEqual([
      'Kaynak kullanımı',
      'Diskler',
      'Veritabanı',
      'Toplayıcı',
      'İçe aktarma',
      'Sürüm ve güncelleme',
    ]);
  });

  it('shows the error alone when nothing has arrived', () => {
    renderView({
      summary: undefined,
      health: undefined,
      error: new Error('offline'),
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Sistem durumu alınamadı.');
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });
});
