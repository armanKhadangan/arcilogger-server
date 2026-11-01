import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ReactNode } from 'react';

import { BACKGROUND_HEADER } from '@/lib/api';
import type { ApiErrorBody, Role } from '@/lib/api-types';
import { COMPLIANCE_POLL_MS, useCompliance } from '@/lib/compliance-hooks';
import type { ComplianceStatus, ConfigBackupOut } from '@/lib/compliance-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp, testQueryClient } from '@/test/render';

/*
 * Uyum Durumu as the app opens it (`/compliance`, Wave 4 contract 2.8.1, tests of §3 W4-F): the
 * page's hooks on the answers captured from the real API (`e2e/samples/compliance.json`), the
 * auditor of D15, the report download, the configuration backup confirmation and its refusal, and
 * the 30 s background refresh. The view's own rules are pinned in `compliance-view.test.tsx`.
 */

interface ErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

const SAMPLE = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'compliance.json'), 'utf8'),
) as {
  admin: ComplianceStatus;
  auditor: ComplianceStatus;
  after_backup: ComplianceStatus;
  config_backup: ConfigBackupOut;
  report_pdf: { headers: Record<string, string> };
  errors: Record<'forbidden' | 'config_backup_unreadable', ErrorSample>;
};

/** The report's bytes: a small fixed file (the capture keeps the real one's headers only). */
const PDF = new TextEncoder().encode('%PDF-1.7\n% ARCILOGGER Uyum Durumu Raporu\n%%EOF\n');
const PDF_SHA256 = createHash('sha256').update(PDF).digest('hex');
const PDF_NAME = 'ARCILOGGER-uyum-durumu-20261013-0701.pdf';

function sampleError(name: keyof typeof SAMPLE.errors): Response {
  const sample = SAMPLE.errors[name];
  return jsonResponse(sample.status, sample.body, sample.headers);
}

interface Serve {
  /** The confirmation answers the captured 409 `config_backup_unreadable`. */
  unreadable?: boolean;
  /** The report answers this instead of the file. */
  reportFails?: Response;
}

