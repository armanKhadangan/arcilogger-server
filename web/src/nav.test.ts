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

  // Canlı Durum (W5 §6 M3, N6): every role that sees the Panel or usage figures sees the page;
  // each of its parts is checked again on the page and by the API.
  it('auditor: panel, live tail, Canlı Durum, search, lookup, usage (anonymous), sealing, evidence, compliance status, audit', () => {
    expect(menuFor('auditor')).toEqual([
      'dashboard',
      'live',
      'activity',
      'search',
      'lookup',
      'usage',
      'sealing',
      'evidence',
      'compliance',
      'audit',
    ]);
  });

  it('hr: panel (usage), Canlı Durum and usage only', () => {
    expect(menuFor('hr')).toEqual(['dashboard', 'activity', 'usage']);
  });

  it('viewer: panel, Canlı Durum and anonymous usage only', () => {
    expect(menuFor('viewer')).toEqual(['dashboard', 'activity', 'usage']);
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
  it('no menu entry is a placeholder', () => {
    expect(NAV_ITEMS.filter((item) => !BUILT_PAGES.has(item.key)).map((item) => item.key)).toEqual(
      [],
    );
    const keys = new Set(NAV_ITEMS.map((item) => item.key));
    expect([...BUILT_PAGES].filter((key) => !keys.has(key))).toEqual([]);
  });

  it('keeps Kaynaklar, Canlı Akış, Canlı Durum and Arama in 5651 Temel: no licence module locks them', () => {
    // Canlı Durum's "Şu an" table checks `usage_analytics` itself; the line speed is base (QB1).
    const pages = NAV_ITEMS.filter((item) =>
      ['sources', 'live', 'activity', 'search'].includes(item.key),
    );
    expect(pages.map((item) => [item.key, item.path, item.module])).toEqual([
      ['sources', '/sources', undefined],
      ['live', '/live', undefined],
      ['activity', '/activity', undefined],
      ['search', '/search', undefined],
    ]);
  });

  it('keeps Uyum Durumu and Sistem in 5651 Temel: their menu entries name no licence module', () => {
    // Wave 4 contract 0.1 item 2: no `require_module`, no `ModuleGate`. The routes are opened
    // under an expired trial in `compliance-page.test.tsx` and `system-page.test.tsx`.
    const pages = NAV_ITEMS.filter((item) => ['compliance', 'system'].includes(item.key));
    expect(pages.map((item) => [item.key, item.path, item.module])).toEqual([
      ['compliance', '/compliance', undefined],
      ['system', '/system', undefined],
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
