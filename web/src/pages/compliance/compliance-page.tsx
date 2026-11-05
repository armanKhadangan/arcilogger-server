import { isApiError } from '@/lib/api';
import { useCompliance, useComplianceReport, useConfirmConfigBackup } from '@/lib/compliance-hooks';

import { ComplianceView } from './compliance-view';

/**
 * Uyum Durumu (`/compliance`, `compliance.read`; Wave 4 contract 2.8.1): the hooks of
 * `compliance-hooks.ts` wired to `ComplianceView`. The cards refresh themselves every 30 s in the
 * background; "Yenile" and "Tekrar dene" are the user's (`refresh()`). The report's and the
 * confirmation's failures are shown where they happened, with their code: every failure of these
 * requests, an unreachable server too, is an `ApiError`.
 */
export function CompliancePage() {
  const status = useCompliance();
  const report = useComplianceReport();
  const confirm = useConfirmConfigBackup();
  return (
    <ComplianceView
      status={status.data}
      error={status.error}
      loading={status.isPending}
      updatedAt={status.dataUpdatedAt}
      onRefresh={() => void status.refresh()}
      onExport={() => report.mutate()}
      exporting={report.isPending}
      exportError={isApiError(report.error) ? report.error : null}
      onConfirmBackup={() => confirm.mutateAsync()}
      confirming={confirm.isPending}
      confirmError={isApiError(confirm.error) ? confirm.error : null}
    />
  );
}
