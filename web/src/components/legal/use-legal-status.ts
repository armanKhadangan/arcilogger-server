/**
 * The shared read of `GET /legal-status` (Wave 5 contract 2.3.1, 2.3.2): any signed-in user, not
 * audited, cached by the server for 60 s. The banner above every page, the sealing page and the
 * Panel read it at once, so — as `useLicense()` — the background header is decided from the shared
 * cache, not per caller: the first answer of a session is the user's request, every later refresh
 * (every 5 minutes, only while the tab is visible) is sent with `X-Arci-Background: 1`, so an open
 * tab does not keep the session alive. Signing out clears the cache (`lib/auth.tsx`). Only the
 * banner polls: TanStack keeps one timer per reader, so the other readers pass `poll: false` and
 * get the banner's refreshes from the shared cache.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api';

import type { LegalStatus } from '@/lib/legal-status-types';

export const LEGAL_STATUS_KEY = ['legal-status'] as const;

/** The server keeps the status for 60 s; asking sooner returns the same answer. */
export const LEGAL_STATUS_STALE_MS = 60_000;
/** The banner refreshes itself every 5 minutes (contract 2.3.2). */
export const LEGAL_STATUS_POLL_MS = 300_000;

/** `GET /legal-status`: the level, the provider and the reasons, worst first. */
export function useLegalStatus({ poll = true }: { poll?: boolean } = {}) {
  const client = useQueryClient();
  return useQuery({
    queryKey: LEGAL_STATUS_KEY,
    queryFn: ({ signal }) => {
      const state = client.getQueryState(LEGAL_STATUS_KEY);
      const answered = state !== undefined && state.dataUpdatedAt + state.errorUpdatedAt > 0;
      return api.get<LegalStatus>('/legal-status', { signal, background: answered });
    },
    staleTime: LEGAL_STATUS_STALE_MS,
    refetchInterval: poll ? LEGAL_STATUS_POLL_MS : false,
    refetchIntervalInBackground: false,
  });
}
