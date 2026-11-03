import { focusManager, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { BACKGROUND_HEADER } from '@/lib/api';
import { installFetch, jsonResponse } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';
import { LEGAL_PROVIDER_NOT_LEGAL } from '@/test/w5-fixtures';

import {
  LEGAL_STATUS_KEY,
  LEGAL_STATUS_POLL_MS,
  LEGAL_STATUS_STALE_MS,
  useLegalStatus,
} from './use-legal-status';

/*
 * One shared `GET /legal-status` for the banner, the sealing page and the Panel (contract 2.3.2):
 * refreshed every 5 minutes, the first answer as the user's request, every refresh with
 * `X-Arci-Background: 1` whichever reader triggers it.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useLegalStatus', () => {
  it('one shared read: the first answer is foreground, every refresh background', async () => {
    expect(LEGAL_STATUS_STALE_MS).toBe(60_000);
    expect(LEGAL_STATUS_POLL_MS).toBe(300_000);
    const client = testQueryClient();
    const calls = installFetch(() => jsonResponse(200, LEGAL_PROVIDER_NOT_LEGAL));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    // Banner, sealing page and Panel read it at the same time.
    const { result } = renderHook(() => [useLegalStatus(), useLegalStatus(), useLegalStatus()], {
      wrapper,
    });
    await waitFor(() => {
      expect(result.current[0]?.data?.level).toBe('danger');
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.path).toBe('/api/v1/legal-status');
    expect(calls[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    await act(async () => {
      await client.invalidateQueries({ queryKey: LEGAL_STATUS_KEY });
    });
    expect(calls).toHaveLength(2);
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
  });
});

describe('useLegalStatus — refresh', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    act(() => {
      focusManager.setFocused(undefined);
    });
  });

  it('every 5 minutes in the background, not while the tab is hidden', async () => {
    const calls = installFetch(() => jsonResponse(200, LEGAL_PROVIDER_NOT_LEGAL));
    renderHook(() => useLegalStatus(), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(calls).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(LEGAL_STATUS_POLL_MS - 1_000));
    expect(calls).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(calls).toHaveLength(2);
    expect(calls[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');

    act(() => {
      focusManager.setFocused(false);
    });
    await act(() => vi.advanceTimersByTimeAsync(3 * LEGAL_STATUS_POLL_MS));
    expect(calls).toHaveLength(2);
  });

  it('only the banner polls: another reader (`poll: false`) adds no timer, only shares the answers', async () => {
    const calls = installFetch(() => jsonResponse(200, LEGAL_PROVIDER_NOT_LEGAL));
    const { result } = renderHook(
      () => [useLegalStatus(), useLegalStatus({ poll: false })] as const,
      { wrapper },
    );
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(calls).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(3 * LEGAL_STATUS_POLL_MS));
    expect(calls).toHaveLength(4);
    expect(result.current[1].dataUpdatedAt).toBe(result.current[0].dataUpdatedAt);
  });

  it('a reader alone with `poll: false` asks once', async () => {
    const calls = installFetch(() => jsonResponse(200, LEGAL_PROVIDER_NOT_LEGAL));
    renderHook(() => useLegalStatus({ poll: false }), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    await act(() => vi.advanceTimersByTimeAsync(3 * LEGAL_STATUS_POLL_MS));
    expect(calls).toHaveLength(1);
  });
});
