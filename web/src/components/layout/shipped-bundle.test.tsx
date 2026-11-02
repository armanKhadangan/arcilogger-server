import { act, screen, within } from '@testing-library/react';

import { NAV_ITEMS, UI_KIT_ITEM } from '@/nav';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

/*
 * The bundle the installer ships (`npm run build`) is built without the developer UI kit
 * (`__UI_KIT__` false, vite.config.ts, whose build check refuses any of it): `UI_KIT_ITEM` is
 * null there. The unit tests build it in, so this file takes it out the same way.
 */
vi.mock('@/nav', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/nav')>()),
  UI_KIT_ITEM: null,
}));

describe('without the UI kit (the shipped bundle)', () => {
  beforeEach(() => {
    window.localStorage.setItem('arci.sidebar.collapsed', '0');
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

  it('has no menu entry for it, even for an administrator', async () => {
    expect(UI_KIT_ITEM).toBeNull();
    renderApp('/');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(within(nav).getAllByRole('link')).toHaveLength(NAV_ITEMS.length);
    expect(within(nav).queryByRole('link', { name: 'Bileşen Kiti' })).not.toBeInTheDocument();
  });

  it('has no /ui-kit page', async () => {
    renderApp('/ui-kit');
    expect(await screen.findByText('Sayfa bulunamadı')).toBeInTheDocument();
  });

  it('placeholder pages name no milestone and do not point to the UI kit', async () => {
    renderApp('/compliance');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Uyum Durumu' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Bu sayfa sonraki bir sürümde eklenecek.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Bileşen Kitini aç' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Bileşen Kiti/)).not.toBeInTheDocument();
  });
});
