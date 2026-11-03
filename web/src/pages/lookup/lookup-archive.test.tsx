import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ARCHIVE_RELOAD_POLL_MS } from '@/components/archive/archive-model';
import { BACKGROUND_HEADER } from '@/lib/api';
import type { ArchiveReload } from '@/lib/archive-reload-types';
import { setLanguage } from '@/lib/i18n';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import {
  ARCHIVE_CREATED,
  ARCHIVE_LOADING,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_READY,
} from '@/test/archive-fixtures';
import { renderApp } from '@/test/render';
import { LOOKUPS } from '@/test/samples';

/*
 * 5651 Sorgu when the records it needs are in the sealed archive (Wave 5 contract §5, G7, G10):
 * the 409 `archive_reload_required` of `POST /lookup/5651` is no failure of the form but the
 * archive card with the API's text; the reload asks for the days without a lookup id (no lookup
 * is stored before the 409), and once it is ready the same lookup body runs again, exactly once,
 * and its result opens. The card's own rules are pinned in `archive-reload-card.test.tsx`.
 */

const LOOKUP_PATH = '/api/v1/lookup/5651';
const RELOADS_PATH = '/api/v1/archive-reloads';
const LOOKUP_409_TR = ARCHIVE_LOOKUP_REQUIRED.body.error.message_tr;
const LOOKUP_409_EN = ARCHIVE_LOOKUP_REQUIRED.body.error.message_en;
/** The lookup of the capture: an internal IP on 09.10.2026, its days 04.–05.10.2026 archived. */
const RESULT = LOOKUPS.internal;

type Answer = () => Response | Promise<Response>;

const required: Answer = () =>
  errorResponse(409, 'archive_reload_required', {
    tr: LOOKUP_409_TR,
    en: LOOKUP_409_EN,
    details: ARCHIVE_LOOKUP_REQUIRED.body.error.details,
  });
const stored: Answer = () => jsonResponse(201, RESULT);

/**
 * The API for an auditor: `POST /lookup/5651` answers `lookups` in turn (the last one again and
 * again), `POST /archive-reloads` the 202 of the capture, its GETs `reloads` in turn.
 */
function serve(lookups: Answer[], reloads: ArchiveReload[] = [ARCHIVE_LOADING, ARCHIVE_READY]) {
  let lookup = 0;
  let poll = 0;
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('auditor'));
    if (request.method === 'POST' && request.path === LOOKUP_PATH) {
      const answer = lookups[Math.min(lookup, lookups.length - 1)];
      lookup += 1;
      return answer ? answer() : errorResponse(404, 'not_found');
    }
    if (request.method === 'GET' && request.path === `/api/v1/lookup/${String(RESULT.id)}`)
      return jsonResponse(200, RESULT);
    if (request.method === 'POST' && request.path === RELOADS_PATH)
      return jsonResponse(202, ARCHIVE_CREATED);
    if (
      request.method === 'GET' &&
      request.path === `${RELOADS_PATH}/${String(ARCHIVE_CREATED.id)}`
    ) {
      const answer = reloads[Math.min(poll, reloads.length - 1)];
      poll += 1;
      return answer ? jsonResponse(200, answer) : errorResponse(404, 'not_found');
    }
    return errorResponse(404, 'not_found');
  });
}

const lookupPosts = (calls: RecordedRequest[]) =>
  calls.filter((call) => call.method === 'POST' && call.path === LOOKUP_PATH);
const reloadPosts = (calls: RecordedRequest[]) =>
  calls.filter((call) => call.method === 'POST' && call.path === RELOADS_PATH);
const polls = (calls: RecordedRequest[]) =>
  calls.filter((call) => call.method === 'GET' && call.path.startsWith(`${RELOADS_PATH}/`));

const card = () => screen.getByTestId('archive-reload-card');

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

