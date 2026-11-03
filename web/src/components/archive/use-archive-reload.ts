/**
 * The requests of an archive reload (Wave 5 contract §5, G9, G10): `POST /archive-reloads` asks
 * for the days, `GET /archive-reloads/{id}` follows the reload every 2 s until it is ready, failed
 * or expired. The first read of a reload counts as the user's request (they just asked for it);
 * every later poll is sent with `X-Arci-Background: 1` (`usePollingQuery`), so following a reload
 * does not keep the session alive. A hidden tab does not poll.
 */

import { useMutation } from '@tanstack/react-query';

import { api } from '@/lib/api';
import { usePollingQuery } from '@/lib/api-hooks';
import type { ArchiveReload, ArchiveReloadRequest } from '@/lib/archive-reload-types';

import { ARCHIVE_RELOAD_POLL_MS, isFinished } from './archive-model';

export const ARCHIVE_RELOAD_KEY = (id: number) => ['archive-reloads', 'one', id] as const;

/** `GET /archive-reloads/{id}` every 2 s while the reload is queued or loading. */
export function useArchiveReload(id: number | null) {
  return usePollingQuery<ArchiveReload>({
    queryKey: ARCHIVE_RELOAD_KEY(id ?? 0),
    path: `/archive-reloads/${String(id)}`,
    enabled: id !== null,
    intervalMs: (reload) => (reload && isFinished(reload.status) ? false : ARCHIVE_RELOAD_POLL_MS),
  });
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
