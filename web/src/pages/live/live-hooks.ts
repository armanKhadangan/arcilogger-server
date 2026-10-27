/**
 * The search language's catalogue as Canlı Akış's filters read it: the kinds and the configured
 * sources of `GET /search/fields` (`search.read`; Arama's `FieldCatalogue`, `search-types.ts`).
 * The query has Arama's key, so both pages share one answer; once Arama's hooks are on the same
 * branch, the page takes `useSearchFields` from `lib/search-hooks.ts` instead of this one.
 */

import { useQuery } from '@tanstack/react-query';

import { api } from '@/lib/api';

/** A kind (`type:` value) with its labels. */
export interface LiveKindOption {
  key: string;
  label_tr: string;
  label_en: string;
}

/** A configured source; `network`: an address range whose members are `<name>-<ip>` folders. */
export interface LiveSourceOption {
  name: string;
  type: string;
  network: boolean;
}

/** The parts of the field catalogue the filters use. */
export interface LiveCatalogue {
  kinds: LiveKindOption[];
  sources: LiveSourceOption[];
}

/** The catalogue changes only with the configuration: fresh for 5 minutes (as Arama keeps it). */
export const CATALOGUE_STALE_MS = 300_000;

export function useLiveCatalogue(enabled = true) {
  return useQuery({
    queryKey: ['search', 'fields'],
    queryFn: ({ signal }) => api.get<LiveCatalogue>('/search/fields', { signal }),
    staleTime: CATALOGUE_STALE_MS,
    enabled,
  });
}
