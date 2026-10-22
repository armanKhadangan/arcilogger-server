import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { apiDownload, errorMessage } from '@/lib/api';
import { queryKeys } from '@/lib/api-hooks';
import type { EvidenceOut } from '@/lib/api-types';
import { checkDownload, reprDigestSha256, saveBlob, type HashCheck } from '@/lib/download';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

export interface DownloadState {
  id: number;
  busy: boolean;
  problem?: string;
  check?: HashCheck;
  filename?: string;
  /** Bytes received so far while `busy` (undefined before the first chunk). */
  received?: number;
  /** Size of the whole file (`Content-Length`, else the package's recorded size). */
  total?: number | null;
}

/** Shortest time between two progress updates of the page (ms). */
const PROGRESS_INTERVAL = 250;

/**
 * Download an evidence package: `GET /evidence/{id}/download` needs a re-authentication within
 * 5 minutes (403 `reauth_required` → password dialog, then the download again) and the accepted
 * KVKK notice. Refusals (410 expired, 409 not ready …) come back as the server's message. The
 * received bytes are checked against the `Repr-Digest` header (RFC 9530). The bytes received so
 * far are reported while the download runs (the browser receives the whole package before it
 * offers it as a file).
 */
export function usePackageDownload() {
  const { withReauth } = useReauth();
  const client = useQueryClient();
  const [state, setState] = useState<DownloadState | null>(null);

  const download = useCallback(
    async (item: Pick<EvidenceOut, 'id' | 'file' | 'sha256' | 'size'>) => {
      setState({ id: item.id, busy: true });
      let shown = 0;
      const onProgress = (received: number, total: number | null) => {
        const now = Date.now();
        if (now - shown < PROGRESS_INTERVAL && received !== total) return;
        shown = now;
        setState((current) =>
          current?.id === item.id && current.busy
            ? { ...current, received, total: total ?? item.size }
            : current,
        );
      };
      try {
        const file = await withReauth(() =>
          apiDownload(`/evidence/${item.id}/download`, {
            fallbackName: item.file ?? `ARCILOGGER-kanit-${item.id}.zip`,
            onProgress,
          }),
        );
        saveBlob(file.blob, file.filename);
        const expected = reprDigestSha256(file.headers.get('Repr-Digest')) ?? item.sha256;
        setState({
          id: item.id,
          busy: false,
          filename: file.filename,
          check: await checkDownload(file.blob, expected),
        });
      } catch (error) {
        setState(
          isReauthCancelled(error)
            ? null
            : { id: item.id, busy: false, problem: errorMessage(error) },
        );
      } finally {
        // The download count, or the state (an expired package answers 410), has changed.
        void client.invalidateQueries({ queryKey: queryKeys.evidenceAll });
      }
    },
    [client, withReauth],
  );

  return { state, download, reset: () => setState(null) };
}
