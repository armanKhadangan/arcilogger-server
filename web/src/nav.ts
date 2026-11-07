import {
  Activity,
  ClipboardCheck,
  Clock3,
  Database,
  FileText,
  Gauge,
  HeartPulse,
  LayoutDashboard,
  MonitorSmartphone,
  Package,
  Palette,
  Scale,
  ScrollText,
  Search,
  Server,
  Settings,
  ShieldAlert,
  Stamp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { ModuleKey } from '@/lib/license';
import { hasAny } from '@/lib/permissions';

export interface NavItem {
  /** i18n key under "nav." */
  key: string;
  path: string;
  icon: LucideIcon;
  /**
   * The entry (and its page) is shown when the user holds ANY of these permissions
   * (docs/MVP-CONTRACTS.md section 6). The API checks again on every request.
   */
  permissions: readonly string[];
  /**
   * The licence module the page belongs to (`arcilogger_core.license.modules`). While the module
   * is off the entry stays visible with a lock and the page shows its locked page instead of
   * asking the API (licensing contract 8.4); the API refuses the module's routes anyway.
   */
  module?: ModuleKey;
  /** The menu group the entry is listed under (`NAV_GROUPS`; the order of the menu is kept). */
  group: NavGroup;
}

/**
 * The menu's groups, in order: each is a labelled list in the sidebar ("nav.groups.*") and a
 * heading in the "Sayfaya git" palette. The groups follow the menu's order (spec 12.2), so a
 * group never changes where an entry sits.
 */
export const NAV_GROUPS = ['overview', 'investigation', 'evidence', 'oversight', 'admin'] as const;
export type NavGroup = (typeof NAV_GROUPS)[number];

/** Main menu, in the order of the build specification (section 12.2). */
export const NAV_ITEMS: readonly NavItem[] = [
  {
    key: 'dashboard',
    group: 'overview',
    path: '/',
    icon: LayoutDashboard,
    // hr: "Panel ✔ (usage only)"
    permissions: ['dashboard.view', 'dashboard.usage'],
  },
  {
    key: 'sources',
    group: 'overview',
    path: '/sources',
    icon: Server,
    permissions: ['sources.read'],
  },
  { key: 'live', path: '/live', icon: Activity, permissions: ['search.read'], group: 'overview' },
  {
    // Canlı Durum (W5 §6 M1): the lines need `dashboard.view` or `system.read` and are in the
    // base product; "Şu an" needs a usage permission and the module, checked on the page.
    key: 'activity',
    group: 'overview',
    path: '/activity',
    icon: Gauge,
    permissions: ['dashboard.view', 'system.read', 'usage.person', 'usage.anonymous'],
  },
  {
    key: 'search',
    path: '/search',
    icon: Search,
    permissions: ['search.read'],
    group: 'investigation',
  },
  {
    key: 'lookup',
    path: '/lookup',
    icon: Scale,
    permissions: ['lookup.run'],
    group: 'investigation',
  },
  {
    key: 'usage',
    group: 'investigation',
    path: '/usage',
    icon: Clock3,
    permissions: ['usage.person', 'usage.anonymous'],
    module: 'usage_analytics',
  },
  {
    key: 'security',
    group: 'investigation',
    path: '/security',
    icon: ShieldAlert,
    permissions: ['alerts.read'],
  },
  { key: 'sealing', path: '/sealing', icon: Stamp, permissions: ['seal.read'], group: 'evidence' },
  {
    key: 'evidence',
    group: 'evidence',
    path: '/evidence',
    icon: Package,
    permissions: ['evidence.read'],
  },
  {
    key: 'devices',
    group: 'evidence',
    path: '/devices',
    icon: MonitorSmartphone,
    permissions: ['devices.read'],
  },
  {
    key: 'reports',
    group: 'oversight',
    path: '/reports',
    icon: FileText,
    permissions: ['reports.manage'],
    module: 'usage_reports',
  },
  {
    key: 'compliance',
    group: 'oversight',
    path: '/compliance',
    icon: ClipboardCheck,
    permissions: ['compliance.read'],
  },
  {
    key: 'audit',
    path: '/audit',
    icon: ScrollText,
    permissions: ['audit.read'],
    group: 'oversight',
  },
  {
    key: 'settings',
    group: 'admin',
    path: '/settings',
    icon: Settings,
    permissions: ['settings.manage', 'users.manage'],
  },
  {
    key: 'system',
    group: 'admin',
    path: '/system',
    icon: HeartPulse,
    permissions: ['system.read'],
  },
  {
    key: 'database',
    group: 'admin',
    path: '/database',
    icon: Database,
    permissions: ['system.read'],
  },
];

/**
 * Menu entries whose page has content: since Wave 4 every entry (no page is a placeholder any
 * more; `nav.test.ts` keeps it so). Features that lead to a page (e.g. the top bar's global
 * search → "Arama") check this set, and README's "Yer tutucu sayfalar" is tied to it.
 */
export const BUILT_PAGES: ReadonlySet<string> = new Set([
  'dashboard',
  'sources',
  'live',
  'activity',
  'search',
  'security',
  'reports',
  'audit',
  'settings',
  'lookup',
  'sealing',
  'evidence',
  'devices',
  'usage',
  'compliance',
  'system',
  'database',
]);

/**
 * Developer page with every component (administrators only). `null` in the bundle the installer
 * ships (`__UI_KIT__`, vite.config.ts), which has neither the page nor this entry.
 */
export const UI_KIT_ITEM: NavItem | null = __UI_KIT__
  ? {
      key: 'uiKit',
      group: 'admin',
      path: '/ui-kit',
      icon: Palette,
      permissions: ['settings.manage'],
    }
  : null;

/** The menu entries a user with `granted` permissions sees. */
export function visibleNavItems(
  granted: ReadonlySet<string>,
  items: readonly NavItem[] = NAV_ITEMS,
): NavItem[] {
  return items.filter((item) => hasAny(granted, item.permissions));
}
