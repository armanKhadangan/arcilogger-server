import i18n from '@/lib/i18n';
import type {
  AgentHealth,
  ApplySystemHealth,
  JobHealth,
  TaskHealth,
} from '@/lib/system-health-types';
import type { StatusFileState } from '@/lib/system-types';
import {
  CLOCK_SAMPLES,
  driveInfo,
  hostInfo,
  JOBS,
  systemLicense,
  TASKS,
  W4_NOW,
} from '@/test/w4-fixtures';

import {
  AGENT_STYLE,
  APPLY_SYSTEM_STYLE,
  cpuPending,
  DATABASE_STYLE,
  databaseState,
  daysBetween,
  DRIVE_STYLE,
  driveLevel,
  forecastText,
  formatPercent,
  JOB_STYLE,
  MAINTENANCE_STYLE,
  maintenanceState,
  memoryText,
  SAMPLE_STYLE,
  sampleState,
  scheduleText,
  STATUS_FILE_STYLE,
  SYSTEM_BLOCKS,
  TASK_STYLE,
  type StateStyle,
} from './system-model';

const tr = i18n.getFixedT('tr');
const en = i18n.getFixedT('en');

describe('formatting', () => {
  it('puts the percent sign first in Turkish and last in English', () => {
    expect(formatPercent(54, 'tr')).toBe('%54');
    expect(formatPercent(54, 'en')).toBe('54%');
    expect(formatPercent(12.5, 'tr', 1)).toBe('%12,5');
    expect(formatPercent(1234, 'en')).toBe('1,234%');
  });

  it('gives the memory in binary units with its share, and nothing when not read', () => {
    expect(memoryText(hostInfo(), 'tr')).toBe('9 GB / 16 GB (%56)');
    expect(memoryText(hostInfo(), 'en')).toBe('9 GB / 16 GB (56%)');
    expect(memoryText(hostInfo('unreadable'), 'tr')).toBeNull();
  });

  it('says "measuring" for the first reading and "unreadable" where the platform has none', () => {
    expect(cpuPending(hostInfo('first_reading'))).toBe('measuring');
    expect(cpuPending(hostInfo('unreadable'))).toBe('unreadable');
    expect(tr('system.host.measuring')).toBe('ölçülüyor');
    expect(tr('system.host.unreadable')).toBe('bu platformda okunamıyor');
  });

  it('lists the blocks in the order of the contract', () => {
    expect(SYSTEM_BLOCKS.map((block) => tr(`system.blocks.${block}`))).toEqual([
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
    ]);
  });
});

describe('disk forecast', () => {
  it('counts calendar days between site days, never negative', () => {
    expect(daysBetween('2026-10-13', '2027-03-02')).toBe(140);
    // Across the end of summer time (25.10.2026) a day is still one day.
    expect(daysBetween('2026-10-24', '2026-10-26')).toBe(2);
    expect(daysBetween('2026-10-13', '2026-10-13')).toBe(0);
    expect(daysBetween('2026-10-13', '2026-10-01')).toBe(0);
  });

  it('words an estimate as the contract does, counting from the day the server measured', () => {
    const drive = driveInfo('data');
    expect(forecastText(drive, W4_NOW, tr, 'tr')).toBe(
      'Bu hızla %80 eşiğine 30.12.2026, dolmaya 02.03.2027 (~140 gün).',
    );
    expect(forecastText(drive, W4_NOW, en, 'en')).toBe(
      'At this rate the 80% mark is reached on 30.12.2026, full on 02.03.2027 (~140 days).',
    );
    // 23:30 UTC on the 12th is already the 13th at the site (Europe/Istanbul).
    expect(forecastText(drive, '2026-10-12T23:30:00Z', tr, 'tr')).toContain('(~140 gün)');
  });

  it('says one day in the singular', () => {
    const drive = driveInfo('data');
    drive.forecast.warning_on = '2026-10-14';
    drive.forecast.full_on = '2026-10-14';
    expect(forecastText(drive, W4_NOW, en, 'en')).toBe(
      'At this rate the 80% mark is reached on 14.10.2026, full on 14.10.2026 (~1 day).',
    );
  });

  it('gives only the day it is full once the drive is past the warning mark', () => {
    const drive = driveInfo('system', { forecast: 'estimated', usedPercent: 84 });
    drive.forecast.warning_on = null;
    expect(forecastText(drive, W4_NOW, tr, 'tr')).toBe(
      '%80 eşiği aşıldı; bu hızla dolmaya 02.03.2027 (~140 gün).',
    );
    expect(forecastText(drive, W4_NOW, en, 'en')).toBe(
      'The 80% mark is passed; at this rate the drive is full on 02.03.2027 (~140 days).',
    );
  });

  it('asks for seven days of samples, or says the use did not grow', () => {
    expect(forecastText(driveInfo('system'), W4_NOW, tr, 'tr')).toBe(
      'Tahmin için en az 7 günlük ölçüm gerekiyor (4/7).',
    );
    expect(forecastText(driveInfo('system'), W4_NOW, en, 'en')).toBe(
      'A forecast needs at least 7 days of samples (4/7).',
    );
    const flat = driveInfo('data', { forecast: 'not_growing' });
    expect(forecastText(flat, W4_NOW, tr, 'tr')).toBe('Son 30 günde disk kullanımı artmadı.');
    expect(forecastText(flat, W4_NOW, en, 'en')).toBe('Disk use did not grow in the last 30 days.');
  });
});

