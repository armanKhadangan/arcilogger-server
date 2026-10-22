import { ApiError } from './api';
import { shouldRetry } from './query-client';
import { recoveryCodesFile, recoveryCodesFileName } from './recovery-codes';
import { groupSecret, svgDataUri } from './totp-display';
import { initials } from './utils';

describe('initials', () => {
  it.each([
    ['Tolga Yılmaz', 'TY'],
    ['ismail', 'İ'],
    ['  çağla   özkan  ', 'ÇÖ'],
    ['Ayşe Nur Demir', 'AD'],
    ['😀 x', '😀X'],
    ['', ''],
  ])('%j → %j', (name, expected) => {
    expect(initials(name)).toBe(expected);
  });
});

describe('recovery code file', () => {
  it('lists the codes with site time and CRLF line ends', () => {
    const text = recoveryCodesFile(
      ['aaaa-bbbb-cccc-dddd', 'eeee-ffff-gggg-hhhh'],
      'tolga',
      { heading: 'Kodlar', account: 'Hesap', created: 'Oluşturulma' },
      new Date(Date.UTC(2026, 9, 12, 6, 44, 7)),
    );
    expect(text).toBe(
      'Kodlar\r\nHesap: tolga\r\nOluşturulma: 12.10.2026 09:44:07\r\n\r\n' +
        ' 1. aaaa-bbbb-cccc-dddd\r\n 2. eeee-ffff-gggg-hhhh\r\n',
    );
    expect(text).not.toMatch(/[^\r]\n/);
  });

  it('builds a safe file name', () => {
    expect(recoveryCodesFileName('ayse.yilmaz')).toBe(
      'arcilogger-kurtarma-kodlari-ayse.yilmaz.txt',
    );
    expect(recoveryCodesFileName('../x y')).toBe('arcilogger-kurtarma-kodlari-.._x_y.txt');
  });
});

describe('TOTP display', () => {
  it('groups the key by four', () => {
    expect(groupSecret('JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP')).toBe(
      'JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP',
    );
    expect(groupSecret('ABCDEF')).toBe('ABCD EF');
  });

  it('embeds the SVG as a data URI image (never as markup)', () => {
    const uri = svgDataUri('<svg xmlns="http://www.w3.org/2000/svg"><title>TOTP QR</title></svg>');
    expect(uri.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
    expect(uri).not.toContain('<');
    expect(decodeURIComponent(uri.split(',')[1] ?? '')).toContain('<title>TOTP QR</title>');
  });
});

describe('query retries', () => {
  const error = (status: number) =>
    new ApiError({ status, code: 'x', messageTr: 'x', messageEn: 'x' });

  it('never retries client errors, retries network and server errors once', () => {
    expect(shouldRetry(0, error(401))).toBe(false);
    expect(shouldRetry(0, error(403))).toBe(false);
    expect(shouldRetry(0, error(0))).toBe(true);
    expect(shouldRetry(0, error(503))).toBe(true);
    expect(shouldRetry(1, error(503))).toBe(false);
    expect(shouldRetry(0, new Error('x'))).toBe(true);
  });
});
