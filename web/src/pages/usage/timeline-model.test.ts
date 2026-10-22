import type { SubjectHour, TimelineSpan } from '@/lib/api-types';

import { clockOfMinute, layoutTimeline } from './timeline-model';

const DAY = '2026-10-13';
const LABELS = { unknown: 'Bilinmiyor', other: 'Diğer' };

/** A span in site time (UTC+03:00). */
function span(from: string, to: string, category: string | null): TimelineSpan {
  return { start: `${DAY}T${from}:00+03:00`, end: `${DAY}T${to}:00+03:00`, category };
}

function hour(clock: string, top: string | null): SubjectHour {
  return {
    hour: `${DAY}T${clock}:00+03:00`,
    active_seconds: 600,
    online_seconds: 900,
    download_bytes: 0,
    upload_bytes: 0,
    top_domain: top,
  };
}

describe('day timeline layout', () => {
  it('turns instants into minutes of the site day and merges touching spans', () => {
    const layout = layoutTimeline(
      [
        span('09:00', '09:30', 'Business'),
        span('09:30', '09:45', 'Business'),
        span('09:45', '10:10', 'Streaming Media and Download'),
        span('13:05', '13:06', null),
      ],
      DAY,
      [],
      LABELS,
    );
    expect(layout.segments.map(({ start, end, category }) => [start, end, category])).toEqual([
      [540, 585, 'Business'],
      [585, 610, 'Streaming Media and Download'],
      [785, 786, 'Bilinmiyor'],
    ]);
    expect(layout.activeMinutes).toBe(71);
    // Colours follow the time per category (most first), not the order of the day.
    expect(layout.categories).toEqual(['Business', 'Streaming Media and Download', 'Bilinmiyor']);
  });

  it('clips spans to the day and rounds partial minutes outwards', () => {
    const layout = layoutTimeline(
      [
        { start: '2026-10-12T23:50:00+03:00', end: '2026-10-13T00:10:00+03:00', category: 'A' },
        { start: '2026-10-13T08:00:20+03:00', end: '2026-10-13T08:01:10+03:00', category: 'B' },
        { start: '2026-10-13T23:55:00+03:00', end: '2026-10-14T00:30:00+03:00', category: 'C' },
        { start: '2026-10-11T10:00:00+03:00', end: '2026-10-11T11:00:00+03:00', category: 'D' },
        { start: 'bozuk', end: '2026-10-13T11:00:00+03:00', category: 'E' },
      ],
      DAY,
      [],
      LABELS,
    );
    expect(layout.segments.map(({ start, end, category }) => [start, end, category])).toEqual([
      [0, 10, 'A'],
      [480, 482, 'B'],
      [1435, 1440, 'C'],
    ]);
  });

  it('folds the eighth category and beyond into "Diğer"', () => {
    const spans = Array.from({ length: 9 }, (_, index) => {
      // Category K0 has the most minutes, K8 the fewest.
      const start = index * 60;
      return {
        start: new Date(Date.parse(`${DAY}T00:00:00+03:00`) + start * 60_000).toISOString(),
        end: new Date(
          Date.parse(`${DAY}T00:00:00+03:00`) + (start + 30 - index) * 60_000,
        ).toISOString(),
        category: `K${index}`,
      };
    });
    const layout = layoutTimeline(spans, DAY, [], LABELS);
    expect(layout.categories).toEqual(['K0', 'K1', 'K2', 'K3', 'K4', 'K5', 'K6', 'Diğer']);
    expect(layout.segments.filter((segment) => segment.category === 'Diğer')).toHaveLength(2);
  });

  it('knows the top domains of the hours a segment covers (hover text)', () => {
    const layout = layoutTimeline(
      [span('09:50', '11:05', 'Business'), span('14:00', '14:20', 'News')],
      DAY,
      [
        hour('09:00', 'office.com'),
        hour('10:00', 'office.com'),
        hour('11:00', 'sahibinden.com'),
        hour('14:00', null),
      ],
      LABELS,
    );
    expect(layout.segments[0]?.topDomains).toEqual(['office.com', 'sahibinden.com']);
    expect(layout.segments[1]?.topDomains).toEqual([]);
  });

  it('writes minutes as a clock', () => {
    expect(clockOfMinute(0)).toBe('00:00');
    expect(clockOfMinute(545)).toBe('09:05');
    expect(clockOfMinute(1440)).toBe('24:00');
  });
});
