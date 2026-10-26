import i18n, { addTexts, LOCALE_AREAS, withAreas } from '@/lib/i18n';

import en from './en.json';
import tr from './tr.json';
import uiKitEn from './ui-kit/en.json';
import uiKitTr from './ui-kit/tr.json';

type Tree = { [key: string]: string | Tree };

function keys(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === 'string' ? [`${prefix}${key}`] : keys(value, `${prefix}${key}.`),
  );
}

/** The keys of `extra` that would replace a text of `base`, or a group of its texts. */
function collisions(base: Tree, extra: Tree): string[] {
  const existing = keys(base);
  const leaves = new Set(existing);
  return keys(extra).filter(
    (key) =>
      leaves.has(key) ||
      existing.some((other) => other.startsWith(`${key}.`) || key.startsWith(`${other}.`)),
  );
}

/** Every area file (`areas/<area>/<lang>.json`) by a name like `search/tr`. */
const AREA_FILES: Record<string, Tree> = Object.fromEntries(
  Object.entries(LOCALE_AREAS).flatMap(([area, files]) =>
    Object.entries(files).map(([lang, tree]) => [`${area}/${lang}`, tree]),
  ),
);

/** The texts the app ships (the locale files with every area) and the UI kit's. */
const ALL: Record<string, Tree> = { tr, en, uiKitTr, uiKitEn, ...AREA_FILES };
/** The texts of the shipped bundle: the locale files and the areas, not the UI kit. */
const SHIPPED: Record<string, Tree> = { tr, en, ...AREA_FILES };

describe('locale files', () => {
  it('have exactly the same keys in Turkish and English', () => {
    expect(keys(en as Tree).sort()).toEqual(keys(tr as Tree).sort());
    expect(keys(uiKitEn as Tree).sort()).toEqual(keys(uiKitTr as Tree).sort());
  });

  it('keep the UI kit’s texts in ui-kit/: they add keys, replace none, and the shipped bundle has none', () => {
    const shipped = new Set(keys(withAreas(tr, 'tr')));
    expect(keys(uiKitTr as Tree).filter((key) => shipped.has(key))).toEqual([]);
    for (const [name, tree] of Object.entries(SHIPPED))
      expect(JSON.stringify(tree), name).not.toMatch(/Bileşen Kit|UI kit/i);
  });

  it('have no empty strings', () => {
    for (const [name, tree] of Object.entries(ALL)) {
      const empty = keys(tree).filter((key) => {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Tree)[part], tree);
        return typeof value !== 'string' || value.trim() === '';
      });
      expect(empty, name).toEqual([]);
    }
  });

  it('claim no legal compliance and no copy protection (licensing contract 3.5, 8.3)', () => {
    for (const [name, tree] of Object.entries(SHIPPED)) {
      const text = JSON.stringify(tree).toLocaleLowerCase('tr');
      for (const claim of [
        'yasal olarak uyumlu',
        'legally compliant',
        'kopyalanamaz',
        'kırılamaz',
        'tamper-proof',
      ])
        expect(text, `${name}: ${claim}`).not.toContain(claim);
    }
  });

  it('name no development milestone (M1, M6, …): a later page "arrives in a later version"', () => {
    for (const [name, tree] of Object.entries(ALL)) {
      const text = JSON.stringify(tree);
      expect(text.match(/\bM\d{1,2}\b/g), name).toBeNull();
      expect(text.toLowerCase(), name).not.toContain('milestone');
    }
  });

  it('give the licensor’s address exactly as the contract does in the licence texts', () => {
    for (const [name, tree] of Object.entries({ tr, en })) {
      const addresses = JSON.stringify(tree.license).match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g) ?? [];
      expect(addresses.length, name).toBeGreaterThan(0);
      expect(new Set(addresses), name).toEqual(new Set(['arman@khadangan.com']));
    }
  });

  it('name no organisation and no host of an example network', () => {
    // The organisation's name comes from the server (`site_name` of /auth/me); devices are named
    // by their role or vendor ("DHCP kaydı (MikroTik)"), never by a site's host names.
    for (const [name, tree] of Object.entries(SHIPPED)) {
      expect(keys(tree), name).not.toContain('app.site');
      const text = JSON.stringify(tree);
      for (const example of [
        'Örnek A.Ş.',
        'CORE-R1',
        'CORE-S1',
        'FIREWALL-01',
        'NAS-01',
        'ADMIN-PC',
      ])
        expect(text.toLowerCase(), `${name}: ${example}`).not.toContain(example.toLowerCase());
    }
  });
});

