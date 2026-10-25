import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { errorResponse, installFetch, jsonResponse, setCsrfCookie } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';
import { licenseSample } from '@/test/samples';

import { BACKGROUND_HEADER } from './api';
import {
  LICENSE_POLL_MS,
  LICENSE_STALE_MS,
  queryKeys,
  useAuditEntries,
  useImportCsv,
  useLicense,
  usePollingQuery,
  useUpdatePerson,
  useUploadLicense,
  useUsers,
} from './api-hooks';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('usePollingQuery', () => {
  it('first load and refresh() count as activity, interval refetches are background', async () => {
    let count = 0;
    const calls = installFetch(() => jsonResponse(200, { value: ++count }));
    const { result } = renderHook(
      () => usePollingQuery<{ value: number }>({ queryKey: ['poll'], path: '/x', intervalMs: 40 }),
      { wrapper },
    );
    await waitFor(() => {
      expect(calls.length).toBeGreaterThanOrEqual(3);
    });
    const background = calls.map((call) => call.headers.get(BACKGROUND_HEADER));
    expect(background[0]).toBeNull();
    expect(background.slice(1, 3)).toEqual(['1', '1']);

    const before = calls.length;
    await act(async () => {
      await result.current.refresh();
    });
    const manual = calls[before];
    expect(manual?.headers.get(BACKGROUND_HEADER)).toBeNull();
    expect(result.current.data?.value).toBeGreaterThan(1);
  });

  it('keeps refreshing a failed answer in the background (an error does not keep the session alive)', async () => {
    const calls = installFetch(() => errorResponse(500, 'internal_error'));
    renderHook(
      () =>
        usePollingQuery<{ value: number }>({ queryKey: ['poll-fail'], path: '/x', intervalMs: 40 }),
      { wrapper },
    );
    await waitFor(() => {
      expect(calls.length).toBeGreaterThanOrEqual(3);
    });
    const background = calls.map((call) => call.headers.get(BACKGROUND_HEADER));
    expect(background[0]).toBeNull();
    expect(background.slice(1, 3)).toEqual(['1', '1']);
  });

  it('the first load of a new key (the user changed the filter) is not a background request', async () => {
    let count = 0;
    const calls = installFetch(() => jsonResponse(200, { value: ++count }));
    const { result, rerender } = renderHook(
      ({ day }: { day: string }) =>
        usePollingQuery<{ value: number }>({
          queryKey: ['poll', day],
          path: '/x',
          params: { day },
          intervalMs: 40,
        }),
      { wrapper, initialProps: { day: '2026-10-12' } },
    );
    await waitFor(() => {
      expect(calls.length).toBeGreaterThanOrEqual(2);
    });
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');

    const before = calls.length;
    rerender({ day: '2026-10-13' });
    await waitFor(() => {
      expect(result.current.data).toBeDefined();
      expect(calls.some((call) => call.params.get('day') === '2026-10-13')).toBe(true);
    });
    const changed = calls.slice(before).filter((call) => call.params.get('day') === '2026-10-13');
    expect(changed[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    // Its own interval refetches are background again.
    await waitFor(() => {
      expect(
        calls.filter((call) => call.params.get('day') === '2026-10-13').length,
      ).toBeGreaterThanOrEqual(2);
    });
    const later = calls.filter((call) => call.params.get('day') === '2026-10-13');
    expect(later[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');

    // Back to a day already in the cache: the user asked for it, so again not background.
    const back = calls.length;
    rerender({ day: '2026-10-12' });
    await waitFor(() => {
      expect(calls.slice(back).some((call) => call.params.get('day') === '2026-10-12')).toBe(true);
    });
    const returned = calls.slice(back).find((call) => call.params.get('day') === '2026-10-12');
    expect(returned?.headers.get(BACKGROUND_HEADER)).toBeNull();
  });
});

describe('cursor pagination', () => {
  it('loads audit pages with the previous next_cursor and the filters', async () => {
    const calls = installFetch((request) =>
      request.params.get('cursor') === null
        ? jsonResponse(200, { items: [{ id: 200 }], next_cursor: 101 })
        : jsonResponse(200, { items: [{ id: 100 }], next_cursor: null }),
    );
    const { result } = renderHook(() => useAuditEntries({ action: 'auth.', actor: 'tolga' }), {
      wrapper,
    });
    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(true);
    });
    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(calls.map((call) => call.url)).toEqual([
      '/api/v1/audit?action=auth.&actor=tolga&limit=100',
      '/api/v1/audit?action=auth.&actor=tolga&limit=100&cursor=101',
    ]);
    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(false);
    });
    expect(result.current.data?.pages.flatMap((page) => page.items.map((item) => item.id))).toEqual(
      [200, 100],
    );
  });

  it('sends the user filters and pages by user name', async () => {
    const calls = installFetch(() => jsonResponse(200, { items: [], next_cursor: null }));
    renderHook(() => useUsers({ role: 'hr', active: false }), { wrapper });
    await waitFor(() => {
      expect(calls).toHaveLength(1);
    });
    expect(calls[0]?.url).toBe('/api/v1/users?limit=500&role=hr&active=false');
  });
});