beforeEach(() => {
  // TanStack polls with `setInterval`; the rest (React Query's notifications, Radix) runs on time.
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  user = userEvent.setup({ delay: null });
  setLanguage('tr');
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

/** `/lookup` with the request details and the captured query filled in, then "Sorgula". */
async function runLookup() {
  const view = renderApp('/lookup');
  const form = await screen.findByRole('form', { name: '5651 sorgusu' });
  const field = (name: string) => within(form).getByRole('textbox', { name });
  await user.type(field('Makam'), RESULT.request.authority);
  await user.type(field('Dosya / evrak no'), RESULT.request.file_no);
  await user.type(field('Talep tarihi'), '10.10.2026');
  await user.type(field('Gerekçe'), 'Müzekkere no 2026/771: erişim kayıtları talebi.');
  await user.type(field('Tarih'), '09.10.2026');
  await user.type(field('Saat'), '11:23:41');
  await user.type(field('İç IP'), '172.20.60.142');
  await user.click(within(form).getByRole('button', { name: 'Sorgula' }));
  await settle();
  return view;
}

/** "Arşivden yükle", then "Yükle" in the confirmation. */
async function confirmReload(): Promise<void> {
  await user.click(within(card()).getByRole('button', { name: 'Arşivden yükle' }));
  const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
  await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
  await settle();
}

describe('5651 Sorgu — days in the sealed archive (409 archive_reload_required)', () => {
  it('shows the card with the API’s text instead of a failure; the reload re-runs the same lookup once', async () => {
    const calls = serve([required, stored]);
    const { router } = await runLookup();

    const region = screen.getByRole('region', { name: 'Günler mühürlü arşivde' });
    expect(region).toBe(card());
    expect(region).toHaveTextContent(LOOKUP_409_TR);
    // In place of the failure's alert: announced when it appears.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(within(region).getByRole('status')).toHaveTextContent(LOOKUP_409_TR);
    expect(router.state.location.pathname).toBe('/lookup');
    const [first] = lookupPosts(calls);
    expect(first?.body).toMatchObject({
      query: { at: '2026-10-09T11:23:41', internal_ip: '172.20.60.142' },
    });

    await user.click(within(region).getByRole('button', { name: 'Arşivden yükle' }));
    const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
    expect(dialog).toHaveTextContent('04.10.2026 – 05.10.2026 günlerinin kayıtları');
    // The keep time the 409 gives (C23), not the setting's default.
    expect(ARCHIVE_LOOKUP_REQUIRED.body.error.details.keep_days).toBe(7);
    expect(dialog).toHaveTextContent('ve 7 gün boyunca veritabanında kalır');
    expect(dialog).not.toHaveTextContent('db.reload_keep_days');
    await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
    await settle();

    // The days of the 409, no lookup id (none is stored before it).
    const [post] = reloadPosts(calls);
    expect(post?.body).toEqual({
      reason: 'lookup',
      first_day: '2026-10-04',
      last_day: '2026-10-05',
    });
    expect(post?.body).not.toHaveProperty('lookup_id');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const bar = within(card()).getByRole('progressbar', { name: 'Arşivden yükleme ilerlemesi' });
    expect(bar).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByTestId('archive-reload-status')).toHaveTextContent('Yükleniyor…');
    // Not ready yet: the lookup is not run again (it would answer 409 again).
    expect(lookupPosts(calls)).toHaveLength(1);

    await nextPoll();
    // Ready: the lookup the 409 answered runs again, unchanged, and its result opens.
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/lookup/${String(RESULT.id)}`);
    });
    const posts = lookupPosts(calls);
    expect(posts).toHaveLength(2);
    expect(posts[1]?.body).toEqual(first?.body);
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Sorgu sonuçları' })).not.toHaveTextContent(
      'Talep bilgilerini ve sorguyu girin',
    );

    // The poll has stopped and nothing runs again.
    await nextPoll();
    await nextPoll();
    expect(lookupPosts(calls)).toHaveLength(2);
    expect(polls(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([null, '1']);
    expect(reloadPosts(calls)).toHaveLength(1);
  });

  it('a 409 for the 14 days one reload holds (W5-G-17) is asked for whole, without the note', async () => {
    // The capture's lookup needs only 04.–05.10.2026; a moment about 18 days old needs all 14 days
    // of the lookup, the most one reload may ask for: its 409, with the captured texts' days moved.
    const { details, message_tr, message_en } = ARCHIVE_LOOKUP_REQUIRED.body.error;
    const fourteen = () =>
      errorResponse(409, 'archive_reload_required', {
        tr: message_tr.replace('04.10.2026 - 05.10.2026', '22.09.2026 - 05.10.2026'),
        en: message_en.replace('2026-10-04 - 2026-10-05', '2026-09-22 - 2026-10-05'),
        details: { ...details, first_day: '2026-09-22', last_day: '2026-10-05' },
      });
    const calls = serve([fourteen, stored]);
    await runLookup();
    expect(card()).toHaveTextContent('(22.09.2026 - 05.10.2026) veritabanında değil');
    expect(screen.queryByTestId('archive-reload-clipped')).not.toBeInTheDocument();

    await user.click(within(card()).getByRole('button', { name: 'Arşivden yükle' }));
    const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
    expect(dialog).toHaveTextContent('22.09.2026 – 05.10.2026 günlerinin kayıtları');
    await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
    await settle();
    expect(reloadPosts(calls).map((call) => call.body)).toEqual([
      { reason: 'lookup', first_day: '2026-09-22', last_day: '2026-10-05' },
    ]);
  });

  it('the card’s 409 text follows a switch to English', async () => {
    serve([required]);
    await runLookup();
    expect(card()).toHaveTextContent(LOOKUP_409_TR);
    act(() => {
      setLanguage('en');
    });
    const region = screen.getByRole('region', { name: 'Days in the sealed archive' });
    expect(within(region).getByRole('status')).toHaveTextContent(LOOKUP_409_EN);
    expect(region).not.toHaveTextContent(LOOKUP_409_TR);
    expect(within(region).getByRole('button', { name: 'Reload from the archive' })).toBeVisible();
  });

  it('a 409 again on the re-run is a new card, not a loop', async () => {
    const calls = serve([required], [ARCHIVE_READY]);
    await runLookup();
    await confirmReload();
    await waitFor(() => {
      expect(lookupPosts(calls)).toHaveLength(2);
    });
    await settle();
    // The new card offers the reload again; it does not run the lookup by itself.
    expect(within(card()).getByRole('button', { name: 'Arşivden yükle' })).toBeVisible();
    expect(within(card()).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(card()).toHaveTextContent(LOOKUP_409_TR);
    await nextPoll();
    await nextPoll();
    expect(lookupPosts(calls)).toHaveLength(2);
    expect(reloadPosts(calls)).toHaveLength(1);
  });

  it('another refusal of the re-run replaces the card with the failure', async () => {
    const timeout = LOOKUPS.errors.lookup_timeout;
    if (!timeout) throw new Error('no lookup_timeout sample');
    const calls = serve(
      [required, () => jsonResponse(timeout.status, timeout.body)],
      [ARCHIVE_READY],
    );
    await runLookup();
    await confirmReload();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(timeout.body.error.message_tr);
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    expect(lookupPosts(calls)).toHaveLength(2);
  });

  it('a new “Sorgula” drops the card: the form’s own lookup runs', async () => {
    let answer: (response: Response) => void = () => undefined;
    const calls = serve([
      required,
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    ]);
    const { router } = await runLookup();
    expect(card()).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Sorgula' }));
    expect(screen.queryByTestId('archive-reload-card')).not.toBeInTheDocument();
    answer(jsonResponse(201, RESULT));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/lookup/${String(RESULT.id)}`);
    });
    expect(lookupPosts(calls)).toHaveLength(2);
    expect(reloadPosts(calls)).toHaveLength(0);
    expect(polls(calls)).toHaveLength(0);
  });
});
