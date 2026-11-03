import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ARCHIVE_RELOAD_POLL_MS } from '@/components/archive/archive-model';
import type { ArchiveReload } from '@/lib/archive-reload-types';
import type { SearchPage } from '@/lib/search-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import type { RecordedRequest } from '@/test/api-mock';
import {
  ARCHIVE_CREATED,
  ARCHIVE_LOADING,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_ARCHIVED,
  ARCHIVE_SEARCH_REQUIRED,
} from '@/test/archive-fixtures';
import { renderApp } from '@/test/render';

import { CATALOGUE, page, row, stats } from './test-data';

/*
 * Arama and the days outside the database window (Wave 5 contract §5, G8, G10): a 409
 * `archive_reload_required` shows the card with the API's text instead of the red refusal, a
 * page with `archived` days shows the card beside its rows; once the reload is ready the same
 * search runs again by itself, once — and not at all when the user has started another search
 * meanwhile.
 */

const SEARCH_PATH = '/api/v1/search';
const RELOADS_PATH = '/api/v1/archive-reloads';
const REQUIRED_TR = ARCHIVE_SEARCH_REQUIRED.body.error.message_tr;
const ARCHIVED_TR =
  'Bu aramanın 04.10.2026 – 05.10.2026 günleri veritabanında değil; bu günlerin kayıtları mühürlü arşivde. Arşivden yüklerseniz arama bu günleri de kapsar.';

/** A search answer whose range reaches before the window (`archived` is not on wave-5's type). */
function archivedPage(overrides: Partial<SearchPage> = {}): SearchPage {
  return {
    ...page({ search_id: 'ornek-arama-arsiv', range: ARCHIVE_SEARCH_ARCHIVED.range, items: [] }),
    archived: ARCHIVE_SEARCH_ARCHIVED.archived,
    ...overrides,
  } as SearchPage;
}

interface Server {
  /** The answers of `POST /search` in turn, the last one again and again. */
  searches: (() => Response | Promise<Response>)[];
  /** The answers of `GET /archive-reloads/{id}` in turn, the last one again and again. */
  reloads: ArchiveReload[];
  /** The answer of `POST /search/stats` for a search id, when not the usual statistics. */
  statsOf?: (searchId: string) => Response | Promise<Response> | null;
}

function serve({ searches, reloads, statsOf }: Server): RecordedRequest[] {
  let search = 0;
  let poll = 0;
  return installFetch((request) => {
    const { method, path } = request;
    if (path === '/api/v1/auth/me') return jsonResponse(200, makeMe('auditor'));
    if (path === '/api/v1/search/fields') return jsonResponse(200, CATALOGUE);
    if (method === 'POST' && path === '/api/v1/search/stats') {
      const searchId = (request.body as { search_id: string }).search_id;
      return statsOf?.(searchId) ?? jsonResponse(200, stats({ search_id: searchId }));
    }
    if (method === 'POST' && path === SEARCH_PATH) {
      const answer = searches[Math.min(search, searches.length - 1)];
      search += 1;
      return answer ? answer() : errorResponse(404, 'not_found');
    }
    if (method === 'POST' && path === RELOADS_PATH) return jsonResponse(202, ARCHIVE_CREATED);
    if (method === 'GET' && path.startsWith(`${RELOADS_PATH}/`)) {
      const answer = reloads[Math.min(poll, reloads.length - 1)];
      poll += 1;
      return answer ? jsonResponse(200, answer) : errorResponse(404, 'not_found');
    }
    return errorResponse(404, 'not_found');
  });
}

function searches(calls: RecordedRequest[]): unknown[] {
  return calls
    .filter((call) => call.method === 'POST' && call.path === SEARCH_PATH)
    .map((call) => call.body);
}

function reloadPosts(calls: RecordedRequest[]): unknown[] {
  return calls
    .filter((call) => call.method === 'POST' && call.path === RELOADS_PATH)
    .map((call) => call.body);
}

/** The answers of the requests on their way arrive (in real time: only the poll is faked). */
async function settle(): Promise<void> {
  await act(
    () =>
      new Promise((resolve) => {
        setTimeout(resolve, 50);
      }),
  );
}

/** 2 s later: the next poll, and its answer. */
async function nextPoll(): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(ARCHIVE_RELOAD_POLL_MS));
  await settle();
}

let user: ReturnType<typeof userEvent.setup>;

/** Opens Arama and searches `q`. */
async function searchFor(q: string): Promise<void> {
  const box = await screen.findByRole('combobox', { name: 'Sorgu' });
  await user.clear(box);
  await user.type(box, `${q}{Enter}`);
}

async function card(): Promise<HTMLElement> {
  return screen.findByTestId('archive-reload-card');
}

/** The rows (histogram, table) the focus goes to when the card goes with the re-run. */
function results(): HTMLElement {
  return screen.getByRole('region', { name: 'Arama sonuçları' });
}

/** "Arşivden yükle", then "Yükle" in the confirmation. */
async function confirmReload(): Promise<void> {
  await user.click(within(await card()).getByRole('button', { name: 'Arşivden yükle' }));
  const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
  await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
  await settle();
}

