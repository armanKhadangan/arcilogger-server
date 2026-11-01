import { renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';

import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

import { handoff, useHandoff, type HandoffPayloads } from './handoff';

const QUERY: HandoffPayloads['/search'] = { q: 'ip:172.20.41.23', range: { preset: '24h' } };
const EXTRACT: HandoffPayloads['/evidence'] = {
  extract: [{ day: '2026-10-09', source: 'fortigate', line: 1234 }],
  from_day: '2026-10-09',
  to_day: '2026-10-09',
  truncated: false,
  complete: true,
  unsealed_days: [],
  query: 'ip:172.20.41.23',
};
const PREFILL: HandoffPayloads['/settings/sources'] = {
  prefill: { name: 'switch-3', type: 'generic', address: '172.20.10.13' },
};

afterEach(() => {
  handoff.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('handoff (in memory, one-shot)', () => {
  it('gives each page what was left for it, once', () => {
    handoff.put('/search', QUERY);
    handoff.put('/evidence', EXTRACT);
    handoff.put('/settings/sources', PREFILL);
    expect(handoff.take('/search')).toEqual(QUERY);
    expect(handoff.take('/search')).toBeNull();
    expect(handoff.take('/settings/sources')).toEqual(PREFILL);
    expect(handoff.take('/evidence')).toEqual(EXTRACT);
    expect(handoff.take('/evidence')).toBeNull();
  });

  it('keeps only the latest entry a page has not taken yet', () => {
    handoff.put('/search', { q: 'mac:00:00:5E:00:53:17' });
    handoff.put('/search', QUERY);
    expect(handoff.take('/search')).toEqual(QUERY);
    expect(handoff.take('/search')).toBeNull();
  });

  it('forgets everything on clear', () => {
    handoff.put('/search', QUERY);
    handoff.put('/evidence', EXTRACT);
    handoff.clear();
    expect(handoff.take('/search')).toBeNull();
    expect(handoff.take('/evidence')).toBeNull();
  });

  it('never writes the address, the session history or browser storage', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const pushState = vi.spyOn(window.history, 'pushState');
    const replaceState = vi.spyOn(window.history, 'replaceState');
    const href = window.location.href;
    const state: unknown = window.history.state;
    handoff.put('/search', QUERY);
    handoff.take('/search');
    handoff.put('/evidence', EXTRACT);
    expect(setItem).not.toHaveBeenCalled();
    expect(pushState).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalled();
    expect(window.location.href).toBe(href);
    expect(window.history.state).toBe(state);
    expect(JSON.stringify(window.sessionStorage)).not.toContain('172.20.41.23');
    expect(JSON.stringify(window.localStorage)).not.toContain('172.20.41.23');
  });
});

describe('useHandoff', () => {
  it('delivers what was left before the page opened exactly once, also under StrictMode', () => {
    handoff.put('/search', QUERY);
    const receive = vi.fn();
    renderHook(() => useHandoff('/search', receive), { wrapper: StrictMode });
    expect(receive.mock.calls).toEqual([[QUERY]]);
    expect(handoff.take('/search')).toBeNull();
  });

  it('delivers what arrives while the page is open, to that page only', () => {
    const search = vi.fn();
    const sources = vi.fn();
    renderHook(() => {
      useHandoff('/search', search);
      useHandoff('/settings/sources', sources);
    });
    expect(search).not.toHaveBeenCalled();
    handoff.put('/search', QUERY);
    handoff.put('/search', { q: 'domain:example.com' });
    expect(search.mock.calls).toEqual([[QUERY], [{ q: 'domain:example.com' }]]);
    expect(sources).not.toHaveBeenCalled();
    expect(handoff.take('/search')).toBeNull();
  });

  it('uses the latest receive function and stops receiving once the page is closed', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender, unmount } = renderHook(
      ({ receive }: { receive: () => void }) => {
        useHandoff('/evidence', receive);
      },
      { initialProps: { receive: first } },
    );
    rerender({ receive: second });
    handoff.put('/evidence', EXTRACT);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    unmount();
    handoff.put('/evidence', EXTRACT);
    expect(second).toHaveBeenCalledTimes(1);
    expect(handoff.take('/evidence')).toEqual(EXTRACT); // left for the next time it opens
  });
});

