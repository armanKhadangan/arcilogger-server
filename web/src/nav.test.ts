import { NAV_ITEMS, UI_KIT_ITEM, visibleNavItems } from '@/nav';
import { hasAny, isRole, PERMISSIONS, roleHas, roleLabel, ROLES } from '@/lib/permissions';

function menuFor(role: string): string[] {
  const permissions = new Set(ROLES.find((item) => item.id === role)?.permissions ?? []);
  return visibleNavItems(permissions).map((item) => item.key);
}

describe('permission-filtered menu (docs/MVP-CONTRACTS.md section 6)', () => {
  it('shows every entry to the administrator', () => {
    expect(menuFor('admin')).toEqual(NAV_ITEMS.map((item) => item.key));
  });

  it('auditor: panel, search, lookup, usage (anonymous), sealing, evidence, audit', () => {
    expect(menuFor('auditor')).toEqual([
      'dashboard',
      'live',
      'search',
      'lookup',
      'usage',
      'sealing',
      'evidence',
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

describe('generated role contract', () => {
  it('matches the spec 13 table in the cells the owner decided', () => {
    expect(roleHas('auditor', 'evidence.create')).toBe(false); // owner decision
    expect(roleHas('auditor', 'search.export')).toBe(false); // "✔ (read)"
    expect(roleHas('hr', 'usage.person')).toBe(true);
    expect(roleHas('viewer', 'usage.person')).toBe(false);
    expect(roleHas('admin', 'users.manage')).toBe(true);
    expect(roleHas('nobody', 'audit.read')).toBe(false);
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
