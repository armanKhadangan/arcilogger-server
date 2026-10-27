/**
 * TanStack Query hooks of the search API (`/search`, contract 2.10) and the search session of the
 * Arama page.
 *
 * Conventions (those of `api-hooks.ts`, plus):
 *  - Everything that runs a search is a POST: the query text is in the request body, never in an
 *    address (the web server's access log, `?next=` after a time-out, the tab's history).
 *  - `POST /search` is audited (`search.run`) and answers a `search_id`; pages, statistics,
 *    evidence references and the export take only that id. An expired id (409 `search_expired`)
 *    runs the same search again, once, without asking (`useSearchSession`).
 *  - Audited reads (`GET /search/event`: `search.event`) are fetched once per screen
 *    (`staleTime: Infinity`, no retry but the one below); statistics belong to their `search_id`
 *    and never change.
 *  - The field catalogue changes only with the configuration: kept for five minutes.
 *  - Every request that takes one of the server's search slots (run, page, statistics, record,
 *    references, export) is sent once more after 429 `search_busy`, after its `Retry-After`
 *    (contract 2.6.4): a page and its statistics, a drawer and a continuation may well overlap.
 *  - Rows per page: none asked for. The server's default is `min(100, search.page_size_max)` for
 *    a run and the search's own for its pages; a fixed 100 would be refused on a site whose
 *    `search.page_size_max` is set below it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  ApiError,
  api,
  buildUrl,
  CSRF_COOKIE,
  CSRF_HEADER,
  filenameFrom,
  isApiError,
  reactGlobally,
  readCookie,
  type DownloadedFile,
} from './api';
import i18n from './i18n';
import type {
  ExportFormat,
  FieldCatalogue,
  ParseResult,
  SavedSearch,
  SavedSearchIn,
  SavedSearchList,
  SearchEvent,
  SearchPage,
  SearchRefs,
  SearchRequest,
  SearchRow,
  SearchStats,
} from './search-types';

/** Most rows kept in the list; beyond them the query should be narrowed (or exported). */
export const SEARCH_VIEW_ROWS = 2_000;
/** Times an incomplete scan continues on its own before it waits for "Devam et". */
export const SEARCH_AUTO_CONTINUES = 5;
/** The field catalogue's freshness (it changes only with the configuration). */
export const FIELDS_STALE_MS = 300_000;
/** Seconds before a request refused with `search_busy` is sent again, without a `Retry-After`. */
export const BUSY_RETRY_SECONDS = 2;
/** The longest wait a `Retry-After` of `search_busy` may ask for (seconds). */
const BUSY_RETRY_MAX_SECONDS = 10;

export const searchKeys = {
  fields: ['search', 'fields'] as const,
  parse: (q: string) => ['search', 'parse', q] as const,
  stats: (searchId: string) => ['search', 'stats', searchId] as const,
  event: (row: Pick<SearchRow, 'table' | 'time' | 'key'>) =>
    ['search', 'event', row.table, row.time, row.key] as const,
  saved: ['search', 'saved'] as const,
};

/** The wait before a request refused with 429 `search_busy` goes again (ms). */
export function busyDelay(error: unknown): number {
  const seconds = error instanceof ApiError ? error.retryAfter : null;
  return Math.min(seconds ?? BUSY_RETRY_SECONDS, BUSY_RETRY_MAX_SECONDS) * 1000;
}

/**
 * `action`, sent once more after its `Retry-After` when the server answered `search_busy` —
 * unless `wanted` says by then that its answer would be void (a newer search started).
 */
export async function retryBusyOnce<T>(
  action: () => Promise<T>,
  wanted: () => boolean = () => true,
): Promise<T> {
  try {
    return await action();
  } catch (error) {
    if (!isApiError(error, 'search_busy')) throw error;
    await new Promise((resolve) => setTimeout(resolve, busyDelay(error)));
    if (!wanted()) throw error;
    return action();
  }
}

/** The retry of a query that takes a search slot: once, and only after `search_busy`. */
const busyRetry = {
  retry: (failures: number, error: unknown) => failures < 1 && isApiError(error, 'search_busy'),
  retryDelay: (_failures: number, error: unknown) => busyDelay(error),
};

/** The fields, kinds, sources and limits of the query language. */
export function useSearchFields(enabled = true) {
  return useQuery({
    queryKey: searchKeys.fields,
    queryFn: ({ signal }) => api.get<FieldCatalogue>('/search/fields', { signal }),
    staleTime: FIELDS_STALE_MS,
    enabled,
  });
}

/** `POST /search/parse` of a (debounced) query text; nothing for an empty one. */
export function useParseQuery(q: string) {
  return useQuery({
    queryKey: searchKeys.parse(q),
    queryFn: ({ signal }) => api.post<ParseResult>('/search/parse', { q }, { signal }),
    enabled: q.trim() !== '',
    staleTime: Infinity,
    retry: false,
  });
}

