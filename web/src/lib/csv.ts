/**
 * CSV export that opens correctly in Excel with Turkish regional settings:
 *  - semicolon delimiter (Turkish Excel uses ";" because the decimal separator is ",");
 *  - UTF-8 byte order mark so ı, İ, ş, ğ are not garbled;
 *  - CRLF line endings.
 *
 * Log data is attacker-controlled (hostnames, URLs, DHCP host names). Text cells that start with
 * = + - @ or a control character are prefixed with an apostrophe so a spreadsheet never runs
 * them as formulas (CSV/formula injection).
 */

export type CsvValue = string | number | boolean | null | undefined;

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => CsvValue;
}

export interface CsvOptions {
  delimiter?: string;
  bom?: boolean;
}

const FORMULA_START = /^[=+\-@\t\r]/;

export function csvCell(value: CsvValue, delimiter = ';'): string {
  if (value === null || value === undefined) return '';
  let text = String(value);
  if (typeof value === 'string' && FORMULA_START.test(text)) text = `'${text}`;
  const needsQuotes =
    text.includes(delimiter) ||
    /["\r\n]/.test(text) ||
    text !== text.trim() ||
    text.startsWith("'");
  return needsQuotes ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv<T>(
  rows: readonly T[],
  columns: readonly CsvColumn<T>[],
  options: CsvOptions = {},
): string {
  const { delimiter = ';', bom = true } = options;
  const lines = [
    columns.map((column) => csvCell(column.header, delimiter)).join(delimiter),
    ...rows.map((row) =>
      columns.map((column) => csvCell(column.value(row), delimiter)).join(delimiter),
    ),
  ];
  return `${bom ? '﻿' : ''}${lines.join('\r\n')}\r\n`;
}

/** Offer text as a file download (no server round trip). */
export function downloadText(
  fileName: string,
  text: string,
  mime = 'text/csv;charset=utf-8',
): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
