import { LOCALE_AREAS } from '@/lib/i18n';
import en from '@/locales/en.json';
import tr from '@/locales/tr.json';
import { LEGAL_REASONS } from '@/test/w5-fixtures';

/*
 * Wave 5 contract 2.6: "No text says 'uyumlu', 'garanti' or 'compliant'" (and no "guarantee"):
 * the product supports 5651 obligations, it does not promise compliance. Scanned here are the
 * texts of the Wave 5 web package (W5-D): its locale areas, the base texts of Ayarlar → Zaman
 * damgası it renames, and the reason sentences of the API (`w5-fixtures.ts`, §2.6 verbatim). It
 * is limited to them on purpose: older texts name the "Uyum Durumu" page ("compliance") and are
 * checked for claims by `locales/locales.test.ts`.
 */

type Tree = { [key: string]: string | Tree };

/** The locale areas of W5-D (each `areas/<area>/{tr,en}.json`). */
const AREAS = ['legalStatus', 'sealRenewal', 'dashboardBlocking', 'tsaProvider'];
/** The base groups W5-D rewrites (the Kamu SM page becomes "Zaman damgası"). */
const BASE_GROUPS = ['settings.kamusm', 'settings.sections.kamusm'];

const FORBIDDEN = /uyumlu|uyumu|garanti|complian|guarantee/;

/** Lower case with the dotless and dotted i folded and white space collapsed (`locales.test.ts`). */
function fold(text: string): string {
  return text
    .toLocaleLowerCase('tr')
    .replace(/i\u0307/g, 'i')
    .replace(/ı/g, 'i')
    .replace(/\s+/g, ' ');
}

/** Every text of `node` by its key under `prefix`. */
function texts(node: string | Tree | undefined, prefix: string): [string, string][] {
  if (node === undefined) return [];
  if (typeof node === 'string') return [[prefix, node]];
  return Object.entries(node).flatMap(([key, value]) => texts(value, `${prefix}.${key}`));
}

function group(tree: Tree, path: string): string | Tree | undefined {
  return path
    .split('.')
    .reduce<string | Tree | undefined>(
      (node, part) => (typeof node === 'object' ? node[part] : undefined),
      tree,
    );
}

function offending(entries: [string, string][]): string[] {
  return entries.filter(([, text]) => FORBIDDEN.test(fold(text))).map(([key]) => key);
}

describe('Wave 5 texts claim no compliance and no guarantee', () => {
  it.each(AREAS)('locale area %s', (area) => {
    const files = LOCALE_AREAS[area];
    expect(files?.tr, `areas/${area}/tr.json`).toBeDefined();
    expect(files?.en, `areas/${area}/en.json`).toBeDefined();
    for (const lang of ['tr', 'en'] as const) {
      const found = texts(files?.[lang], `${area}/${lang}`);
      expect(found.length, `${area}/${lang}`).toBeGreaterThan(0);
      expect(offending(found)).toEqual([]);
    }
  });

  it.each(BASE_GROUPS)('base texts %s', (path) => {
    for (const [lang, tree] of [
      ['tr', tr],
      ['en', en],
    ] as const) {
      const found = texts(group(tree, path), `${lang}:${path}`);
      expect(found.length, `${lang}:${path}`).toBeGreaterThan(0);
      expect(offending(found)).toEqual([]);
    }
  });

  it('the API’s reason sentences (§2.6)', () => {
    const found = Object.values(LEGAL_REASONS).flatMap((reason): [string, string][] => [
      [`${reason.code}.message_tr`, reason.message_tr],
      [`${reason.code}.message_en`, reason.message_en],
    ]);
    expect(found).toHaveLength(16);
    expect(offending(found)).toEqual([]);
  });

  it('the scan finds the words in any case and with the Turkish i', () => {
    expect(
      offending([
        ['a', 'Yasal olarak UYUMLU'],
        ['b', 'GARANTİ'],
        ['c', 'GARANTI'],
        ['d', 'Compliant'],
        ['e', 'we guarantee'],
        ['f', 'mevzuata uyumu'],
        ['g', '5651 yükümlülüklerini desteklemek için tasarlanmıştır'],
      ]),
    ).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
  });
});
