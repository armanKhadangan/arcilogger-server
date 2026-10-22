import { QueryClient } from '@tanstack/react-query';

import { ApiError } from './api';

/**
 * Retry only what may succeed on a second try: network failures and 5xx. A 4xx answer (no
 * session, no permission, invalid input) is final, and repeating it would only add log noise.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: shouldRetry },
      mutations: { retry: false },
    },
  });
}
