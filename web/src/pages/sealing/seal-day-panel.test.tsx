import { act, screen } from '@testing-library/react';

import { setLanguage } from '@/lib/i18n';
import type { SealDayDetail } from '@/lib/seal-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { SEALS, need } from '@/test/samples';

const DAY = '2026-10-04';
const SEALED = need(SEALS.days[DAY], `${DAY} sample`);
const CALENDAR = need(SEALS.calendars['2026-10'], 'October calendar');
const TOKEN = need(SEALED.token, 'token of the sealed day');
const MISMATCH = '2026/10/04/fortigate.log.gz: SHA-256 mismatch (file changed)';
const MISMATCH_TR = '2026/10/04/fortigate.log.gz: SHA-256 özeti tutmuyor (dosya değişmiş)';

/** The day page of a day the scheduled Verify task found changed (stored, not a fresh check). */
function tampered(overrides: Partial<SealDayDetail['token']> = {}): SealDayDetail {
  return {
    ...SEALED,
    verification: {
      at: '2026-10-05T01:30:00+03:00',
      ok: false,
      deep: true,
      source: 'scheduled',
      by: 'verify',
      errors: [MISMATCH],
      warnings: ['2026/10/04/fortigate.log.gz: not read-only'],
      errors_tr: [MISMATCH_TR],
      warnings_tr: ['2026/10/04/fortigate.log.gz: salt okunur değil'],
    },
    token: {
      ...TOKEN,
      check: {
        ok: false,
        root: null,
        errors: ['certificate chain does not reach a pinned root certificate'],
        errors_tr: ['sertifika zinciri güvenilen (sabitlenmiş) bir kök sertifikaya ulaşmıyor'],
      },
      ...overrides,
    },
  };
}

function serve(detail: SealDayDetail) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('auditor'));
    if (request.path === `/api/v1/seals/${DAY}`) return jsonResponse(200, detail);
    if (request.path === '/api/v1/seals/calendar') return jsonResponse(200, CALENDAR);
    return errorResponse(404, 'not_found');
  });
}

describe('the day page in Turkish (spec 12.4)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      setLanguage('tr');
    });
  });

  it("shows a stored verification's and the token check's errors in Turkish only", async () => {
    serve(tampered());
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByText(MISMATCH_TR)).toBeInTheDocument();
    expect(
      screen.getByText('sertifika zinciri güvenilen (sabitlenmiş) bir kök sertifikaya ulaşmıyor'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/mismatch|pinned root/)).not.toBeInTheDocument();
  });

  it('keeps the English technical text for the English interface', async () => {
    serve(tampered());
    act(() => {
      setLanguage('en');
    });
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByText(MISMATCH)).toBeInTheDocument();
    expect(screen.queryByText(MISMATCH_TR)).not.toBeInTheDocument();
  });

  it('never calls a production day whose root is not listed a test stamp (decision 5)', async () => {
    serve(
      tampered({
        provider: 'zamane',
        legal_grade: false,
        legal_grade_claimed: true,
        test_stamp: false,
      }),
    );
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByTestId('legal-root-unconfirmed')).toHaveTextContent(
      'tsa.legal_root_fingerprints',
    );
    expect(screen.queryByTestId('not-legal-grade')).not.toBeInTheDocument();
    expect(screen.getByText('Kamu SM üretim (kök tanımlı değil)')).toBeInTheDocument();
    expect(screen.queryByText(/test\/mock/)).not.toBeInTheDocument();
  });

  it('keeps the red banner for the mock TSA, also under a production claim', async () => {
    serve(tampered({ legal_grade_claimed: true, test_stamp: true }));
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByTestId('not-legal-grade')).toHaveTextContent('mock');
    expect(screen.queryByTestId('legal-root-unconfirmed')).not.toBeInTheDocument();
  });
});
