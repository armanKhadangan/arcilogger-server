import { screen, within } from '@testing-library/react';

import type { LicenseOut, Role } from '@/lib/api-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';

/*
 * The licence on the Settings page (contract 8.2: a "Lisans" card above "Kullanıcılar ve
 * Roller", "Hakkında" moves to the licence page) and the users counter of the users page (8.4).
 */

function serve(license: LicenseOut, role: Role = 'admin') {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/license') return jsonResponse(200, license);
    if (request.path === '/api/v1/users')
      return jsonResponse(200, { items: [], next_cursor: null });
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Ayarlar — Lisans card', () => {
  it('comes first, with the state badge and the licensee', async () => {
    serve(licenseSample('licensed'));
    renderApp('/settings');
    const card = await screen.findByTestId('settings-license');
    expect(await within(card).findByTestId('license-state')).toHaveTextContent('Lisanslı');
    expect(card).toHaveTextContent('Örnek A.Ş.');
    expect(within(card).getByRole('link')).toHaveAttribute('href', '/settings/license');
    const links = screen.getAllByRole('link', { name: /Lisans|Kullanıcılar ve Roller/ });
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/settings/license',
      '/settings/users',
    ]);
  });

  it('"Hakkında" is no later section any more (the licence page holds it)', async () => {
    serve(licenseSample('trial'));
    renderApp('/settings');
    await screen.findByTestId('settings-license');
    expect(screen.queryByText('Hakkında')).not.toBeInTheDocument();
    expect(screen.getByText('Yedekleme')).toBeInTheDocument();
  });
});

describe('Kullanıcılar — users limit', () => {
  it('shows "Kullanıcı: used / max" when a limit exists', async () => {
    serve(licenseSample('licensed'));
    renderApp('/settings/users');
    expect(await screen.findByTestId('users-limit')).toHaveTextContent('Kullanıcı: 4 / 25');
    expect(screen.getByRole('button', { name: 'Kullanıcı ekle' })).toBeEnabled();
  });

  it('no counter without a limit (trial)', async () => {
    serve(licenseSample('trial'));
    renderApp('/settings/users');
    await screen.findByRole('heading', { level: 1, name: 'Kullanıcılar ve Roller' });
    await screen.findByTestId('license-banners');
    expect(screen.queryByTestId('users-limit')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Kullanıcı ekle' })).toBeEnabled();
  });

  it('at the limit: "Kullanıcı ekle" is off and says why (the API still decides)', async () => {
    const status = licenseSample('trial_expired');
    status.limits.users = { max: 3, used: 3, unconfigured: 0, exceeded: false, full: true };
    serve(status);
    renderApp('/settings/users');
    expect(await screen.findByTestId('users-limit')).toHaveTextContent('Kullanıcı: 3 / 3');
    const add = screen.getByRole('button', { name: 'Kullanıcı ekle' });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(
      'Kullanıcı sayısı lisans sınırında (3/3): yeni kullanıcı eklenemez ya da yeniden etkinleştirilemez. Var olan kullanıcılar çalışmaya devam eder.',
    );
  });
});