describe('licence hooks', () => {
  it('one shared GET /license: the first answer is foreground, every refresh background', async () => {
    expect(LICENSE_STALE_MS).toBe(60_000);
    expect(LICENSE_POLL_MS).toBe(300_000);
    const client = testQueryClient();
    const calls = installFetch(() => jsonResponse(200, licenseSample('trial')));
    const local = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    // Banner, menu and module gate read it at the same time.
    const { result } = renderHook(() => [useLicense(), useLicense(), useLicense()], {
      wrapper: local,
    });
    await waitFor(() => {
      expect(result.current[0]?.data?.status).toBe('trial');
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    await act(async () => {
      await client.invalidateQueries({ queryKey: queryKeys.license });
    });
    expect(calls).toHaveLength(2);
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
  });

  it('an upload sends the file as multipart `file` and reads every query again', async () => {
    const client = testQueryClient();
    setCsrfCookie('csrf-1');
    let installed = false;
    const calls = installFetch((request) => {
      if (request.method === 'POST') installed = true;
      if (request.path !== '/api/v1/license') return jsonResponse(200, {});
      return jsonResponse(200, licenseSample(installed ? 'licensed' : 'trial'));
    });
    const local = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => ({
        license: useLicense(),
        other: usePollingQuery<object>({ queryKey: ['other'], path: '/x', intervalMs: 600_000 }),
        upload: useUploadLicense(),
      }),
      { wrapper: local },
    );
    await waitFor(() => {
      expect(result.current.license.data?.status).toBe('trial');
      expect(result.current.other.data).toBeDefined();
    });
    const before = calls.length;
    await act(async () => {
      await result.current.upload.mutateAsync(new File(['{}'], 'arci.lic'));
    });
    const post = calls.find((call) => call.method === 'POST');
    expect(post?.path).toBe('/api/v1/license');
    expect(((post?.body as FormData).get('file') as File).name).toBe('arci.lic');
    await waitFor(() => {
      expect(
        calls
          .slice(before)
          .map((call) => call.path)
          .sort(),
      ).toEqual(['/api/v1/license', '/api/v1/license', '/api/v1/x']);
    });
    expect(result.current.license.data?.status).toBe('licensed');
    setCsrfCookie(null);
  });
});

describe('person changes', () => {
  it('an edit or a persons import marks the Kullanım answers stale, an assignments import not', async () => {
    const client = testQueryClient();
    setCsrfCookie('csrf-1');
    installFetch(() => jsonResponse(200, { dry_run: false }));
    const local = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const usageKey = queryKeys.usage('subjects', { kind: 'person' });
    const stale = () => client.getQueryState(usageKey)?.isInvalidated;
    const { result } = renderHook(() => ({ update: useUpdatePerson(), csv: useImportCsv() }), {
      wrapper: local,
    });
    const file = new File(['mac\n'], 'a.csv');

    client.setQueryData(usageKey, {});
    await act(async () => {
      await result.current.csv.mutateAsync({ kind: 'assignments', file, dryRun: false });
    });
    expect(stale()).toBe(false);
    await act(async () => {
      await result.current.csv.mutateAsync({ kind: 'persons', file, dryRun: false });
    });
    expect(stale()).toBe(true);

    client.setQueryData(usageKey, {});
    await act(async () => {
      await result.current.update.mutateAsync({ id: 1, usage_excluded: true });
    });
    expect(stale()).toBe(true);
    setCsrfCookie(null);
  });
});
