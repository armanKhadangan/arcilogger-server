import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';

import { RULES_KEY } from '@/pages/live/live-model';

import type { Me } from './api-types';
import { BACKGROUND_HEADER } from './api';
import { IDLE_CHECK_MS } from './auth';

const NO_SETUP = { needs_setup: false, token_active: false, idle_timeout_minutes: 15 };

/** A small fake API: /auth/me answers `me` (or an error), the rest per `extra`. */
function fakeApi(
  me: Me | Response,
  extra: (request: RecordedRequest) => Response | undefined = () => undefined,
) {
  return installFetch((request) => {
    const special = extra(request);
    if (special) return special;
    if (request.path === '/api/v1/auth/me')
      return me instanceof Response ? me.clone() : jsonResponse(200, me);
    if (request.path === '/api/v1/auth/setup/status') return jsonResponse(200, NO_SETUP);
    return errorResponse(404, 'not_found');
  });
}

function location(router: ReturnType<typeof renderApp>['router']): string {
  const { pathname, search } = router.state.location;
  return `${pathname}${search}`;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setCsrfCookie(null);
});

describe('RequireAuth (route guard)', () => {
  it('sends a visitor without a session to the sign-in page, keeping the target', async () => {
    fakeApi(errorResponse(401, 'not_authenticated'));
    const { router } = renderApp('/audit?actor=tolga');
    await waitFor(() => {
      expect(location(router)).toBe('/login?next=%2Faudit%3Factor%3Dtolga');
    });
    expect(await screen.findByRole('heading', { level: 1, name: 'Giriş yap' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('explains an idle timeout on the sign-in page', async () => {
    fakeApi(errorResponse(401, 'session_idle'));
    const { router } = renderApp('/settings/users');
    await waitFor(() => {
      expect(location(router)).toBe('/login?next=%2Fsettings%2Fusers&reason=session_idle');
    });
    expect(
      await screen.findByText('Oturumunuz zaman aşımına uğradı. Lütfen yeniden giriş yapın.'),
    ).toBeInTheDocument();
  });

  it.each([
    ['totp', 'İki adımlı doğrulama'],
    ['password_change', 'Parolanızı değiştirin'],
  ] as const)('sends a session at step %s back to that sign-in step', async (next, heading) => {
    fakeApi(makeMe('admin', { next }));
    const { router } = renderApp('/audit');
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
  });

  it('lets a complete sign-in through to the page', async () => {
    fakeApi(makeMe('auditor'));
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: 'Panel' })).toBeInTheDocument();
  });

  it('offers a retry when the API is down (503), without leaving the page', async () => {
    fakeApi(errorResponse(503, 'unavailable', { tr: 'Hizmet şu anda kullanılamıyor.' }));
    renderApp('/');
    expect(await screen.findByText('Hizmet şu anda kullanılamıyor.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tekrar dene' })).toBeInTheDocument();
  });
});

describe('global reactions inside the app', () => {
  it('a 401 from any request empties the cache and shows the timeout on sign-in', async () => {
    let expired = false;
    fakeApi(makeMe('admin'), (request) => {
      if (request.path === '/api/v1/audit/verify' || request.path === '/api/v1/audit') {
        expired = true;
        return errorResponse(401, 'session_expired');
      }
      if (expired && request.path === '/api/v1/auth/me')
        return errorResponse(401, 'session_revoked');
      return undefined;
    });
    const { router, client } = renderApp('/audit');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login');
    });
    expect(router.state.location.search).toContain('reason=session_expired');
    expect(router.state.location.search).toContain('next=%2Faudit');
    expect(
      await screen.findByText(/Oturumunuz zaman aşımına uğradı \(en uzun oturum süresi doldu\)/),
    ).toBeInTheDocument();
    expect(client.getQueryData(['audit', 'entries', {}])).toBeUndefined();
  });

  it('signing out or a 401 leaves no Canlı Akış highlight rule in the tab (R14)', async () => {
    const rules = JSON.stringify([{ match: 'srcip:172.20.41.105', color: 'amber' }]);
    window.sessionStorage.setItem(RULES_KEY, rules);
    let ended = false;
    fakeApi(makeMe('admin'), (request) => {
      if (request.path === '/api/v1/auth/logout') {
        ended = true;
        return new Response(null, { status: 204 });
      }
      if (ended && request.path === '/api/v1/auth/me')
        return errorResponse(401, 'not_authenticated');
      return undefined;
    });
    const user = userEvent.setup();
    const first = renderApp('/');
    await user.click(await screen.findByRole('button', { name: /Kullanıcı menüsü/ }));
    await user.click(await screen.findByRole('menuitem', { name: 'Çıkış yap' }));
    await waitFor(() => {
      expect(first.router.state.location.pathname).toBe('/login');
    });
    expect(window.sessionStorage.getItem(RULES_KEY)).toBeNull();
    first.unmount();

    window.sessionStorage.setItem(RULES_KEY, rules);
    fakeApi(makeMe('admin'), (request) =>
      request.path === '/api/v1/audit' ? errorResponse(401, 'session_idle') : undefined,
    );
    const second = renderApp('/audit');
    await waitFor(() => {
      expect(second.router.state.location.pathname).toBe('/login');
    });
    expect(window.sessionStorage.getItem(RULES_KEY)).toBeNull();
  });

  it('403 password_change_required goes to the password change', async () => {
    let changed = false;
    fakeApi(makeMe('admin'), (request) => {
      if (request.path === '/api/v1/audit/verify') {
        changed = true;
        return errorResponse(403, 'password_change_required');
      }
      if (changed && request.path === '/api/v1/auth/me')
        return jsonResponse(200, makeMe('admin', { next: 'password_change' }));
      return undefined;
    });
    const { router } = renderApp('/audit');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Parolanızı değiştirin' }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/login');
  });

  it('asks the server in the background once the idle time has passed', async () => {
    const intervals: (() => void)[] = [];
    const realSetInterval = window.setInterval.bind(window);
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: () => void, ms?: number) => {
      if (ms === IDLE_CHECK_MS) intervals.push(handler);
      return realSetInterval(handler, ms);
    }) as typeof window.setInterval);
    const calls = fakeApi(makeMe('viewer', { idle_timeout_minutes: 15 }));
    renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Panel' });
    // The idle watch, and the Panel's own 30-second refresh of its live blocks (background
    // requests: they never count as activity, so ticking them too changes nothing here).
    expect(intervals.length).toBeGreaterThanOrEqual(1);
    const tick = () => {
      for (const handler of intervals) handler();
    };
    const meCalls = () => calls.filter((call) => call.path === '/api/v1/auth/me');
    const before = meCalls().length;

    const start = Date.now();
    vi.spyOn(Date, 'now').mockReturnValue(start + 10 * 60_000);
    tick(); // 10 minutes: nothing to ask yet
    expect(meCalls()).toHaveLength(before);

    vi.spyOn(Date, 'now').mockReturnValue(start + 16 * 60_000);
    tick();
    await waitFor(() => {
      expect(meCalls()).toHaveLength(before + 1);
    });
    expect(meCalls().at(-1)?.headers.get(BACKGROUND_HEADER)).toBe('1');
  });
});

