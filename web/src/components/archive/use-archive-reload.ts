/**
 * The requests of an archive reload (Wave 5 contract §5, G9, G10): `POST /archive-reloads` asks
 * for the days, `GET /archive-reloads/{id}` follows the reload every 2 s until it is ready, failed
 * or expired. The first read of a reload counts as the user's request (they just asked for it);
 * every later poll is sent with `X-Arci-Background: 1` (`usePollingQuery`), so following a reload
 * does not keep the session alive. A hidden tab does not poll. A 5xx or an unreachable server is
 * polled through; a 403 or 404 stops the poll until the user tries again (#134).
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, isApiError } from '@/lib/api';
import { usePollingQuery } from '@/lib/api-hooks';
import type { ArchiveReload, ArchiveReloadRequest } from '@/lib/archive-reload-types';

import { ARCHIVE_RELOAD_POLL_MS, isFinished } from './archive-model';

export const ARCHIVE_RELOAD_KEY = (id: number) => ['archive-reloads', 'one', id] as const;

/**
 * A poll error that the next poll would only repeat: 403 or 404, the reload is not (or no longer)
 * the user's — for example after the session changed hands. A 5xx or a network error may pass.
 */
export function stopsPolling(error: unknown): boolean {
  return isApiError(error) && (error.status === 403 || error.status === 404);
}

/**
 * `GET /archive-reloads/{id}` every 2 s while the reload is queued or loading. After a 403 or 404
 * the poll stops (`stopped`); `refresh()` asks once more and, when that answers, polling resumes
 * until the reload is finished.
 */
export function useArchiveReload(id: number | null) {
  const client = useQueryClient();
  const key = ARCHIVE_RELOAD_KEY(id ?? 0);
  const query = usePollingQuery<ArchiveReload>({
    queryKey: key,
    path: `/archive-reloads/${String(id)}`,
    enabled: id !== null,
    // The interval sees only the data (the last good answer): the error is read from the cache,
    // which already holds it when TanStack computes the next interval after a failed poll.
    intervalMs: (reload) =>
      stopsPolling(client.getQueryState(key)?.error) || (reload && isFinished(reload.status))
        ? false
        : ARCHIVE_RELOAD_POLL_MS,
  });
  return { ...query, stopped: query.isError && stopsPolling(query.error) };
}

/**
 * `POST /archive-reloads`: 202 with the new reload — or with the user's own active one when it
 * already covers the days.
 */
export function useRequestArchiveReload() {
  return useMutation({
    mutationFn: (body: ArchiveReloadRequest) => api.post<ArchiveReload>('/archive-reloads', body),
  });
}
