import { toFilters, EMPTY_DRAFT } from '@/pages/audit/audit-filters';

import { SITE_UTC_OFFSET, siteDayEnd, siteDayStart } from './site-day';

describe('site days', () => {
  it('uses the calendar date the user picked, at Istanbul midnight', () => {
    expect(siteDayStart(new Date(2026, 9, 12, 18, 30))).toBe('2026-10-12T00:00:00+03:00');
    expect(siteDayEnd(new Date(2026, 9, 12))).toBe('2026-10-13T00:00:00+03:00');
    expect(siteDayEnd(new Date(2026, 11, 31))).toBe('2027-01-01T00:00:00+03:00');
    expect(siteDayEnd(new Date(2028, 1, 28))).toBe('2028-02-29T00:00:00+03:00');
  });

  it('agrees with the time-zone database: Europe/Istanbul is UTC+03:00 all year', () => {
    // Fails loudly if Türkiye's time rules ever change (spec rule 0.8 says: no DST).
    const format = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Europe/Istanbul',
      timeZoneName: 'longOffset',
    });
    for (const month of [0, 3, 6, 9]) {
      const offset = format
        .formatToParts(new Date(Date.UTC(2026, month, 15, 12)))
        .find((part) => part.type === 'timeZoneName')?.value;
      expect(offset).toBe(`GMT${SITE_UTC_OFFSET}`);
    }
  });
});

describe('audit filters', () => {
  it('sends nothing for an empty form', () => {
    expect(toFilters(EMPTY_DRAFT)).toEqual({});
  });

  it('turns the range into [first day 00:00, day after last day 00:00) and trims texts', () => {
    expect(
      toFilters({
        range: { from: new Date(2026, 9, 10), to: new Date(2026, 9, 12, 23, 59) },
        actor: ' tolga ',
        action: 'auth.',
        target: ' ',
      }),
    ).toEqual({
      from: '2026-10-10T00:00:00+03:00',
      to: '2026-10-13T00:00:00+03:00',
      actor: 'tolga',
      action: 'auth.',
    });
  });
});
