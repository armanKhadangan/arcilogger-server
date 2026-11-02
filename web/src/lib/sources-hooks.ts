/**
 * TanStack Query hooks of the Kaynaklar page (`/sources`, `sources-types.ts`).
 *
 * - The overview refreshes itself every 30 s in the background (`usePollingQuery`:
 *   `X-Arci-Background`, so an unattended page does not keep the session alive; the server caches
 *   it for 15 s).
 * - A source's detail is read when its drawer opens and kept for 30 s (the server's cache).
 * - "Son satırlar" are raw lines, audited by the server (`raw.tail`): read once when the tab opens,
 *   never in the background, and forgotten when the drawer closes (`gcTime: 0`). "Yenile" reads
 *   them again (a new audit entry).
 */

import { useQuery } from '@tanstack/react-query';

import { api } from './api';
import { usePollingQuery } from './api-hooks';
import type { SourceDetail, SourcesOverview, SourceTail } from './sources-types';

export const SOURCES_POLL_MS = 30_000;
/** How long a source's detail counts as fresh (the server caches it as long). */
export const SOURCE_DETAIL_STALE_MS = 30_000;
/** Lines of "Son satırlar" (the API allows up to 200). */
export const TAIL_LINES = 50;

export const sourcesKeys = {
  overview: ['sources', 'overview'] as const,
  detail: (name: string) => ['sources', 'detail', name] as const,
  tail: (name: string) => ['sources', 'tail', name] as const,
};

/** A source's name as a path segment. */
function segment(name: string | null): string {
  return encodeURIComponent(name ?? '');
}

/** `GET /sources` (`sources.read`): every source with its state. */
export function useSourcesOverview(enabled = true) {
  return usePollingQuery<SourcesOverview>({
    queryKey: sourcesKeys.overview,
    path: '/sources',
    intervalMs: SOURCES_POLL_MS,
    enabled,
  });
}

/** `GET /sources/{name}`: 48 hours, 14 days, gaps and parse failures of one source. */
export function useSourceDetail(name: string | null) {
  return useQuery({
    queryKey: sourcesKeys.detail(name ?? ''),
    queryFn: ({ signal }) => api.get<SourceDetail>(`/sources/${segment(name)}`, { signal }),
    enabled: name !== null,
    staleTime: SOURCE_DETAIL_STALE_MS,
  });
}

/** `GET /sources/{name}/tail` (audited `raw.tail`): only while `enabled` (the tab is open). */
export function useSourceTail(name: string | null, enabled: boolean) {
  return useQuery({
    queryKey: sourcesKeys.tail(name ?? ''),
    queryFn: ({ signal }) =>
      api.get<SourceTail>(`/sources/${segment(name)}/tail`, {
        signal,
        params: { limit: TAIL_LINES },
      }),
    enabled: name !== null && enabled,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });
}
