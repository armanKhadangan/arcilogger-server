import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';

import { errorResponse, installFetch, jsonResponse, makeMe, setCsrfCookie } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';

import { api } from './api';
import { AuthProvider } from './auth';
import { isReauthCancelled, ReauthProvider, useReauth } from './reauth';

/** A button that runs a sensitive request through `withReauth` and shows the outcome. */
function Sensitive() {
  const { withReauth } = useReauth();
  const [outcome, setOutcome] = useState('');
  return (
    <>
      <button
        type="button"
        onClick={() => {
          withReauth(() => api.post<{ ok: boolean }>('/users/7/reset-totp'))
            .then(() => {
              setOutcome('done');
            })
            .catch((error: unknown) => {
              setOutcome(isReauthCancelled(error) ? 'cancelled' : 'failed');
            });
        }}
      >
        run
      </button>
      <output>{outcome}</output>
    </>
  );
}

function renderSensitive(totp: boolean) {
  const me = makeMe('admin', {
    mfa: { passed: true, enabled: totp, required: totp, recovery_codes_left: 9 },
  });
  render(
    <QueryClientProvider client={testQueryClient()}>
      <MemoryRouter>
        <AuthProvider me={me}>
          <ReauthProvider>
            <Sensitive />
          </ReauthProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

describe('withReauth', () => {
  it('asks for password and TOTP on 403 reauth_required, then repeats the action once', async () => {
    const user = userEvent.setup();
    setCsrfCookie('csrf-1');
    let confirmed = false;
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/auth/reauth') {
        const body = request.body as { password: string; code: string | null };
        if (body.password !== 'doğru parola')
          return errorResponse(403, 'credentials_invalid', {
            tr: 'Parola veya doğrulama kodu hatalı.',
          });
        confirmed = true;
        return jsonResponse(200, { reauthenticated_at: '2026-10-12T10:00:00Z', valid_minutes: 5 });
      }
      if (request.path === '/api/v1/users/7/reset-totp')
        return confirmed ? jsonResponse(200, { ok: true }) : errorResponse(403, 'reauth_required');
      return errorResponse(404, 'not_found');
    });
    renderSensitive(true);
    await user.click(screen.getByRole('button', { name: 'run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Kimliğinizi doğrulayın' });

    await user.type(within(dialog).getByLabelText(/^Parola/), 'yanlış');
    await user.type(within(dialog).getByLabelText(/Doğrulama kodu/), '12 34 56');
    await user.click(within(dialog).getByRole('button', { name: 'Doğrula' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Parola veya doğrulama kodu hatalı.',
    );

    await user.type(within(dialog).getByLabelText(/^Parola/), 'doğru parola');
    await user.type(within(dialog).getByLabelText(/Doğrulama kodu/), '654321');
    await user.click(within(dialog).getByRole('button', { name: 'Doğrula' }));
    await waitFor(() => {
      expect(screen.getByRole('status', { hidden: true })).toHaveTextContent('done');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const reauths = calls.filter((call) => call.path === '/api/v1/auth/reauth');
    expect(reauths.map((call) => call.body)).toEqual([
      { password: 'yanlış', code: '123456' },
      { password: 'doğru parola', code: '654321' },
    ]);
    expect(calls.filter((call) => call.path === '/api/v1/users/7/reset-totp')).toHaveLength(2);
  });

  it('without TOTP asks only for the password; cancelling rejects quietly', async () => {
    const user = userEvent.setup();
    setCsrfCookie('csrf-1');
    const calls = installFetch(() => errorResponse(403, 'reauth_required'));
    renderSensitive(false);
    await user.click(screen.getByRole('button', { name: 'run' }));
    const dialog = await screen.findByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
    expect(within(dialog).queryByLabelText(/Doğrulama kodu/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    await waitFor(() => {
      expect(screen.getByRole('status', { hidden: true })).toHaveTextContent('cancelled');
    });
    expect(calls).toHaveLength(1);
  });

  it('passes other errors through without a dialog', async () => {
    const user = userEvent.setup();
    setCsrfCookie('csrf-1');
    installFetch(() => errorResponse(409, 'last_admin'));
    renderSensitive(true);
    await user.click(screen.getByRole('button', { name: 'run' }));
    await waitFor(() => {
      expect(screen.getByRole('status', { hidden: true })).toHaveTextContent('failed');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
