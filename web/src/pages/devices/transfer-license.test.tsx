import { act, screen, within } from '@testing-library/react';

import type { LicenseOut } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';

/*
 * "İçe/Dışa aktar" (contract 1.5, 8.4): the CSV import is the "Toplu içe aktarma (CSV)" module;
 * while it is off a locked card takes its place, the exports stay.
 */

function serve(license: LicenseOut | null): RecordedRequest[] {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/license' && license) return jsonResponse(200, license);
    return errorResponse(404, 'not_found');
  });
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('İçe/Dışa aktar — licence', () => {
  it('module off: the locked card instead of the import, the exports stay', async () => {
    const calls = serve(licenseSample('licensed'));
    renderApp('/devices?tab=transfer');
    const locked = await screen.findByTestId('import-locked');
    expect(locked).toHaveTextContent('Toplu içe aktarma (CSV) lisansınızda yok.');
    expect(within(locked).getByRole('link', { name: 'Lisans sayfası' })).toHaveAttribute(
      'href',
      '/settings/license',
    );
    expect(screen.queryByText('İçe aktar (CSV)')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cihaz listesini indir' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Kişi listesini indir' })).toBeEnabled();
    await settle();
    expect(calls.filter((call) => call.path.startsWith('/api/v1/imports'))).toEqual([]);
  });

  it('module on (trial): the import', async () => {
    serve(licenseSample('trial'));
    renderApp('/devices?tab=transfer');
    expect(await screen.findByText('İçe aktar (CSV)')).toBeInTheDocument();
    expect(screen.queryByTestId('import-locked')).not.toBeInTheDocument();
  });

  it('licence unknown: the import (the API decides)', async () => {
    serve(null);
    renderApp('/devices?tab=transfer');
    expect(await screen.findByText('İçe aktar (CSV)')).toBeInTheDocument();
  });
});
