import { ApiError } from '@/lib/api';
import type { DashboardSummary } from '@/lib/api-types';
import { ROLES } from '@/lib/permissions';
import { DASHBOARD, USAGE } from '@/test/samples';

import {
  categorySlices,
  clockVerdict,
  creditDuration,
  dashboardRequests,
  failureLevel,
  formatTotalTime,
  hourlyRows,
  lastDays,
  linkTarget,
  sealDayHref,
  sealStatus,
  visibleBlocks,
} from './dashboard-model';
import { withParseFailures } from './test-data';

function granted(role: string): Set<string> {
  return new Set(ROLES.find((item) => item.id === role)?.permissions ?? []);
}

/**
 * The summary the API sent to `role` (captured: the blocks a role may not see are `null`), with a
 * known parse-failure box beside the ingest box.
 */
function summaryFor(role: 'admin' | 'auditor' | 'viewer'): DashboardSummary {
  return withParseFailures(structuredClone(DASHBOARD[role]));
}

const usage = { data: USAGE.company, error: null };

describe('dashboard requests by permission', () => {
  it.each([
    ['admin', true, true],
    ['auditor', true, true],
    ['viewer', true, true],
    ['hr', false, true],
  ])('%s: summary %s, usage %s', (role, summary, wantsUsage) => {
    expect(dashboardRequests(granted(role))).toEqual({ summary, usage: wantsUsage });
  });

  it('asks nothing without a dashboard or usage permission', () => {
    expect(dashboardRequests(new Set(['audit.read']))).toEqual({ summary: false, usage: false });
  });
});

describe('visible blocks', () => {
  it('administrator: every block', () => {
    expect([...visibleBlocks(granted('admin'), summaryFor('admin'), usage)].sort()).toEqual(
      [
        'alerts',
        'calendar',
        'categories',
        'clock',
        'credit',
        'ingest',
        'parseFailures',
        'seal',
        'sites',
        'usage',
      ].sort(),
    );
  });

  it('auditor: ingest, seal, credit and clock but no alerts block (no alerts.read)', () => {
    const blocks = visibleBlocks(granted('auditor'), summaryFor('auditor'), usage);
    expect(blocks.has('alerts')).toBe(false);
    expect(blocks.has('ingest')).toBe(true);
    expect(blocks.has('parseFailures')).toBe(true);
    expect(blocks.has('seal')).toBe(true);
    expect(blocks.has('credit')).toBe(true);
    expect(blocks.has('clock')).toBe(true);
    expect(blocks.has('usage')).toBe(true);
  });

  it('viewer: ingest, its parse failures and usage only (the API leaves the rest out)', () => {
    expect([...visibleBlocks(granted('viewer'), summaryFor('viewer'), usage)].sort()).toEqual(
      ['categories', 'ingest', 'parseFailures', 'sites', 'usage'].sort(),
    );
  });

  it('no parse-failure box from an API that does not send one', () => {
    const summary = { ...summaryFor('admin'), parse_failures: null };
    expect(visibleBlocks(granted('admin'), summary, usage).has('parseFailures')).toBe(false);
    const older = { ...summaryFor('admin') } as Partial<DashboardSummary>;
    delete older.parse_failures;
    expect(
      visibleBlocks(granted('admin'), older as DashboardSummary, usage).has('parseFailures'),
    ).toBe(false);
  });

  it('HR: the usage blocks only, without asking the summary', () => {
    expect([...visibleBlocks(granted('hr'), undefined, usage)].sort()).toEqual(
      ['categories', 'sites', 'usage'].sort(),
    );
    // Even a stray summary is ignored for a user who may not ask it.
    expect(visibleBlocks(granted('hr'), summaryFor('admin'), usage).has('seal')).toBe(false);
  });

  it('a block the API sends as null is hidden, not an error', () => {
    const summary = { ...summaryFor('admin'), clock: null, credit: null };
    const blocks = visibleBlocks(granted('admin'), summary, usage);
    expect(blocks.has('clock')).toBe(false);
    expect(blocks.has('credit')).toBe(false);
    expect(blocks.has('ingest')).toBe(true);
  });

  it('a refused usage request hides the usage blocks; another error shows them', () => {
    const forbidden = new ApiError({
      status: 403,
      code: 'forbidden',
      messageTr: '',
      messageEn: '',
    });
    const broken = new ApiError({ status: 500, code: 'http_500', messageTr: '', messageEn: '' });
    expect(
      visibleBlocks(granted('admin'), summaryFor('admin'), {
        data: undefined,
        error: forbidden,
      }).has('usage'),
    ).toBe(false);
    expect(
      visibleBlocks(granted('admin'), summaryFor('admin'), { data: undefined, error: broken }).has(
        'usage',
      ),
    ).toBe(true);
  });

  it('links only to pages the user may open', () => {
    expect(linkTarget('sealing', granted('admin'))).toBe('/sealing');
    expect(linkTarget('sealing', granted('viewer'))).toBeNull();
    expect(linkTarget('usage', granted('viewer'))).toBe('/usage');
    expect(linkTarget('sources', granted('viewer'))).toBeNull();
    expect(linkTarget('security', granted('auditor'))).toBeNull();
    expect(sealDayHref('/sealing', '2026-09-30')).toBe('/sealing?month=2026-09&day=2026-09-30');
  });
});

