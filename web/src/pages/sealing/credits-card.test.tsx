import { act, screen, waitFor, within } from '@testing-library/react';

import type { Credits, Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { SEALS, need } from '@/test/samples';

/*
 * The credit card for a provider without a credit query (Wave 5 contract 2.4, 2.6, C3): the
 * Credit task records `supported: false` (C21: today `rfc3161` and TÜRKTRUST), so the card says
 * so in neutral words under a plain "Kontör" title instead of numbers, offers no "Kontör sorgula"
 * and never warns about a threshold. Only the flag decides, never the provider name. Hand-built answers
 * until W5-C's samples carry one; `supported` is cast in until W5-C adds it to `Credits` (C2).
 */

const CALENDAR = need(SEALS.calendars['2026-10'], 'October calendar');

const KAMUSM: Credits = {
  provider: 'zamane',
  legal_grade: true,
  threshold: 10_000,
  latest: { time: '2026-10-13T06:30:00+03:00', provider: 'zamane', credits: 9_612, error: null },
  remaining: 9_612,
  below_threshold: false,
  history: [{ time: '2026-10-13T06:30:00+03:00', provider: 'zamane', credits: 9_612, error: null }],
  unreadable_lines: 0,
};

/** TÜRKTRUST: the Credit task records `{"supported": false}`; nothing is ever counted. */
const TURKTRUST = {
  ...KAMUSM,
  provider: 'turktrust',
  supported: false,
  latest: null,
  remaining: null,
  below_threshold: false,
  history: [],
} as Credits;

function serve(role: Role, credits: Credits) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/seals/calendar') return jsonResponse(200, CALENDAR);
    if (request.path === '/api/v1/credits') return jsonResponse(200, credits);
    return errorResponse(404, 'not_found');
  });
}

async function card(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title });
  const found = heading.closest('.rounded-lg');
  if (!(found instanceof HTMLElement)) throw new Error(`no card around ${title}`);
  return found;
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

describe('the credit card without a credit query', () => {
  it('TÜRKTRUST: the neutral sentence, a plain title, no number and no query button', async () => {
    serve('admin', TURKTRUST);
    renderApp('/sealing');
    const credits = await card('Kontör');
    expect(within(credits).getByTestId('credits-no-query')).toHaveTextContent(
      'Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin.',
    );
    expect(credits).toHaveTextContent('Sağlayıcı: turktrust');
    expect(within(credits).queryByText('Bilinmiyor')).not.toBeInTheDocument();
    expect(within(credits).queryByText(/uyarı eşiği/)).not.toBeInTheDocument();
    expect(within(credits).queryByText(/Kamu SM/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kontör sorgula' })).not.toBeInTheDocument();
  });

  it('any provider the Credit task recorded as not supported', async () => {
    serve('admin', { ...KAMUSM, supported: false } as Credits);
    renderApp('/sealing');
    expect(await screen.findByTestId('credits-no-query')).toBeInTheDocument();
    expect(screen.queryByText('9.612', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kontör sorgula' })).not.toBeInTheDocument();
  });

  it('a plain RFC 3161 server, which has no credit query either (C21)', async () => {
    serve('admin', { ...TURKTRUST, provider: 'rfc3161', legal_grade: false });
    renderApp('/sealing');
    expect(await screen.findByTestId('credits-no-query')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Kontör' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kontör sorgula' })).not.toBeInTheDocument();
  });

  it('never decides by the provider name: without the flag the numbers stay (C3)', async () => {
    serve('admin', { ...KAMUSM, provider: 'turktrust' });
    renderApp('/sealing');
    const credits = await card('Kontör (Kamu SM)');
    await waitFor(() => {
      expect(within(credits).getByText('kalan · uyarı eşiği 10.000')).toBeInTheDocument();
    });
    expect(within(credits).queryByTestId('credits-no-query')).not.toBeInTheDocument();
    expect(within(credits).getByRole('button', { name: 'Kontör sorgula' })).toBeEnabled();
  });

  it('in English', async () => {
    serve('auditor', TURKTRUST);
    act(() => {
      setLanguage('en');
    });
    renderApp('/sealing');
    expect(await screen.findByTestId('credits-no-query')).toHaveTextContent(
      "This provider offers no credit query; watch the balance in the provider's portal.",
    );
    expect(screen.getByRole('heading', { name: 'Credit' })).toBeInTheDocument();
  });

  it('Kamu SM keeps its numbers, its title and "Kontör sorgula" (supported: true)', async () => {
    serve('admin', { ...KAMUSM, supported: true } as Credits);
    renderApp('/sealing');
    const credits = await card('Kontör (Kamu SM)');
    await waitFor(() => {
      expect(within(credits).getByText('kalan · uyarı eşiği 10.000')).toBeInTheDocument();
    });
    expect(within(credits).getAllByText('9.612', { exact: true }).length).toBeGreaterThan(0);
    expect(within(credits).queryByTestId('credits-no-query')).not.toBeInTheDocument();
    expect(within(credits).getByRole('button', { name: 'Kontör sorgula' })).toBeEnabled();
  });
});
