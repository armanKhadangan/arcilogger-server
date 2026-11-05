import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ARCHIVE_RELOAD_POLL_MS } from '@/components/archive/archive-model';
import type { ArchiveReload } from '@/lib/archive-reload-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import type { RecordedRequest } from '@/test/api-mock';
import {
  ARCHIVE_CREATED,
  ARCHIVE_LOADING,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_REQUIRED,
} from '@/test/archive-fixtures';
import { renderApp } from '@/test/render';

import { CATALOGUE, page, row, stats } from './test-data';

/*
 * "Dışa aktar" and the days outside the database window (G8, W5-G-24): a file without some days
 * of the range is partial (`X-Arci-Export-Truncated: archived`) and the toast names the days
 * (`X-Arci-Export-Archived`, also beside `rows`); a range whose every day is gone answers 409
 * `archive_reload_required` as the search does, and the dialog shows the search's reload card with
 * the API's text instead of the red refusal; once the reload is ready the export runs again, once.
 */

const EXPORT_PATH = '/api/v1/search/export';
const RELOADS_PATH = '/api/v1/archive-reloads';
const REQUIRED_TR = ARCHIVE_SEARCH_REQUIRED.body.error.message_tr;

const WHOLE = 'arama-20261004T0000-20261010T0000.xlsx';
const PARTIAL = 'arama-20261004T0000-20261010T0000-kismi.xlsx';

/** A file of `POST /search/export` with its headers (a partial one's name ends in `-kismi`). */
function exportFile(headers: Record<string, string> = {}): Response {
  return new Response('PK', {
    status: 200,
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Disposition': `attachment; filename="${'X-Arci-Export-Truncated' in headers ? PARTIAL : WHOLE}"`,
      ...headers,
    },
  });
}

interface Server {
  /** The answers of `POST /search/export` in turn, the last one again and again. */
  exports: (() => Response)[];
  /** The answers of `GET /archive-reloads/{id}` in turn, the last one again and again. */
  reloads?: ArchiveReload[];
}

function serve({ exports, reloads = [ARCHIVE_LOADING] }: Server): RecordedRequest[] {
  let exported = 0;
  let poll = 0;
  return installFetch((request) => {
    const { method, path } = request;
    if (path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (path === '/api/v1/search/fields') return jsonResponse(200, CATALOGUE);
    if (method === 'POST' && path === '/api/v1/search')
      return jsonResponse(200, page({ items: [row()] }));
    if (method === 'POST' && path === '/api/v1/search/stats')
      return jsonResponse(200, stats({ search_id: page().search_id }));
    if (method === 'POST' && path === EXPORT_PATH) {
      const answer = exports[Math.min(exported, exports.length - 1)];
      exported += 1;
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

function posted(calls: RecordedRequest[], path: string): unknown[] {
  return calls
    .filter((call) => call.method === 'POST' && call.path === path)
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
/** The names of the files saved (jsdom has no object URLs: the link's `download` name). */
let saved: string[];

/** Searches, opens "Dışa aktar" and starts the export; returns the dialog. */
async function exportSearch(): Promise<HTMLElement> {
  renderApp('/search');
  const box = await screen.findByRole('combobox', { name: 'Sorgu' });
  await user.type(box, 'srcip:172.20.42.57{Enter}');
  await user.click(await screen.findByRole('button', { name: 'Dışa aktar' }));
  const dialog = await screen.findByRole('dialog', { name: 'Arama sonuçlarını dışa aktar' });
  await user.click(within(dialog).getByRole('button', { name: 'Dışa aktar' }));
  await settle();
  return dialog;
}

beforeEach(() => {
  // TanStack polls with `setInterval`; the rest (the query bar's delay, Radix) runs on time.
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  user = userEvent.setup({ delay: null });
  saved = [];
  Object.assign(URL, { createObjectURL: () => 'blob:arama', revokeObjectURL: () => undefined });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push(this.download);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Arama — the export and the days not in the database', () => {
  it('a file without some days is partial: the toast says so and names the days', async () => {
    serve({
      exports: [
        () =>
          exportFile({
            'X-Arci-Export-Truncated': 'archived',
            'X-Arci-Export-Archived': '2026-10-04/2026-10-05',
          }),
      ],
    });
    const dialog = await exportSearch();

    expect(
      await screen.findByText(
        'Aramanın bazı günleri veritabanında yok (mühürlü arşivde ya da yeniden yükleniyor): dosya yalnızca veritabanındaki satırları içerir. Veritabanında olmayan günler: 04.10.2026 – 05.10.2026',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Dosya kısaltıldı')).toBeInTheDocument();
    expect(within(dialog).getByText(PARTIAL)).toBeVisible();
    expect(saved).toEqual([PARTIAL]);
  });

  it('a file cut at the row limit that also misses a day names that day beside the limit', async () => {
    serve({
      exports: [
        () =>
          exportFile({
            'X-Arci-Export-Truncated': 'rows',
            'X-Arci-Export-Archived': '2026-10-04',
          }),
      ],
    });
    await exportSearch();

    expect(
      await screen.findByText(
        'Satır sınırına ulaşıldı: dosya aramanın yalnızca ilk satırlarını içerir. Veritabanında olmayan gün: 04.10.2026',
      ),
    ).toBeInTheDocument();
  });

  it('a 409 shows the reload card with the API’s text; on ready the export runs again once', async () => {
    const calls = serve({
      exports: [
        () => jsonResponse(ARCHIVE_SEARCH_REQUIRED.status, ARCHIVE_SEARCH_REQUIRED.body),
        () => exportFile(),
      ],
      reloads: [ARCHIVE_LOADING, ARCHIVE_READY],
    });
    const dialog = await exportSearch();

    const shown = await within(dialog).findByTestId('archive-reload-card');
    expect(within(shown).getByRole('status')).toHaveTextContent(REQUIRED_TR);
    // Not the red refusal: the card is announced in its place.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(posted(calls, EXPORT_PATH)).toHaveLength(1);
    expect(saved).toEqual([]);

    await user.click(within(shown).getByRole('button', { name: 'Arşivden yükle' }));
    const confirm = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
    // The keep time the 409 gives (C23).
    expect(confirm).toHaveTextContent('ve 7 gün boyunca veritabanında kalır');
    await user.click(within(confirm).getByRole('button', { name: 'Yükle' }));
    await settle();
    expect(posted(calls, RELOADS_PATH)).toEqual([
      { reason: 'search', first_day: '2026-10-04', last_day: '2026-10-05' },
    ]);
    expect(await within(dialog).findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');

    await nextPoll();
    await waitFor(() => {
      expect(posted(calls, EXPORT_PATH)).toHaveLength(2);
    });
    const [first, again] = posted(calls, EXPORT_PATH);
    expect(again).toEqual(first);
    expect(await within(dialog).findByText(WHOLE)).toBeVisible();
    expect(saved).toEqual([WHOLE]);
    expect(within(dialog).queryByTestId('archive-reload-card')).not.toBeInTheDocument();

    await nextPoll();
    await nextPoll();
    expect(posted(calls, EXPORT_PATH)).toHaveLength(2);
  });

  it('any other refusal stays the red one, with no card', async () => {
    serve({
      exports: [() => errorResponse(429, 'search_export_rate_limited', { tr: 'Saatte en çok 3.' })],
    });
    const dialog = await exportSearch();

    expect(await within(dialog).findByText('Saatte en çok 3.')).toBeVisible();
    expect(within(dialog).queryByTestId('archive-reload-card')).not.toBeInTheDocument();
  });
});
