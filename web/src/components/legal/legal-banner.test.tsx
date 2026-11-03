import { focusManager } from '@tanstack/react-query';
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { BACKGROUND_HEADER } from '@/lib/api';
import type { Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import type { RecordedRequest } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import {
  LEGAL_MIXED,
  LEGAL_OK,
  LEGAL_PROVIDER_NOT_LEGAL,
  LEGAL_REASONS,
  LEGAL_WARNING_ONLY,
  legalStatusWith,
} from '@/test/w5-fixtures';

import { LEGAL_BANNER_HIDE_KEY, LEGAL_BANNER_HIDE_MS } from './banner-hide';
import type { LegalStatus } from '@/lib/legal-status-types';
import { LEGAL_STATUS_KEY, LEGAL_STATUS_POLL_MS } from './use-legal-status';

/*
 * The legal-validity banner above every page (contract 2.3.2), for every role: the worst reason
 * with its documentation reference, danger as `alert` without a close control, a warning as
 * `status` that can be hidden for 24 hours in this browser, "ve N uyarı daha" opening the others,
 * links only to pages the role may open. The page under it (Kullanım) is one every role may open
 * and has nothing to do with time stamps.
 */

const PAGE = '/usage';
const LEGAL_PATH = '/api/v1/legal-status';

/** The served status; a test changes `current` for the next answer. */
const served: { current: LegalStatus | Response } = { current: LEGAL_OK };

function serve(role: Role, status: LegalStatus | Response): RecordedRequest[] {
  served.current = status;
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === LEGAL_PATH) {
      const answer = served.current;
      return answer instanceof Response ? answer.clone() : jsonResponse(200, answer);
    }
    return errorResponse(404, 'not_found');
  });
}

function legalCalls(calls: RecordedRequest[]): RecordedRequest[] {
  return calls.filter((call) => call.path === LEGAL_PATH);
}

async function banner(code: string): Promise<HTMLElement> {
  const area = await screen.findByTestId('legal-banner');
  return within(area).getByTestId(`legal-banner-${code}`);
}

/** The page has loaded, the status has been answered, and no banner is on screen. */
async function expectNoBanner(calls: RecordedRequest[]): Promise<void> {
  await screen.findByRole('heading', { level: 1 });
  await vi.waitFor(() => {
    expect(legalCalls(calls).length).toBeGreaterThan(0);
  });
  await act(() => Promise.resolve());
  expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  act(() => {
    setLanguage('tr');
  });
});

describe('LegalBanner — levels', () => {
  it('ok: no banner', async () => {
    const calls = serve('auditor', LEGAL_OK);
    renderApp(PAGE);
    await expectNoBanner(calls);
  });

  it('nothing while the status cannot be read (404 before the endpoint exists, 503)', async () => {
    const calls = serve('admin', errorResponse(404, 'not_found'));
    const { unmount } = renderApp(PAGE);
    await expectNoBanner(calls);
    unmount();
    const again = serve('admin', errorResponse(503, 'unavailable'));
    renderApp(PAGE);
    await expectNoBanner(again);
  });

  it('danger: announced at once, red, with the reference, and no close control', async () => {
    serve('auditor', LEGAL_PROVIDER_NOT_LEGAL);
    renderApp(PAGE);
    const shown = await banner('provider_not_legal');
    expect(shown).toHaveAttribute('role', 'alert');
    expect(shown).toHaveClass('bg-danger-soft');
    expect(shown.querySelector('svg')).not.toBeNull();
    expect(shown).toHaveTextContent(
      "Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu damgaların yasal geçerliliği yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim sunucusunu seçin. (ZAMAN-DAMGASI.md bölüm 5.2)",
    );
    expect(within(shown).queryByRole('button')).not.toBeInTheDocument();
    // The banner is no page heading and no landmark of the top bar.
    expect(within(shown).queryByRole('heading')).not.toBeInTheDocument();
  });

  it('danger stays even with a pause stored for its reasons', async () => {
    window.localStorage.setItem(
      LEGAL_BANNER_HIDE_KEY,
      JSON.stringify({ until: Date.now() + LEGAL_BANNER_HIDE_MS, codes: ['provider_not_legal'] }),
    );
    serve('auditor', LEGAL_PROVIDER_NOT_LEGAL);
    renderApp(PAGE);
    expect(await banner('provider_not_legal')).toHaveAttribute('role', 'alert');
  });

  it('warning: polite, amber, with "24 saat gizle"', async () => {
    serve('viewer', LEGAL_WARNING_ONLY);
    renderApp(PAGE);
    const shown = await banner('crl_stale');
    expect(shown).toHaveAttribute('role', 'status');
    expect(shown).toHaveClass('bg-warning-soft');
    expect(shown).toHaveTextContent(
      '7 günden yeni doğrulanmış bir iptal listesi (SİL) yok. (ZAMAN-DAMGASI.md bölüm 5.9)',
    );
    expect(within(shown).getByRole('button', { name: '24 saat gizle' })).toBeInTheDocument();
  });

  it('a danger among warnings is the worst: shown first and not hideable', async () => {
    serve('admin', LEGAL_MIXED);
    renderApp(PAGE);
    const shown = await banner('legal_root_missing');
    expect(shown).toHaveAttribute('role', 'alert');
    expect(within(shown).queryByRole('button', { name: '24 saat gizle' })).not.toBeInTheDocument();
  });
});

