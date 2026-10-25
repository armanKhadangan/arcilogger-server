import en from './en.json';
import tr from './tr.json';

type Tree = { [key: string]: string | Tree };

function keys(tree: Tree, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === 'string' ? [`${prefix}${key}`] : keys(value, `${prefix}${key}.`),
  );
}

describe('locale files', () => {
  it('have exactly the same keys in Turkish and English', () => {
    expect(keys(en as Tree).sort()).toEqual(keys(tr as Tree).sort());
  });

  it('have no empty strings', () => {
    for (const [name, tree] of Object.entries({ tr, en })) {
      const empty = keys(tree as Tree).filter((key) => {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Tree)[part], tree);
        return typeof value !== 'string' || value.trim() === '';
      });
      expect(empty, name).toEqual([]);
    }
  });

  it('claim no legal compliance and no copy protection (licensing contract 3.5, 8.3)', () => {
    for (const [name, tree] of Object.entries({ tr, en })) {
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
    for (const [name, tree] of Object.entries({ tr, en })) {
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
    for (const [name, tree] of Object.entries({ tr, en })) {
      expect(keys(tree as Tree), name).not.toContain('app.site');
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
