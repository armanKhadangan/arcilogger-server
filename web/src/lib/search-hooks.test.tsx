import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { page, row } from '@/pages/search/test-data';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { testQueryClient } from '@/test/render';

import { ApiError, CSRF_HEADER } from './api';
import {
  downloadSearchExport,
  rowId,
  SEARCH_AUTO_CONTINUES,
  SEARCH_VIEW_ROWS,
  useSearchSession,
} from './search-hooks';
import type { SearchPage } from './search-types';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  setCsrfCookie('csrf-1');
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

const INPUT = {
  q: 'type:traffic',
  from: '2026-10-09T00:00:00+03:00',
  to: '2026-10-10T00:00:00+03:00',
};

/** A page whose scan stopped at its time budget (`frontier` cursor). */
function partial(id: string, rows: number): SearchPage {
  return page({
    search_id: id,
    items: Array.from({ length: rows }, (_, index) => row({ key: `fg_traffic:t:${id}-${index}` })),
    next_cursor: 'frontier',
    scan: { ...page().scan, complete: false },
  });
}

function bodies(calls: RecordedRequest[], path: string): unknown[] {
  return calls.filter((call) => call.path === `/api/v1${path}`).map((call) => call.body);
}

describe('useSearchSession', () => {
  it('runs a search in a POST body and reads further pages by its id', async () => {
    const calls = installFetch((request) =>
      request.path === '/api/v1/search'
        ? jsonResponse(200, page({ next_cursor: 'c1' }))
        : jsonResponse(200, page({ items: [row({ key: 'fg_traffic:t:2' })] })),
    );
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run(INPUT);
    });
    expect(bodies(calls, '/search')).toEqual([{ ...INPUT, limit: 100 }]);
    expect(calls[0]?.url).toBe('/api/v1/search');
    await act(async () => {
      await result.current.next('more');
    });
    expect(bodies(calls, '/search/page')).toEqual([
      { search_id: 'ornek-arama-01', cursor: 'c1', limit: 100 },
    ]);
    expect(result.current.state.rows.map((item) => item.key)).toEqual([
      'fg_traffic:2026-10-09T10:30:00Z:25',
      'fg_traffic:t:2',
    ]);
    expect(result.current.state.last?.next_cursor).toBeNull();
  });

  it('runs a saved search by its id alone', async () => {
    const calls = installFetch(() => jsonResponse(200, page()));
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run({ saved_id: 3 });
    });
    expect(bodies(calls, '/search')).toEqual([{ saved_id: 3, limit: 100 }]);
  });

  it('continues an incomplete scan by itself five times, then waits for "Devam et"', async () => {
    let pages = 0;
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/search') return jsonResponse(200, partial('s1', 1));
      pages += 1;
      return jsonResponse(200, partial('s1', 0));
    });
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run(INPUT);
    });
    await waitFor(() => {
      expect(result.current.state.continues).toBe(SEARCH_AUTO_CONTINUES);
      expect(result.current.state.paging).toBeNull();
    });
    expect(pages).toBe(SEARCH_AUTO_CONTINUES);
    await act(async () => {
      await result.current.next('continue');
    });
    // A user's "Devam et" starts the count again.
    await waitFor(() => {
      expect(pages).toBe(2 * SEARCH_AUTO_CONTINUES + 1);
    });
    expect(bodies(calls, '/search')).toHaveLength(1);
  });

  it('runs an expired search again once, silently, and not a second time', async () => {
    let runs = 0;
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/search') {
        runs += 1;
        return jsonResponse(200, page({ search_id: `s${runs}`, next_cursor: 'c1' }));
      }
      return errorResponse(409, 'search_expired');
    });
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run(INPUT);
    });
    await act(async () => {
      await result.current.next('more');
    });
    expect(runs).toBe(2);
    expect(result.current.state.first?.search_id).toBe('s2');
    expect(result.current.state.error).toBeNull();
    expect(bodies(calls, '/search')).toEqual([
      { ...INPUT, limit: 100 },
      { ...INPUT, limit: 100 },
    ]);
    await act(async () => {
      await result.current.next('more');
    });
    expect(runs).toBe(2);
    expect(result.current.state.error).toMatchObject({ code: 'search_expired' });
  });

  it('repeats an action with the new id after the expired search ran again', async () => {
    let runs = 0;
    installFetch(() => {
      runs += 1;
      return jsonResponse(200, page({ search_id: `s${runs}` }));
    });
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run(INPUT);
    });
    const expired = new ApiError({
      status: 409,
      code: 'search_expired',
      messageTr: 'süresi doldu',
      messageEn: 'expired',
    });
    const used: string[] = [];
    let value: string | null = null;
    await act(async () => {
      value = await result.current.withSearch((id) => {
        used.push(id);
        return id === 's1' ? Promise.reject(expired) : Promise.resolve(id);
      });
    });
    expect(value).toBe('s2');
    expect(used).toEqual(['s1', 's2']);
    // Any other refusal is the caller's: no rerun.
    await expect(
      result.current.withSearch(() => Promise.reject(new Error('refused'))),
    ).rejects.toThrow('refused');
    expect(runs).toBe(2);
  });

  it('keeps the rows in view at the cap', async () => {
    const many = Array.from({ length: 100 }, (_, index) => row({ key: `fg_traffic:t:${index}` }));
    installFetch(() => jsonResponse(200, page({ items: many, next_cursor: 'c' })));
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run(INPUT);
    });
    for (let index = 0; index < SEARCH_VIEW_ROWS / 100; index += 1) {
      await act(async () => {
        await result.current.next('more');
      });
    }
    expect(result.current.state.rows).toHaveLength(SEARCH_VIEW_ROWS);
  });

  it('forgets the answer of a search that a newer one replaced', async () => {
    const held: { release?: () => void } = {};
    installFetch(
      (request) =>
        new Promise<Response>((resolve) => {
          const body = request.body as { q: string };
          const answer = () => {
            resolve(jsonResponse(200, page({ search_id: body.q })));
          };
          if (body.q === 'old') held.release = answer;
          else answer();
        }),
    );
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    let old: Promise<SearchPage | null> = Promise.resolve(null);
    act(() => {
      old = result.current.run({ ...INPUT, q: 'old' });
    });
    await act(async () => {
      await result.current.run({ ...INPUT, q: 'new' });
    });
    await act(async () => {
      held.release?.();
      await old;
    });
    expect(result.current.state.first?.search_id).toBe('new');
  });

  it('clears the rows when a run is refused', async () => {
    installFetch((request) =>
      (request.body as { q: string }).q === 'ok'
        ? jsonResponse(200, page())
        : errorResponse(422, 'search_syntax', { details: { start: 0, end: 1, params: {} } }),
    );
    const { result } = renderHook(() => useSearchSession(), { wrapper });
    await act(async () => {
      await result.current.run({ ...INPUT, q: 'ok' });
    });
    await act(async () => {
      await result.current.run({ ...INPUT, q: '"' });
    });
    expect(result.current.state.rows).toEqual([]);
    expect(result.current.state.error).toMatchObject({ code: 'search_syntax' });
  });
});

