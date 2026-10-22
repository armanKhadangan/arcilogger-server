import { act, screen, within } from '@testing-library/react';

import type { LicenseOut, Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import type { LicenseDocumentName } from '@/test/license-samples';
import { renderApp } from '@/test/render';
import { licenseError, licenseSample } from '@/test/samples';

/*
 * The licence banners above every page (contract 8.3): at most two, danger as `alert`, the
 * others as `status`; administrators get the licence page link, everyone else the sentence to
 * contact them. A page that is not about licences (the sealing page) shows them too.
 */

function serve(role: Role, license: LicenseOut | Response) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/license')
      return license instanceof Response ? license.clone() : jsonResponse(200, license);
    return errorResponse(404, 'not_found');
  });
}

function fixtureFor(name: LicenseDocumentName, role: Role): LicenseOut {
  const status = licenseSample(name);
  return role === 'admin' ? status : { ...status, details: null };
}

async function banners(): Promise<HTMLElement[]> {
  const area = await screen.findByTestId('license-banners');
  return within(area).getAllByTestId(/^license-banner-/);
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

describe('LicenseBanner', () => {
  it('trial: an info banner (status) with the days left and, for admins, the licence page', async () => {
    serve('admin', fixtureFor('trial', 'admin'));
    renderApp('/sealing');
    const [banner, ...rest] = await banners();
    expect(rest).toEqual([]);
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveTextContent('Deneme sürümü — 6 gün kaldı.');
    expect(
      within(banner as HTMLElement).getByRole('link', { name: 'Lisans sayfası' }),
    ).toHaveAttribute('href', '/settings/license');
    expect(banner).not.toHaveTextContent('Yöneticinize başvurun.');
  });

  it('users get "contact your administrator" instead of the link', async () => {
    serve('viewer', fixtureFor('trial_expired', 'viewer'));
    renderApp('/');
    const [banner] = await banners();
    expect(banner).toHaveTextContent(
      'Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor. Yöneticinize başvurun.',
    );
    expect(within(banner as HTMLElement).queryByRole('link')).not.toBeInTheDocument();
  });

  it('an unverifiable licence is announced at once (role alert)', async () => {
    serve('auditor', fixtureFor('invalid', 'auditor'));
    renderApp('/sealing');
    const [banner] = await banners();
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveTextContent('Lisans dosyası doğrulanamadı');
  });

  it('shows at most two banners, the state first', async () => {
    const status = licenseSample('version_not_covered');
    status.limits = licenseSample('licensed_limits_exceeded').limits;
    serve('admin', status);
    renderApp('/sealing');
    const shown = await banners();
    expect(shown.map((item) => item.dataset.testid)).toEqual([
      'license-banner-versionNotCovered',
      'license-banner-usersExceeded',
    ]);
  });

  it('limits and maintenance only for administrators', async () => {
    serve('auditor', fixtureFor('licensed_limits_exceeded', 'auditor'));
    renderApp('/sealing');
    await screen.findByRole('heading', { level: 1, name: 'Mühürleme' });
    expect(screen.queryByTestId('license-banners')).not.toBeInTheDocument();
  });

  it('nothing while the licence could not be read', async () => {
    serve('admin', new Response(JSON.stringify(licenseError('unavailable').body), { status: 503 }));
    renderApp('/sealing');
    await screen.findByRole('heading', { level: 1, name: 'Mühürleme' });
    expect(screen.queryByTestId('license-banners')).not.toBeInTheDocument();
  });

  it('follows the interface language', async () => {
    serve('admin', fixtureFor('expired', 'admin'));
    renderApp('/sealing');
    const [banner] = await banners();
    expect(banner).toHaveTextContent('Lisansın süresi 10.10.2026 tarihinde doldu');
    act(() => {
      setLanguage('en');
    });
    expect(banner).toHaveTextContent(
      'The licence expired on 2026-10-10: paid modules are off. The 5651 functions keep working. Licence page',
    );
  });
});
