import { act, screen, waitFor, within } from '@testing-library/react';

import { LEGAL_STATUS_KEY } from '@/components/legal/use-legal-status';
import type { LegalStatus } from '@/lib/legal-status-types';
import type { Credits, Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { SEALS, need } from '@/test/samples';
import { LEGAL_MIXED, LEGAL_OK, LEGAL_PROVIDER_NOT_LEGAL, LEGAL_REASONS } from '@/test/w5-fixtures';

/*
 * The sealing page's red alert for a time-stamp provider without legal validity (Wave 5 contract
 * 2.3.2): fed from `GET /legal-status` (`provider_not_legal`), with the banner's own text and
 * documentation reference, no longer from the credit status.
 */

const CALENDAR = need(SEALS.calendars['2026-10'], 'October calendar');

/** The credit status of the mock provider (`legal_grade: false`): it no longer raises the alert. */
const MOCK_CREDITS: Credits = {
  provider: 'mock',
  legal_grade: false,
  // The mock provider simulates a balance (C21).
  supported: true,
  threshold: 10_000,
  latest: null,
  remaining: null,
  below_threshold: false,
  history: [],
  unreadable_lines: 0,
};

function serve(role: Role, legal: LegalStatus | null) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/seals/calendar') return jsonResponse(200, CALENDAR);
    if (request.path === '/api/v1/credits') return jsonResponse(200, MOCK_CREDITS);
    if (request.path === '/api/v1/legal-status' && legal) return jsonResponse(200, legal);
    return errorResponse(404, 'not_found');
  });
}

const SENTENCE_TR = LEGAL_REASONS.provider_not_legal.message_tr;
const SENTENCE_EN = LEGAL_REASONS.provider_not_legal.message_en;

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

describe('the sealing page’s provider alert (Wave 5)', () => {
  it('names a provider without legal validity with the banner’s text and reference', async () => {
    const calls = serve('admin', LEGAL_PROVIDER_NOT_LEGAL);
    renderApp('/sealing');
    const alert = await screen.findByTestId('provider-not-legal');
    expect(alert).toHaveClass('bg-danger-soft');
    expect(alert).toHaveTextContent('Yasal geçerliliği olmayan zaman damgası');
    expect(alert).toHaveTextContent(`${SENTENCE_TR} (ZAMAN-DAMGASI.md bölüm 5.2)`);
    // The banner above the page announces it; the page's own alert does not again.
    expect(alert).toHaveAttribute('role', 'status');
    expect(within(alert).getByRole('link', { name: 'Ayarlar → Zaman damgası' })).toHaveAttribute(
      'href',
      '/settings/kamusm',
    );
    // One read for the banner and the page.
    expect(calls.filter((call) => call.path === '/api/v1/legal-status')).toHaveLength(1);
  });

  it('gives the auditor the text without the settings link', async () => {
    serve('auditor', LEGAL_PROVIDER_NOT_LEGAL);
    renderApp('/sealing');
    const alert = await screen.findByTestId('provider-not-legal');
    expect(alert).toHaveTextContent(SENTENCE_TR);
    expect(within(alert).queryByRole('link')).not.toBeInTheDocument();
  });

  it('in English', async () => {
    serve('admin', LEGAL_PROVIDER_NOT_LEGAL);
    act(() => {
      setLanguage('en');
    });
    renderApp('/sealing');
    const alert = await screen.findByTestId('provider-not-legal');
    expect(alert).toHaveTextContent('Time stamp without legal validity');
    expect(alert).toHaveTextContent(`${SENTENCE_EN} (TIME-STAMPING.md section 5.2)`);
    expect(within(alert).getByRole('link', { name: 'Settings → Time stamping' })).toBeVisible();
  });

  it.each([
    ['nothing to report', LEGAL_OK, 'success'],
    ['other reasons only', LEGAL_MIXED, 'success'],
    ['the status not served (404)', null, 'error'],
  ] as const)('shows nothing for %s, whatever the credit status says', async (_, legal, state) => {
    serve('admin', legal);
    const { client } = renderApp('/sealing');
    await waitFor(() => {
      expect(client.getQueryState(LEGAL_STATUS_KEY)?.status).toBe(state);
    });
    expect(await screen.findByText(/Sağlayıcı: mock/)).toBeInTheDocument();
    expect(screen.queryByTestId('provider-not-legal')).not.toBeInTheDocument();
  });
});
