import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Me } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';

type Reply = Response | undefined;

/** /auth/me answers `me()` (401 while null); setup is done unless `needsSetup`. */
function fakeApi(
  me: () => Me | null,
  extra: (request: RecordedRequest) => Reply = () => undefined,
  needsSetup = false,
  idleMinutes = 15,
) {
  setCsrfCookie('csrf-1');
  return installFetch((request) => {
    const special = extra(request);
    if (special) return special;
    if (request.path === '/api/v1/auth/me') {
      const current = me();
      return current ? jsonResponse(200, current) : errorResponse(401, 'not_authenticated');
    }
    if (request.path === '/api/v1/auth/setup/status')
      return jsonResponse(200, {
        needs_setup: needsSetup,
        token_active: needsSetup,
        idle_timeout_minutes: idleMinutes,
      });
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

describe('sign-in: user name and password', () => {
  it('checks the fields before sending anything', async () => {
    const user = userEvent.setup();
    const calls = fakeApi(() => null);
    renderApp('/login');
    await user.click(await screen.findByRole('button', { name: 'Giriş yap' }));
    expect(screen.getAllByText('Bu alan zorunludur.')).toHaveLength(2);
    expect(screen.getByLabelText(/Kullanıcı adı/)).toHaveAttribute('aria-invalid', 'true');
    expect(calls.some((call) => call.path === '/api/v1/auth/login')).toBe(false);
  });

  it('shows the API message for a refused sign-in and clears the password', async () => {
    const user = userEvent.setup();
    const calls = fakeApi(
      () => null,
      (request) =>
        request.path === '/api/v1/auth/login'
          ? errorResponse(401, 'login_failed', {
              tr: 'Giriş başarısız: kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli.',
            })
          : undefined,
    );
    renderApp('/login');
    await user.type(await screen.findByLabelText(/Kullanıcı adı/), '  Tolga ');
    await user.type(screen.getByLabelText(/^Parola/), 'yanlış parola');
    await user.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/hesap geçici olarak kilitli/);
    expect(screen.getByLabelText(/^Parola/)).toHaveValue('');
    const login = calls.find((call) => call.path === '/api/v1/auth/login');
    expect(login?.body).toEqual({ username: 'Tolga', password: 'yanlış parola' });
    expect(login?.headers.get('X-CSRF-Token')).toBe('csrf-1');
  });

  it('shows the wait time of the rate limit', async () => {
    const user = userEvent.setup();
    fakeApi(
      () => null,
      (request) =>
        request.path === '/api/v1/auth/login'
          ? errorResponse(429, 'rate_limited', {
              tr: 'Çok fazla deneme yapıldı.',
              headers: { 'Retry-After': '30' },
            })
          : undefined,
    );
    renderApp('/login');
    await user.type(await screen.findByLabelText(/Kullanıcı adı/), 'tolga');
    await user.type(screen.getByLabelText(/^Parola/), 'x');
    await user.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Çok fazla deneme yapıldı. 30 saniye sonra tekrar deneyebilirsiniz.',
    );
  });

  it('goes to the first-run setup while no administrator exists', async () => {
    fakeApi(() => null, undefined, true);
    const { router } = renderApp('/login');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'İlk kurulum' }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/setup');
  });

  it('tells a fresh browser the idle minutes from the server (spec 12.3)', async () => {
    // New PC, private window, cleared storage: nothing remembered from an earlier sign-in.
    expect(window.localStorage.getItem('arci.idleMinutes')).toBeNull();
    fakeApi(() => null);
    renderApp('/login');
    expect(await screen.findByText('Oturum 15 dk hareketsizlikte kapanır.')).toBeInTheDocument();
    expect(screen.queryByText(/bir süre hareketsiz kalındığında/)).not.toBeInTheDocument();
  });

  it("prefers the server's current idle minutes over the remembered ones", async () => {
    window.localStorage.setItem('arci.idleMinutes', '20');
    fakeApi(() => null, undefined, false, 25);
    renderApp('/login');
    expect(await screen.findByText('Oturum 25 dk hareketsizlikte kapanır.')).toBeInTheDocument();
  });

  it('falls back to the remembered minutes when the status cannot be read', async () => {
    window.localStorage.setItem('arci.idleMinutes', '20');
    fakeApi(
      () => null,
      (request) =>
        request.path === '/api/v1/auth/setup/status'
          ? errorResponse(503, 'unavailable')
          : undefined,
    );
    renderApp('/login');
    expect(await screen.findByText('Oturum 20 dk hareketsizlikte kapanır.')).toBeInTheDocument();
  });
});

