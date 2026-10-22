/**
 * Golden test: the browser's canonical form and hash of audit entries must equal what the
 * server's own code computed (`src/generated/audit-vectors.json`, from `arcilogger_api.audit`).
 * jsdom has no `crypto.subtle`; Node's Web Crypto stands in for the browser's (HTTPS).
 */

import { webcrypto } from 'node:crypto';

import vectors from '@/generated/audit-vectors.json';

import type { AuditEntry } from './api-types';
import {
  CanonicalJsonError,
  canHash,
  canonicalEntryText,
  canonicalJson,
  canonicalTime,
  checkEntry,
  entryHash,
  GENESIS_HASH,
} from './audit-chain';

/** The vectors are entries as a reader who sees names gets them: with both hashes. */
type StoredEntry = AuditEntry & { prev_hash: string; hash: string };
const ENTRIES = vectors.entries as { api: StoredEntry; canonical: string }[];

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('without Web Crypto (plain HTTP on another host)', () => {
  it('shows the canonical text but says it cannot hash', async () => {
    vi.stubGlobal('crypto', { getRandomValues: () => undefined });
    expect(canHash()).toBe(false);
    const entry = ENTRIES[1]?.api;
    if (!entry) throw new Error('vector missing');
    await expect(checkEntry(entry)).resolves.toEqual({
      state: 'unavailable',
      canonical: ENTRIES[1]?.canonical,
      reason: 'no-crypto',
    });
  });
});

describe('canonicalJson (RFC 8785 without floats)', () => {
  it('sorts keys by UTF-16 code units, not by code point', () => {
    // U+1F600 is D83D DE00 in UTF-16 and sorts before U+FF61; by code point it would be after.
    expect(canonicalJson({ '｡': 1, '😀': 2, é: 3, B: 4, a: 5 })).toBe(
      '{"B":4,"a":5,"é":3,"😀":2,"｡":1}',
    );
  });

  it('writes strings like JSON.stringify, without whitespace', () => {
    expect(
      canonicalJson({ s: 'a"b\\c\n\t\u0001\u2028İ', n: [1, -2, null, true, false, {}, []] }),
    ).toBe('{"n":[1,-2,null,true,false,{},[]],"s":"a\\"b\\\\c\\n\\t\\u0001\u2028İ"}');
  });

  it('refuses floats and integers outside the safe range', () => {
    expect(() => canonicalJson({ x: 1.5 })).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(2 ** 53)).toThrow(CanonicalJsonError);
    expect(canonicalJson(2 ** 53 - 1)).toBe('9007199254740991');
  });
});

describe('canonicalTime', () => {
  it('pads missing microseconds and writes +00:00', () => {
    expect(canonicalTime('2026-10-12T05:00:00Z')).toBe('2026-10-12T05:00:00.000000+00:00');
    expect(canonicalTime('2026-10-12T05:02:17.007919Z')).toBe('2026-10-12T05:02:17.007919+00:00');
    expect(canonicalTime('2026-10-12T05:02:17.5Z')).toBe('2026-10-12T05:02:17.500000+00:00');
  });

  it('converts other offsets to UTC across the day boundary', () => {
    expect(canonicalTime('2026-10-12T01:30:00.000120+03:00')).toBe(
      '2026-10-11T22:30:00.000120+00:00',
    );
    expect(canonicalTime('2026-12-31T23:00:00-02:00')).toBe('2027-01-01T01:00:00.000000+00:00');
  });

  it('refuses text that is not an ISO time', () => {
    expect(() => canonicalTime('12.10.2026 09:44')).toThrow(CanonicalJsonError);
  });
});

describe('golden vectors from the server', () => {
  it('has enough vectors, starting at the genesis hash', () => {
    expect(ENTRIES.length).toBeGreaterThan(100);
    expect(ENTRIES[0]?.api.prev_hash).toBe(GENESIS_HASH);
  });

  it.each(ENTRIES.slice(0, 12).map((vector) => [vector.api.id, vector] as const))(
    'entry %i: canonical text and hash equal the server',
    async (_, vector) => {
      const canonical = canonicalEntryText(vector.api);
      expect(canonical).toBe(vector.canonical);
      expect(await entryHash(vector.api.prev_hash, canonical)).toBe(vector.api.hash);
      await expect(checkEntry(vector.api)).resolves.toEqual({ state: 'match', canonical });
    },
  );

  it('every entry links to the previous hash and verifies', async () => {
    let previous = GENESIS_HASH;
    for (const { api } of ENTRIES) {
      expect(api.prev_hash).toBe(previous);
      expect((await checkEntry(api)).state).toBe('match');
      previous = api.hash;
    }
  });

  it('detects a changed detail, actor, time or hash', async () => {
    const original = ENTRIES[7]?.api;
    if (!original) throw new Error('vector missing');
    const changes: AuditEntry[] = [
      { ...original, details: { ...original.details, new: 'Örnek A.Ş.' } },
      { ...original, actor: 'mallory' },
      { ...original, time: original.time.replace('05:15', '05:16') },
      { ...original, hash: original.hash.replace(/^./, (c) => (c === '0' ? '1' : '0')) },
    ];
    for (const changed of changes) {
      expect((await checkEntry(changed)).state).toBe('mismatch');
    }
  });

  it('reports details it cannot hash exactly instead of guessing', async () => {
    const original = ENTRIES[0]?.api;
    if (!original) throw new Error('vector missing');
    const result = await checkEntry({ ...original, details: { ratio: 0.5 } });
    expect(result).toEqual({ state: 'unavailable', canonical: null, reason: 'not-canonical' });
  });

  it('does not check an entry whose target the API masked (it was hashed with the real one)', async () => {
    const original = ENTRIES[7]?.api;
    if (!original) throw new Error('vector missing');
    expect(original.target_masked).toBe(false);
    const masked = { ...original, target: 'usage:p:3f2a9c1b7e3d5a60', target_masked: true };
    await expect(checkEntry(masked)).resolves.toEqual({
      state: 'unavailable',
      canonical: null,
      reason: 'masked',
    });
  });

  it('does not check an entry without hashes (a reader who sees pseudonyms gets none)', async () => {
    const vector = ENTRIES[7];
    if (!vector) throw new Error('vector missing');
    for (const hidden of [
      { ...vector.api, prev_hash: null, hash: null },
      { ...vector.api, hash: null },
      { ...vector.api, prev_hash: null },
    ]) {
      await expect(checkEntry(hidden)).resolves.toEqual({
        state: 'unavailable',
        canonical: vector.canonical,
        reason: 'hidden',
      });
    }
  });
});
