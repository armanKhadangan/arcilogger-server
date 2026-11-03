import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { LOCALE_AREAS } from '@/lib/i18n';
import i18n from '@/lib/i18n';
import { ARCHIVE_FAILURE_CODES } from '@/test/archive-fixtures';

/*
 * The texts of the archive reload card (`locales/areas/archive`, Wave 5 contract §5, G10): the
 * same keys in Turkish and English, i18next placeholders, no compliance claim and no guarantee,
 * and the failure texts word for word as W5-G words them (`FAILURE_MESSAGES` of the API; the API
 * sends only the code of a failed reload, the web keeps the text).
 */

/**
 * `FAILURE_MESSAGES` of `api/src/arcilogger_api/archive/service.py` (the tests run from `web/`):
 * each code with its Turkish and English text, the adjacent string literals joined.
 */
function apiFailures(): Record<string, { tr: string; en: string }> {
  const source = readFileSync(
    join(process.cwd(), '..', 'api', 'src', 'arcilogger_api', 'archive', 'service.py'),
    'utf8',
  );
  const block = /^FAILURE_MESSAGES[^=]*= \{\n([\s\S]*?)^\}/m.exec(source)?.[1] ?? '';
  const entries = [...block.matchAll(/^ {4}"([a-z_]+)": \(\n([\s\S]*?)^ {4}\),/gm)];
  return Object.fromEntries(
    entries.map(([, code = '', body = '']) => {
      // A text is one or more literals; a comma after a literal ends it.
      const parts: string[] = [];
      let text = '';
      for (const [, literal = '', comma] of body.matchAll(/"((?:[^"\\]|\\.)*)"\s*(,)?/g)) {
        text += literal;
        if (comma) {
          parts.push(text);
          text = '';
        }
      }
      if (text) parts.push(text);
      return [code, { tr: parts[0] ?? '', en: parts[1] ?? '' }];
    }),
  );
}

type Tree = { [key: string]: string | Tree };

const FORBIDDEN = /uyumlu|uyumu|garanti|complian|guarantee/;

/** W5-G's `FAILURE_MESSAGES`, verbatim. */
const FAILURES = {
  archive_day_not_in_vault: {
    tr: 'Yükleme yapılamadı: günlerden biri artık mühürlü arşivde yok (istek beklerken saklama süresi doldu). Bu gün yeniden yüklenemez.',
    en: 'The reload failed: one of the days is no longer in the sealed archive (removed by retention while the request waited). That day cannot be reloaded.',
  },
  archive_file_unreadable: {
    tr: 'Yükleme yapılamadı: bir günün mühürlü dosyası okunamadı veya mühür kaydıyla eşleşmiyor. Sistem yöneticisine bildirin; arşiv doğrulamasını çalıştırın.',
    en: 'The reload failed: a sealed file of one of the days could not be read or does not match its seal record. Tell the administrator and run the archive verification.',
  },
} as const;

/** Lower case with the dotless and dotted i folded and white space collapsed (`locales.test.ts`). */
function fold(text: string): string {
  return text.toLocaleLowerCase('tr').replace(/i̇/g, 'i').replace(/ı/g, 'i').replace(/\s+/g, ' ');
}

/** Every text of `node` by its key under `prefix`. */
function texts(node: string | Tree | undefined, prefix: string): [string, string][] {
  if (node === undefined) return [];
  if (typeof node === 'string') return [[prefix, node]];
  return Object.entries(node).flatMap(([key, value]) =>
    texts(value, prefix ? `${prefix}.${key}` : key),
  );
}

/** The `{{name}}` placeholders of a text, sorted. */
function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\{\s*([^}\s]+)\s*\}\}/g)].map((match) => match[1] ?? '').sort();
}

const files = LOCALE_AREAS.archive;
const tr = new Map(texts(files?.tr, ''));
const en = new Map(texts(files?.en, ''));

describe('archive locale area', () => {
  it('has the same keys in Turkish and English, all under "archive"', () => {
    expect(files?.tr, 'areas/archive/tr.json').toBeDefined();
    expect(files?.en, 'areas/archive/en.json').toBeDefined();
    expect(Object.keys(files?.tr ?? {})).toEqual(['archive']);
    expect([...en.keys()].sort()).toEqual([...tr.keys()].sort());
    expect(tr.size).toBeGreaterThan(15);
  });

  it('uses the same i18next placeholders in both languages, no other kind', () => {
    for (const [key, text] of tr) {
      expect(placeholders(en.get(key) ?? ''), key).toEqual(placeholders(text));
      for (const lang of [text, en.get(key) ?? '']) {
        expect(lang.replace(/\{\{[^}]+\}\}/g, ''), key).not.toMatch(/[{}]|%\(|\$\{/);
      }
    }
    expect(placeholders(tr.get('archive.card.clipped') ?? '')).toEqual([
      'first',
      'last',
      'max',
      'max',
    ]);
    expect(placeholders(tr.get('archive.progress.of') ?? '')).toEqual(['done', 'percent', 'total']);
    expect(placeholders(tr.get('archive.failure.unknown') ?? '')).toEqual(['code']);
  });

  it('claims no compliance and no guarantee', () => {
    const offending = [...tr, ...en]
      .filter(([, text]) => FORBIDDEN.test(fold(text)))
      .map(([key]) => key);
    expect(offending).toEqual([]);
    expect(FORBIDDEN.test(fold('GARANTİ'))).toBe(true);
    expect(FORBIDDEN.test(fold('Compliant'))).toBe(true);
  });

  it('keeps the failure texts of W5-G word for word, one per failure code', () => {
    expect(apiFailures()).toEqual(FAILURES);
    expect([...ARCHIVE_FAILURE_CODES].sort()).toEqual(Object.keys(FAILURES).sort());
    for (const code of ARCHIVE_FAILURE_CODES) {
      expect(tr.get(`archive.failure.${code}`), code).toBe(FAILURES[code].tr);
      expect(en.get(`archive.failure.${code}`), code).toBe(FAILURES[code].en);
    }
    expect(tr.get('archive.failure.unknown')).toBe('Yükleme yapılamadı ({{code}}).');
    expect(en.get('archive.failure.unknown')).toBe('The reload failed ({{code}}).');
  });

  it('names db.reload_keep_days with its default and the audit in the dialog', () => {
    expect(tr.get('archive.dialog.body')).toContain('db.reload_keep_days');
    expect(tr.get('archive.dialog.body')).toContain('(varsayılan 7 gün)');
    expect(tr.get('archive.dialog.body')).toContain('denetim kaydına yazılır');
    expect(en.get('archive.dialog.body')).toContain('(7 days by default)');
    expect(en.get('archive.dialog.body')).toContain('written to the audit log');
  });

  it('every key reaches its own text through i18next', () => {
    for (const [lang, map] of [
      ['tr', tr],
      ['en', en],
    ] as const) {
      const t = i18n.getFixedT(lang);
      for (const key of map.keys()) expect(t(key), key).not.toBe(key);
      expect(t('archive.card.title')).toBe(map.get('archive.card.title'));
    }
  });
});
