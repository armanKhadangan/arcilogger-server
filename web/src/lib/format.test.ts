import {
  formatBytes,
  formatCompact,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatTime,
  type Lang,
} from './format';

import sharedVectors from '../../../tests/fixtures/format-vectors.json';

interface Vectors {
  datetime: { utc: string; expected: string }[];
  date: { utc: string; expected: string }[];
  number: { value: number; decimals: number; lang: Lang; expected: string }[];
  bytes: { value: number; lang: Lang; expected: string }[];
  duration: { seconds: number; lang: Lang; expected: string }[];
}

// Shared with the Python implementation (core/tests/test_fmt.py).
const vectors = sharedVectors as Vectors;

describe('shared format vectors', () => {
  it.each(vectors.datetime)('datetime $utc', ({ utc, expected }) => {
    expect(formatDateTime(utc)).toBe(expected);
  });
  it.each(vectors.date)('date $utc', ({ utc, expected }) => {
    expect(formatDate(utc)).toBe(expected);
  });
  it.each(vectors.number)(
    'number $value ($lang, $decimals)',
    ({ value, decimals, lang, expected }) => {
      expect(formatNumber(value, decimals, lang)).toBe(expected);
    },
  );
  it.each(vectors.bytes)('bytes $value ($lang)', ({ value, lang, expected }) => {
    expect(formatBytes(value, lang)).toBe(expected);
  });
  it.each(vectors.duration)('duration $seconds ($lang)', ({ seconds, lang, expected }) => {
    expect(formatDuration(seconds, lang)).toBe(expected);
  });
});

describe('web-only helpers', () => {
  it('formats time of day in Istanbul', () => {
    expect(formatTime('2026-09-23T06:44:07Z')).toBe('09:44');
    expect(formatTime('2026-09-23T06:44:07Z', true)).toBe('09:44:07');
  });

  it('compacts large counts', () => {
    expect(formatCompact(1284)).toBe('1.284');
    expect(formatCompact(12_900)).toBe('12,9 bin');
    expect(formatCompact(4_200_000)).toBe('4,2 Mn');
    expect(formatCompact(4_200_000, 'en')).toBe('4.2 M');
    expect(formatCompact(3_000_000_000, 'en')).toBe('3 B');
    expect(formatCompact(-25_000)).toBe('-25 bin');
  });

  it('rejects invalid input', () => {
    expect(() => formatDateTime('not a date')).toThrow(RangeError);
    expect(() => formatBytes(-1)).toThrow(RangeError);
    expect(() => formatDuration(Number.NaN)).toThrow(RangeError);
    expect(() => formatNumber(Infinity)).toThrow(RangeError);
    expect(() => formatNumber(1, -1)).toThrow(RangeError);
  });
});
