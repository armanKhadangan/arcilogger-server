import { csvCell, toCsv } from './csv';

describe('csvCell', () => {
  it('leaves plain values alone', () => {
    expect(csvCell('172.20.60.142')).toBe('172.20.60.142');
    expect(csvCell(1234)).toBe('1234');
    expect(csvCell(-5)).toBe('-5');
    expect(csvCell(null)).toBe('');
    expect(csvCell(undefined)).toBe('');
  });

  it('quotes delimiters, quotes and line breaks', () => {
    expect(csvCell('a;b')).toBe('"a;b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell('two\nlines')).toBe('"two\nlines"');
    expect(csvCell(' padded ')).toBe('" padded "');
  });

  it('neutralises formula injection in text cells', () => {
    expect(csvCell('=HYPERLINK("http://x","y")')).toBe(`"'=HYPERLINK(""http://x"",""y"")"`);
    expect(csvCell('+1')).toBe(`"'+1"`);
    expect(csvCell('-cmd')).toBe(`"'-cmd"`);
    expect(csvCell('@SUM(A1)')).toBe(`"'@SUM(A1)"`);
  });

  it('keeps Turkish characters', () => {
    expect(csvCell('İdari İşler')).toBe('İdari İşler');
  });
});

describe('toCsv', () => {
  const rows = [
    { ip: '172.20.10.37', person: 'Tolga', bytes: 1536 },
    { ip: '172.20.60.142', person: 'Misafir; Telefon', bytes: 0 },
  ];

  it('writes BOM, header, semicolons and CRLF', () => {
    const csv = toCsv(rows, [
      { header: 'IP', value: (r) => r.ip },
      { header: 'Kişi', value: (r) => r.person },
      { header: 'Bayt', value: (r) => r.bytes },
    ]);
    expect(csv).toBe(
      '﻿IP;Kişi;Bayt\r\n172.20.10.37;Tolga;1536\r\n172.20.60.142;"Misafir; Telefon";0\r\n',
    );
  });

  it('supports comma delimiter without BOM', () => {
    expect(
      toCsv([{ a: 'x,y' }], [{ header: 'a', value: (r) => r.a }], { delimiter: ',', bom: false }),
    ).toBe('a\r\n"x,y"\r\n');
  });
});
