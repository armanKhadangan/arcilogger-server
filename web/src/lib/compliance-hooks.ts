/**
 * TanStack Query hooks of the Uyum Durumu page (`/compliance`, `compliance-types.ts`; Wave 4
 * contract 2.3, 2.8.1).
 *
 * - The ten cards refresh themselves every 30 s in the background (`usePollingQuery`:
 *   `X-Arci-Background`, so an unattended page does not keep the session alive; the server caches
 *   them for 30 s). Viewing is not audited.
 * - "Uyum Durumu Raporu (PDF)" is computed fresh on every download and audited
 *   (`compliance.report_export`); the received bytes are checked against `X-Arci-Sha256`.
 * - "Yapılandırma yedeğini aldım" (`settings.manage`) is audited
 *   (`compliance.config_backup_confirm`) and clears the server's cache: the cards are read again.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, apiDownload } from './api';
import { usePollingQuery } from './api-hooks';
import type { ComplianceStatus, ConfigBackupOut } from './compliance-types';
import { checkDownload, saveBlob, type HashCheck } from './download';

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
 * (`ARCILOGGER-uyum-durumu-<YYYYMMDD-HHMM>.pdf`); refusals come back as the mutation's error.
 */
export function useComplianceReport() {
  return useMutation({
    mutationFn: async (): Promise<ComplianceReportFile> => {
      const file = await apiDownload('/compliance/report.pdf', {
        fallbackName: 'ARCILOGGER-uyum-durumu.pdf',
      });
      saveBlob(file.blob, file.filename);
      const sha256 = file.headers.get('X-Arci-Sha256');
      return { filename: file.filename, sha256, check: await checkDownload(file.blob, sha256) };
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
