import { act, screen, within } from '@testing-library/react';

import { setLanguage } from '@/lib/i18n';
import type { SealDayDetail, SealRenewal, SealRevocation } from '@/lib/seal-types';
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

/*
 * Re-stamping and revocation (Wave 5 contract 2.3.1 "Day detail", 2.6, C4): the captured day
 * carries no `renewal` and a `crl-none` finding; the other states are hand-built on top of it.
 */
describe('re-stamping and revocation on the day page (Wave 5)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    act(() => {
      setLanguage('tr');
    });
  });

  function withRenewal(
    renewal: SealRenewal | null,
    revocation: SealRevocation | null = null,
  ): SealDayDetail {
    return { ...SEALED, renewal, revocation };
  }

  const REVOKED: SealRevocation = {
    code: 'crl-revoked',
    level: 'fail',
    // Worded as the verifier's finding (`check_revocation`); the certificate name is made up.
    message_tr:
      'Deneme TSA Sertifikası 1: sertifika zaman damgasından ÖNCE iptal edilmiş (03.10.2026 09:00)',
    message_en:
      'Deneme TSA Sertifikası 1: revoked before the time stamp (2026-10-03T06:00:00+00:00)',
  };

  it('the captured day: no re-stamping row, the revocation finding as information', async () => {
    const revocation = need(SEALED.revocation, 'revocation of the captured day');
    expect(SEALED.renewal).toBeNull();
    serve(SEALED);
    renderApp(`/sealing?day=${DAY}`);
    const alert = await screen.findByTestId('seal-revocation');
    expect(alert).toHaveClass('bg-info-soft');
    expect(alert).toHaveTextContent(revocation.message_tr);
    expect(screen.queryByTestId('seal-renewal')).not.toBeInTheDocument();
    expect(screen.queryByText('Yeniden damgalama')).not.toBeInTheDocument();
  });

  it('shows neither when the day has none', async () => {
    serve(withRenewal(null, null));
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByText('Zaman damgası')).toBeInTheDocument();
    expect(screen.queryByTestId('seal-renewal')).not.toBeInTheDocument();
    expect(screen.queryByTestId('seal-revocation')).not.toBeInTheDocument();
  });

  it.each([
    [
      'not_due',
      { cert_not_after: '2031-09-30T21:00:00Z', covered_by: null, renewed_at: null },
      'Gerekmiyor',
      'Yeniden damgalama henüz gerekmiyor (sertifika 01.10.2031 tarihine kadar geçerli).',
    ],
    [
      'due',
      { cert_not_after: '2027-03-31T21:00:00Z', covered_by: null, renewed_at: null },
      'Bekleniyor',
      'Damga sertifikası 01.04.2027 tarihinde sona eriyor; yeniden damgalama bekleniyor.',
    ],
    [
      'covered',
      {
        cert_not_after: '2027-03-31T21:00:00Z',
        covered_by: 'renewals/2027-02-01-1.json',
        renewed_at: '2027-02-01T00:40:12Z',
      },
      'Yeniden damgalandı',
      'Bu damga, sertifikası sona ermeden yeniden damgalanmış: renewals/2027-02-01-1.json.',
    ],
    [
      'late',
      // The date is the counting renewal's stamp time (C4) in the site's zone, not its file name.
      {
        cert_not_after: '2027-03-31T21:00:00Z',
        covered_by: 'renewals/2027-04-02-1.json',
        renewed_at: '2027-04-02T22:15:07Z',
      },
      'Geç yeniden damgalandı',
      'Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış (03.04.2027).',
    ],
    [
      'late',
      // A server before C4 sends no stamp time: the parenthesis is left out.
      { cert_not_after: '2027-03-31T21:00:00Z', covered_by: 'renewals/2027-04-03-1.json' },
      'Geç yeniden damgalandı',
      'Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış.',
    ],
    [
      'uncovered',
      { cert_not_after: '2027-03-31T21:00:00Z', covered_by: null, renewed_at: null },
      'Yeniden damgalanmadı',
      'Damga sertifikasının süresi 01.04.2027 tarihinde doldu ve yeniden damgalama yok; damga, alındığı an için geçerlidir.',
    ],
  ] as const)('%s: badge and the 2.6 sentence (%s)', async (status, fields, badge, sentence) => {
    serve(withRenewal({ status, ...fields } as SealRenewal));
    renderApp(`/sealing?day=${DAY}`);
    const renewal = await screen.findByTestId('seal-renewal');
    expect(renewal).toHaveAttribute('data-status', status);
    expect(within(renewal).getByText(badge)).toBeInTheDocument();
    expect(within(renewal).getByText(sentence)).toBeInTheDocument();
    // A fact of the time-stamp section, after the certificate's validity.
    const token = screen.getByRole('heading', { name: 'Zaman damgası' }).closest('section');
    expect(token).toContainElement(renewal);
    expect(within(token as HTMLElement).getByText('Yeniden damgalama').tagName).toBe('DT');
  });

  it('a renewal in English', async () => {
    serve(
      withRenewal({
        status: 'due',
        cert_not_after: '2027-03-31T21:00:00Z',
        covered_by: null,
        renewed_at: null,
      }),
    );
    act(() => {
      setLanguage('en');
    });
    renderApp(`/sealing?day=${DAY}`);
    const renewal = await screen.findByTestId('seal-renewal');
    expect(renewal).toHaveTextContent('Due');
    expect(renewal).toHaveTextContent(
      'The stamp certificate expires on 01.04.2027; re-stamping is due.',
    );
  });

  it.each([
    ['fail', 'bg-danger-soft'],
    ['warn', 'bg-warning-soft'],
    ['info', 'bg-info-soft'],
    ['ok', 'bg-success-soft'],
  ] as const)(
    'a revocation of level %s is shown with its message (%s, C4)',
    async (level, tone) => {
      serve(withRenewal(null, { ...REVOKED, level }));
      renderApp(`/sealing?day=${DAY}`);
      const alert = await screen.findByTestId('seal-revocation');
      expect(alert).toHaveClass(tone);
      // The banner above the page announces a revoked certificate; the day page does not again.
      expect(alert).toHaveAttribute('role', 'status');
      expect(alert).toHaveTextContent('İptal listesi (SİL) denetimi');
      expect(alert).toHaveTextContent(REVOKED.message_tr);
      expect(screen.queryByTestId('seal-renewal')).not.toBeInTheDocument();
    },
  );

  it('a revocation in English', async () => {
    serve(withRenewal(null, REVOKED));
    act(() => {
      setLanguage('en');
    });
    renderApp(`/sealing?day=${DAY}`);
    const alert = await screen.findByTestId('seal-revocation');
    expect(alert).toHaveTextContent('Revocation list check');
    expect(alert).toHaveTextContent(REVOKED.message_en);
    expect(alert).not.toHaveTextContent(REVOKED.message_tr);
  });

  it('names TÜRKTRUST, not Kamu SM, on a TÜRKTRUST stamp (decision 12)', async () => {
    serve(
      tampered({
        provider: 'turktrust',
        legal_grade: false,
        legal_grade_claimed: true,
        test_stamp: false,
      }),
    );
    renderApp(`/sealing?day=${DAY}`);
    const unconfirmed = await screen.findByTestId('legal-root-unconfirmed');
    expect(unconfirmed).toHaveTextContent('TÜRKTRUST üretim sunucusundan (turktrust)');
    expect(unconfirmed).not.toHaveTextContent('Kamu SM');
    const token = screen.getByRole('heading', { name: 'Zaman damgası' }).closest('section');
    expect(token).toHaveTextContent('TÜRKTRUST üretim (kök tanımlı değil)');
    expect(token).not.toHaveTextContent('Kamu SM');
    expect(screen.queryByTestId('not-legal-grade')).not.toBeInTheDocument();
  });

  it('calls a verified TÜRKTRUST stamp by its server', async () => {
    serve({ ...SEALED, token: { ...TOKEN, provider: 'turktrust', legal_grade: true } });
    renderApp(`/sealing?day=${DAY}`);
    expect(await screen.findByText('TÜRKTRUST üretim sunucusu')).toBeInTheDocument();
    expect(screen.queryByText('Kamu SM üretim sunucusu')).not.toBeInTheDocument();
    expect(screen.queryByTestId('legal-root-unconfirmed')).not.toBeInTheDocument();
  });
});
