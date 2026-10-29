import { BUILT_PAGES, NAV_ITEMS, UI_KIT_ITEM, visibleNavItems } from '@/nav';
import { hasAny, isRole, PERMISSIONS, roleHas, roleLabel, ROLES } from '@/lib/permissions';

function menuFor(role: string): string[] {
  const permissions = new Set(ROLES.find((item) => item.id === role)?.permissions ?? []);
  return visibleNavItems(permissions).map((item) => item.key);
}

describe('permission-filtered menu (docs/MVP-CONTRACTS.md section 6)', () => {
  it('shows every entry to the administrator', () => {
    expect(menuFor('admin')).toEqual(NAV_ITEMS.map((item) => item.key));
  });

  it('auditor: panel, live tail, search, lookup, usage (anonymous), sealing, evidence, compliance status, audit', () => {
    expect(menuFor('auditor')).toEqual([
      'dashboard',
      'live',
      'search',
      'lookup',
      'usage',
      'sealing',
      'evidence',
      'compliance',
      'audit',
    ]);
  });

  it('hr: panel (usage) and usage reports only', () => {
    expect(menuFor('hr')).toEqual(['dashboard', 'usage']);
  });

  it('viewer: panel and anonymous usage only', () => {
    expect(menuFor('viewer')).toEqual(['dashboard', 'usage']);
  });

  it('shows nothing without permissions and hides the UI kit from non-admins', () => {
    expect(visibleNavItems(new Set())).toEqual([]);
    // Built in for the unit tests (`__UI_KIT__`, vite.config.ts); never in the shipped bundle.
    if (!UI_KIT_ITEM) throw new Error('the unit tests build the UI kit in');
    const auditor = new Set(ROLES.find((item) => item.id === 'auditor')?.permissions);
    expect(hasAny(auditor, UI_KIT_ITEM.permissions)).toBe(false);
  });

  it('only uses permissions the server knows', () => {
    const known = new Set(PERMISSIONS.map((permission) => permission.id));
    const used = [...NAV_ITEMS, ...(UI_KIT_ITEM ? [UI_KIT_ITEM] : [])].flatMap(
      (item) => item.permissions,
    );
    expect(used.filter((permission) => !known.has(permission))).toEqual([]);
  });
});

describe('pages with content', () => {
  it('leaves only Uyum Durumu and Sistem as placeholders', () => {
    expect(NAV_ITEMS.filter((item) => !BUILT_PAGES.has(item.key)).map((item) => item.key)).toEqual([
      'compliance',
      'system',
    ]);
    const keys = new Set(NAV_ITEMS.map((item) => item.key));
    expect([...BUILT_PAGES].filter((key) => !keys.has(key))).toEqual([]);
  });

  it('keeps Kaynaklar, Canlı Akış and Arama in 5651 Temel: no licence module locks them', () => {
    const pages = NAV_ITEMS.filter((item) => ['sources', 'live', 'search'].includes(item.key));
    expect(pages.map((item) => [item.key, item.path, item.module])).toEqual([
      ['sources', '/sources', undefined],
      ['live', '/live', undefined],
      ['search', '/search', undefined],
    ]);
  });
});

describe('generated role contract', () => {
  it('matches the spec 13 table in the cells the owner decided', () => {
    expect(roleHas('auditor', 'evidence.create')).toBe(false); // owner decision
    expect(roleHas('auditor', 'search.export')).toBe(false); // "✔ (read)"
    expect(roleHas('hr', 'usage.person')).toBe(true);
    expect(roleHas('viewer', 'usage.person')).toBe(false);
    expect(roleHas('admin', 'users.manage')).toBe(true);
    expect(roleHas('nobody', 'audit.read')).toBe(false);
  });

  it('gives Uyum Durumu to the auditor, Sistem and the settings to the administrator only', () => {
    // Owner decision 2026-10-28 (Wave 4 contract D15, Q1, Q2).
    expect(roleHas('auditor', 'compliance.read')).toBe(true);
    expect(roleHas('auditor', 'system.read')).toBe(false);
    expect(roleHas('auditor', 'settings.manage')).toBe(false);
    for (const role of ['hr', 'viewer']) {
      for (const permission of ['compliance.read', 'system.read', 'settings.manage']) {
        expect(roleHas(role, permission), `${role} ${permission}`).toBe(false);
      }
    }
  });

  it('labels roles in both languages and knows the role ids', () => {
    expect(roleLabel('hr', 'tr')).toBe('İK Raporları');
    expect(roleLabel('hr', 'en')).toBe('HR reports');
    expect(roleLabel('ghost', 'tr')).toBe('ghost');
    expect(isRole('auditor')).toBe(true);
    expect(isRole('root')).toBe(false);
  });

  it('treats an empty requirement as allowed', () => {
    expect(hasAny(new Set(), [])).toBe(true);
    expect(hasAny(new Set(['a']), ['b', 'a'])).toBe(true);
  });
});