/** The statistics of a search (they belong to its `search_id` and never change). */
export function useSearchStats(searchId: string | null) {
  return useQuery({
    queryKey: searchKeys.stats(searchId ?? ''),
    queryFn: ({ signal }) =>
      api.post<SearchStats>('/search/stats', { search_id: searchId }, { signal }),
    enabled: searchId !== null,
    staleTime: Infinity,
    ...busyRetry,
  });
}

/** The database id of a row (`<table>:<time>:<id>`). */
export function rowId(row: Pick<SearchRow, 'key'>): number {
  return Number(row.key.slice(row.key.lastIndexOf(':') + 1));
}

/**
 * Every column of one record (audited `search.event`: read once per screen; sent again only
 * after `search_busy`, which the server answers before it reads or audits anything).
 */
export function useSearchEvent(row: SearchRow | null) {
  return useQuery({
    queryKey: row ? searchKeys.event(row) : ['search', 'event', 'none'],
    queryFn: ({ signal }) =>
      api.get<SearchEvent>('/search/event', {
        signal,
        params: { table: row?.table, time: row?.time, id: row ? rowId(row) : undefined },
      }),
    enabled: row !== null,
    staleTime: Infinity,
    ...busyRetry,
  });
}

/**
 * `POST /search/refs` (audited `search.refs`): the raw lines of a whole search. The page sends it
 * through the session's `withSearch`, which also asks again after `search_busy`.
 */
export function useSearchRefs() {
  return useMutation({
    mutationFn: ({ searchId, limit }: { searchId: string; limit: number }) =>
      api.post<SearchRefs>('/search/refs', { search_id: searchId, limit }),
  });
}

// -- saved searches (personal) --------------------------------------------------------------------

export function useSavedSearches(enabled = true) {
  return useQuery({
    queryKey: searchKeys.saved,
    queryFn: ({ signal }) => api.get<SavedSearchList>('/search/saved', { signal }),
    enabled,
  });
}

function useSavedMutation<TVariables, TResult>(fn: (variables: TVariables) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: searchKeys.saved }),
  });
}

export function useCreateSavedSearch() {
  return useSavedMutation((body: SavedSearchIn) => api.post<SavedSearch>('/search/saved', body));
}

export function useUpdateSavedSearch() {
  return useSavedMutation(({ id, body }: { id: number; body: SavedSearchIn }) =>
    api.put<SavedSearch>(`/search/saved/${id}`, body),
  );
}

export function useDeleteSavedSearch() {
  return useSavedMutation((id: number) => api.delete<undefined>(`/search/saved/${id}`));
}

// -- export ---------------------------------------------------------------------------------------

/** The API's error of a failed download (its error body, else a generic HTTP text). */
async function downloadError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const error = (body as { error?: Record<string, unknown> } | null)?.error;
  const retry = response.headers.get('Retry-After');
  const retryAfter = retry !== null && /^\d+$/.test(retry.trim()) ? Number(retry.trim()) : null;
  if (
    typeof error?.code === 'string' &&
    typeof error.message_tr === 'string' &&
    typeof error.message_en === 'string'
  )
    return new ApiError({
      status: response.status,
      code: error.code,
      messageTr: error.message_tr,
      messageEn: error.message_en,
      details: error.details,
      retryAfter,
    });
  const texts = { status: response.status };
  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    messageTr: i18n.getFixedT('tr')('errors.http', texts),
    messageEn: i18n.getFixedT('en')('errors.http', texts),
    retryAfter,
  });
}

/**
 * `POST /search/export` as a file. `apiDownload` (lib/api.ts) GETs a file; the export is a POST
 * (the search's id, not its query, in the body), so this sends it the same way: the session
 * cookie, the CSRF token of every mutation, the API's errors with their global reactions (401 →
 * sign-in, 403 `privacy_notice_required` → the notice). The caller wraps it in `withReauth` and
 * checks the file against `Repr-Digest`.
 */
export async function downloadSearchExport(
  searchId: string,
  format: ExportFormat,
): Promise<DownloadedFile> {
  const headers: Record<string, string> = {
    Accept: '*/*',
    'Content-Type': 'application/json',
  };
  const token = readCookie(CSRF_COOKIE);
  if (token) headers[CSRF_HEADER] = token;
  let response: Response;
  try {
    response = await fetch(buildUrl('/search/export'), {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: JSON.stringify({ search_id: searchId, format }),
    });
  } catch {
    throw new ApiError({
      status: 0,
      code: 'network_error',
      messageTr: i18n.getFixedT('tr')('errors.network'),
      messageEn: i18n.getFixedT('en')('errors.network'),
    });
  }
  if (!response.ok) {
    const error = await downloadError(response);
    reactGlobally(error);
    throw error;
  }
  return {
    blob: await response.blob(),
    filename: filenameFrom(response.headers.get('Content-Disposition'), `arama.${format}`),
    headers: response.headers,
  };
}

// -- the search session ----------------------------------------------------------------------------

/** What a search runs on: a query and an absolute range, or a saved search. */
export type SearchInput = { q: string; from: string; to: string } | { saved_id: number };

