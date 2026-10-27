import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Histogram } from './histogram';
import { bucketLabel, clipSpan, histogramRows, zoomSpan } from './histogram-data';

const HOURS = Array.from({ length: 4 }, (_, hour) => ({
  start: `2026-10-09T${String(hour + 10).padStart(2, '0')}:00:00+03:00`,
  count: hour + 1,
}));

describe('histogram data', () => {
  it('gives every bar its end and whether it was counted', () => {
    const rows = histogramRows(HOURS, 3600, {
      from: '2026-10-09T11:00:00+03:00',
      to: '2026-10-09T14:00:00+03:00',
    });
    expect(rows.map((row) => [row.label, row.end, row.counted])).toEqual([
      ['10:00', '2026-10-09T11:00:00+03:00', false],
      ['11:00', '2026-10-09T12:00:00+03:00', true],
      ['12:00', '2026-10-09T13:00:00+03:00', true],
      ['13:00', '2026-10-09T14:00:00+03:00', true],
    ]);
    expect(histogramRows(HOURS, 3600, null).every((row) => row.counted)).toBe(true);
  });

  it('labels bars by time, with the day when the range spans several days', () => {
    expect(bucketLabel('2026-10-09T14:00:00+03:00', 3600, false)).toBe('14:00');
    expect(bucketLabel('2026-10-09T14:00:00+03:00', 3600, true)).toBe('09.10 14:00');
    expect(bucketLabel('2026-10-09T00:00:00+03:00', 86_400, true)).toBe('09.10.2026');
    const days = histogramRows(
      [
        { start: '2026-10-08T23:00:00+03:00', count: 1 },
        { start: '2026-10-09T00:00:00+03:00', count: 2 },
      ],
      3600,
      null,
    );
    expect(days.map((row) => row.label)).toEqual(['08.10 23:00', '09.10 00:00']);
  });

  it('zooms to the span of the dragged bars, in either direction', () => {
    const rows = histogramRows(HOURS, 3600, null);
    const span = { from: '2026-10-09T11:00:00+03:00', to: '2026-10-09T14:00:00+03:00' };
    expect(zoomSpan(rows, 1, 3)).toEqual(span);
    expect(zoomSpan(rows, 3, 1)).toEqual(span);
    expect(zoomSpan(rows, 0, 9)).toBeNull();
  });

  it("cuts a zoom to the search's range: its first and last buckets are whole", () => {
    // "Son 24 saat" at 14:23:17: the hourly buckets run from 14:00 the day before to 15:00.
    const range = { from: '2026-10-08T14:23:17+03:00', to: '2026-10-09T14:23:17+03:00' };
    expect(
      clipSpan({ from: '2026-10-08T14:00:00+03:00', to: '2026-10-09T15:00:00+03:00' }, range),
    ).toEqual(range);
    expect(
      clipSpan({ from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T15:00:00+03:00' }, range),
    ).toEqual({ from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T14:23:17+03:00' });
    const inside = { from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T12:00:00+03:00' };
    expect(clipSpan(inside, range)).toEqual(inside);
    expect(
      clipSpan({ from: '2026-10-09T15:00:00+03:00', to: '2026-10-09T16:00:00+03:00' }, range),
    ).toBeNull();
  });
});

describe('Histogram', () => {
  it('says "at least" when the count is incomplete and zooms from the table view', async () => {
    const onZoom = vi.fn();
    render(
      <Histogram
        buckets={HOURS}
        bucketSeconds={3600}
        covered={{ from: '2026-10-09T12:00:00+03:00', to: '2026-10-09T14:00:00+03:00' }}
        total={{ value: 7, exact: false }}
        onZoom={onZoom}
      />,
    );
    expect(screen.getByText(/^≥ 7 kayıt/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Tablo olarak göster' }));
    const table = screen.getByRole('table', { name: 'Zaman dağılımı' });
    expect(table).toBeInTheDocument();
    expect(screen.getAllByText('sayılmadı')).toHaveLength(2);
    await userEvent.click(
      screen.getByRole('button', {
        name: '09.10.2026 13:00:00 – 09.10.2026 14:00:00 aralığına yakınlaş',
      }),
    );
    expect(onZoom).toHaveBeenCalledWith({
      from: '2026-10-09T13:00:00+03:00',
      to: '2026-10-09T14:00:00+03:00',
    });
    await userEvent.click(screen.getByRole('button', { name: 'Grafik olarak göster' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('gives an exact total as it is', () => {
    render(
      <Histogram
        buckets={HOURS}
        bucketSeconds={3600}
        covered={null}
        total={{ value: 1, exact: true }}
      />,
    );
    expect(screen.getByText('1 kayıt')).toBeInTheDocument();
  });
});