describe('the export download', () => {
  it('POSTs the search id with the CSRF token and returns the file and its headers', async () => {
    const calls = installFetch(
      () =>
        new Response('Zaman;Kaynak\r\n', {
          status: 200,
          headers: {
            'Content-Type': 'text/csv; charset=utf-8',
            'Content-Disposition':
              'attachment; filename="arama-20261009T0000-20261010T0000-kismi.csv"',
            'X-Arci-Export-Truncated': 'rows',
          },
        }),
    );
    const file = await downloadSearchExport('s1', 'csv');
    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.body).toEqual({ search_id: 's1', format: 'csv' });
    expect(calls[0]?.headers.get(CSRF_HEADER)).toBe('csrf-1');
    expect(file.filename).toBe('arama-20261009T0000-20261010T0000-kismi.csv');
    expect(file.headers.get('X-Arci-Export-Truncated')).toBe('rows');
    expect(await file.blob.text()).toBe('Zaman;Kaynak\r\n');
  });

  it('turns a refusal into the API error with its Retry-After', async () => {
    installFetch(() =>
      errorResponse(429, 'search_export_rate_limited', { headers: { 'Retry-After': '3598' } }),
    );
    await expect(downloadSearchExport('s1', 'xlsx')).rejects.toMatchObject({
      status: 429,
      code: 'search_export_rate_limited',
      retryAfter: 3598,
    });
    installFetch(() => new Response('<html>bad gateway</html>', { status: 502 }));
    await expect(downloadSearchExport('s1', 'xlsx')).rejects.toMatchObject({ code: 'http_502' });
  });
});

describe('rows', () => {
  it('know their database id', () => {
    expect(rowId(row({ key: 'fg_traffic:2026-10-09T10:30:00.000000+00:00:25' }))).toBe(25);
  });
});
