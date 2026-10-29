import { QueryClientProvider, focusManager } from '@tanstack/react-query';
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FileWarning } from 'lucide-react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';

import { BACKGROUND_HEADER } from '@/lib/api';
import {
  DASHBOARD_POLL_MS,
  USAGE_POLL_MS,
  useDashboardSummary,
  useTodayCompanyUsage,
} from '@/lib/api-hooks';
import type { DashboardSummary, LicenseOut, Role } from '@/lib/api-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import { formatBytes } from '@/lib/format';
import { handoff } from '@/lib/handoff';
import { setLanguage } from '@/lib/i18n';
import { renderApp, testQueryClient } from '@/test/render';
import { DASHBOARD, licenseSample, RAW, USAGE } from '@/test/samples';

import { DashCard } from './dashboard-parts';
import { PARSE_FAILURES, withParseFailures } from './test-data';

/**
 * The summary the API sent to `role` (captured per role; `hr` is refused), with a known
 * parse-failure box beside the ingest box.
 */
function summaryFor(role: Role): DashboardSummary | null {
  if (role === 'admin' || role === 'auditor' || role === 'viewer')
    return withParseFailures(structuredClone(DASHBOARD[role]));
  return null;
}

function serve(
  role: Role,
  change?: (summary: DashboardSummary) => void,
  license: LicenseOut | Response = licenseSample('trial'),
): RecordedRequest[] {
  return installFetch((request) => {
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, makeMe(role));
      // The usage widgets need the licence first (contract 8.4); the trial enables everything.
      case '/api/v1/license':
        return license instanceof Response ? license.clone() : jsonResponse(200, license);
      case '/api/v1/dashboard/summary': {
        const summary = summaryFor(role);
        if (!summary) return errorResponse(403, 'forbidden');
        change?.(summary);
        return jsonResponse(200, summary);
      }
      case '/api/v1/usage/company':
        return jsonResponse(200, USAGE.company);
      case '/api/v1/raw': {
        const line =
          RAW.lines[
            `${request.params.get('source') ?? ''}|${request.params.get('day') ?? ''}|${request.params.get('line') ?? ''}`
          ];
        return line ? jsonResponse(200, line) : errorResponse(404, 'raw_not_found');
      }
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

const CARDS = [
  'card-ingest',
  'card-parse-failures',
  'card-seal',
  'card-credit',
  'card-clock',
  'card-usage',
  'card-alerts',
  'card-sites',
  'card-categories',
  'card-calendar',
];

async function shownCards(): Promise<string[]> {
  await screen.findByTestId('card-usage');
  return CARDS.filter((id) => screen.queryByTestId(id) !== null);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Panel — blocks by permission', () => {
  it('administrator: every block, each linking to its page', async () => {
    serve('admin');
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: 'Panel' })).toBeInTheDocument();
    await screen.findByTestId('card-ingest');
    expect(await shownCards()).toEqual(CARDS);
    const ingest = screen.getByTestId('card-ingest');
    expect(within(ingest).getByText('2/3 kaynak aktif')).toBeInTheDocument();
    expect(within(ingest).getByTestId('silent-sources')).toHaveTextContent('switch-3');
    expect(within(ingest).getByRole('link', { name: 'Kaynaklar sayfasını aç' })).toHaveAttribute(
      'href',
      '/sources',
    );
    expect(
      within(screen.getByTestId('card-seal')).getByRole('link', {
        name: 'Mühürleme sayfasını aç',
      }),
    ).toHaveAttribute('href', '/sealing?month=2026-10&day=2026-10-12');
    expect(screen.getByTestId('card-seal')).toHaveTextContent('Henüz mühürlenmedi');
    expect(screen.getByTestId('card-credit')).toHaveTextContent('9.612 kaldı');
    expect(screen.getByTestId('card-credit')).toHaveTextContent('~6 yıl yeter');
    expect(screen.getByTestId('card-credit')).not.toHaveTextContent('Eşiğin altında');
    expect(screen.getByTestId('card-clock')).toHaveTextContent('Normal');
    const alerts = screen.getByTestId('card-alerts');
    expect(within(alerts).getByRole('link', { name: /Yüksek 2/ })).toHaveAttribute(
      'href',
      '/security?state=open&severity=high',
    );
    expect(within(alerts).getAllByRole('link', { name: /switch-3/ })[0]).toHaveAttribute(
      'href',
      '/security?alert=4',
    );
    // The captured company usage of 12.10.2026: 5 active users, 17 minutes active.
    const metrics = USAGE.company.metrics;
    expect(screen.getByTestId('card-usage')).toHaveTextContent(
      formatBytes(metrics.download_bytes, 'tr'),
    );
    expect(screen.getByTestId('card-usage')).toHaveTextContent('17 dk');
    expect(screen.getByTestId('card-sites')).toHaveTextContent('portal.example');
  });

  it('no watched source (a new installation): what to add, no "active" badge', async () => {
    serve('admin', (summary) => {
      if (summary.ingest) {
        summary.ingest = { ...summary.ingest, sources_total: 0, sources_active: 0, silent: [] };
      }
    });
    renderApp('/');
    const ingest = await screen.findByTestId('card-ingest');
    expect(ingest).toHaveTextContent('İzlenen kaynak yok');
    expect(ingest).not.toHaveTextContent('0/0');
    expect(within(ingest).queryByText('Aktif')).not.toBeInTheDocument();
    expect(within(ingest).getByTestId('no-sources')).toHaveTextContent(
      'arcilogger.toml içindeki tek adresli, beklenen [[sources]] kayıtlarını izler',
    );
    // Unknown senders still send: the rate stays.
    expect(ingest).toHaveTextContent('mesaj/dk');
  });

  it('credit below the threshold is marked', async () => {
    serve('admin', (summary) => {
      summary.credit = { remaining: 420, estimated_days: 74, threshold: 500 };
    });
    renderApp('/');
    const credit = await screen.findByTestId('card-credit');
    expect(credit).toHaveTextContent('Eşiğin altında');
    expect(credit).toHaveTextContent('~2 ay yeter');
  });

  it('a clock sample older than 3 hours is no verdict', async () => {
    serve('admin', (summary) => {
      // The hourly check stopped at 09:00; the API keeps the old offset but `ok: null`.
      summary.clock = {
        offset_ms: 20,
        ntp_server: '172.20.10.1',
        checked_at: '2026-10-12T06:00:00Z',
        ok: null,
      };
    });
    renderApp('/');
    const clock = await screen.findByTestId('card-clock');
    expect(clock).toHaveTextContent('Bilinmiyor');
    expect(clock).not.toHaveTextContent('Normal');
    expect(clock).toHaveTextContent('Son ölçüm 12.10.2026 09:00');
  });

  it('the unknown category is "Unknown" on the English Panel', async () => {
    serve('admin');
    setLanguage('en');
    try {
      renderApp('/');
      const categories = await screen.findByTestId('card-categories');
      const legend = await within(categories).findByRole('list');
      expect(legend).toHaveTextContent('Unknown');
      expect(legend).not.toHaveTextContent('Bilinmiyor');
    } finally {
      setLanguage('tr');
    }
  });

  it('auditor: no alerts block (no alerts.read)', async () => {
    serve('auditor');
    renderApp('/');
    await screen.findByTestId('card-seal');
    expect(await shownCards()).toEqual(CARDS.filter((id) => id !== 'card-alerts'));
    // The auditor may not open "Kaynaklar" or "Sistem": no links there.
    expect(within(screen.getByTestId('card-ingest')).queryByRole('link')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('card-clock')).queryByRole('link')).not.toBeInTheDocument();
  });

  it('report viewer: log intake and the company usage only', async () => {
    serve('viewer');
    renderApp('/');
    await screen.findByTestId('card-ingest');
    expect(await shownCards()).toEqual([
      'card-ingest',
      'card-parse-failures',
      'card-usage',
      'card-sites',
      'card-categories',
    ]);
    expect(
      within(screen.getByTestId('card-usage')).getByRole('link', {
        name: 'Kullanım sayfasını aç',
      }),
    ).toHaveAttribute('href', '/usage');
    // Counts only: no raw line and no search for a role without either.
    const failures = screen.getByTestId('card-parse-failures');
    expect(failures).toHaveTextContent('fortigate.kv');
    expect(within(failures).queryByRole('button')).not.toBeInTheDocument();
    expect(within(failures).queryByRole('link')).not.toBeInTheDocument();
  });

  it('HR: usage only, the summary is never asked', async () => {
    const calls = serve('hr');
    renderApp('/');
    expect(await shownCards()).toEqual(['card-usage', 'card-sites', 'card-categories']);
    expect(calls.some((call) => call.path === '/api/v1/dashboard/summary')).toBe(false);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('a failing summary is an error with a retry; the usage blocks stay', async () => {
    installFetch((request) =>
      request.path === '/api/v1/auth/me'
        ? jsonResponse(200, makeMe('admin'))
        : request.path === '/api/v1/license'
          ? jsonResponse(200, licenseSample('trial'))
          : request.path === '/api/v1/usage/company'
            ? jsonResponse(200, USAGE.company)
            : errorResponse(500, 'internal_error', { tr: 'Sunucu hatası.' }),
    );
    renderApp('/');
    expect(await screen.findByText('Panel bilgileri alınamadı.')).toBeInTheDocument();
    expect(screen.getByText('Sunucu hatası.')).toBeInTheDocument();
    expect(await screen.findByTestId('card-usage')).toBeInTheDocument();
    expect(screen.queryByTestId('card-ingest')).not.toBeInTheDocument();
  });
});