describe('KVKK notice', () => {
  it('blocks the app until accepted, then records the acceptance', async () => {
    const user = userEvent.setup();
    setCsrfCookie('csrf-1');
    let accepted = false;
    const calls = fakeApi(makeMe('hr'), (request) => {
      if (request.path === '/api/v1/auth/me')
        return jsonResponse(
          200,
          makeMe('hr', {
            privacy_notice: { version: '3', accepted, required: true },
            next: accepted ? 'done' : 'privacy_notice',
          }),
        );
      if (request.path === '/api/v1/auth/privacy-notice') {
        accepted = true;
        return jsonResponse(200, { version: '3', accepted: true, required: true });
      }
      return undefined;
    });
    renderApp('/');
    const dialog = await screen.findByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    expect(within(dialog).getByText('Taslak — hukuk müşaviri onayı gerekir')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Kapat' })).not.toBeInTheDocument();
    const accept = within(dialog).getByRole('button', { name: 'Onaylıyorum' });
    expect(accept).toBeDisabled();
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: /KVKK/ })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(accept);
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /KVKK/ })).not.toBeInTheDocument();
    });
    const post = calls.find((call) => call.path === '/api/v1/auth/privacy-notice');
    expect(post?.body).toEqual({ version: '3' });
    expect(post?.headers.get('X-CSRF-Token')).toBe('csrf-1');
  });

  it('is not shown to roles that see no person-level data', async () => {
    fakeApi(
      makeMe('viewer', { privacy_notice: { version: '1', accepted: false, required: false } }),
    );
    renderApp('/');
    await screen.findByRole('heading', { level: 1, name: 'Panel' });
    act(() => undefined);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
