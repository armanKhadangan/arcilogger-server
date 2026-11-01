import { isApiError } from '@/lib/api';
import { useLicense } from '@/lib/api-hooks';
import { useSystemHealth, useSystemSummary } from '@/lib/system-hooks';

import { SystemView } from './system-view';

/**
 * Sistem (`/system`, `system.read`; Wave 4 contract 2.8.2): `GET /system` and `GET /system/health`
 * (every 30 s in the background, `system-hooks.ts`) and the licence maintenance of `GET /license`
 * wired to `SystemView`. The summary's failure is the page's error; the health's shows in the Saat
 * block only, so one failing request never hides the other's blocks. "Yenile" and "Tekrar dene"
 * ask both again as the user (`refresh()`).
 */
export function SystemPage() {
  const summary = useSystemSummary();
  const health = useSystemHealth();
  const license = useLicense();
  return (
    <SystemView
      summary={summary.data}
      health={health.data}
      license={license.data}
      error={summary.error}
      healthError={isApiError(health.error) ? health.error : null}
      loading={summary.isPending || health.isPending}
      updatedAt={summary.dataUpdatedAt}
      onRefresh={() => {
        void summary.refresh();
        void health.refresh();
      }}
    />
  );
}