describe('Panel — parse failures (contract 2.17)', () => {
  it("today's rate, counts and patterns; the example line and Arama by permission", async () => {
    const requests = serve('admin');
    const put = vi.spyOn(handoff, 'put');
    const { router } = renderApp('/');
    const card = await screen.findByTestId('card-parse-failures');
    expect(within(card).getByRole('heading', { name: 'Ayrıştırma hataları' })).toBeInTheDocument();
    expect(card).toHaveTextContent('Binde 25,0');
    expect(card).toHaveTextContent('Bugün 31 hata / 1.240 satır');
    expect(card).toHaveTextContent('Yüksek');
    expect(within(card).getByRole('link', { name: 'Kaynaklar sayfasını aç' })).toHaveAttribute(
      'href',
      '/sources',
    );
    const patterns = within(card).getAllByRole('listitem');
    expect(patterns.map((item) => item.dataset.pattern)).toEqual([
      'fortigate.kv',
      'rawlog.invalid',
    ]);
    // A line not in the collector's format has no database row: its example only.
    expect(
      within(card).queryByRole('button', { name: 'Aramada göster: rawlog.invalid kalıbı' }),
    ).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(
      within(card).getByRole('button', { name: 'Örnek satır: fortigate.kv kalıbı' }),
    );
    const sheet = await screen.findByRole('dialog', { name: 'Ham kayıt satırı' });
    expect(
      await within(sheet).findByText(RAW.lines['fortigate|2026-10-12|49']?.sha256 ?? '-'),
    ).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.click(
      within(card).getByRole('button', { name: 'Aramada göster: fortigate.kv kalıbı' }),
    );
    expect(put).toHaveBeenCalledWith('/search', {
      q: 'source:fortigate error:fortigate.kv',
      range: { preset: 'today' },
    });
    // Arama took the hand-over once it has answered its search (this mock refuses it).
    expect(await screen.findByText('TR not_found')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/search');
    // One search, on today's range (the address's default range does not run it again).
    const searches = requests.filter((r) => r.method === 'POST' && r.path === '/api/v1/search');
    expect(searches.map((r) => r.body)).toEqual([
      {
        q: 'source:fortigate error:fortigate.kv',
        from: expect.stringMatching(/T00:00:00\+03:00$/) as string,
        to: expect.any(String) as string,
      },
    ]);
    // In memory only: the address holds the range, neither it nor the history entry the query.
    expect(router.state.location.search).toBe('?range=today');
    expect(router.state.location.state).toBeNull();
    put.mockRestore();
  });

  it('no failure today is said so; an API without the box shows no card', async () => {
    serve('admin', (summary) => {
      summary.parse_failures = { ...PARSE_FAILURES, failures: 0, permille: 0, top_patterns: [] };
    });
    renderApp('/');
    const card = await screen.findByTestId('card-parse-failures');
    expect(card).toHaveTextContent('Hata yok');
    expect(card).toHaveTextContent('Binde 0,0');
    expect(within(card).queryByRole('list')).not.toBeInTheDocument();
  });

  it('a summary without the box (an API before Wave 2) leaves it out', async () => {
    serve('admin', (summary) => {
      delete (summary as Partial<DashboardSummary>).parse_failures;
    });
    renderApp('/');
    await screen.findByTestId('card-ingest');
    expect(screen.queryByTestId('card-parse-failures')).not.toBeInTheDocument();
  });
});

describe('DashCard', () => {
  it('wraps a long title instead of cutting it, and links from its footer', () => {
    const title = 'Çok uzun bir başlık: dünkü mühür, kontör ve saat sapması';
    const { container } = render(
      <MemoryRouter>
        <DashCard
          title={title}
          icon={FileWarning}
          to="/sources"
          page="Kaynaklar"
          status={<span>Durum</span>}
          testId="card-long"
        >
          <p>içerik</p>
        </DashCard>
      </MemoryRouter>,
    );
    const card = within(container).getByTestId('card-long');
    const heading = within(card).getByRole('heading', { name: title });
    for (const element of [heading, ...heading.querySelectorAll('*')])
      expect(element.getAttribute('class') ?? '').not.toMatch(/\btruncate\b/);
    expect(heading.className).toMatch(/break-words/);
    const link = within(card).getByRole('link', { name: 'Kaynaklar sayfasını aç' });
    expect(link).toHaveTextContent('Kaynaklar');
    // The link is in the footer, after the content, not in the header beside the title.
    expect(heading.parentElement?.contains(link)).toBe(false);
    expect(
      within(card).getByText('içerik').compareDocumentPosition(link) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe('Panel — licence (contract 8.4)', () => {
  const usageCalls = (calls: RecordedRequest[]) =>
    calls.filter((call) => call.path.startsWith('/api/v1/usage'));

  async function settle() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
  }

  it('usage analytics off: a locked card, no /usage/company request, the health boxes unchanged', async () => {
    const calls = serve('admin', undefined, licenseSample('trial_expired'));
    renderApp('/');
    const locked = await screen.findByTestId('card-usage-locked');
    expect(locked).toHaveTextContent('Kullanım analizi lisansınızda yok.');
    expect(within(locked).getByRole('link', { name: 'Lisans sayfası' })).toHaveAttribute(
      'href',
      '/settings/license',
    );
    expect(await screen.findByTestId('card-ingest')).toHaveTextContent('2/3 kaynak aktif');
    for (const id of ['card-seal', 'card-credit', 'card-clock', 'card-alerts', 'card-calendar'])
      expect(screen.getByTestId(id)).toBeInTheDocument();
    for (const id of ['card-usage', 'card-sites', 'card-categories'])
      expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    await settle();
    expect(usageCalls(calls)).toEqual([]);
  });

  it('"Yenile" asks no usage while the module is off', async () => {
    const calls = serve('admin', undefined, licenseSample('expired'));
    renderApp('/');
    await screen.findByTestId('card-usage-locked');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Yenile' }));
    await settle();
    expect(usageCalls(calls)).toEqual([]);
    expect(calls.filter((call) => call.path === '/api/v1/dashboard/summary').length).toBe(2);
  });

  it('HR (usage only): the locked card without the licence page link', async () => {
    const calls = serve('hr', undefined, { ...licenseSample('trial_expired'), details: null });
    renderApp('/');
    const locked = await screen.findByTestId('card-usage-locked');
    expect(within(locked).queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText(/panelde gösterilecek bir bölüm yok/)).not.toBeInTheDocument();
    await settle();
    expect(usageCalls(calls)).toEqual([]);
  });

  it('no usage request before the licence is known', async () => {
    let answer: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      if (request.path === '/api/v1/license') return pending;
      if (request.path === '/api/v1/dashboard/summary') return jsonResponse(200, DASHBOARD.admin);
      if (request.path === '/api/v1/usage/company') return jsonResponse(200, USAGE.company);
      return errorResponse(404, 'not_found');
    });
    renderApp('/');
    await screen.findByTestId('card-ingest');
    await settle();
    expect(usageCalls(calls)).toEqual([]);
    answer(jsonResponse(200, licenseSample('licensed')));
    await waitFor(() => {
      expect(usageCalls(calls).map((call) => call.path)).toEqual(['/api/v1/usage/company']);
    });
    expect(await screen.findByTestId('card-usage')).toHaveTextContent('17 dk');
  });

  it('licence unreadable: its message in place of the usage widgets, no usage request', async () => {
    const calls = serve(
      'admin',
      undefined,
      errorResponse(503, 'license_unavailable', { tr: 'Lisans bileşeni yüklenemedi.' }),
    );
    renderApp('/');
    const card = await screen.findByTestId('card-usage-license');
    expect(card).toHaveTextContent('Lisans bilgisi alınamadı.');
    expect(card).toHaveTextContent('Lisans bileşeni yüklenemedi.');
    expect(await screen.findByTestId('card-ingest')).toBeInTheDocument();
    await settle();
    expect(usageCalls(calls)).toEqual([]);
  });
});

describe('Panel — polling', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    act(() => {
      focusManager.setFocused(undefined);
    });
  });

  function count(calls: RecordedRequest[], path: string): RecordedRequest[] {
    return calls.filter((call) => call.path === `/api/v1${path}`);
  }

  function serveBoth(): RecordedRequest[] {
    return installFetch((request) =>
      request.path === '/api/v1/dashboard/summary'
        ? jsonResponse(200, DASHBOARD.admin)
        : jsonResponse(200, USAGE.company),
    );
  }

  it('refreshes the live blocks every 30 s and the usage every 5 min, in the background', async () => {
    expect(DASHBOARD_POLL_MS).toBe(30_000);
    expect(USAGE_POLL_MS).toBe(300_000);
    const calls = serveBoth();
    renderHook(
      () => {
        useDashboardSummary();
        useTodayCompanyUsage();
      },
      { wrapper },
    );
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(count(calls, '/dashboard/summary')).toHaveLength(1);
    expect(count(calls, '/usage/company')).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(count(calls, '/dashboard/summary')).toHaveLength(2);
    expect(count(calls, '/usage/company')).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(270_000));
    expect(count(calls, '/dashboard/summary')).toHaveLength(11);
    expect(count(calls, '/usage/company')).toHaveLength(2);

    // The first load is the user's; every refresh after it is a background request.
    const summary = count(calls, '/dashboard/summary');
    expect(summary[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    expect(summary.slice(1).every((call) => call.headers.get(BACKGROUND_HEADER) === '1')).toBe(
      true,
    );
    expect(count(calls, '/usage/company')[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
  });

  it('pauses while the tab is hidden and catches up in the background when it is shown', async () => {
    const calls = serveBoth();
    renderHook(() => useDashboardSummary(), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(count(calls, '/dashboard/summary')).toHaveLength(1);

    act(() => {
      focusManager.setFocused(false);
    });
    await act(() => vi.advanceTimersByTimeAsync(5 * 60_000));
    expect(count(calls, '/dashboard/summary')).toHaveLength(1);

    act(() => {
      focusManager.setFocused(true);
    });
    await act(() => vi.advanceTimersByTimeAsync(10));
    const summary = count(calls, '/dashboard/summary');
    expect(summary).toHaveLength(2);
    expect(summary[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
  });
});
