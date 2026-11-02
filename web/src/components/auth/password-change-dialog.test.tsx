import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { SignInState } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';

const CURRENT = 'mavi bisiklet kırk iki limon';
const NEW = 'yeşil tren yirmi üç portakal';

/** Signed in as an auditor; `password` answers `POST /auth/password`, `signedIn` `/auth/me`. */
function fakeApi(password: (request: RecordedRequest) => Response, signedIn = () => true) {
  setCsrfCookie('csrf-1');
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me')
      return signedIn()
        ? jsonResponse(200, makeMe('auditor'))
        : errorResponse(401, 'not_authenticated');
    if (request.path === '/api/v1/auth/password') return password(request);
    if (request.path === '/api/v1/auth/setup/status')
      return jsonResponse(200, {
        needs_setup: false,
        token_active: false,
        idle_timeout_minutes: 15,
      });
    return errorResponse(404, 'not_found');
  });
}

function changed(): SignInState {
  const me = makeMe('auditor');
  return { next: 'done', mfa_passed: true, must_change_password: false, user: me.user };
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /Kullanıcı menüsü/ }));
  await user.click(await screen.findByRole('menuitem', { name: 'Parolayı değiştir' }));
  return screen.findByRole('dialog', { name: 'Parolayı değiştir' });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

describe('user menu: change the own password', () => {
  it('asks for the own current password, shows a refusal, then changes it', async () => {
    const user = userEvent.setup();
    const calls = fakeApi((request) =>
      (request.body as { current_password: string }).current_password === CURRENT
        ? jsonResponse(200, changed())
        : errorResponse(403, 'credentials_invalid', { tr: 'Mevcut parola hatalı.' }),
    );
    // Arama runs no search when it opens; its catalogue and saved searches answer 404 here.
    renderApp('/search');
    const dialog = await openDialog(user);
    expect(
      within(dialog).getByText(/Diğer tarayıcı ve cihazlardaki oturumlarınız kapatılır/),
    ).toBeInTheDocument();
    // The own password, not the temporary one of the forced change after sign-in.
    expect(within(dialog).queryByLabelText(/geçici/)).not.toBeInTheDocument();
    const current = within(dialog).getByLabelText(/^Mevcut parola/);
    expect(current).toHaveFocus();

    await user.type(current, 'yanlış parola bir iki üç');
    await user.type(within(dialog).getByLabelText(/^Yeni parola\*?$/), NEW);
    await user.type(within(dialog).getByLabelText(/Yeni parola \(tekrar\)/), NEW);
    await user.click(within(dialog).getByRole('button', { name: 'Parolayı değiştir' }));
    expect(await within(dialog).findByText('Mevcut parola hatalı.')).toBeInTheDocument();
    expect(current).toHaveValue('');

    const meCalls = calls.filter((call) => call.path === '/api/v1/auth/me').length;
    await user.type(current, CURRENT);
    await user.click(within(dialog).getByRole('button', { name: 'Parolayı değiştir' }));
    expect(
      await screen.findByText('Parolanız değiştirildi. Diğer oturumlarınız kapatıldı.'),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Kullanıcı menüsü/ })).toHaveFocus();
    const sent = calls.filter((call) => call.path === '/api/v1/auth/password');
    expect(sent.map((call) => call.body)).toEqual([
      { current_password: 'yanlış parola bir iki üç', new_password: NEW },
      { current_password: CURRENT, new_password: NEW },
    ]);
    expect(sent[1]?.headers.get('X-CSRF-Token')).toBe('csrf-1');
    // The session was renewed: the app reads /auth/me again.
    expect(calls.filter((call) => call.path === '/api/v1/auth/me').length).toBeGreaterThan(meCalls);
    expect(screen.getByRole('heading', { level: 1, name: 'Arama' })).toBeInTheDocument();
  });

  it('checks the new password in the browser before asking the server', async () => {
    const user = userEvent.setup();
    const calls = fakeApi(() => jsonResponse(200, changed()));
    renderApp('/search');
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/^Mevcut parola/), CURRENT);
    await user.type(within(dialog).getByLabelText(/^Yeni parola\*?$/), CURRENT);
    await user.type(within(dialog).getByLabelText(/Yeni parola \(tekrar\)/), CURRENT);
    await user.click(within(dialog).getByRole('button', { name: 'Parolayı değiştir' }));
    expect(
      within(dialog).getByText('Yeni parola mevcut paroladan farklı olmalıdır.'),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.path === '/api/v1/auth/password')).toBe(false);
  });

  it('a locked account (too many wrong passwords) goes to sign-in with the reason', async () => {
    const user = userEvent.setup();
    let signedIn = true;
    fakeApi(
      () => {
        signedIn = false; // the server ended the session
        return errorResponse(401, 'account_locked');
      },
      () => signedIn,
    );
    const { router } = renderApp('/search');
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/^Mevcut parola/), 'yanlış parola bir iki üç');
    await user.type(within(dialog).getByLabelText(/^Yeni parola\*?$/), NEW);
    await user.type(within(dialog).getByLabelText(/Yeni parola \(tekrar\)/), NEW);
    await user.click(within(dialog).getByRole('button', { name: 'Parolayı değiştir' }));
    expect(
      await screen.findByText(/Çok fazla hatalı deneme: hesabınız geçici olarak kilitlendi/),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
    expect(router.state.location.search).toBe('?next=%2Fsearch&reason=account_locked');
  });

  it('"İptal" closes the dialog without a request', async () => {
    const user = userEvent.setup();
    const calls = fakeApi(() => jsonResponse(200, changed()));
    renderApp('/search');
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(calls.some((call) => call.path === '/api/v1/auth/password')).toBe(false);
  });
});
