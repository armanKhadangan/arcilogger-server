import { act, screen, waitFor, within } from '@testing-library/react';

import type { LicenseOut, Role } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseError, licenseSample, USAGE } from '@/test/samples';

/*
 * ModuleGate (contract 8.4): the page's skeleton until the licence is known, the locked page
 * while the module is off — none of the page's requests is made —, and the page itself when the
 * module is on or the licence could not be read (the API enforces anyway).
 */

beforeAll(async () => {
  await import('@/pages/usage/usage-page');
}, 120_000);

type LicenseAnswer = LicenseOut | Response | Promise<Response>;

function serve(role: Role, license: () => LicenseAnswer): RecordedRequest[] {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/license') {
      const answer = license();
      if (answer instanceof Promise || answer instanceof Response) return answer;
      return jsonResponse(200, answer);
    }
    if (request.path === '/api/v1/usage/company') return jsonResponse(200, USAGE.company);
    return errorResponse(404, 'not_found');
  });
}

const gated = (calls: RecordedRequest[]) =>
  calls.filter((call) => /^\/api\/v1\/(usage|reports|imports)\b/.test(call.path));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ModuleGate', () => {
  it('shows the skeleton while the licence loads and asks nothing of the module', async () => {
    const calls = serve('admin', () => new Promise<Response>(() => undefined));
    renderApp('/usage');
    expect(await screen.findByTestId('page-skeleton')).toHaveAttribute('role', 'status');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(gated(calls)).toEqual([]);
    expect(screen.queryByRole('heading', { level: 1, name: 'Kullanım' })).not.toBeInTheDocument();
  });

  it.each([
    ['/usage', 'Kullanım analizi lisansınızda yok'],
    ['/reports', 'Zamanlanmış kullanım raporları lisansınızda yok'],
  ])('locked %s: the locked page, no request to the gated API', async (path, title) => {
    const calls = serve('admin', () => licenseSample('trial_expired'));
    renderApp(path);
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    const locked = screen.getByTestId('module-locked');
    expect(locked).toHaveTextContent(
      'Bu modül bu kurulumun lisansında açık değil. 5651 işlevleri (log toplama, mühürleme, sorgu, kanıt paketleri, uyarılar) bundan etkilenmez.',
    );
    expect(within(locked).getByRole('link', { name: 'Lisans sayfasına git' })).toHaveAttribute(
      'href',
      '/settings/license',
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(gated(calls)).toEqual([]);
  });

  it('users without the licence page are told to contact their administrator', async () => {
    serve('hr', () => ({ ...licenseSample('expired'), details: null }));
    renderApp('/usage');
    const locked = await screen.findByTestId('module-locked');
    expect(within(locked).queryByRole('link')).not.toBeInTheDocument();
    expect(locked).toHaveTextContent('Yöneticinize başvurun.');
  });

  it('module on: the page and its requests', async () => {
    const calls = serve('admin', () => licenseSample('licensed'));
    renderApp('/usage');
    expect(await screen.findByRole('heading', { level: 1, name: 'Kullanım' })).toBeInTheDocument();
    await waitFor(() => {
      expect(gated(calls).length).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId('module-locked')).not.toBeInTheDocument();
  });

  it('licence unreadable: the page (the API decides)', async () => {
    serve(
      'admin',
      () => new Response(JSON.stringify(licenseError('unavailable').body), { status: 503 }),
    );
    renderApp('/usage');
    expect(await screen.findByRole('heading', { level: 1, name: 'Kullanım' })).toBeInTheDocument();
    expect(screen.queryByTestId('module-locked')).not.toBeInTheDocument();
  });

  it('a 403 module_not_licensed of a stale page reads the licence again', async () => {
    let licence: LicenseOut = licenseSample('licensed');
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      if (request.path === '/api/v1/license') return jsonResponse(200, licence);
      if (request.path.startsWith('/api/v1/usage')) {
        // The licence was removed meanwhile (by the CLI): every usage route refuses.
        licence = licenseSample('trial_expired');
        const { status, body } = licenseError('module_locked_usage');
        return jsonResponse(status, body);
      }
      return errorResponse(404, 'not_found');
    });
    renderApp('/usage');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Kullanım analizi lisansınızda yok' }),
    ).toBeInTheDocument();
    expect(calls.filter((call) => call.path === '/api/v1/license').length).toBeGreaterThan(1);
  });
});