/*
 * Page areas (`areas/<area>/{tr,en}.json`, lib/i18n.ts): their texts join the `translation`
 * namespace, so a key of an area must be new there — neither a text of tr.json/en.json nor of
 * another area, nor a group (an object) where the other file has a text or the other way round.
 */
describe('locale areas', () => {
  const areas = Object.keys(LOCALE_AREAS).sort();

  it('include Arama, Canlı Akış and Kaynaklar, each in Turkish and English', () => {
    expect(areas).toEqual(expect.arrayContaining(['live', 'search', 'sources']));
    for (const area of areas)
      expect(Object.keys(LOCALE_AREAS[area] ?? {}).sort(), area).toEqual(['en', 'tr']);
  });

  it.each(areas)('%s: has exactly the same keys in Turkish and English', (area) => {
    const files = LOCALE_AREAS[area] ?? {};
    expect(keys(files.en ?? {}).sort()).toEqual(keys(files.tr ?? {}).sort());
    expect(keys(files.tr ?? {}).length).toBeGreaterThan(0);
  });

  it.each(areas)('%s: adds keys and replaces none of the locale files', (area) => {
    const files = LOCALE_AREAS[area] ?? {};
    expect(collisions(tr, files.tr ?? {})).toEqual([]);
    expect(collisions(en, files.en ?? {})).toEqual([]);
  });

  it('replace none of each other’s keys', () => {
    for (const [index, area] of areas.entries()) {
      for (const other of areas.slice(index + 1)) {
        for (const lang of ['tr', 'en'] as const) {
          const mine = LOCALE_AREAS[area]?.[lang] ?? {};
          const theirs = LOCALE_AREAS[other]?.[lang] ?? {};
          expect(collisions(mine, theirs), `${area} / ${other} (${lang})`).toEqual([]);
        }
      }
    }
  });

  it('reach the pages: every key of an area is in the translation with its own text', () => {
    for (const lang of ['tr', 'en'] as const) {
      const t = i18n.getFixedT(lang);
      for (const area of areas) {
        const texts = LOCALE_AREAS[area]?.[lang] ?? {};
        for (const key of keys(texts)) {
          const own = key.split('.').reduce<unknown>((node, part) => (node as Tree)[part], texts);
          expect(t(key), `${area}/${lang}: ${key}`).toBe(own);
        }
      }
    }
    expect(i18n.getFixedT('tr')('nav.search')).toBe('Arama');
  });
});

describe('addTexts (how an area joins the translation)', () => {
  it('adds missing keys at any depth and keeps every existing text', () => {
    const base: Tree = { nav: { search: 'Arama' }, common: { save: 'Kaydet' } };
    const extra: Tree = {
      nav: { search: 'Başka', live: 'Canlı Akış' },
      common: 'yerine',
      search: { description: 'Açıklama' },
    };
    expect(addTexts(base, extra)).toEqual({
      nav: { search: 'Arama', live: 'Canlı Akış' },
      common: { save: 'Kaydet' },
      search: { description: 'Açıklama' },
    });
    expect(base).toEqual({ nav: { search: 'Arama' }, common: { save: 'Kaydet' } });
    // The test above finds what the merge would have dropped.
    expect(collisions(base, extra)).toEqual(['nav.search', 'common']);
  });
});
