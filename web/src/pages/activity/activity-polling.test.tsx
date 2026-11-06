import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';

import { ACTIVITY_POLL_MS, useActivityDevices, useActivityWan } from '@/lib/api-hooks';
import { installFetch, jsonResponse, type RecordedRequest } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';

/* Canlı Durum polls both parts every 10 s (W5 §6 M3), with the window and range it shows. */

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
}

function count(calls: RecordedRequest[], path: string): RecordedRequest[] {
  return calls.filter((call) => call.path === `/api/v1${path}`);
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('asks both parts every 10 s with the window and the range', async () => {
  expect(ACTIVITY_POLL_MS).toBe(10_000);
  const calls = installFetch(() =>
    jsonResponse(200, { rows: [], lines: [], per_minute: [], totals: {} }),
  );
  renderHook(
    () => {
      useActivityDevices(15);
      useActivityWan('60m');
    },
    { wrapper },
  );
  await act(() => vi.advanceTimersByTimeAsync(10));
  expect(count(calls, '/activity/devices')).toHaveLength(1);
  expect(count(calls, '/activity/devices')[0]?.params.get('window')).toBe('15');
  expect(count(calls, '/activity/wan')[0]?.params.get('range')).toBe('60m');
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(count(calls, '/activity/devices')).toHaveLength(2);
  expect(count(calls, '/activity/wan')).toHaveLength(2);
});
