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
}

function serve({ searches, reloads }: Server): RecordedRequest[] {
  let search = 0;
  let poll = 0;
  return installFetch((request) => {
    const { method, path } = request;
    if (path === '/api/v1/auth/me') return jsonResponse(200, makeMe('auditor'));
    if (path === '/api/v1/search/fields') return jsonResponse(200, CATALOGUE);
    if (method === 'POST' && path === '/api/v1/search/stats')
      return jsonResponse(
        200,
        stats({ search_id: (request.body as { search_id: string }).search_id }),
      );
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
    // Not the red refusal of the run, and not the empty page's invitation either.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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
    // The rows of the second answer; the card is gone with the 409.
    expect(await screen.findByRole('table', { name: 'Arama sonuçları' })).toBeInTheDocument();
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();

    await nextPoll();
    await nextPoll();
    expect(searches(calls)).toHaveLength(2);
  });

  it('a page with archived days shows the card with the search text; on ready it runs again', async () => {
    const calls = serve({
      searches: [
        () => jsonResponse(200, archivedPage()),
        () => jsonResponse(200, page({ items: [row()] })),
      ],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
    });
    renderApp('/search');
    await searchFor('srcip:172.20.42.57');

    const shown = await card();
    expect(shown).toHaveTextContent(ARCHIVED_TR);
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
    // The new answer has no archived days: no card.
    await waitFor(() => {
      expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    });

    await nextPoll();
    expect(searches(calls)).toHaveLength(2);
  });

  it('a new search meanwhile: the reload getting ready runs nothing again', async () => {
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

    // Another search starts; while it runs, the old answer and its card stay on screen.
    await searchFor('srcip:172.20.42.58');
    await waitFor(() => {
      expect(searches(calls)).toHaveLength(2);
    });
    await nextPoll();
    expect(await card()).toHaveTextContent('Günler yüklendi;');
    expect(searches(calls)).toHaveLength(2);

    answer(jsonResponse(200, page({ search_id: 'ornek-arama-02' })));
    await settle();
    await nextPoll();
    expect(searches(calls)).toEqual([
      expect.objectContaining({ q: 'srcip:172.20.42.57' }),
      expect.objectContaining({ q: 'srcip:172.20.42.58' }),
    ]);
  });
});
