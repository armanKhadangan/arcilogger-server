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

describe('"Şimdi mühürle" (contract 2.9)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function serveAdmin() {
    return installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      const day = /^\/api\/v1\/seals\/(\d{4}-\d{2}-\d{2})$/.exec(request.path)?.[1];
      if (day) return jsonResponse(200, need(SEALS.days[day], `${day} sample`));
      if (request.path === '/api/v1/seals/calendar') return jsonResponse(200, CALENDAR);
      return errorResponse(404, 'not_found');
    });
  }

  it('is offered for the oldest failed day only; other days say why not', async () => {
    serveAdmin();
    const { router } = renderApp('/sealing?day=2026-10-11');
    const button = await screen.findByRole('button', { name: 'Şimdi mühürle' });
    expect(button).toBeEnabled();
    expect(button).toHaveAccessibleDescription(/yeniden deneme zamanını beklemeden bir kez dener/);

    await act(async () => {
      await router.navigate('/sealing?day=2026-10-12');
    });
    expect(
      await screen.findByText(
        'Şimdi yalnızca sıradaki (en eski) mühürlenmemiş gün mühürlenebilir: önce 11.10.2026.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Şimdi mühürle' })).toBeDisabled();
  });

  it('is not shown without seal.manage (the auditor verifies but does not seal)', async () => {
    serve(SEALED);
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByRole('button', { name: 'Doğrula' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Şimdi mühürle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kontör sorgula' })).not.toBeInTheDocument();
  });
});