beforeEach(() => {
  // TanStack polls with `setInterval`; the rest (the query bar's delay, Radix) runs on time.
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  user = userEvent.setup({ delay: null });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Arama — days in the sealed archive', () => {
  it('a 409 shows the card with the API’s text; on ready the same search runs again once', async () => {
    const calls = serve({
      searches: [
        () => jsonResponse(ARCHIVE_SEARCH_REQUIRED.status, ARCHIVE_SEARCH_REQUIRED.body),
        () => jsonResponse(200, page()),
      ],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
    });
    renderApp('/search');
    await searchFor('srcip:172.20.42.57');

    const shown = await card();
    expect(shown).toHaveTextContent(REQUIRED_TR);
    // Not the red refusal of the run, and not the empty page's invitation either; it is announced
    // in its place.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(within(shown).getByRole('status')).toHaveTextContent(REQUIRED_TR);
    expect(screen.queryByText('Bir sorgu yazıp “Ara”ya basın')).not.toBeInTheDocument();

    await confirmReload();
    expect(reloadPosts(calls)).toEqual([
      { reason: 'search', first_day: '2026-10-04', last_day: '2026-10-05' },
    ]);
    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(searches(calls)).toHaveLength(1);

    await nextPoll();
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(2);
    });
    const [ran, again] = searches(calls);
    expect(again).toEqual(ran);
    expect(ran).toMatchObject({ q: 'srcip:172.20.42.57' });
    // The rows of the second answer; the card is gone with the 409, and the focus that was in it
    // is on the results, not lost to the page.
    expect(await screen.findByRole('table', { name: 'Arama sonuçları' })).toBeInTheDocument();
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(results()).toHaveFocus();
    });

    await nextPoll();
    await nextPoll();
    expect(searches(calls)).toHaveLength(2);
  });

  it('a page with archived days shows the card with the search text; on ready it runs again', async () => {
    const calls = serve({
      searches: [
        () =>
          jsonResponse(
            200,
            archivedPage({
              notices: [
                {
                  code: 'vault_only_before',
                  message_tr: 'Eski kayıtlar mühürlü arşivde.',
                  message_en: 'Older records are in the sealed vault.',
                  params: {},
                },
              ],
            }),
          ),
        () => jsonResponse(200, page({ items: [row()] })),
      ],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
    });
    renderApp('/search');
    await searchFor('srcip:172.20.42.57');

    const shown = await card();
    expect(within(shown).getByRole('status')).toHaveTextContent(ARCHIVED_TR);
    // Both stay; the card first, the notice (the evidence-package route) after it (#112).
    const notice = screen.getByText('Eski kayıtlar mühürlü arşivde.');
    expect(shown.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(shown).not.toHaveTextContent(REQUIRED_TR);
    // The answer's rows (none here) are shown beside it.
    expect(await screen.findByRole('table', { name: 'Arama sonuçları' })).toBeInTheDocument();

    await confirmReload();
    expect(reloadPosts(calls)).toEqual([
      { reason: 'search', first_day: '2026-10-04', last_day: '2026-10-05' },
    ]);
    await nextPoll();
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(2);
    });
    const [ran, again] = searches(calls);
    expect(again).toEqual(ran);
    // The new answer has no archived days: no card; the focus goes to its rows.
    await waitFor(() => {
      expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    });
    expect(results()).toHaveFocus();

    await nextPoll();
    expect(searches(calls)).toHaveLength(2);
  });

  it('a new search meanwhile: its card goes, nothing runs again; its answer has a new card', async () => {
    let answer: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const calls = serve({
      searches: [() => jsonResponse(200, archivedPage()), () => pending],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
    });
    renderApp('/search');
    await searchFor('srcip:172.20.42.57');
    await confirmReload();
    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');

    // Another search starts; while it runs, the old answer stays on screen, but not its card:
    // its reload getting ready would run nothing again.
    await searchFor('srcip:172.20.42.58');
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(2);
    });
    expect(screen.getByRole('table', { name: 'Arama sonuçları' })).toBeInTheDocument();
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    await nextPoll();
    expect(screen.queryByText('Arama yeniden çalıştırılıyor…')).not.toBeInTheDocument();
    expect(searches(calls)).toHaveLength(2);

    // Its answer has the same archived days: a new card, from the start.
    answer(jsonResponse(200, archivedPage({ search_id: 'ornek-arama-02' })));
    const again = await card();
    expect(within(again).getByRole('button', { name: 'Arşivden yükle' })).toBeVisible();
    expect(within(again).queryByRole('progressbar')).not.toBeInTheDocument();
    await nextPoll();
    expect(searches(calls)).toEqual([
      expect.objectContaining({ q: 'srcip:172.20.42.57' }),
      expect.objectContaining({ q: 'srcip:172.20.42.58' }),
    ]);
  });

  it('the expired search id is renewed meanwhile: the card keeps its reload and runs again once', async () => {
    let expire: (response: Response) => void = () => undefined;
    const expired = new Promise<Response>((resolve) => {
      expire = resolve;
    });
    const calls = serve({
      searches: [
        () => jsonResponse(200, archivedPage()),
        () => jsonResponse(200, archivedPage({ search_id: 'ornek-arama-arsiv-2' })),
        () => jsonResponse(200, page({ items: [row()] })),
      ],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
      // The statistics of the first id find it expired: the page runs the same search again.
      statsOf: (searchId) => (searchId === 'ornek-arama-arsiv' ? expired : null),
    });
    renderApp('/search');
    await searchFor('srcip:172.20.42.57');
    await confirmReload();
    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');

    expire(errorResponse(409, 'search_expired'));
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(2);
    });
    await settle();
    // The same search under a new id: the same card, still following its reload.
    expect(within(await card()).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(within(await card()).queryByRole('button')).not.toBeInTheDocument();

    await nextPoll();
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(3);
    });
    const [ran, renewed, again] = searches(calls);
    expect(renewed).toEqual(ran);
    expect(again).toEqual(ran);
    await nextPoll();
    await nextPoll();
    expect(searches(calls)).toHaveLength(3);
    expect(reloadPosts(calls)).toHaveLength(1);
  });
});
