import { Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ModuleLockedPage } from '@/components/license/module-locked';
import { Skeleton } from '@/components/ui/skeleton';
import { useLicense } from '@/lib/api-hooks';
import { ApiEventsBridge, RequirePermission } from '@/lib/auth';
import { moduleEnabled, type ModuleKey } from '@/lib/license';

/** Every route: the global API reactions (401 → sign-in …) need the router, so they live here. */
export function RootLayout() {
  return (
    <>
      <ApiEventsBridge />
      <Outlet />
    </>
  );
}

/** Child routes rendered only for holders of any of `anyOf` (else "no permission"). */
export function PermissionGate({ anyOf }: { anyOf: readonly string[] }) {
  return (
    <RequirePermission anyOf={anyOf}>
      <Outlet />
    </RequirePermission>
  );
}

/** A page's outline while what it needs is still loading. */
export function PageSkeleton() {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-label={t('common.loading')}
      className="flex flex-col gap-4"
      data-testid="page-skeleton"
    >
      <Skeleton className="h-8 w-64 max-w-full" />
      <Skeleton className="h-4 w-96 max-w-full" />
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

/**
 * Child routes of a licence module (inside the route's `PermissionGate`; licensing contract 8.4):
 * the page's skeleton until the licence is known, the locked page while the module is off — the
 * page itself, and so every request it would make, is never rendered then — and the page when
 * the module is on or the licence could not be read (the API enforces anyway).
 */
export function ModuleGate({ module }: { module: ModuleKey }) {
  const license = useLicense();
  if (license.data) {
    return moduleEnabled(license.data, module) ? (
      <Outlet />
    ) : (
      <ModuleLockedPage moduleKey={module} status={license.data} />
    );
  }
  return license.isError ? <Outlet /> : <PageSkeleton />;
}
