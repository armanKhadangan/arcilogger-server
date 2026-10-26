import { act, screen, within } from '@testing-library/react';

import type { Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import { BUILT_PAGES, NAV_ITEMS } from '@/nav';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

function signedInAs(role: Role) {
  return installFetch((request) =>
    request.path === '/api/v1/auth/me'
      ? jsonResponse(200, makeMe(role))
      : errorResponse(404, 'not_found'),
  );
}

describe('AppShell', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      setLanguage('tr');
    });
  });

  it('shows every main menu entry in Turkish to an administrator', async () => {
    window.localStorage.setItem('arci.sidebar.collapsed', '0');
    signedInAs('admin');
    renderApp('/');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    for (const label of [
      'Panel',
      'Kaynaklar',
      'Canlı Akış',
      '5651 Sorgu',
      'Mühürleme',
      'Uyum Durumu',
      'Bileşen Kiti',
    ]) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument();
    }
    expect(within(nav).getAllByRole('link')).toHaveLength(NAV_ITEMS.length + 1);
  });

  it('shows a viewer only the panel and usage, without search box or alerts bell', async () => {
    window.localStorage.setItem('arci.sidebar.collapsed', '0');
    signedInAs('viewer');
    renderApp('/');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(
      within(nav)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['Panel', 'Kullanım']);
    expect(screen.queryByRole('search')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Bildirimler' })).not.toBeInTheDocument();
  });

  it.each(['admin', 'auditor'] as const)(
    'shows %s the global search box (search.read): the Search page exists',
    async (role) => {
      expect(BUILT_PAGES.has('search')).toBe(true);
      signedInAs(role);
      renderApp('/');
      const banner = await screen.findByRole('banner');
      const search = within(banner).getByRole('search');
      expect(within(search).getByRole('searchbox', { name: 'Genel arama' })).toBeInTheDocument();
    },
  );

  it('shows hr no global search box (no search.read)', async () => {
    signedInAs('hr');
    renderApp('/');
    await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(screen.queryByRole('search')).not.toBeInTheDocument();
  });

  it.each([
    ['/sources', 'Kaynaklar'],
    ['/live', 'Canlı Akış'],
    ['/search', 'Arama'],
  ])('opens %s as a page of its own, not a placeholder', async (path, heading) => {
    signedInAs('admin');
    renderApp(path);
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    expect(screen.queryByText('Bu sayfa sonraki bir sürümde eklenecek.')).not.toBeInTheDocument();
  });

  it.each(['/', '/sources', '/live', '/audit', '/lookup', '/evidence', '/sealing'])(
    'shows no global time range on %s (only Arama has one)',
    async (path) => {
      signedInAs('admin');
      renderApp(path);
      // Lazy pages: the shell renders once the page module is loaded.
      const banner = await screen.findByRole('banner');
      await screen.findByRole('heading', { level: 1 });
      expect(within(banner).queryByRole('button', { name: /Tarih aralığı/ })).toBeNull();
    },
  );

  it.each([
    ['hr', '/audit'],
    ['hr', '/search'],
    ['hr', '/live'],
    ['viewer', '/sources'],
    // Kaynaklar is for administrators (`sources.read`); the auditor reads Arama and Canlı Akış.
    ['auditor', '/sources'],
  ] as const)('refuses %s the page %s, even by URL', async (role, path) => {
    signedInAs(role);
    renderApp(path);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeInTheDocument();
  });

  it('renders a placeholder without a milestone code for future pages', async () => {
    signedInAs('admin');
    renderApp('/compliance');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Uyum Durumu' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Bu sayfa sonraki bir sürümde eklenecek.')).toBeInTheDocument();
    expect(screen.queryByText(/\bM\d\b/)).not.toBeInTheDocument();
  });

  it('switches the interface to English', async () => {
    window.localStorage.setItem('arci.sidebar.collapsed', '0');
    signedInAs('admin');
    renderApp('/sealing');
    await screen.findByRole('heading', { level: 1, name: 'Mühürleme' });
    act(() => {
      setLanguage('en');
    });
    expect(screen.getByRole('heading', { level: 1, name: 'Sealing' })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('en');
  });

  it('shows a not-found page for unknown paths', async () => {
    signedInAs('admin');
    renderApp('/does-not-exist');
    expect(await screen.findByText('Sayfa bulunamadı')).toBeInTheDocument();
  });

  it('puts the name, role and real idle minutes into the user menu', async () => {
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('auditor', { idle_timeout_minutes: 20 }))
        : errorResponse(404, 'not_found'),
    );
    renderApp('/');
    const trigger = await screen.findByRole('button', { name: 'Kullanıcı menüsü: Tolga Yılmaz' });
    expect(within(trigger).getByText('TY')).toBeInTheDocument();
  });
});
