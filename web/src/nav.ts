import {
  Activity,
  ClipboardCheck,
  Clock3,
  FileText,
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
  /** Milestone in which the page gets its real content. */
  milestone: string;
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
}

/** Main menu, in the order of the build specification (section 12.2). */
export const NAV_ITEMS: readonly NavItem[] = [
  {
    key: 'dashboard',
    path: '/',
    icon: LayoutDashboard,
    milestone: 'M2',
    // hr: "Panel ✔ (usage only)"
    permissions: ['dashboard.view', 'dashboard.usage'],
  },
  {
    key: 'sources',
    path: '/sources',
    icon: Server,
    milestone: 'M2',
    permissions: ['sources.read'],
  },
  { key: 'live', path: '/live', icon: Activity, milestone: 'M2', permissions: ['search.read'] },
  { key: 'search', path: '/search', icon: Search, milestone: 'M2', permissions: ['search.read'] },
  { key: 'lookup', path: '/lookup', icon: Scale, milestone: 'M3', permissions: ['lookup.run'] },
  {
    key: 'usage',
    path: '/usage',
    icon: Clock3,
    milestone: 'M4',
    permissions: ['usage.person', 'usage.anonymous'],
    module: 'usage_analytics',
  },
  {
    key: 'security',
    path: '/security',
    icon: ShieldAlert,
    milestone: 'M5',
    permissions: ['alerts.read'],
  },
  { key: 'sealing', path: '/sealing', icon: Stamp, milestone: 'M2', permissions: ['seal.read'] },
  {
    key: 'evidence',
    path: '/evidence',
    icon: Package,
    milestone: 'M3',
    permissions: ['evidence.read'],
  },
  {
    key: 'devices',
    path: '/devices',
    icon: MonitorSmartphone,
    milestone: 'M3',
    permissions: ['devices.read'],
  },
  {
    key: 'reports',
    path: '/reports',
    icon: FileText,
    milestone: 'M4',
    permissions: ['reports.manage'],
    module: 'usage_reports',
  },
  {
    key: 'compliance',
    path: '/compliance',
    icon: ClipboardCheck,
    milestone: 'M6',
    permissions: ['compliance.read'],
  },
  { key: 'audit', path: '/audit', icon: ScrollText, milestone: 'M6', permissions: ['audit.read'] },
  {
    key: 'settings',
    path: '/settings',
    icon: Settings,
    milestone: 'M6',
    permissions: ['settings.manage', 'users.manage'],
  },
  {
    key: 'system',
    path: '/system',
    icon: HeartPulse,
    milestone: 'M6',
    permissions: ['system.read'],
  },
];

/**
 * Menu entries whose page already has content; the others show their milestone. Features that
 * lead to one of those pages (e.g. the top bar's global search → "Arama") stay hidden until the
 * page is in this set.
 */
export const BUILT_PAGES: ReadonlySet<string> = new Set([
  'dashboard',
  'security',
  'reports',
  'audit',
  'settings',
  'lookup',
  'sealing',
  'evidence',
  'devices',
  'usage',
]);

/** Developer page with every component (administrators only). */
export const UI_KIT_ITEM: NavItem = {
  key: 'uiKit',
  path: '/ui-kit',
  icon: Palette,
  milestone: 'M0',
  permissions: ['settings.manage'],
};

/** The menu entries a user with `granted` permissions sees. */
export function visibleNavItems(
  granted: ReadonlySet<string>,
  items: readonly NavItem[] = NAV_ITEMS,
): NavItem[] {
  return items.filter((item) => hasAny(granted, item.permissions));
}
