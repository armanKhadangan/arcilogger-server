/**
 * The canonical form and hash of one audit-log entry, recomputed in the browser.
 *
 * Twin of `arcilogger_api.audit` (canonical entry, `hash = sha256(prev_hash || RFC 8785 JSON)`),
 * checked against golden vectors made by the server's own code (`src/generated/audit-vectors.json`).
 * The server verifies the whole chain (`GET /audit/verify`); this lets the "Denetim Kaydı" drawer
 * show *why* an entry is trusted — its exact hashed bytes — and confirm that the hash the page
 * received belongs to the content it shows.
 *
 * RFC 8785 here: object keys sorted by UTF-16 code units (JavaScript's default string order), no
 * whitespace, strings exactly as `JSON.stringify` writes them. Floats never occur in audit entries
 * (the server refuses them) and integers stay within ±(2^53 − 1); anything else is reported as not
 * recomputable instead of guessed.
 */

import type { AuditEntry } from './api-types';

export const GENESIS_HASH = '0'.repeat(64);

export class CanonicalJsonError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CanonicalJsonError';
  }
}

/** RFC 8785 serialisation of a JSON value without floating-point numbers. */
export function canonicalJson(value: unknown): string {
  if (value === null) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new CanonicalJsonError(`not a safe integer: ${value}`);
    return String(value);
  }
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
  }
  throw new CanonicalJsonError(`unsupported value: ${typeof value}`);
}

const API_TIME =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/;

function pad(value: number, width = 2): string {
  return String(value).padStart(width, '0');
}

/**
 * The time as the server hashed it: UTC, microseconds, `+00:00`
 * (`2026-10-12T06:44:07Z` → `2026-10-12T06:44:07.000000+00:00`). Microseconds are kept from
 * the text (a JavaScript Date has only milliseconds).
 */
export function canonicalTime(text: string): string {
  const match = API_TIME.exec(text);
  if (!match) throw new CanonicalJsonError(`not an ISO time: ${text}`);
  const [, y, mo, d, h, mi, s, fraction = '', zone = 'Z'] = match;
  let offsetMinutes = 0;
  if (zone !== 'Z') {
    const sign = zone.startsWith('-') ? -1 : 1;
    offsetMinutes = sign * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4, 6)));
  }
  const utc = new Date(
    Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)) -
      offsetMinutes * 60_000,
  );
  const date = `${pad(utc.getUTCFullYear(), 4)}-${pad(utc.getUTCMonth() + 1)}-${pad(utc.getUTCDate())}`;
  const time = `${pad(utc.getUTCHours())}:${pad(utc.getUTCMinutes())}:${pad(utc.getUTCSeconds())}`;
  return `${date}T${time}.${fraction.padEnd(6, '0')}+00:00`;
}

/** The hashed object of an entry (`arcilogger_api.audit.canonical_entry`). */
export function canonicalEntry(entry: AuditEntry): Record<string, unknown> {
  return {
    v: 1,
    id: entry.id,
    time: canonicalTime(entry.time),
    actor_id: entry.actor_id,
    actor: entry.actor,
    action: entry.action,
    target: entry.target,
    details: entry.details,
    ip: entry.ip,
  };
}

/** The exact text that was hashed (UTF-8 encoded before hashing). */
export function canonicalEntryText(entry: AuditEntry): string {
  return canonicalJson(canonicalEntry(entry));
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Web Crypto exists only in secure contexts (HTTPS, or localhost during development). */
export function canHash(): boolean {
  // Typed as always present, but `undefined` on plain HTTP pages of another host.
  const subtle = (globalThis.crypto as Partial<Crypto> | undefined)?.subtle;
  return typeof subtle?.digest === 'function';
}

/** `sha256(prev_hash || canonical)` as lower-case hex. */
export async function entryHash(prevHash: string, canonical: string): Promise<string> {
  const data = new TextEncoder().encode(`${prevHash}${canonical}`);
  return hex(await crypto.subtle.digest('SHA-256', data));
}

export type EntryCheck =
  | { state: 'match'; canonical: string }
  | { state: 'mismatch'; canonical: string; computed: string }
  | {
      state: 'unavailable';
      canonical: string | null;
      reason: 'no-crypto' | 'not-canonical' | 'masked' | 'hidden';
    };

/**
 * Recompute an entry's hash from what the page shows and compare it with the stored one. A masked
 * target (`target_masked`) is not what was hashed, and a reader who sees pseudonyms gets no
 * hashes (`hidden`): such an entry cannot be checked here.
 */
export async function checkEntry(entry: AuditEntry): Promise<EntryCheck> {
  if (entry.target_masked) return { state: 'unavailable', canonical: null, reason: 'masked' };
  let canonical: string;
  try {
    canonical = canonicalEntryText(entry);
  } catch {
    return { state: 'unavailable', canonical: null, reason: 'not-canonical' };
  }
  if (entry.prev_hash === null || entry.hash === null)
    return { state: 'unavailable', canonical, reason: 'hidden' };
  if (!canHash()) return { state: 'unavailable', canonical, reason: 'no-crypto' };
  const computed = await entryHash(entry.prev_hash, canonical);
  return computed === entry.hash
    ? { state: 'match', canonical }
    : { state: 'mismatch', canonical, computed };
}