describe('LegalBanner — "ve N uyarı daha"', () => {
  it('opens and closes the list of the other reasons', async () => {
    const user = userEvent.setup();
    serve('auditor', LEGAL_MIXED);
    renderApp(PAGE);
    const shown = await banner('legal_root_missing');
    const more = within(shown).getByRole('button', { name: 've 3 uyarı daha' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    const list = document.getElementById(more.getAttribute('aria-controls') ?? '');
    expect(list).not.toBeNull();
    expect(list).not.toBeVisible();

    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'true');
    const open = within(shown).getByRole('list', { name: 'Diğer yasal geçerlilik uyarıları' });
    expect(open).toBe(list);
    expect(
      within(open)
        .getAllByRole('listitem')
        .map((item) => item.dataset.testid),
    ).toEqual([
      'legal-reason-unstamped_days',
      'legal-reason-crl_stale',
      'legal-reason-renewal_due',
    ]);
    expect(within(open).getByTestId('legal-reason-unstamped_days')).toHaveTextContent(
      '1 kapanmış gün 24 saatten uzun süredir damgalanmadı. (ZAMAN-DAMGASI.md bölüm 5.5)',
    );
    expect(within(open).getByTestId('legal-reason-renewal_due')).toHaveTextContent(
      'ZAMAN-DAMGASI.md bölüm 5.6',
    );
    for (const item of within(open).getAllByRole('listitem')) {
      expect(item.querySelector('svg')).not.toBeNull();
    }

    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(list).not.toBeVisible();
  });

  it('counts in the singular and in English', async () => {
    serve('auditor', LEGAL_WARNING_ONLY);
    renderApp(PAGE);
    const shown = await banner('crl_stale');
    expect(within(shown).getByRole('button', { name: 've 1 uyarı daha' })).toBeInTheDocument();
    act(() => {
      setLanguage('en');
    });
    expect(within(shown).getByRole('button', { name: 'and 1 more' })).toBeInTheDocument();
  });

  it('a single reason has no list', async () => {
    serve('auditor', LEGAL_PROVIDER_NOT_LEGAL);
    renderApp(PAGE);
    const shown = await banner('provider_not_legal');
    expect(within(shown).queryByRole('list', { hidden: true })).not.toBeInTheDocument();
  });
});

describe('LegalBanner — links by role', () => {
  it.each([
    [
      'admin',
      [
        'Ayarlar → Zaman damgası',
        'Mühürleme sayfası',
        'Ayarlar → Zaman damgası',
        'Mühürleme sayfası',
      ],
    ],
    ['auditor', ['Mühürleme sayfası', 'Mühürleme sayfası']],
    ['hr', []],
    ['viewer', []],
  ] as const)('%s: %j', async (role, links) => {
    const user = userEvent.setup();
    serve(role, LEGAL_MIXED);
    renderApp(PAGE);
    const shown = await banner('legal_root_missing');
    await user.click(within(shown).getByRole('button', { name: 've 3 uyarı daha' }));
    expect(
      within(shown)
        .queryAllByRole('link')
        .map((link) => [link.textContent, link.getAttribute('href')]),
    ).toEqual(
      links.map((name) => [name, name === 'Mühürleme sayfası' ? '/sealing' : '/settings/kamusm']),
    );
  });

  it('the sealing link opens the sealing page', async () => {
    const user = userEvent.setup();
    serve('auditor', legalStatusWith(['unstamped_days']));
    const { router } = renderApp(PAGE);
    const shown = await banner('unstamped_days');
    await user.click(within(shown).getByRole('link', { name: 'Mühürleme sayfası' }));
    expect(router.state.location.pathname).toBe('/sealing');
    // The shell stays: the banner is on the next page too.
    expect(await banner('unstamped_days')).toBeInTheDocument();
  });
});

describe('LegalBanner — language', () => {
  it('follows the interface language: the API’s sentence, the reference and the controls', async () => {
    serve('admin', LEGAL_WARNING_ONLY);
    renderApp(PAGE);
    const shown = await banner('crl_stale');
    act(() => {
      setLanguage('en');
    });
    expect(shown).toHaveTextContent(
      'No verified revocation list newer than 7 days. (TIME-STAMPING.md section 5.9)',
    );
    expect(within(shown).getByRole('button', { name: 'Hide for 24 hours' })).toBeInTheDocument();
    act(() => {
      setLanguage('tr');
    });
    expect(shown).toHaveTextContent(LEGAL_REASONS.crl_stale.message_tr);
  });
});

describe('LegalBanner — hidden for 24 hours', () => {
  const NOW = new Date('2026-10-13T12:00:00Z');
  const MINUTE = 60_000;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** The clock moves to `at`, then the status is fetched again (as the 5-minute refresh does). */
  async function refreshAt(client: ReturnType<typeof renderApp>['client'], at: number) {
    vi.setSystemTime(at);
    await act(() => client.invalidateQueries({ queryKey: LEGAL_STATUS_KEY }));
  }

  it('hides a warning until the first refresh after 24 hours, also after a reload', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    serve('auditor', LEGAL_WARNING_ONLY);
    const first = renderApp(PAGE);
    const shown = await banner('crl_stale');
    await user.click(within(shown).getByRole('button', { name: '24 saat gizle' }));

    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();
    expect(
      await screen.findByText(
        'Uyarı bu tarayıcıda 24 saat gizlendi; yeni bir uyarı çıkarsa yeniden gösterilir.',
      ),
    ).toBeInTheDocument();
    // The keyboard stays on the page.
    expect(document.activeElement).toBe(document.getElementById('content'));
    expect(JSON.parse(window.localStorage.getItem(LEGAL_BANNER_HIDE_KEY) ?? 'null')).toEqual({
      until: NOW.getTime() + LEGAL_BANNER_HIDE_MS,
      codes: ['crl_stale', 'renewal_due'],
    });

    await refreshAt(first.client, NOW.getTime() + 60 * MINUTE);
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();

    // A reload (a new page in this browser) keeps the pause.
    first.unmount();
    const calls = serve('auditor', LEGAL_WARNING_ONLY);
    const second = renderApp(PAGE);
    await expectNoBanner(calls);

    await refreshAt(second.client, NOW.getTime() + LEGAL_BANNER_HIDE_MS - MINUTE);
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();
    await refreshAt(second.client, NOW.getTime() + LEGAL_BANNER_HIDE_MS);
    expect(await banner('crl_stale')).toHaveAttribute('role', 'status');
  });

  it('shows the banner again for a new reason or for danger', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    serve('auditor', LEGAL_WARNING_ONLY);
    const { client } = renderApp(PAGE);
    await user.click(
      within(await banner('crl_stale')).getByRole('button', { name: '24 saat gizle' }),
    );
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();

    // One of the hidden reasons went away: still hidden.
    served.current = legalStatusWith(['renewal_due']);
    await refreshAt(client, NOW.getTime() + MINUTE);
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();

    // A warning the user has not seen.
    served.current = legalStatusWith(['crl_stale', 'renewal_due', 'renewal_uncovered']);
    await refreshAt(client, NOW.getTime() + 2 * MINUTE);
    expect(await banner('crl_stale')).toHaveAttribute('role', 'status');

    await user.click(
      within(await banner('crl_stale')).getByRole('button', { name: '24 saat gizle' }),
    );
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();

    // Danger is never hidden.
    served.current = legalStatusWith(['crl_stale', 'renewal_due', 'unstamped_days']);
    await refreshAt(client, NOW.getTime() + 3 * MINUTE);
    expect(await banner('unstamped_days')).toHaveAttribute('role', 'alert');
  });

  it('without storage the banner shows, and "24 saat gizle" hides it on this page only', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    serve('auditor', LEGAL_WARNING_ONLY);
    const first = renderApp(PAGE);
    await user.click(
      within(await banner('crl_stale')).getByRole('button', { name: '24 saat gizle' }),
    );
    expect(screen.queryByTestId('legal-banner')).not.toBeInTheDocument();

    first.unmount();
    serve('auditor', LEGAL_WARNING_ONLY);
    renderApp(PAGE);
    expect(await banner('crl_stale')).toHaveAttribute('role', 'status');
  });
});

