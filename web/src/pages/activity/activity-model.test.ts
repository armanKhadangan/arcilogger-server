import type { WanLine } from '@/lib/activity-types';

import {
  capacityShare,
  formatBps,
  lineSeries,
  mbpsDecimals,
  mbpsText,
  minuteSeries,
  shareText,
  toMbps,
  windowFromSearch,
} from './activity-model';

describe('Canlı Durum model (W5 P3)', () => {
  it('gives one chart one number of decimals, enough for a quiet line', () => {
    expect(mbpsDecimals(34.2)).toBe(0);
    expect(mbpsDecimals(3.46)).toBe(1);
    expect(mbpsDecimals(0.25)).toBe(2);
    expect(mbpsDecimals(0.0016)).toBe(4);
    expect(mbpsDecimals(0)).toBe(1);
    expect(mbpsDecimals(1e-9)).toBe(6);
    // A quiet line's axis: every tick distinct, none "0,0".
    const ticks = [0, 0.0004, 0.0008, 0.0012, 0.0016].map((tick) => mbpsText(tick, 4, 'tr'));
    expect(ticks).toEqual(['0', '0,0004', '0,0008', '0,0012', '0,0016']);
    expect(mbpsText(34.2, 0, 'en')).toBe('34');
    expect(mbpsText(0.012, 3, 'en')).toBe('0.012');
  });

  it('reads the window from the address, 5 minutes otherwise', () => {
    expect(windowFromSearch('1')).toBe(1);
    expect(windowFromSearch('15')).toBe(15);
    expect(windowFromSearch(null)).toBe(5);
    expect(windowFromSearch('10')).toBe(5);
    expect(windowFromSearch('abc')).toBe(5);
  });

  it('writes rates in the unit a line is sold in', () => {
    expect(formatBps(12_400_000, 'tr')).toBe('12,4 Mbit/s');
    expect(formatBps(12_400_000, 'en')).toBe('12.4 Mbit/s');
    expect(formatBps(850_000, 'tr')).toBe('850 kbit/s');
    expect(formatBps(1_250_000_000, 'tr')).toBe('1,25 Gbit/s');
    expect(formatBps(null, 'tr')).toBe('—');
    expect(toMbps(2_000_000)).toBe(2);
  });

  it('a share of the capacity only when it is known', () => {
    expect(capacityShare(185_000_000, 500)).toBeCloseTo(0.37);
    expect(capacityShare(185_000_000, undefined)).toBeNull();
    expect(capacityShare(null, 500)).toBeNull();
    expect(capacityShare(10, 0)).toBeNull();
    expect(shareText(0.37, 'tr')).toBe('%37');
    expect(shareText(0.37, 'en')).toBe('37%');
  });

  it('chart rows in Mbit/s and site time', () => {
    const line: WanLine = {
      source: 'fortigate',
      name: 'wan1',
      oper_status: 'up',
      download_bps: 2_000_000,
      upload_bps: 500_000,
      capacity_mbps: null,
      series: [{ time: '2026-10-13T11:35:00Z', download_bps: 2_000_000, upload_bps: 500_000 }],
      status: 'ok',
      updated_at: '2026-10-13T11:35:00Z',
      last_error_at: null,
    };
    expect(lineSeries(line)).toEqual([{ time: '14:35', download: 2, upload: 0.5 }]);
    // 15 MB in a minute = 2 Mbit/s on average.
    expect(
      minuteSeries([
        { minute: '2026-10-13T11:34:00Z', download_bytes: 15_000_000, upload_bytes: 750_000 },
      ]),
    ).toEqual([{ time: '14:34', download: 2, upload: 0.1 }]);
  });
});