describe('the top bar’s search box', () => {
  it('hands its term to Arama in memory: not in router state, not in the address', async () => {
    const user = userEvent.setup();
    const calls = installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : errorResponse(404, 'not_found'),
    );
    const { router } = renderApp('/audit');
    const box = await screen.findByRole('searchbox', { name: 'Genel arama' });
    await user.type(box, '172.20.41.23{Enter}');
    await screen.findByRole('heading', { level: 1, name: 'Arama' });
    const { location } = router.state;
    expect([location.pathname, location.search, location.hash]).toEqual(['/search', '', '']);
    expect(location.state as unknown).toBeNull();
    // Arama took the entry (once) as a query, and searched it in a request body.
    expect(await screen.findByRole('combobox', { name: 'Sorgu' })).toHaveValue('ip:172.20.41.23');
    await waitFor(() => {
      expect(
        calls.find((call) => call.method === 'POST' && call.path === '/api/v1/search')?.body,
      ).toMatchObject({ q: 'ip:172.20.41.23' });
    });
    expect(handoff.take('/search')).toBeNull();
  });

  it('opens Arama with nothing handed over for an empty box', async () => {
    const user = userEvent.setup();
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : errorResponse(404, 'not_found'),
    );
    const { router } = renderApp('/audit');
    await user.type(await screen.findByRole('searchbox', { name: 'Genel arama' }), '  {Enter}');
    await screen.findByRole('heading', { level: 1, name: 'Arama' });
    expect(router.state.location.pathname).toBe('/search');
    expect(handoff.take('/search')).toBeNull();
  });
});

describe('a hand-over with a range', () => {
  it.each([false, true])(
    'Arama searches it once, on its range, and the address holds it (StrictMode %s)',
    async (strict) => {
      const calls = installFetch((request) =>
        request.path === '/api/v1/auth/me'
          ? jsonResponse(200, makeMe('admin'))
          : errorResponse(404, 'not_found'),
      );
      handoff.put('/search', { q: 'source:fortigate', range: { preset: 'today' } });
      const { router } = renderApp('/search', { strict });
      // Arama has answered (this mock refuses the search).
      expect(await screen.findByText('TR not_found')).toBeInTheDocument();
      // The address's default range (the last 24 h) does not run it again.
      const searches = calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/search');
      expect(searches.map((call) => call.body)).toEqual([
        {
          q: 'source:fortigate',
          from: expect.stringMatching(/T00:00:00\+03:00$/) as string,
          to: expect.any(String) as string,
        },
      ]);
      expect(router.state.location.search).toBe('?range=today');
      expect(router.state.location.state).toBeNull();
    },
  );
});

/*
 * Signing out, or a session the server ended, must not leave a query (an IP, a MAC, a name) for
 * whoever signs in next on the same browser.
 */
describe('handoff and the end of a session', () => {
  it('"Çıkış yap" forgets every entry', async () => {
    const user = userEvent.setup();
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : errorResponse(404, 'not_found'),
    );
    const { router } = renderApp('/search');
    await screen.findByRole('heading', { level: 1, name: 'Arama' });
    handoff.put('/evidence', EXTRACT);
    handoff.put('/settings/sources', PREFILL);
    await user.click(screen.getByRole('button', { name: /Kullanıcı menüsü/ }));
    await user.click(await screen.findByRole('menuitem', { name: 'Çıkış yap' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login');
    });
    expect(handoff.take('/evidence')).toBeNull();
    expect(handoff.take('/settings/sources')).toBeNull();
  });

  it('a session the server ended (401) forgets every entry', async () => {
    let ended = false;
    installFetch((request) => {
      if (request.path === '/api/v1/auth/me')
        return ended ? errorResponse(401, 'session_idle') : jsonResponse(200, makeMe('admin'));
      if (request.path.startsWith('/api/v1/audit')) {
        ended = true;
        return errorResponse(401, 'session_idle');
      }
      return errorResponse(404, 'not_found');
    });
    handoff.put('/search', QUERY);
    const { router } = renderApp('/audit');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/login');
    });
    expect(handoff.take('/search')).toBeNull();
  });
});