describe('LegalBanner — refresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    act(() => {
      focusManager.setFocused(undefined);
    });
  });

  /** Let the fake clock run in small steps until `check` passes (the app's first requests). */
  async function until(check: () => void): Promise<void> {
    for (let step = 0; ; step += 1) {
      await act(() => vi.advanceTimersByTimeAsync(10));
      try {
        check();
        return;
      } catch (error) {
        if (step >= 100) throw error;
      }
    }
  }

  it('every 5 minutes in the background, not while the tab is hidden', async () => {
    const calls = serve('auditor', LEGAL_WARNING_ONLY);
    renderApp(PAGE);
    await until(() => {
      expect(screen.getByTestId('legal-banner-crl_stale')).toHaveAttribute('role', 'status');
    });
    expect(legalCalls(calls)).toHaveLength(1);

    served.current = LEGAL_PROVIDER_NOT_LEGAL;
    await act(() => vi.advanceTimersByTimeAsync(LEGAL_STATUS_POLL_MS));
    expect(legalCalls(calls)).toHaveLength(2);
    expect(screen.getByTestId('legal-banner-provider_not_legal')).toHaveAttribute('role', 'alert');
    expect(screen.queryByTestId('legal-banner-crl_stale')).not.toBeInTheDocument();
    expect(legalCalls(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([
      null,
      '1',
    ]);

    act(() => {
      focusManager.setFocused(false);
    });
    await act(() => vi.advanceTimersByTimeAsync(3 * LEGAL_STATUS_POLL_MS));
    expect(legalCalls(calls)).toHaveLength(2);
  });
});
