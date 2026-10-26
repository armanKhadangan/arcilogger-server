import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';

import { AuthProvider } from '@/lib/auth';
import { ReauthProvider } from '@/lib/reauth';
import type {
  AdminAccepted,
  AdminCheck,
  AdminConfig,
  AdminRequestOut,
  AdminStatus,
  SmtpBody,
} from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe, setCsrfCookie } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';

import { useMachineSection } from './use-machine-section';

/*
 * A section's save flow forgets a typed credential (contract 2.17 "secret fields", R7): once the
 * dry run and the change are answered, neither the query client's mutation cache nor its query
 * cache nor the flow holds the body that carried it.
 */

const PASSWORD = 'yeni-smtp-degeri'; // gitleaks:allow — fake test value
const ID = '6f1c1a2b'.repeat(4);

const CONFIG = {
  etags: { smtp: 'smtp:1' },
  sections: {},
  locked: [],
  pending: [],
} as unknown as AdminConfig;
const STATUS = { agent: { state: 'ok' }, restart_required: false } as unknown as AdminStatus;
const CHECK: AdminCheck = {
  changes: { 'smtp.password': { secret: true, old: 'set', new: 'set', changed: true } },
  effects: ['reload.worker'],
  warnings: [],
  guarded: false,
  effective_at: null,
  reauth_required: true,
};
const ACCEPTED = {
  request_id: ID,
  short_id: ID.slice(0, 8),
  state: 'queued',
  status_url: `/api/v1/admin/requests/${ID}`,
  guarded: false,
  effective_at: null,
  changes: CHECK.changes,
  effects: CHECK.effects,
  warnings: [],
} as AdminAccepted;
const ACTIVE = {
  id: ID,
  short_id: ID.slice(0, 8),
  kind: 'config.apply',
  section: 'smtp',
  state: 'active',
  terminal: true,
  label_tr: 'Uygulandı',
  label_en: 'Applied',
  steps: [],
  changes: CHECK.changes,
  warnings: [],
} as unknown as AdminRequestOut;
const BODY: SmtpBody = {
  etag: 'smtp:1',
  enabled: true,
  host: 'smtp.ornek.example',
  port: 587,
  security: 'starttls',
  username: 'arcilogger',
  from_address: 'arcilogger@ornek.example',
  timeout_seconds: 30,
  password: PASSWORD,
  probe: true,
};

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AuthProvider me={makeMe('admin')}>
            <ReauthProvider>{children}</ReauthProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };
}

/** Everything the query client still holds: mutation variables and query data. */
function cached(client: QueryClient): string {
  return JSON.stringify([
    client
      .getMutationCache()
      .getAll()
      .map((mutation) => mutation.state.variables),
    client
      .getQueryCache()
      .getAll()
      .map((query) => query.state.data),
  ]);
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

describe('useMachineSection', () => {
  it('check → confirm → apply with a new password leaves no credential behind', async () => {
    setCsrfCookie('csrf-1');
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/admin/config') return jsonResponse(200, CONFIG);
      if (request.path === '/api/v1/admin/status') return jsonResponse(200, STATUS);
      if (request.path === '/api/v1/admin/config/smtp/check') return jsonResponse(200, CHECK);
      if (request.method === 'PUT' && request.path === '/api/v1/admin/config/smtp')
        return jsonResponse(202, ACCEPTED);
      if (request.path === `/api/v1/admin/requests/${ID}`) return jsonResponse(200, ACTIVE);
      return errorResponse(404, 'not_found');
    });
    const client = testQueryClient();
    const { result } = renderHook(() => useMachineSection('smtp'), {
      wrapper: wrapperFor(client),
    });
    await waitFor(() => {
      expect(result.current.etag).toBe('smtp:1');
    });

    await act(async () => {
      await result.current.flow.start(BODY);
    });
    expect(result.current.flow.phase).toBe('confirm');
    expect(result.current.flow.check).toEqual(CHECK);
    // Only the flow keeps the body until it is confirmed; the dry run's mutation forgot it.
    await waitFor(() => {
      expect(cached(client)).not.toContain(PASSWORD);
    });

    await act(async () => {
      await result.current.flow.confirm();
    });
    await waitFor(() => {
      expect(result.current.flow.request?.state).toBe('active');
    });
    expect(result.current.flow.body).toBeNull();
    await waitFor(() => {
      expect(cached(client)).not.toContain(PASSWORD);
    });
    expect(
      calls
        .filter((call) => JSON.stringify(call.body ?? null).includes(PASSWORD))
        .map((call) => `${call.method} ${call.path}`),
    ).toEqual(['POST /api/v1/admin/config/smtp/check', 'PUT /api/v1/admin/config/smtp']);
  });
});