/** Every style has an icon and a text in both languages: status is never colour alone. */
function expectWorded(styles: Record<string, StateStyle>) {
  for (const [state, style] of Object.entries(styles)) {
    expect(style.icon, state).toBeTruthy();
    for (const t of [tr, en]) {
      const text = t(style.label);
      expect(text, `${state}: ${style.label}`).not.toBe(style.label);
      expect(text.trim(), state).not.toBe('');
    }
  }
}

describe('state badges', () => {
  it('judge a drive by its own marks, as the disk alert does', () => {
    expect(driveLevel(driveInfo('data'))).toBe('ok');
    expect(driveLevel(driveInfo('system'))).toBe('warning');
    expect(driveLevel(driveInfo('data', { usedPercent: 80 }))).toBe('ok');
    expect(driveLevel(driveInfo('data', { usedPercent: 81 }))).toBe('warning');
    expect(driveLevel(driveInfo('data', { usedPercent: 95 }))).toBe('critical');
    expect(DRIVE_STYLE.critical.tone).toBe('danger');
    expectWorded(DRIVE_STYLE);
  });

  it('map every status-file, agent and ApplySystem state', () => {
    const files: StatusFileState[] = ['running', 'stale', 'missing'];
    expect(Object.keys(STATUS_FILE_STYLE).sort()).toEqual([...files].sort());
    expect(files.map((state) => tr(STATUS_FILE_STYLE[state].label))).toEqual([
      'Çalışıyor',
      'Güncel değil',
      'Durum dosyası yok',
    ]);
    const agent: AgentHealth['state'][] = ['ok', 'stale', 'missing', 'disabled'];
    expect(Object.keys(AGENT_STYLE).sort()).toEqual([...agent].sort());
    const apply: ApplySystemHealth['state'][] = ['ok', 'failed', 'never', 'disabled'];
    expect(Object.keys(APPLY_SYSTEM_STYLE).sort()).toEqual([...apply].sort());
    expect(APPLY_SYSTEM_STYLE.failed).toMatchObject({ tone: 'danger' });
    expectWorded(STATUS_FILE_STYLE);
    expectWorded(AGENT_STYLE);
    expectWorded(APPLY_SYSTEM_STYLE);
  });

  it('map every task and job state of the fixtures', () => {
    const tasks = new Set<TaskHealth['state']>(TASKS.map((task) => task.state));
    expect([...tasks].sort()).toEqual(Object.keys(TASK_STYLE).sort());
    const jobs = new Set<JobHealth['state']>(JOBS.map((job) => job.state));
    expect([...jobs].sort()).toEqual(Object.keys(JOB_STYLE).sort());
    expect(en(TASK_STYLE.not_observable.label)).toBe('Not observable');
    expect(tr(JOB_STYLE.failing.label)).toBe('Hata veriyor');
    expect(JOB_STYLE.failing.tone).toBe('danger');
    expectWorded(TASK_STYLE);
    expectWorded(JOB_STYLE);
  });

  it('judge a clock sample against the limit, the limit itself being over it', () => {
    expect(CLOCK_SAMPLES.map((sample) => sampleState(sample, 1000))).toEqual([
      'ok',
      'ok',
      'noAnswer',
      'ok',
    ]);
    const sample = { ...CLOCK_SAMPLES[0]!, offset_ms: -1000 };
    expect(sampleState(sample, 1000)).toBe('over');
    expect(sampleState({ ...sample, offset_ms: 999.9 }, 1000)).toBe('ok');
    expectWorded(SAMPLE_STYLE);
  });

  it('word the database connection and the licence maintenance', () => {
    expect([true, false, null].map(databaseState)).toEqual(['ok', 'failed', 'unknown']);
    expectWorded(DATABASE_STYLE);
    expect(maintenanceState(systemLicense('licensed').maintenance)).toBe('valid');
    expect(maintenanceState(systemLicense('maintenance_ends_soon').maintenance)).toBe('endsSoon');
    expect(maintenanceState(systemLicense('maintenance_expired').maintenance)).toBe('expired');
    expect(maintenanceState(systemLicense('trial').maintenance)).toBe('none');
    expectWorded(MAINTENANCE_STYLE);
  });

  it('take the schedule in the interface language', () => {
    const seal = TASKS[0]!;
    expect(scheduleText(seal, 'tr')).toBe("Her gün 00:10'dan sonra 5 dakikada bir");
    expect(scheduleText(seal, 'en')).toBe('Every 5 minutes from 00:10');
  });
});
