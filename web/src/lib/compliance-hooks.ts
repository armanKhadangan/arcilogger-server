/**
 * TanStack Query hooks of the Uyum Durumu page (`/compliance`, `compliance-types.ts`; Wave 4
 * contract 2.3, 2.8.1).
 *
 * - The ten cards refresh themselves every 30 s in the background (`usePollingQuery`:
 *   `X-Arci-Background`, so an unattended page does not keep the session alive; the server caches
 *   them for 30 s). Viewing is not audited.
 * - "Uyum Durumu Raporu (PDF)" is computed fresh on every download and audited
 *   (`compliance.report_export`); the received bytes are checked against `X-Arci-Sha256`, and a
 *   mismatch is the download's error (`download_mismatch`), shown beside the button.
 * - "Yapılandırma yedeğini aldım" (`settings.manage`) is audited
 *   (`compliance.config_backup_confirm`) and clears the server's cache: the cards are read again.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, apiDownload, ApiError } from './api';
import { usePollingQuery } from './api-hooks';
import type { ComplianceStatus, ConfigBackupOut } from './compliance-types';
import { checkDownload, saveBlob, type HashCheck } from './download';
import i18n from './i18n';

export const COMPLIANCE_POLL_MS = 30_000;

export const complianceKeys = {
  status: ['compliance', 'status'] as const,
};

/** `GET /compliance` (`compliance.read`): the ten cards with their summary. */
export function useCompliance(enabled = true) {
  return usePollingQuery<ComplianceStatus>({
    queryKey: complianceKeys.status,
    path: '/compliance',
    intervalMs: COMPLIANCE_POLL_MS,
    enabled,
  });
}

/** A saved report: its file name, the hash the server announced and the browser's check. */
export interface ComplianceReportFile {
  filename: string;
  sha256: string | null;
  check: HashCheck;
}

/**
 * `GET /compliance/report.pdf` (`compliance.read`), offered as a file
 * (`ARCILOGGER-uyum-durumu-<YYYYMMDD-HHMM>.pdf`); refusals come back as the mutation's error. So
 * does a file whose bytes differ from the `X-Arci-Sha256` the server recorded in the audit log
 * (`download_mismatch`, with the texts of `HashCheckBadge`: do not use it, download it again), so
 * the page shows it beside the button with its code and a wrong file never passes silently.
 */
export function useComplianceReport() {
  return useMutation({
    mutationFn: async (): Promise<ComplianceReportFile> => {
      const file = await apiDownload('/compliance/report.pdf', {
        fallbackName: 'ARCILOGGER-uyum-durumu.pdf',
      });
      saveBlob(file.blob, file.filename);
      const sha256 = file.headers.get('X-Arci-Sha256');
      const check = await checkDownload(file.blob, sha256);
      if (check.state === 'mismatch')
        throw new ApiError({
          status: 200,
          code: 'download_mismatch',
          messageTr: i18n.getFixedT('tr')('download.mismatch'),
          messageEn: i18n.getFixedT('en')('download.mismatch'),
        });
      return { filename: file.filename, sha256, check };
    },
  });
}

/**
 * `POST /compliance/config-backup` (`settings.manage`; 409 `config_backup_unreadable` when the
 * configuration file cannot be read). Pending until the cards are read again, so card 10 shows
 * the new confirmation when the button is free again.
 */
export function useConfirmConfigBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ConfigBackupOut>('/compliance/config-backup'),
    onSuccess: () => client.invalidateQueries({ queryKey: complianceKeys.status }),
  });
}
