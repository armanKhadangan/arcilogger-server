import type { RouteObject } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { ModuleGate, PermissionGate, RootLayout } from '@/components/layout/route-frames';
import { RequireAuth } from '@/lib/auth';
import { LICENSE_ADMIN_PERMISSION, LICENSE_PAGE } from '@/lib/license';
import { BUILT_PAGES, NAV_ITEMS, UI_KIT_ITEM, type NavItem } from '@/nav';
import { LoginPage } from '@/pages/login/login-page';
import { NotFoundPage } from '@/pages/not-found-page';
import { PlaceholderPage } from '@/pages/placeholder-page';
import { SetupPage } from '@/pages/setup/setup-page';

/*
 * Public: /login, /setup. Everything else is inside RequireAuth (complete sign-in) and, per page,
 * a PermissionGate with the menu entry's permissions; the API checks every request again. Pages
 * of a licence module (Kullanım, Raporlar) also sit in a ModuleGate: while the module is off the
 * locked page is shown and the page's requests are never made.
 */

function placeholder(item: NavItem): RouteObject {
  const page = { element: <PlaceholderPage item={item} /> };
  return {
    element: <PermissionGate anyOf={item.permissions} />,
    children: [
      item.path === '/' ? { index: true, ...page } : { path: item.path.slice(1), ...page },
    ],
  };
}

const navItem = (key: string): NavItem => {
  const found = NAV_ITEMS.find((item) => item.key === key);
  if (!found) throw new Error(`unknown menu entry: ${key}`);
  return found;
};

export const routes: RouteObject[] = [
  {
    element: <RootLayout />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/setup', element: <SetupPage /> },
      {
        path: '/',
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          ...NAV_ITEMS.filter((item) => !BUILT_PAGES.has(item.key)).map(placeholder),
          {
            element: <PermissionGate anyOf={navItem('dashboard').permissions} />,
            children: [
              {
                index: true,
                lazy: async () => ({
                  Component: (await import('@/pages/dashboard/dashboard-page')).DashboardPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('security').permissions} />,
            children: [
              {
                path: 'security',
                lazy: async () => ({
                  Component: (await import('@/pages/security/security-page')).SecurityPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('reports').permissions} />,
            children: [
              {
                element: <ModuleGate module="usage_reports" />,
                children: [
                  {
                    path: 'reports',
                    lazy: async () => ({
                      Component: (await import('@/pages/reports/reports-page')).ReportsPage,
                    }),
                  },
                ],
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('audit').permissions} />,
            children: [
              {
                path: 'audit',
                // Route-level code splitting (spec 12.1): the page loads when it is opened.
                lazy: async () => ({
                  Component: (await import('@/pages/audit/audit-page')).AuditPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('lookup').permissions} />,
            children: ['lookup', 'lookup/:lookupId'].map((path) => ({
              path,
              lazy: async () => ({
                Component: (await import('@/pages/lookup/lookup-page')).LookupPage,
              }),
            })),
          },
          {
            element: <PermissionGate anyOf={navItem('evidence').permissions} />,
            children: [
              {
                path: 'evidence',
                lazy: async () => ({
                  Component: (await import('@/pages/evidence/evidence-page')).EvidencePage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('sealing').permissions} />,
            children: [
              {
                path: 'sealing',
                lazy: async () => ({
                  Component: (await import('@/pages/sealing/sealing-page')).SealingPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('devices').permissions} />,
            children: [
              {
                path: 'devices',
                lazy: async () => ({
                  Component: (await import('@/pages/devices/devices-page')).DevicesPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('usage').permissions} />,
            children: [
              {
                element: <ModuleGate module="usage_analytics" />,
                children: [
                  {
                    path: 'usage',
                    lazy: async () => ({
                      Component: (await import('@/pages/usage/usage-page')).UsagePage,
                    }),
                  },
                ],
              },
            ],
          },
          {
            element: <PermissionGate anyOf={navItem('settings').permissions} />,
            children: [
              {
                path: 'settings',
                lazy: async () => ({
                  Component: (await import('@/pages/settings/settings-page')).SettingsPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={['users.manage']} />,
            children: [
              {
                path: 'settings/users',
                lazy: async () => ({
                  Component: (await import('@/pages/settings/users-page')).UsersPage,
                }),
              },
            ],
          },
          {
            element: <PermissionGate anyOf={[LICENSE_ADMIN_PERMISSION]} />,
            children: [
              {
                path: LICENSE_PAGE.slice(1),
                lazy: async () => ({
                  Component: (await import('@/pages/settings/license-page')).LicensePage,
                }),
              },
            ],
          },
          // Developer page: not in the bundle the installer ships (`UI_KIT_ITEM` is null there).
          ...(UI_KIT_ITEM
            ? [
                {
                  element: <PermissionGate anyOf={UI_KIT_ITEM.permissions} />,
                  children: [
                    {
                      path: 'ui-kit',
                      // Charts and demo data load only when the page is opened.
                      lazy: async () => ({
                        Component: (await import('@/pages/ui-kit/ui-kit-page')).UiKitPage,
                      }),
                    },
                  ],
                },
              ]
            : []),
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