describe('card values', () => {
  it('parse failures: none, some, or above 1 % of the lines (R20-parse)', () => {
    expect(failureLevel({ failures: 0, permille: 0 })).toBe('none');
    expect(failureLevel({ failures: 0, permille: null })).toBe('none');
    expect(failureLevel({ failures: 3, permille: 2.4 })).toBe('some');
    expect(failureLevel({ failures: 31, permille: 10 })).toBe('high');
    expect(failureLevel({ failures: 2, permille: null })).toBe('some');
  });

  it('credit lasts in days, months or years', () => {
    expect(creditDuration(12)).toEqual({ unit: 'days', count: 12 });
    expect(creditDuration(74)).toEqual({ unit: 'months', count: 2 });
    expect(creditDuration(9490)).toEqual({ unit: 'years', count: 26 });
  });

  it('clock: the server decides; without a recent sample it is unknown', () => {
    expect(clockVerdict({ offset_ms: 3.2, ok: true })).toBe('ok');
    expect(clockVerdict({ offset_ms: 3.2, ok: false })).toBe('off');
    // `ok: null`: the newest sample is older than 3 hours — its offset says nothing about now.
    expect(clockVerdict({ offset_ms: 1500, ok: null })).toBe('unknown');
    expect(clockVerdict({ offset_ms: -40, ok: null })).toBe('unknown');
    expect(clockVerdict({ offset_ms: null, ok: null })).toBe('unknown');
  });

  it('seal states use the sealing calendar statuses', () => {
    expect(sealStatus('sealed', true)).toBe('sealed');
    expect(sealStatus('sealed', false)).toBe('unverified');
    expect(sealStatus('sealed', null)).toBe('unverified');
    expect(sealStatus('sealed')).toBe('sealed');
    expect(sealStatus('pending')).toBe('pending');
    expect(sealStatus('failed')).toBe('failed');
    expect(sealStatus('no_data')).toBe('noData');
    expect(sealStatus('something-new')).toBe('noData');
  });

  it('sums of many people are whole hours from one day on', () => {
    expect(formatTotalTime(669_600, 'tr')).toBe('186 sa');
    expect(formatTotalTime(669_600, 'en')).toBe('186 h');
    expect(formatTotalTime(11_520, 'tr')).toBe('3 sa 12 dk');
  });

  it('hourly rows carry the site hour', () => {
    const rows = hourlyRows(USAGE.company.hourly);
    expect(rows[0]?.hour).toBe('00:00');
    expect(rows[0]?.upload).toBe(0);
    expect(rows).toHaveLength(24);
    expect(rows[9]?.hour).toBe('09:00');
    expect(rows.at(-1)?.hour).toBe('23:00');
  });

  it('folds categories beyond six into "Diğer"', () => {
    const captured = USAGE.company.top_categories;
    const total = USAGE.company.metrics.active_seconds;
    // The captured day has six categories: all shown, with the server's shares.
    const all = categorySlices(captured, 'Diğer', total);
    expect(all.map((slice) => slice.label)).toEqual(captured.map((row) => row.category));
    expect(all.map((slice) => slice.share)).toEqual(captured.map((row) => row.share));
    const [first] = captured;
    if (!first) throw new Error('sample missing');
    const more = [
      ...captured,
      { ...first, category: 'Alışveriş', seconds: 50, share: 0.0424 },
      { ...first, category: 'Arama Motorları', seconds: 40, share: 0.0339 },
    ];
    const slices = categorySlices(more, 'Diğer', total + 90);
    expect(slices).toHaveLength(7);
    expect(slices[0]?.label).toBe('İş Uygulamaları');
    // Alışveriş (50 s) and Arama Motorları (40 s) are folded.
    expect(slices.at(-1)).toMatchObject({ label: 'Diğer', value: 90, other: true });
  });

  it('"Diğer" is the rest of the active time, also beyond the ten categories sent', () => {
    // 100 h of active time; the API sends the ten largest categories (90 h).
    const hours = [18, 15, 12, 10, 9, 8, 6, 5, 4, 3];
    const rows = hours.map((hour, index) => ({
      category: `Kategori ${index + 1}`,
      seconds: hour * 3600,
      share: hour / 100,
      download_bytes: 0,
      upload_bytes: 0,
      users: 1,
    }));
    const slices = categorySlices(rows, 'Diğer', 100 * 3600);
    expect(slices.slice(0, 6).map((slice) => slice.share)).toEqual([
      0.18, 0.15, 0.12, 0.1, 0.09, 0.08,
    ]);
    const other = slices.at(-1);
    expect(other).toMatchObject({ label: 'Diğer', value: 28 * 3600, other: true });
    expect(other?.share).toBeCloseTo(0.28, 6);
    // The slices add up to the active time shown in the middle.
    expect(slices.reduce((sum, slice) => sum + slice.value, 0)).toBe(100 * 3600);
  });

  it('the last 30 days start under their weekday', () => {
    const cells = lastDays(DASHBOARD.admin.seal?.calendar ?? [], '2026-10-12');
    // 13.09.2026 is a Sunday: six blank cells before it (Monday first).
    expect(cells.slice(0, 6)).toEqual([null, null, null, null, null, null]);
    expect(cells[6]).toEqual({ day: '2026-09-13', status: 'no_data' });
    expect(cells.at(-2)).toEqual({ day: '2026-10-11', status: 'failed' });
    expect(cells.at(-1)).toEqual({ day: '2026-10-12', status: 'pending' });
    expect(cells.filter(Boolean)).toHaveLength(30);
    // A day the API did not send is "no data".
    expect(lastDays([], '2026-10-12').at(-1)).toEqual({ day: '2026-10-12', status: 'no_data' });
  });
});
