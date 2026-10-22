import { dayText, parseDay, parseTime } from './dates';

describe('parseDay', () => {
  it('reads the Turkish day format and ISO', () => {
    expect(parseDay('09.10.2026')).toBe('2026-10-09');
    expect(parseDay(' 9.10.2026 ')).toBe('2026-10-09');
    expect(parseDay('09/10/2026')).toBe('2026-10-09');
    expect(parseDay('9-10-2026')).toBe('2026-10-09');
    expect(parseDay('2026-10-09')).toBe('2026-10-09');
  });

  it('day first, never month first', () => {
    // 10/09/2026 is 10 September in Türkiye.
    expect(parseDay('10/09/2026')).toBe('2026-09-10');
    expect(parseDay('10/13/2026')).toBeNull();
  });

  it('refuses days that do not exist and other text', () => {
    expect(parseDay('31.09.2026')).toBeNull();
    expect(parseDay('29.02.2026')).toBeNull();
    expect(parseDay('29.02.2028')).toBe('2028-02-29');
    expect(parseDay('2026-9-1')).toBeNull();
    expect(parseDay('09.10.26')).toBeNull();
    expect(parseDay('')).toBeNull();
    expect(parseDay('dün')).toBeNull();
  });
});

describe('dayText', () => {
  it('writes an ISO day the Turkish way and leaves other text alone', () => {
    expect(dayText('2026-10-09')).toBe('09.10.2026');
    expect(dayText('09.10.2026')).toBe('09.10.2026');
  });
});

describe('parseTime', () => {
  it('reads a 24-hour time', () => {
    expect(parseTime('17:44')).toBe('17:44:00');
    expect(parseTime('17:44:07')).toBe('17:44:07');
    expect(parseTime('8.05')).toBe('08:05:00');
    expect(parseTime('00:00:00')).toBe('00:00:00');
    expect(parseTime('23:59:59')).toBe('23:59:59');
  });

  it('refuses 12-hour clocks and impossible times', () => {
    expect(parseTime('7:30 PM')).toBeNull();
    expect(parseTime('24:00')).toBeNull();
    expect(parseTime('12:60')).toBeNull();
    expect(parseTime('12:00:60')).toBeNull();
    expect(parseTime('1230')).toBeNull();
  });
});
