import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { NAV_GROUPS, NAV_ITEMS } from '@/nav';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

import { foldText, paletteKeys, paletteScore } from './command-palette-model';

describe('"Sayfaya git" model', () => {
  it('folds Turkish letters and case, so either spelling finds the page', () => {
    expect(foldText('Mühürleme')).toBe('muhurleme');
    expect(foldText('İNCELEME')).toBe('inceleme');
    expect(foldText('Kanıt Paketleri')).toBe('kanit paketleri');
    expect(foldText('Çağrı Şöyle')).toBe('cagri soyle');
  });

  it('keeps an entry only when every word starts one of its words', () => {
    expect(paletteScore('Mühürleme · Kanıt', 'muhur')).toBe(1);
    expect(paletteScore('Kanıt Paketleri · Kanıt', 'paket')).toBe(0.6);
    expect(paletteScore('Kanıt Paketleri · Kanıt', 'kanıt pak')).toBe(1);
    expect(paletteScore('Denetim Kaydı · Rapor ve denetim', 'kaydi')).toBe(0.6);
    expect(paletteScore('Panel · Genel bakış', 'xyz')).toBe(0);
    expect(paletteScore('Panel · Genel bakış', 'anel')).toBe(0);
    // The other language's name is a keyword.
    expect(paletteScore('Mühürleme · Kanıt', 'sealing', ['Sealing'])).toBe(1);
    expect(paletteScore('Anything', '   ')).toBe(1);
  });

  it('shows Ctrl K, or ⌘ K on a Mac', () => {
    expect(paletteKeys()).toBe('Ctrl K');
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Macintosh; Intel)');
    expect(paletteKeys()).toBe('⌘ K');
    vi.restoreAllMocks();
  });

  it('puts every menu entry in a group, groups in the menu’s order', () => {
    const order = NAV_ITEMS.map((item) => NAV_GROUPS.indexOf(item.group));
    expect(order.every((group) => group >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });
});

describe('"Sayfaya git" in the application', () => {
  function signedInAs(role: 'admin' | 'viewer') {
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe(role))
        : errorResponse(404, 'not_found'),
    );
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      window.localStorage.clear();
    });
  });

  it('opens with Ctrl+K, finds a page without Turkish letters and opens it with Enter', async () => {
    signedInAs('admin');
    const user = userEvent.setup();
    renderApp('/');
    await screen.findByRole('navigation', { name: 'Ana menü' });
    await user.keyboard('{Control>}k{/Control}');
    const dialog = await screen.findByRole('dialog', { name: 'Sayfaya git' });
    // Pages by group, then the sections of Ayarlar.
    expect(within(dialog).getByRole('option', { name: 'Mühürleme' })).toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: 'Zaman damgası' })).toBeInTheDocument();
    expect(
      within(dialog).getByRole('option', { name: 'Kullanıcılar ve Roller' }),
    ).toBeInTheDocument();
    await user.keyboard('muhur');
    expect(
      within(dialog)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Mühürleme', 'Kayıtlarda ara: “muhur”']);
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('heading', { level: 1, name: 'Mühürleme' })).toBeVisible();
    expect(screen.queryByRole('dialog', { name: 'Sayfaya git' })).not.toBeInTheDocument();
  });

  it('offers a viewer only the pages of the role, and no log search', async () => {
    signedInAs('viewer');
    const user = userEvent.setup();
    renderApp('/');
    await screen.findByRole('navigation', { name: 'Ana menü' });
    await user.click(screen.getByRole('button', { name: /Sayfaya git/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Sayfaya git' });
    expect(
      within(dialog)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Panel', 'Canlı Durum', 'Kullanım']);
    await user.keyboard('kul');
    expect(
      within(dialog)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Kullanım']);
  });
});

describe('the menu’s groups', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      window.localStorage.clear();
    });
  });

  it('lists the entries under named groups, in the menu’s order', async () => {
    window.localStorage.setItem('arci.sidebar.collapsed', '0');
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : errorResponse(404, 'not_found'),
    );
    renderApp('/');
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    const evidence = within(nav).getByRole('list', { name: 'Kanıt' });
    expect(
      within(evidence)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['Mühürleme', 'Kanıt Paketleri', 'Cihazlar ve Kişiler']);
    expect(within(nav).getByRole('list', { name: 'Yönetim' })).toBeInTheDocument();
  });
});
