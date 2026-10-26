import {
  customRange,
  parseRangeText,
  rangeFromParams,
  rangeKey,
  rangeText,
  rangeToParams,
  resolvePreset,
  resolveRange,
  siteIso,
} from './time-range';

/** A moment given in site time. */
const at = (site: string) => new Date(`${site}+03:00`);

describe('time range presets', () => {
  it('end now (to the next whole second) and reach back their span', () => {
    const now = at('2026-10-09T14:30:15.250');
    expect(resolvePreset('15m', now)).toEqual({
      from: '2026-10-09T14:15:16+03:00',
      to: '2026-10-09T14:30:16+03:00',
    });
    expect(resolvePreset('1h', at('2026-10-09T14:30:15'))).toEqual({
      from: '2026-10-09T13:30:15+03:00',
      to: '2026-10-09T14:30:15+03:00',
    });
    expect(resolvePreset('24h', at('2026-10-09T14:30:15'))).toEqual({
      from: '2026-10-08T14:30:15+03:00',
      to: '2026-10-09T14:30:15+03:00',
    });
    expect(resolvePreset('7d', at('2026-10-09T14:30:15')).from).toBe('2026-10-02T14:30:15+03:00');
    expect(resolvePreset('30d', at('2026-10-09T14:30:15')).from).toBe('2026-09-09T14:30:15+03:00');
  });

  it('take "Bugün" from site midnight, at least one second long', () => {
    expect(resolvePreset('today', at('2026-10-09T14:30:15'))).toEqual({
      from: '2026-10-09T00:00:00+03:00',
      to: '2026-10-09T14:30:15+03:00',
    });
    // Exactly at midnight: the range is the first second of the day, never empty.
    expect(resolvePreset('today', at('2026-10-09T00:00:00'))).toEqual({
      from: '2026-10-09T00:00:00+03:00',
      to: '2026-10-09T00:00:01+03:00',
    });
    // One second before midnight it is still the old day.
    expect(resolvePreset('today', at('2026-10-09T23:59:59')).from).toBe(
      '2026-10-09T00:00:00+03:00',
    );
  });

  it('take "Dün" as the whole previous site day, across month and year ends', () => {
    expect(resolvePreset('yesterday', at('2026-10-09T00:00:00'))).toEqual({
      from: '2026-10-08T00:00:00+03:00',
      to: '2026-10-09T00:00:00+03:00',
    });
    expect(resolvePreset('yesterday', at('2026-11-01T08:00:00'))).toEqual({
      from: '2026-10-31T00:00:00+03:00',
      to: '2026-11-01T00:00:00+03:00',
    });
    expect(resolvePreset('yesterday', at('2027-01-01T00:30:00')).from).toBe(
      '2026-12-31T00:00:00+03:00',
    );
  });

  it('follow the site day whatever the moment is in UTC', () => {
    // 22:30 UTC is already the next site day (UTC+03:00).
    const now = new Date('2026-10-09T22:30:00Z');
    expect(siteIso(now)).toBe('2026-10-10T01:30:00+03:00');
    expect(resolvePreset('today', now).from).toBe('2026-10-10T00:00:00+03:00');
    expect(resolvePreset('yesterday', now)).toEqual({
      from: '2026-10-09T00:00:00+03:00',
      to: '2026-10-10T00:00:00+03:00',
    });
  });

  it('leave a fixed span as it is', () => {
    const span = { from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T11:00:00+03:00' };
    expect(resolveRange(span)).toEqual(span);
  });
});

describe('the range in the address', () => {
  it('defaults to the last 24 hours', () => {
    expect(rangeFromParams(new URLSearchParams())).toEqual({ preset: '24h' });
    expect(rangeFromParams(new URLSearchParams('range=2h'))).toEqual({ preset: '24h' });
    expect(rangeFromParams(new URLSearchParams('range=7d'))).toEqual({ preset: '7d' });
  });

  it('reads a fixed span with offsets; refuses one that is reversed or not a time', () => {
    const params = new URLSearchParams();
    params.set('from', '2026-10-09T10:00:00+03:00');
    params.set('to', '2026-10-09T11:00:00+03:00');
    expect(rangeFromParams(params)).toEqual({
      from: '2026-10-09T10:00:00+03:00',
      to: '2026-10-09T11:00:00+03:00',
    });
    params.set('to', '2026-10-09T09:00:00+03:00');
    expect(rangeFromParams(params)).toEqual({ preset: '24h' });
    expect(rangeFromParams(new URLSearchParams('from=yesterday&to=now&range=1h'))).toEqual({
      preset: '1h',
    });
    // No time zone: refused (the API needs one).
    expect(rangeFromParams(new URLSearchParams('from=2026-10-09T10:00&to=2026-10-09T11:00'))).toEqual(
      { preset: '24h' },
    );
  });

  it('writes a range and keeps the other parameters (a saved search)', () => {
    const fixed = rangeToParams(
      { from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T11:00:00+03:00' },
      new URLSearchParams('range=24h&saved=3'),
    );
    expect(fixed.get('range')).toBeNull();
    expect(fixed.get('saved')).toBe('3');
    expect(rangeFromParams(fixed)).toEqual({
      from: '2026-10-09T10:00:00+03:00',
      to: '2026-10-09T11:00:00+03:00',
    });
    const preset = rangeToParams({ preset: '1h' }, fixed);
    expect(preset.toString()).toBe('saved=3&range=1h');
    expect(rangeKey({ preset: '1h' })).not.toBe(rangeKey(rangeFromParams(fixed)));
  });
});

describe('"Özel" typed in the picker', () => {
  it('reads the site format, a day alone meaning its midnight', () => {
    expect(parseRangeText('09.10.2026 14:30')).toBe('2026-10-09T14:30:00+03:00');
    expect(parseRangeText(' 9.10.2026  8:05:07 ')).toBe('2026-10-09T08:05:07+03:00');
    expect(parseRangeText('09.10.2026')).toBe('2026-10-09T00:00:00+03:00');
    expect(parseRangeText('2026-10-09 14:30')).toBe('2026-10-09T14:30:00+03:00');
    expect(parseRangeText('31.02.2026 10:00')).toBeNull();
    expect(parseRangeText('09.10.2026 2:30 PM')).toBeNull();
    expect(rangeText('2026-10-09T14:30:00+03:00')).toBe('09.10.2026 14:30');
    expect(rangeText('2026-10-09T14:30:15+03:00')).toBe('09.10.2026 14:30:15');
  });

  it('needs both bounds in order and at most the longest search range apart', () => {
    expect(customRange('09.10.2026 10:00', '09.10.2026 11:00', 31)).toEqual({
      range: { from: '2026-10-09T10:00:00+03:00', to: '2026-10-09T11:00:00+03:00' },
    });
    expect(customRange('', 'yarın', 31)).toEqual({
      errors: { from: 'validation.required', to: 'timeRange.errors.format' },
    });
    expect(customRange('09.10.2026 11:00', '09.10.2026 11:00', 31)).toEqual({
      errors: { to: 'timeRange.errors.order' },
    });
    expect(customRange('01.09.2026', '02.10.2026 00:00:01', 31)).toEqual({
      errors: { to: 'timeRange.errors.tooLong' },
    });
    expect('range' in customRange('01.09.2026', '02.10.2026', 31)).toBe(true);
  });
});
