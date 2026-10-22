import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PrivacyNoticeDialog } from '@/components/auth/privacy-notice-dialog';
import { LicenseBanner } from '@/components/license/license-banner';
import { ReauthProvider } from '@/lib/reauth';
import { readPreference, writePreference } from '@/lib/storage';

import { Sidebar } from './sidebar';
import { Topbar } from './topbar';

const COLLAPSED_KEY = 'arci.sidebar.collapsed';

function initialCollapsed(): boolean {
  const stored = readPreference(COLLAPSED_KEY);
  if (stored !== null) return stored === '1';
  return window.innerWidth < 1024; // tablets start with the icon rail
}

/**
 * Sidebar + top bar + licence banners + routed page, for signed-in users (inside `RequireAuth`).
 * Also hosts the re-authentication dialog (`useReauth`) and the KVKK notice, which stays open
 * until accepted.
 */
export function AppShell() {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(initialCollapsed);

  return (
    <ReauthProvider>
      <a
        href="#content"
        className="sr-only z-50 rounded-md bg-surface px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t('nav.skipToContent')}
      </a>
      <div className="flex min-h-screen bg-background">
        <Sidebar
          collapsed={collapsed}
          onToggle={() => {
            setCollapsed((value) => {
              writePreference(COLLAPSED_KEY, value ? '0' : '1');
              return !value;
            });
          }}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main
            id="content"
            tabIndex={-1}
            className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 outline-none md:px-6"
          >
            <LicenseBanner />
            <Outlet />
          </main>
        </div>
      </div>
      <PrivacyNoticeDialog />
    </ReauthProvider>
  );
}
