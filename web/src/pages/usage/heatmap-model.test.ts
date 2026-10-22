import { heatLevel, heatmapRows, heatTotal, HEAT_MAX_MINUTES } from './heatmap-model';

describe('heat map scaling', () => {
  it('uses a fixed 60-minute scale in five steps of 12 minutes', () => {
    expect(HEAT_MAX_MINUTES).toBe(60);
    expect([0, 1, 12, 13, 24, 25, 36, 37, 48, 49, 60].map(heatLevel)).toEqual([
      0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
    ]);
    // More than an hour (a rounding of the API) is still the darkest step; negative is none.
    expect(heatLevel(75)).toBe(5);
    expect(heatLevel(-3)).toBe(0);
  });
});

describe('heat map rows', () => {
  it('has the 30 days ending the period, oldest first, every hour filled', () => {
    const rows = heatmapRows(
      [
        { day: '2026-10-13', hour: 9, active_minutes: 42 },
        { day: '2026-10-13', hour: 23, active_minutes: 90 },
        { day: '2026-09-14', hour: 0, active_minutes: 5 },
        // Outside the 30 days, or not an hour: ignored.
        { day: '2026-09-13', hour: 10, active_minutes: 30 },
        { day: '2026-10-14', hour: 10, active_minutes: 30 },
        { day: '2026-10-12', hour: 24, active_minutes: 30 },
      ],
      '2026-10-13',
      (day) => day.slice(5),
    );
    expect(rows).toHaveLength(30);
    expect(rows[0]?.key).toBe('2026-09-14');
    expect(rows[0]?.values[0]).toBe(5);
    expect(rows.at(-1)?.label).toBe('10-13');
    expect(rows.at(-1)?.values[9]).toBe(42);
    // Clamped to the scale.
    expect(rows.at(-1)?.values[23]).toBe(60);
    expect(rows.every((row) => row.values.length === 24)).toBe(true);
    expect(heatTotal(rows)).toBe(5 + 42 + 60);
  });

  it('takes another number of days', () => {
    const rows = heatmapRows([], '2026-10-13', (day) => day, 7);
    expect(rows.map((row) => row.key)).toEqual([
      '2026-10-07',
      '2026-10-08',
      '2026-10-09',
      '2026-10-10',
      '2026-10-11',
      '2026-10-12',
      '2026-10-13',
    ]);
    expect(heatTotal(rows)).toBe(0);
  });
});