export interface SearchSession {
  /** What ran (or runs): reruns and "Devam et" use it. */
  input: SearchInput | null;
  /** The first page: the query as the server read it, the plan, the range, the notices. */
  first: SearchPage | null;
  /** The newest page: its scan and its cursor decide what comes next. */
  last: SearchPage | null;
  /** The rows in view, newest first, at most {@link SEARCH_VIEW_ROWS}. */
  rows: SearchRow[];
  /** Automatic continuations of an incomplete scan since the last action of the user. */
  continues: number;
  /** `POST /search` is running. */
  running: boolean;
  /** A further page is being read: asked for ("Daha fazla", "Devam et") or automatic. */
  paging: 'more' | 'continue' | 'auto' | null;
  /** The refusal of the run (rows cleared) or of the last page (rows kept). */
  error: unknown;
}

const IDLE: SearchSession = {
  input: null,
  first: null,
  last: null,
  rows: [],
  continues: 0,
  running: false,
  paging: null,
  error: null,
};

function requestOf(input: SearchInput): SearchRequest {
  return 'saved_id' in input
    ? { saved_id: input.saved_id }
    : { q: input.q, from: input.from, to: input.to };
}

/** An incomplete scan that should go on by itself (time budget used up, below the caps). */
function continuesByItself(state: SearchSession): boolean {
  const last = state.last;
  return (
    last !== null &&
    last.next_cursor !== null &&
    !last.scan.complete &&
    state.continues < SEARCH_AUTO_CONTINUES &&
    state.rows.length < SEARCH_VIEW_ROWS &&
    state.paging === null &&
    !state.running &&
    state.error === null
  );
}

/**
 * The Arama page's search: `run` (`POST /search`, a new audited search), `next` (`POST
 * /search/page`: "Daha fazla", "Devam et"; an incomplete scan goes on by itself up to
 * {@link SEARCH_AUTO_CONTINUES} times), `withSearch` (an action on the search's id that runs the
 * search again once when the id expired). A newer run makes the answers of older ones void.
 */
export function useSearchSession() {
  const [state, setState] = useState<SearchSession>(IDLE);
  const current = useRef(state);
  current.current = state;
  const generation = useRef(0);
  /** The search was run again after its id expired (once per search the user started). */
  const rerun = useRef(false);

  const run = useCallback(async (input: SearchInput, again = false): Promise<SearchPage | null> => {
    const mine = ++generation.current;
    if (!again) rerun.current = false;
    setState((before) => ({ ...before, input, running: true, paging: null, error: null }));
    try {
      const page = await retryBusyOnce(
        () => api.post<SearchPage>('/search', requestOf(input)),
        () => mine === generation.current,
      );
      if (mine !== generation.current) return null;
      setState({
        ...IDLE,
        input,
        first: page,
        last: page,
        rows: page.items.slice(0, SEARCH_VIEW_ROWS),
      });
      return page;
    } catch (error) {
      if (mine !== generation.current) return null;
      setState({ ...IDLE, input, error });
      return null;
    }
  }, []);

  /** Run the search again after its id expired; `false` when that was done already. */
  const renew = useCallback(async (): Promise<SearchPage | null> => {
    const input = current.current.input;
    if (rerun.current || input === null) return null;
    rerun.current = true;
    return run(input, true);
  }, [run]);

  const next = useCallback(
    async (kind: 'more' | 'continue' | 'auto') => {
      const before = current.current;
      const last = before.last;
      const cursor = last?.next_cursor;
      if (!last || !cursor || before.paging !== null || before.running) return;
      const mine = generation.current;
      setState((now) => ({
        ...now,
        paging: kind,
        continues: kind === 'auto' ? now.continues + 1 : 0,
        error: null,
      }));
      try {
        const page = await retryBusyOnce(
          () => api.post<SearchPage>('/search/page', { search_id: last.search_id, cursor }),
          () => mine === generation.current,
        );
        if (mine !== generation.current) return;
        setState((now) => ({
          ...now,
          last: page,
          rows: [...now.rows, ...page.items].slice(0, SEARCH_VIEW_ROWS),
          paging: null,
        }));
      } catch (error) {
        if (mine !== generation.current) return;
        if (isApiError(error, 'search_expired') && (await renew()) !== null) return;
        setState((now) => ({ ...now, paging: null, error }));
      }
    },
    [renew],
  );

  /**
   * `action` with the search's id; when the id expired, the search runs again (once) and the
   * action is repeated with the new id. Each try is sent once more after `search_busy`.
   */
  const withSearch = useCallback(
    async <T>(action: (searchId: string) => Promise<T>): Promise<T> => {
      const id = current.current.first?.search_id;
      if (!id) throw new Error('no search has run');
      try {
        return await retryBusyOnce(() => action(id));
      } catch (error) {
        if (!isApiError(error, 'search_expired')) throw error;
        const page = await renew();
        if (page === null) throw error;
        return retryBusyOnce(() => action(page.search_id));
      }
    },
    [renew],
  );

  const reset = useCallback(() => {
    generation.current += 1;
    setState(IDLE);
  }, []);

  useEffect(() => {
    if (continuesByItself(state)) void next('auto');
  }, [state, next]);

  return { state, run, next, renew, withSearch, reset };
}
