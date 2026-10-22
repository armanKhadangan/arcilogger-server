/**
 * Day and time fields typed as text in the Turkish format (`12.10.2026`, `09:44:07`).
 *
 * The browser's own date and time inputs follow the browser's UI language: an English Windows
 * shows `10/09/2026` (read as 10 September in Türkiye, but meaning 9 October) and a 12-hour clock
 * with AM/PM. For an official request that ambiguity is not acceptable, so the forms take the
 * site's format (spec rule 0.8: `dd.MM.yyyy HH:mm:ss`, 24 hours) and convert to ISO here.
 */

const DAY_TEXT = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/;
const DAY_ISO = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_TEXT = /^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?$/;

function validDay(year: number, month: number, day: number): string | null {
  if (month < 1 || month > 12 || day < 1 || year < 1900 || year > 9999) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** `09.10.2026`, `9.10.2026`, `09/10/2026` or `2026-10-09` → `2026-10-09`; else `null`. */
export function parseDay(text: string): string | null {
  const value = text.trim();
  const local = DAY_TEXT.exec(value);
  if (local) return validDay(Number(local[3]), Number(local[2]), Number(local[1]));
  const iso = DAY_ISO.exec(value);
  if (iso) return validDay(Number(iso[1]), Number(iso[2]), Number(iso[3]));
  return null;
}

/** `2026-10-09` → `09.10.2026` (other text unchanged). */
export function dayText(iso: string): string {
  const match = DAY_ISO.exec(iso);
  return match ? `${match[3] ?? ''}.${match[2] ?? ''}.${match[1] ?? ''}` : iso;
}

/** `17:44`, `17:44:07`, `8.05` → `17:44:00`, `17:44:07`, `08:05:00` (24 hours); else `null`. */
export function parseTime(text: string): string | null {
  const match = TIME_TEXT.exec(text.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = match[3] === undefined ? 0 : Number(match[3]);
  if (hours > 23 || minutes > 59 || seconds > 59) return null;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':');
}