describe('sign-in: second factor', () => {
  it('validates the code format per mode and sends the normalised code', async () => {
    const user = userEvent.setup();
    let me: Me = makeMe('admin', { next: 'totp' });
    const calls = fakeApi(
      () => me,
      (request) => {
        if (request.path !== '/api/v1/auth/totp') return undefined;
        me = makeMe('admin');
        return jsonResponse(200, {
          next: 'done',
          mfa_passed: true,
          must_change_password: false,
          user: me.user,
        });
      },
    );
    const { router } = renderApp('/login?next=%2Faudit');
    const code = await screen.findByLabelText(/Doğrulama kodu/);
    await user.type(code, '12a');
    await user.click(screen.getByRole('button', { name: 'Doğrula' }));
    expect(screen.getByText('Kod 6 rakamdan oluşmalıdır.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Kurtarma kodu kullan' }));
    const recovery = screen.getByLabelText(/Kurtarma kodu/);
    await user.type(recovery, 'ABCD EFGH-JKMN pqrs');
    await user.click(screen.getByRole('button', { name: 'Doğrula' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/audit');
    });
    expect(calls.find((call) => call.path === '/api/v1/auth/totp')?.body).toEqual({
      code: 'abcdefghjkmnpqrs',
    });
  });

  it('goes back to the first step with a message when the account gets locked', async () => {
    const user = userEvent.setup();
    let me: Me | null = makeMe('auditor', { next: 'totp' });
    fakeApi(
      () => me,
      (request) => {
        if (request.path !== '/api/v1/auth/totp') return undefined;
        me = null; // the server closed the session
        return errorResponse(401, 'account_locked');
      },
    );
    renderApp('/login');
    await user.type(await screen.findByLabelText(/Doğrulama kodu/), '123456');
    await user.click(screen.getByRole('button', { name: 'Doğrula' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Giriş yap' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/hesabınız geçici olarak kilitlendi/);
  });

  it('keeps the step and explains a wrong code', async () => {
    const user = userEvent.setup();
    fakeApi(
      () => makeMe('auditor', { next: 'totp' }),
      (request) =>
        request.path === '/api/v1/auth/totp'
          ? errorResponse(401, 'totp_invalid', { tr: 'Doğrulama kodu hatalı veya süresi geçmiş.' })
          : undefined,
    );
    renderApp('/login');
    const code = await screen.findByLabelText(/Doğrulama kodu/);
    await user.type(code, '123456');
    await user.click(screen.getByRole('button', { name: 'Doğrula' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Doğrulama kodu hatalı');
    expect(screen.getByLabelText(/Doğrulama kodu/)).toHaveValue('');
  });
});

describe('forced password change', () => {
  it('checks length and confirmation, then shows every rule the server refused', async () => {
    const user = userEvent.setup();
    const calls = fakeApi(
      () => makeMe('viewer', { next: 'password_change' }),
      (request) =>
        request.path === '/api/v1/auth/password'
          ? errorResponse(422, 'password_rejected', {
              details: [
                { rule: 'common', message_tr: 'Parola çok yaygın.', message_en: 'Too common.' },
                { rule: 'name', message_tr: 'Adınızı içeriyor.', message_en: 'Contains name.' },
              ],
            })
          : undefined,
    );
    renderApp('/login');
    await user.type(await screen.findByLabelText(/Mevcut \(geçici\) parola/), 'Gecici-Parola-1');
    await user.type(screen.getByLabelText(/^Yeni parola\*?$/), 'kısa');
    await user.type(screen.getByLabelText(/Yeni parola \(tekrar\)/), 'başka');
    await user.click(screen.getByRole('button', { name: 'Parolayı değiştir' }));
    expect(screen.getByText('Parola en az 12 karakter olmalıdır.')).toBeInTheDocument();
    expect(screen.getByText('Parolalar aynı değil.')).toBeInTheDocument();
    expect(calls.some((call) => call.path === '/api/v1/auth/password')).toBe(false);

    await user.clear(screen.getByLabelText(/^Yeni parola\*?$/));
    await user.type(screen.getByLabelText(/^Yeni parola\*?$/), 'Galatasaray1905!');
    await user.clear(screen.getByLabelText(/Yeni parola \(tekrar\)/));
    await user.type(screen.getByLabelText(/Yeni parola \(tekrar\)/), 'Galatasaray1905!');
    await user.click(screen.getByRole('button', { name: 'Parolayı değiştir' }));
    expect(await screen.findByText('Parola çok yaygın. Adınızı içeriyor.')).toBeInTheDocument();
    expect(calls.find((call) => call.path === '/api/v1/auth/password')?.body).toEqual({
      current_password: 'Gecici-Parola-1',
      new_password: 'Galatasaray1905!',
    });
  });
});
