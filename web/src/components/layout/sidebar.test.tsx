import { act, screen, within } from '@testing-library/react';

import type { LicenseOut } from '@/lib/api-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';

/*
 * The active menu entry is highlighted (background + 3 px bar inside the link) in both the
 * expanded and the collapsed rail. In the collapsed rail the link sits in a tooltip trigger
 * (`asChild`): its class names must still be real class names.
 */
describe.each([
  ['expanded', '0'],
  ['collapsed', '1'],
])('Sidebar (%s)', (_name, stored) => {
  beforeEach(() => {
    window.localStorage.setItem('arci.sidebar.collapsed', stored);
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : errorResponse(404, 'not_found'),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      window.localStorage.clear();
    });
  });

  it('highlights the current page and only that one', async () => {
    renderApp('/sealing');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    const active = within(nav).getByRole('link', { name: 'Mühürleme' });
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(active.className).not.toContain('=>');
    expect(active).toHaveClass('relative', 'bg-sidebar-active', 'font-medium', 'text-white');
    if (stored === '1') expect(active).toHaveClass('justify-center');
    const bar = within(active).getByTestId('active-bar');
    expect(bar).toHaveClass('absolute');
    expect(bar.parentElement).toBe(active);

    const other = within(nav).getByRole('link', { name: 'Panel' });
    expect(other).not.toHaveAttribute('aria-current');
    expect(other.className).not.toContain('=>');
    expect(other).toHaveClass('relative', 'text-sidebar-text');
    expect(other).not.toHaveClass('bg-sidebar-active');
    expect(other).not.toHaveClass('text-white');
    expect(other).not.toHaveClass('font-medium');
    expect(within(other).queryByTestId('active-bar')).not.toBeInTheDocument();
    expect(within(nav).getAllByTestId('active-bar')).toHaveLength(1);
  });

  it('marks "Panel" active only on the start page', async () => {
    renderApp('/');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(within(nav).getByRole('link', { name: 'Panel' })).toHaveClass('bg-sidebar-active');
    expect(within(nav).getAllByTestId('active-bar')).toHaveLength(1);
  });
});

/*
 * The organisation's name comes from the server (`site_name` of `/auth/me`, set at installation),
 * never from the web app's texts: under the product name while the rail is expanded, nothing at
 * all while the server has no name.
 */
describe('Sidebar organisation name', () => {
  function signedInWith(siteName: string, collapsed = false) {
    window.localStorage.setItem('arci.sidebar.collapsed', collapsed ? '1' : '0');
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('viewer', { site_name: siteName }))
        : errorResponse(404, 'not_found'),
    );
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      window.localStorage.clear();
    });
  });

  it('shows the configured name under the product name', async () => {
    signedInWith('Deneme Lojistik Ltd. Şti.');
    renderApp('/');
    await screen.findByRole('navigation', { name: 'Ana menü' });
    const name = screen.getByTestId('site-name');
    expect(name).toHaveTextContent(/^Deneme Lojistik Ltd\. Şti\.$/);
    expect(name).toHaveAttribute('title', 'Deneme Lojistik Ltd. Şti.');
    expect(name.previousElementSibling).toHaveTextContent(/^ARCILOGGER$/);
  });

  it.each(['', '   '])(
    'shows no name and no placeholder when the server has none (%j)',
    async (siteName) => {
      signedInWith(siteName);
      renderApp('/');
      const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
      expect(screen.queryByTestId('site-name')).not.toBeInTheDocument();
      const aside = nav.closest('aside');
      if (!aside) throw new Error('no sidebar');
      expect(within(aside).getByText('ARCILOGGER')).toBeInTheDocument();
      expect(aside).not.toHaveTextContent(/Örnek|kuruluş|undefined|null/i);
    },
  );

  it('keeps the collapsed rail to the mark only', async () => {
    signedInWith('Deneme Lojistik Ltd. Şti.', true);
    renderApp('/');
    await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(screen.queryByTestId('site-name')).not.toBeInTheDocument();
    expect(screen.queryByText('Deneme Lojistik Ltd. Şti.')).not.toBeInTheDocument();
  });
});

/*
 * Licence marks (contract 8.4): an entry whose module is off stays in the menu with a lock
 * ("Lisansınızda yok"), also in the collapsed rail; nothing is marked while the licence is
 * unknown. The footer names the licensee of a verified licence under the version.
 */
describe('Sidebar licence marks', () => {
  function signedIn(license: LicenseOut | null, collapsed = false) {
    window.localStorage.setItem('arci.sidebar.collapsed', collapsed ? '1' : '0');
    installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      if (request.path === '/api/v1/license' && license) return jsonResponse(200, license);
      return errorResponse(404, 'not_found');
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      window.localStorage.clear();
    });
  });

  it('locks Kullanım and Raporlar while their modules are off, nothing else', async () => {
    signedIn(licenseSample('trial_expired'));
    renderApp('/sealing');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    const usage = await within(nav).findByRole('link', { name: 'Kullanım Lisansınızda yok' });
    expect(usage).toHaveAttribute('data-locked', 'true');
    expect(within(usage).getByRole('img', { name: 'Lisansınızda yok' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Raporlar Lisansınızda yok' })).toHaveAttribute(
      'href',
      '/reports',
    );
    expect(within(nav).getAllByTestId('nav-lock')).toHaveLength(2);
    expect(within(nav).getByRole('link', { name: '5651 Sorgu' })).not.toHaveAttribute(
      'data-locked',
    );
  });

  it('marks the collapsed rail too, in the link name', async () => {
    signedIn(licenseSample('expired'), true);
    renderApp('/sealing');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(
      await within(nav).findByRole('link', { name: 'Kullanım (Lisansınızda yok)' }),
    ).toHaveAttribute('data-locked', 'true');
    expect(within(nav).getAllByTestId('nav-lock')).toHaveLength(2);
  });

  it('no lock for enabled modules, nor while the licence is unknown', async () => {
    signedIn(licenseSample('licensed'));
    const { unmount } = renderApp('/sealing');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    await screen.findByTestId('sidebar-licensee');
    expect(within(nav).queryAllByTestId('nav-lock')).toEqual([]);
    unmount();

    signedIn(null);
    renderApp('/sealing');
    const again = await screen.findByRole('navigation', { name: 'Ana menü' });
    await screen.findByRole('heading', { level: 1, name: 'Mühürleme' });
    expect(within(again).queryAllByTestId('nav-lock')).toEqual([]);
    expect(within(again).getByRole('link', { name: 'Kullanım' })).toBeInTheDocument();
  });

  it('names the licensee under the version, nothing without a verified licence', async () => {
    signedIn(licenseSample('licensed'));
    const { unmount } = renderApp('/sealing');
    expect(await screen.findByTestId('sidebar-licensee')).toHaveTextContent(
      /^Lisans: Örnek A\.Ş\.$/,
    );
    unmount();

    signedIn(licenseSample('trial'));
    renderApp('/sealing');
    await screen.findByTestId('license-banners');
    expect(screen.queryByTestId('sidebar-licensee')).not.toBeInTheDocument();
  });
});