/** The API as the captured samples have it, for `role`; returns the recorded requests. */
function serve(role: Role, { unreadable = false, reportFails }: Serve = {}): RecordedRequest[] {
  let confirmed = false;
  const me = makeMe(role);
  const may = (permission: string) => me.permissions.includes(permission);
  return installFetch((request) => {
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, me);
      case '/api/v1/compliance':
        if (!may('compliance.read')) return sampleError('forbidden');
        if (confirmed) return jsonResponse(200, SAMPLE.after_backup);
        return jsonResponse(200, role === 'auditor' ? SAMPLE.auditor : SAMPLE.admin);
      case '/api/v1/compliance/report.pdf':
        if (!may('compliance.read')) return sampleError('forbidden');
        if (reportFails) return reportFails;
        return new Response(PDF, {
          status: 200,
          headers: {
            'Content-Type': 'application/pdf',
            'Content-Disposition': SAMPLE.report_pdf.headers['content-disposition'] ?? '',
            'X-Arci-Sha256': PDF_SHA256,
          },
        });
      case '/api/v1/compliance/config-backup':
        if (!may('settings.manage')) return sampleError('forbidden');
        if (unreadable) return sampleError('config_backup_unreadable');
        confirmed = true;
        return jsonResponse(200, SAMPLE.config_backup);
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

function compliance(calls: RecordedRequest[], method = 'GET'): RecordedRequest[] {
  return calls.filter(
    (call) => call.method === method && call.path.startsWith('/api/v1/compliance'),
  );
}

function cards(): HTMLElement[] {
  return within(screen.getByRole('list', { name: 'Denetimler' })).getAllByRole('group');
}

const card = (id: string) => screen.getByTestId(`compliance-card-${id}`);

/** The files the page offered (jsdom has no object URLs: the link's `download` name). */
function captureSaves(): string[] {
  const saved: string[] = [];
  Object.assign(URL, { createObjectURL: () => 'blob:uyum', revokeObjectURL: () => undefined });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push(this.download);
  });
  return saved;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Uyum Durumu page', () => {
  it('shows the administrator the captured cards, the summary and the notice', async () => {
    const calls = serve('admin');
    renderApp('/compliance');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Uyum Durumu' }),
    ).toBeInTheDocument();
    await screen.findByTestId('compliance-card-sources');
    expect(cards()).toHaveLength(10);
    expect(screen.getByTestId('compliance-notice')).toHaveTextContent(SAMPLE.admin.notice_tr);
    expect(screen.getByTestId('compliance-summary')).toHaveTextContent(
      '10 denetimden 1 tanesi tamam',
    );
    expect(card('content_filter')).toHaveTextContent(
      SAMPLE.admin.cards.find((item) => item.id === 'content_filter')?.reason_tr ?? '-',
    );
    expect(screen.getByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toHaveAttribute(
      'href',
      '/settings/setup',
    );
    expect(
      within(card('backup')).getByRole('button', { name: 'Yapılandırma yedeğini aldım' }),
    ).toBeInTheDocument();
    // The first load is the user's own request.
    expect(compliance(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([null]);
  });

  it('gives the auditor the cards and the PDF, no confirm button, no wizard (D15)', async () => {
    const calls = serve('auditor');
    const saved = captureSaves();
    renderApp('/compliance');
    await screen.findByTestId('compliance-card-sources');
    expect(cards()).toHaveLength(10);
    expect(screen.queryByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toBeNull();
    // Links to the pages the auditor may open (Mühürleme, Arama) stay; the others are text.
    const main = screen.getByRole('main');
    const targets = within(main)
      .getAllByRole('link')
      .map((link) => link.getAttribute('href') ?? '');
    expect(new Set(targets)).toEqual(new Set(['/sealing', '/search']));
    expect(within(card('sources')).getByText('Açık uyarı: 1')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }));
    await waitFor(() => {
      expect(saved).toEqual([PDF_NAME]);
    });
    expect(compliance(calls).map((call) => call.path)).toEqual([
      '/api/v1/compliance',
      '/api/v1/compliance/report.pdf',
    ]);
    expect(compliance(calls, 'POST')).toEqual([]);
    expect(screen.queryByTestId('compliance-export-error')).toBeNull();
  });

  it('shows a refused report beside its button, with the code', async () => {
    serve('admin', { reportFails: errorResponse(503, 'database_unavailable') });
    const saved = captureSaves();
    renderApp('/compliance');
    await screen.findByTestId('compliance-card-sources');
    await userEvent.click(screen.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }));
    expect(await screen.findByTestId('compliance-export-error')).toHaveTextContent(
      'TR database_unavailable (database_unavailable)',
    );
    expect(saved).toEqual([]);
  });

  it('records the configuration copy and shows card 10 as read again after it', async () => {
    const calls = serve('admin');
    renderApp('/compliance');
    await screen.findByTestId('compliance-card-sources');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }));
    const dialog = screen.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' });
    await user.click(within(dialog).getByRole('button', { name: 'Onaylıyorum' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    expect(
      await screen.findByText('Yapılandırma kopyasının onayı denetim kaydına yazıldı.'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('compliance-summary')).toHaveTextContent(
      '10 denetimden 2 tanesi tamam',
    );
    // The cards are read again once the confirmation is recorded.
    expect(
      calls
        .filter((call) => call.path.startsWith('/api/v1/compliance'))
        .map((call) => `${call.method} ${call.path}`),
    ).toEqual([
      'GET /api/v1/compliance',
      'POST /api/v1/compliance/config-backup',
      'GET /api/v1/compliance',
    ]);
  });

  it('keeps the dialog open with the refusal of an unreadable configuration file', async () => {
    const calls = serve('admin', { unreadable: true });
    renderApp('/compliance');
    await screen.findByTestId('compliance-card-sources');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }));
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Onaylıyorum' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' });
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Yapılandırma dosyası okunamadı; yedek onayı kaydedilmedi. (config_backup_unreadable)',
    );
    // Nothing was recorded: the cards are not read again.
    expect(compliance(calls)).toHaveLength(1);
  });

  it('"Yenile" asks again as the user, not in the background', async () => {
    const calls = serve('admin');
    renderApp('/compliance');
    await screen.findByTestId('compliance-card-sources');
    await userEvent.click(screen.getByRole('button', { name: 'Yenile' }));
    await waitFor(() => {
      expect(compliance(calls)).toHaveLength(2);
    });
    expect(compliance(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([
      null,
      null,
    ]);
  });

  it.each(['hr', 'viewer'] as const)(
    'refuses %s the page by URL, without asking the API',
    async (role) => {
      const calls = serve(role);
      renderApp('/compliance');
      expect(
        await screen.findByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
      ).toBeInTheDocument();
      expect(compliance(calls)).toEqual([]);
    },
  );
});

describe('Uyum Durumu — polling', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refreshes the cards every 30 s in the background; "Yenile" is the user\'s', async () => {
    expect(COMPLIANCE_POLL_MS).toBe(30_000);
    const calls = installFetch(() => jsonResponse(200, SAMPLE.admin));
    const { result } = renderHook(() => useCompliance(), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    await act(() => result.current.refresh());
    expect(compliance(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([
      null,
      '1',
      '1',
      null,
    ]);
  });
});
