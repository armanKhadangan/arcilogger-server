import { RAW, need } from '@/test/samples';

import {
  endpoint,
  formatDay,
  formatDistance,
  parseId,
  protocolName,
  shortHash,
  siteDateTime,
  siteToday,
  visibleText,
} from './display';

describe('visibleText', () => {
  it('writes reordering and invisible characters as escapes', () => {
    expect(visibleText('fatura\u202excod.exe')).toBe('fatura\\u202excod.exe');
    expect(visibleText('a\u200bb\u2028c\u0000d\u0007')).toBe('a\\u200bb\\u2028c\\u0000d\\u0007');
    expect(visibleText('PLN-PC14 Selin’in bilgisayarı')).toBe('PLN-PC14 Selin’in bilgisayarı');
  });

  it('shows a device line the same way as the API does', () => {
    const line = need(RAW.lines[RAW.odd_line], 'odd raw line');
    const bytes = Uint8Array.from(atob(line.raw_base64), (char) => char.charCodeAt(0));
    const decoded = new TextDecoder().decode(bytes);
    expect(decoded).toContain('fatura\u202excod.exe');
    expect(visibleText(decoded)).toContain('hostname="fatura\\u202excod.exe"');
    // The API's own display text is already safe: nothing changes.
    expect(visibleText(line.text)).toBe(line.text);
    expect(line.text).toContain('fatura\\u202excod.exe');
  });
});

describe('small formats', () => {
  it('protocols and endpoints', () => {
    expect(protocolName(6)).toBe('TCP');
    expect(protocolName(17)).toBe('UDP');
    expect(protocolName(1)).toBe('ICMP');
    expect(protocolName(47)).toBe('47');
    expect(protocolName(null)).toBeNull();
    expect(endpoint('100.64.12.9', 51001)).toBe('100.64.12.9:51001');
    expect(endpoint('2001:db8::1', 443)).toBe('[2001:db8::1]:443');
    expect(endpoint('100.64.12.9', null)).toBe('100.64.12.9');
    expect(endpoint(null, null)).toBe('—');
  });

  it('time distances', () => {
    expect(formatDistance(0)).toBe('0 sn');
    expect(formatDistance(2500)).toBe('2,5 sn');
    expect(formatDistance(30_000, 'en')).toBe('30 s');
  });

  it('site days in Europe/Istanbul', () => {
    expect(siteDateTime('2026-10-09T08:23:41+00:00')).toEqual({
      date: '2026-10-09',
      time: '11:23:41',
    });
    expect(siteToday(new Date('2026-10-12T21:30:00Z'))).toBe('2026-10-13');
    expect(formatDay('2026-10-04')).toBe('04.10.2026');
  });

  it('ids from the URL and short hashes', () => {
    expect(parseId('17')).toBe(17);
    expect(parseId('017')).toBeNull();
    expect(parseId('1e3')).toBeNull();
    expect(parseId('99999999999999999')).toBeNull();
    expect(parseId(null)).toBeNull();
    expect(shortHash('a'.repeat(64))).toBe(`${'a'.repeat(12)}…`);
    expect(shortHash(null)).toBe('—');
  });
});
