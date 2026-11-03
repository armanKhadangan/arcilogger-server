import { focusManager, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { BACKGROUND_HEADER } from '@/lib/api';
import type { ArchiveReload } from '@/lib/archive-reload-types';
import { errorResponse, installFetch, jsonResponse } from '@/test/api-mock';
import {
  ARCHIVE_CREATED,
  ARCHIVE_ERRORS,
  ARCHIVE_LOADING,
  ARCHIVE_READY,
} from '@/test/archive-fixtures';
import { testQueryClient } from '@/test/render';

import { ARCHIVE_RELOAD_POLL_MS } from './archive-model';
import {
  ARCHIVE_RELOAD_KEY,
  useArchiveReload,
  useRequestArchiveReload,
} from './use-archive-reload';

/*
 * Following a reload (Wave 5 contract §5, G10): `GET /archive-reloads/{id}` every 2 s while it is
 * queued or loading, no more requests once it is ready, failed or expired. The first read is the
 * user's (they just asked for the reload); every poll after it is sent in the background. A 5xx or
 * a network error is polled through; a 403 or 404 stops the poll until `refresh()` (#134).
 */

const PATH = '/api/v1/archive-reloads/1';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
}

/** Answers the reloads of `answers` in turn, the last one again and again. */
function serve(...answers: ArchiveReload[]) {
  let next = 0;
  return installFetch((request) => {
    if (request.method === 'GET' && request.path === PATH) {
      const answer = answers[Math.min(next, answers.length - 1)];
      next += 1;
      return jsonResponse(200, answer);
    }
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useArchiveReload', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    act(() => {
      focusManager.setFocused(undefined);
    });
  });

  it('has its own cache key', () => {
    expect(ARCHIVE_RELOAD_KEY(7)).toEqual(['archive-reloads', 'one', 7]);
  });

  it('asks nothing without a reload', async () => {
    const calls = serve(ARCHIVE_CREATED);
    const { result } = renderHook(() => useArchiveReload(null), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(5 * ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(0);
    expect(result.current.data).toBeUndefined();
  });

  it('polls every 2 s in the background until the reload is ready, then stops', async () => {
    const calls = serve(ARCHIVE_CREATED, ARCHIVE_LOADING, ARCHIVE_READY);
    const { result } = renderHook(() => useArchiveReload(1), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(calls).toHaveLength(1);
    expect(result.current.data?.status).toBe('queued');

    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS - 100));
    expect(calls).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(calls).toHaveLength(2);
    expect(result.current.data?.status).toBe('loading');

    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(3);
    expect(result.current.data?.status).toBe('ready');

    await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(3);
    expect(calls.map((call) => call.path)).toEqual([PATH, PATH, PATH]);
    expect(calls.map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([null, '1', '1']);
  });

  it.each([
    ['failed', { ...ARCHIVE_LOADING, status: 'failed', error_code: 'archive_file_unreadable' }],
    ['expired', { ...ARCHIVE_READY, status: 'expired' }],
  ] as const)('stops once the reload is %s', async (_, finished) => {
    const calls = serve(ARCHIVE_LOADING, finished);
    renderHook(() => useArchiveReload(1), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(2);
    await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(2);
  });

  it('keeps polling after an error, and not while the tab is hidden', async () => {
    let failing = true;
    const calls = installFetch(() =>
      failing ? errorResponse(503, 'service_unavailable') : jsonResponse(200, ARCHIVE_LOADING),
    );
    const { result } = renderHook(() => useArchiveReload(1), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.isError).toBe(true);
    failing = false;
    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(2);
    expect(result.current.data?.status).toBe('loading');
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');

    act(() => {
      focusManager.setFocused(false);
    });
    await act(() => vi.advanceTimersByTimeAsync(5 * ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(2);
  });

  it('keeps polling after a network error', async () => {
    let failing = true;
    const calls = installFetch(() =>
      failing
        ? Promise.reject(new TypeError('Failed to fetch'))
        : jsonResponse(200, ARCHIVE_LOADING),
    );
    const { result } = renderHook(() => useArchiveReload(1), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.error).toMatchObject({ status: 0, code: 'network_error' });
    expect(result.current.stopped).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(2);
    failing = false;
    await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
    expect(calls).toHaveLength(3);
    expect(result.current.data?.status).toBe('loading');
  });

  it.each([
    [403, 'forbidden'],
    [404, 'not_found'],
  ] as const)(
    'stops after a %i (the reload is not the user’s); refresh() asks once, then polling resumes',
    async (status, code) => {
      let answer: () => Response = () => jsonResponse(200, ARCHIVE_LOADING);
      const calls = installFetch(() => answer());
      const { result } = renderHook(() => useArchiveReload(1), { wrapper });
      await act(() => vi.advanceTimersByTimeAsync(10));
      expect(result.current.data?.status).toBe('loading');
      expect(result.current.stopped).toBe(false);

      answer = () => errorResponse(status, code);
      await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
      expect(calls).toHaveLength(2);
      expect(result.current.error).toMatchObject({ status, code });
      expect(result.current.stopped).toBe(true);
      await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
      expect(calls).toHaveLength(2);

      // Asked again and refused again: one request, and still stopped.
      await act(async () => {
        await result.current.refresh();
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(calls).toHaveLength(3);
      expect(calls[2]?.headers.get(BACKGROUND_HEADER)).toBeNull();
      expect(result.current.stopped).toBe(true);
      await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
      expect(calls).toHaveLength(3);

      // Asked again and answered: polling resumes until the reload is ready.
      answer = () => jsonResponse(200, ARCHIVE_LOADING);
      await act(async () => {
        await result.current.refresh();
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(calls).toHaveLength(4);
      expect(result.current.isError).toBe(false);
      expect(result.current.stopped).toBe(false);
      answer = () => jsonResponse(200, ARCHIVE_READY);
      await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
      expect(calls).toHaveLength(5);
      expect(calls[4]?.headers.get(BACKGROUND_HEADER)).toBe('1');
      expect(result.current.data?.status).toBe('ready');
      await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
      expect(calls).toHaveLength(5);
    },
  );
});

describe('useRequestArchiveReload', () => {
  it('POSTs the days and answers the reload', async () => {
    const calls = installFetch((request) =>
      request.method === 'POST' && request.path === '/api/v1/archive-reloads'
        ? jsonResponse(202, ARCHIVE_CREATED)
        : errorResponse(404, 'not_found'),
    );
    const { result } = renderHook(() => useRequestArchiveReload(), { wrapper });
    act(() => {
      result.current.mutate({ reason: 'lookup', first_day: '2026-10-04', last_day: '2026-10-05' });
    });
    await waitFor(() => {
      expect(result.current.data).toEqual(ARCHIVE_CREATED);
    });
    const post = calls.find((call) => call.method === 'POST');
    expect(post?.body).toEqual({
      reason: 'lookup',
      first_day: '2026-10-04',
      last_day: '2026-10-05',
    });
    expect(post?.headers.get(BACKGROUND_HEADER)).toBeNull();
  });

  it('a refusal is an error with the API’s texts', async () => {
    installFetch(() =>
      jsonResponse(ARCHIVE_ERRORS.too_large.status, ARCHIVE_ERRORS.too_large.body),
    );
    const { result } = renderHook(() => useRequestArchiveReload(), { wrapper });
    act(() => {
      result.current.mutate({ reason: 'search', first_day: '2026-09-21', last_day: '2026-10-05' });
    });
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.error).toMatchObject({
      status: 422,
      code: 'archive_reload_too_large',
      messageTr: ARCHIVE_ERRORS.too_large.body.error.message_tr,
    });
  });
});
