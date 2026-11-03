import { QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';

import { BACKGROUND_HEADER } from '@/lib/api';
import type { ArchiveReload } from '@/lib/archive-reload-types';
import { setLanguage } from '@/lib/i18n';
import { errorResponse, installFetch, jsonResponse } from '@/test/api-mock';
import type { RecordedRequest } from '@/test/api-mock';
import {
  ARCHIVE_CREATED,
  ARCHIVE_ERRORS,
  ARCHIVE_EXISTING,
  ARCHIVE_FAILED,
  ARCHIVE_LOADING,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_ARCHIVED,
  type ArchiveErrorSample,
} from '@/test/archive-fixtures';
import { testQueryClient } from '@/test/render';

import { ARCHIVE_RELOAD_POLL_MS } from './archive-model';
import { ArchiveReloadCard, type ArchiveReloadCardProps } from './archive-reload-card';

/*
 * The card of the lookup and search pages (Wave 5 contract §5, G10): the API's 409 text or the
 * search text, the note for a range longer than one reload, the confirmation (how long the days
 * stay, that the request is audited), each refusal of G5 in the dialog with the API's text, the
 * progress every 2 s, the re-run exactly once when the reload is ready, and the web's texts for a
 * failed reload.
 */

const POST_PATH = '/api/v1/archive-reloads';
const LOOKUP_409_TR = ARCHIVE_LOOKUP_REQUIRED.body.error.message_tr;
const LOOKUP_409_EN = ARCHIVE_LOOKUP_REQUIRED.body.error.message_en;
const RANGE = { first_day: '2026-10-04', last_day: '2026-10-05' };

const DIALOG_TR =
  '04.10.2026 – 05.10.2026 günlerinin kayıtları mühürlü arşivden veritabanına yüklenir ve db.reload_keep_days ayarının süresi boyunca (varsayılan 7 gün) orada kalır; sonra yeniden kaldırılır. Bu, o günlerin kişisel verilerini de veritabanına geri getirir. İstek denetim kaydına yazılır.';
const DIALOG_EN =
  'The records of 04.10.2026 – 05.10.2026 are loaded from the sealed archive into the database and stay there for the db.reload_keep_days setting (7 days by default); then they are removed again. This also brings the personal data of those days back into the database. The request is written to the audit log.';
const NOT_IN_VAULT_TR =
  'Yükleme yapılamadı: günlerden biri artık mühürlü arşivde yok (istek beklerken saklama süresi doldu). Bu gün yeniden yüklenemez.';
const UNREADABLE_TR =
  'Yükleme yapılamadı: bir günün mühürlü dosyası okunamadı veya mühür kaydıyla eşleşmiyor. Sistem yöneticisine bildirin; arşiv doğrulamasını çalıştırın.';

interface Server {
  /** The answer of the next POST. */
  post: () => Response | Promise<Response>;
  /** The answers of the GETs in turn, the last one again and again. */
  reloads: (ArchiveReload | Response)[];
}

const server: Server = { post: () => jsonResponse(202, ARCHIVE_CREATED), reloads: [] };

function serve(post: Server['post'], ...reloads: Server['reloads']): RecordedRequest[] {
  server.post = post;
  server.reloads = reloads;
  let next = 0;
  return installFetch((request) => {
    if (request.path === '/api/v1/health') return jsonResponse(200, { status: 'ok' });
    if (request.method === 'POST' && request.path === POST_PATH) return server.post();
    if (request.method === 'GET' && request.path.startsWith(`${POST_PATH}/`)) {
      const answer = server.reloads[Math.min(next, server.reloads.length - 1)];
      next += 1;
      if (answer === undefined) return errorResponse(404, 'not_found');
      return answer instanceof Response ? answer.clone() : jsonResponse(200, answer);
    }
    return errorResponse(404, 'not_found');
  });
}

function refusal({ status, body }: ArchiveErrorSample): Response {
  return jsonResponse(status, body);
}

function renderCard(props: Partial<ArchiveReloadCardProps> = {}) {
  const onReady = vi.fn();
  const client = testQueryClient();
  const all: ArchiveReloadCardProps = {
    reason: 'lookup',
    range: RANGE,
    message: LOOKUP_409_TR,
    onReady,
    ...props,
  };
  const view = render(
    <QueryClientProvider client={client}>
      <ArchiveReloadCard {...all} />
    </QueryClientProvider>,
  );
  /** The same card with other props. */
  const rerender = (next: Partial<ArchiveReloadCardProps> = {}) => {
    view.rerender(
      <QueryClientProvider client={client}>
        <ArchiveReloadCard {...all} {...next} />
      </QueryClientProvider>,
    );
  };
  return { ...view, rerender, onReady: (props.onReady as typeof onReady | undefined) ?? onReady };
}

function card(): HTMLElement {
  return screen.getByTestId('archive-reload-card');
}

function posts(calls: RecordedRequest[]): RecordedRequest[] {
  return calls.filter((call) => call.method === 'POST' && call.path === POST_PATH);
}

function polls(calls: RecordedRequest[]): RecordedRequest[] {
  return calls.filter((call) => call.method === 'GET' && call.path.startsWith(`${POST_PATH}/`));
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

beforeEach(() => {
  // TanStack polls with `setInterval`; the rest (React Query's notifications, Radix) runs on time.
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  user = userEvent.setup({ delay: null });
  setLanguage('tr');
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  // The card is still on screen: its texts change with the language.
  act(() => {
    setLanguage('tr');
  });
});

/** Opens the dialog and confirms; answers the dialog. */
async function confirmReload(button = 'Arşivden yükle'): Promise<HTMLElement> {
  await user.click(within(card()).getByRole('button', { name: button }));
  const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
  await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
  return dialog;
}

describe('ArchiveReloadCard — the card', () => {
  it('shows the API’s 409 text as a region with the reload button, nothing loading', () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED));
    renderCard();
    const region = screen.getByRole('region', { name: 'Günler mühürlü arşivde' });
    expect(region).toBe(card());
    expect(within(region).getByRole('heading', { name: 'Günler mühürlü arşivde' })).toBeVisible();
    expect(region).toHaveTextContent(LOOKUP_409_TR);
    // It takes the place of the refusal's alert: a screen reader hears it when it appears.
    const message = within(region).getByRole('status');
    expect(message).toBe(screen.getByTestId('archive-reload-message'));
    expect(message).toHaveTextContent(LOOKUP_409_TR);
    expect(within(region).getByRole('button', { name: 'Arşivden yükle' })).toBeVisible();
    expect(within(region).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('archive-reload-clipped')).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it('without a 409 text (the archived days of a search) it says what the search misses', () => {
    serve(() => jsonResponse(202, ARCHIVE_CREATED));
    renderCard({ reason: 'search', message: null, range: ARCHIVE_SEARCH_ARCHIVED.archived });
    expect(within(card()).getByRole('status')).toHaveTextContent(
      'Bu aramanın 04.10.2026 – 05.10.2026 günleri veritabanında değil; bu günlerin kayıtları mühürlü arşivde. Arşivden yüklerseniz arama bu günleri de kapsar.',
    );
    expect(card()).not.toHaveTextContent(LOOKUP_409_TR);
  });

  it('a range of more than 14 days: the 14 nearest the window are offered, with the note', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_LOADING);
    renderCard({
      reason: 'search',
      message: null,
      range: { first_day: '2026-09-05', last_day: '2026-10-05' },
    });
    expect(card()).toHaveTextContent(
      'Bu aramanın 05.09.2026 – 05.10.2026 günleri veritabanında değil;',
    );
    expect(screen.getByTestId('archive-reload-clipped')).toHaveTextContent(
      'Bir yüklemede en çok 14 gün istenebilir: pencereye en yakın 14 gün (22.09.2026 – 05.10.2026) yüklenir. Daha eski günler için aralığı daraltın.',
    );
    await user.click(screen.getByRole('button', { name: 'Arşivden yükle' }));
    const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
    expect(dialog).toHaveTextContent('22.09.2026 – 05.10.2026 günlerinin kayıtları');
    await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
    await settle();
    expect(posts(calls).map((call) => call.body)).toEqual([
      { reason: 'search', first_day: '2026-09-22', last_day: '2026-10-05' },
    ]);
  });

  it('a range of exactly 14 days is asked for whole, without the note', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_LOADING);
    renderCard({ range: { first_day: '2026-09-22', last_day: '2026-10-05' } });
    expect(screen.queryByTestId('archive-reload-clipped')).not.toBeInTheDocument();
    await confirmReload();
    await settle();
    expect(posts(calls)[0]?.body).toEqual({
      reason: 'lookup',
      first_day: '2026-09-22',
      last_day: '2026-10-05',
    });
  });
});

describe('ArchiveReloadCard — the confirmation', () => {
  it('says how long the days stay and that the request is audited; İptal sends nothing', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED));
    renderCard();
    const opener = screen.getByRole('button', { name: 'Arşivden yükle' });
    await user.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'Günler arşivden yüklensin mi?' });
    expect(dialog).toHaveAccessibleDescription(DIALOG_TR);
    expect(within(dialog).getByRole('button', { name: 'İptal' })).toHaveFocus();
    expect(within(dialog).getByRole('button', { name: 'Yükle' })).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(posts(calls)).toEqual([]);
  });

  it('POSTs the days with the reason and no lookup id; the button gives way to the progress', async () => {
    let answer: (response: Response) => void = () => undefined;
    const calls = serve(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
      ARCHIVE_CREATED,
    );
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Arşivden yükle' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Yükle' }));
    // Busy while the request runs.
    const busy = await within(dialog).findByRole('button', { name: 'İşleniyor…' });
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    expect(posts(calls)).toHaveLength(1);
    const post = posts(calls)[0];
    expect(post?.body).toEqual({
      reason: 'lookup',
      first_day: '2026-10-04',
      last_day: '2026-10-05',
    });
    expect(Object.keys(post?.body as object)).not.toContain('lookup_id');

    await act(async () => {
      answer(jsonResponse(202, ARCHIVE_CREATED));
      await vi.advanceTimersByTimeAsync(20);
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(within(card()).queryByRole('button')).not.toBeInTheDocument();
    expect(
      within(card()).getByRole('progressbar', { name: 'Arşivden yükleme ilerlemesi' }),
    ).toBeVisible();
    // The keyboard stays in the card (the button that opened the dialog is gone).
    await settle();
    expect(card()).toContainElement(document.activeElement as HTMLElement);
  });

  it.each([
    ['archive_reload_too_large', ARCHIVE_ERRORS.too_large],
    ['archive_day_not_in_vault', ARCHIVE_ERRORS.not_in_vault],
    ['archive_reload_disk_low', ARCHIVE_ERRORS.disk_low],
    ['validation_error', ARCHIVE_ERRORS.in_window],
  ] as const)('a refusal %s stays in the dialog with the API’s text', async (code, sample) => {
    expect(sample.body.error.code).toBe(code);
    const calls = serve(() => refusal(sample));
    const { onReady } = renderCard();
    const dialog = await confirmReload();
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent(sample.body.error.message_tr);
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect(within(dialog).getByRole('button', { name: 'Yükle' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(polls(calls)).toEqual([]);
    expect(onReady).not.toHaveBeenCalled();

    // Cancelled and opened again: the old refusal is gone.
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    await user.click(screen.getByRole('button', { name: 'Arşivden yükle' }));
    const again = await screen.findByRole('dialog');
    expect(within(again).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('Esc and İptal do not close the dialog while “Yükle” waits: a late refusal is seen', async () => {
    let answer: (response: Response) => void = () => undefined;
    const calls = serve(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    renderCard();
    const dialog = await confirmReload();
    await within(dialog).findByRole('button', { name: 'İşleniyor…' });
    await user.keyboard('{Escape}');
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    expect(screen.getByRole('dialog')).toBe(dialog);

    await act(async () => {
      answer(refusal(ARCHIVE_ERRORS.too_large));
      await vi.advanceTimersByTimeAsync(20);
    });
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      ARCHIVE_ERRORS.too_large.body.error.message_tr,
    );
    expect(posts(calls)).toHaveLength(1);
    // Answered: the dialog closes again.
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('the KVKK notice (403 privacy_notice_required) closes the dialog: the app shows the notice', async () => {
    serve(() => refusal(ARCHIVE_ERRORS.privacy_notice_required));
    renderCard();
    await confirmReload();
    await settle();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(card()).not.toHaveTextContent(
      ARCHIVE_ERRORS.privacy_notice_required.body.error.message_tr,
    );
    expect(screen.getByRole('button', { name: 'Arşivden yükle' })).toBeVisible();
  });

  it('another error (403 forbidden) is shown in the dialog too', async () => {
    serve(() => refusal(ARCHIVE_ERRORS.forbidden));
    renderCard();
    const dialog = await confirmReload();
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Bu işlem için yetkiniz yok.',
    );
  });
});

describe('ArchiveReloadCard — progress and the re-run', () => {
  it('queued, then loading: the bar and the bytes, polled every 2 s in the background', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_CREATED, ARCHIVE_LOADING);
    const { onReady } = renderCard();
    await confirmReload();
    await settle();
    const bar = screen.getByRole('progressbar', { name: 'Arşivden yükleme ilerlemesi' });
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
    expect(bar).toHaveAttribute('aria-valuenow', '0');
    const status = screen.getByTestId('archive-reload-status');
    expect(status).toHaveAttribute('role', 'status');
    expect(status).toHaveTextContent('Sırada: başka bir yükleme bitince başlar.');
    // The bytes change with every poll: beside the live region, not in it.
    const bytes = screen.getByTestId('archive-reload-bytes');
    expect(bytes).toHaveTextContent('0 B / 63,5 KB yüklendi (%0)');
    expect(status).not.toContainElement(bytes);
    expect(bytes.closest('[role="status"]')).toBeNull();
    expect(status).not.toHaveTextContent('63,5 KB');

    await nextPoll();
    expect(bar).toHaveAttribute('aria-valuenow', '50');
    expect(status).toHaveTextContent('Yükleniyor…');
    expect(status.textContent).toBe('Yükleniyor…');
    expect(bytes).toHaveTextContent('31,7 KB / 63,5 KB yüklendi (%50)');
    expect(onReady).not.toHaveBeenCalled();

    const gets = polls(calls);
    expect(gets.map((call) => call.path)).toEqual([
      '/api/v1/archive-reloads/1',
      '/api/v1/archive-reloads/1',
    ]);
    expect(gets.map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([null, '1']);
  });

  it('ready: the days stay until expires_at, the page runs again exactly once', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_LOADING, ARCHIVE_READY);
    const view = renderCard();
    await confirmReload();
    await settle();
    expect(view.onReady).not.toHaveBeenCalled();
    await nextPoll();
    expect(view.onReady).toHaveBeenCalledTimes(1);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '100');
    const status = screen.getByTestId('archive-reload-status');
    expect(status).toHaveTextContent(
      'Günler yüklendi; 20.10.2026 07:01:26 tarihine kadar veritabanında kalır.',
    );
    expect(status).toHaveTextContent('Sorgu yeniden çalıştırılıyor…');
    expect(status).not.toHaveTextContent('63,5 KB');
    expect(screen.getByTestId('archive-reload-bytes')).toHaveTextContent(
      '63,5 KB / 63,5 KB yüklendi (%100)',
    );
    expect(within(card()).queryByRole('button')).not.toBeInTheDocument();

    // No more polls, and neither re-renders nor a new callback run the page again.
    const asked = polls(calls).length;
    await act(() => vi.advanceTimersByTimeAsync(10 * ARCHIVE_RELOAD_POLL_MS));
    expect(polls(calls)).toHaveLength(asked);
    view.rerender();
    const next = vi.fn();
    view.rerender({ onReady: next });
    await settle();
    expect(view.onReady).toHaveBeenCalledTimes(1);
    expect(next).not.toHaveBeenCalled();
  });

  it('the search card says the search runs again', async () => {
    serve(() => jsonResponse(202, { ...ARCHIVE_CREATED, reason: 'search' }), {
      ...ARCHIVE_READY,
      reason: 'search',
    });
    const { onReady } = renderCard({ reason: 'search', message: null });
    await confirmReload();
    await settle();
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('archive-reload-status')).toHaveTextContent(
      'Arama yeniden çalıştırılıyor…',
    );
  });

  it('the POST answers the user’s own reload that is already ready: the page runs again once', async () => {
    const calls = serve(() => jsonResponse(202, ARCHIVE_EXISTING), ARCHIVE_EXISTING);
    const { onReady } = renderCard();
    await confirmReload();
    await settle();
    await nextPoll();
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(polls(calls).length).toBeLessThanOrEqual(1);
  });

  it('never shows who asked for the reload (an administrator’s POST may answer another user’s)', async () => {
    // Manager on #112: "Never show who else asked for a reload."
    const other = { requested_by: 'tolga' };
    const shown = () => {
      expect(document.body).not.toHaveTextContent('tolga');
      expect(document.body).not.toHaveTextContent(ARCHIVE_CREATED.requested_by);
    };
    serve(
      () => jsonResponse(202, { ...ARCHIVE_CREATED, ...other }),
      { ...ARCHIVE_LOADING, ...other },
      { ...ARCHIVE_FAILED, id: 1, ...other },
    );
    renderCard();
    await confirmReload();
    await settle();
    expect(screen.getByTestId('archive-reload-status')).toHaveTextContent('Yükleniyor…');
    shown();
    await nextPoll();
    expect(within(card()).getByRole('alert')).toHaveTextContent(UNREADABLE_TR);
    shown();

    server.post = () => jsonResponse(202, { ...ARCHIVE_READY, id: 4, ...other });
    server.reloads = [{ ...ARCHIVE_READY, id: 4, ...other }];
    await confirmReload('Yeniden dene');
    await settle();
    expect(screen.getByTestId('archive-reload-status')).toHaveTextContent('Günler yüklendi;');
    shown();
  });

  it('in StrictMode (effects run twice) the page runs again once as well', async () => {
    serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_READY);
    const onReady = vi.fn();
    render(
      <StrictMode>
        <QueryClientProvider client={testQueryClient()}>
          <ArchiveReloadCard
            reason="lookup"
            range={RANGE}
            message={LOOKUP_409_TR}
            onReady={onReady}
          />
        </QueryClientProvider>
      </StrictMode>,
    );
    await confirmReload();
    await settle();
    await nextPoll();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it('a failed poll shows its error and polling goes on', async () => {
    serve(
      () => jsonResponse(202, ARCHIVE_CREATED),
      errorResponse(503, 'service_unavailable', {
        tr: 'Hizmet şu anda kullanılamıyor.',
        en: 'The service is unavailable.',
      }),
      ARCHIVE_READY,
    );
    const { onReady } = renderCard();
    await confirmReload();
    await settle();
    expect(within(card()).getByRole('alert')).toHaveTextContent('Hizmet şu anda kullanılamıyor.');
    await nextPoll();
    expect(within(card()).queryByRole('alert')).not.toBeInTheDocument();
    expect(onReady).toHaveBeenCalledTimes(1);
  });
});

describe('ArchiveReloadCard — failed and expired', () => {
  it.each([
    ['archive_file_unreadable', UNREADABLE_TR],
    ['archive_day_not_in_vault', NOT_IN_VAULT_TR],
    ['archive_something_new', 'Yükleme yapılamadı (archive_something_new).'],
  ])('failed with %s: the web’s text and “Yeniden dene”', async (code, text) => {
    const failed: ArchiveReload = { ...ARCHIVE_FAILED, id: 1, error_code: code };
    const calls = serve(() => jsonResponse(202, ARCHIVE_CREATED), ARCHIVE_LOADING, failed);
    const { onReady } = renderCard();
    await confirmReload();
    await settle();
    await nextPoll();
    const alert = within(card()).getByRole('alert');
    expect(alert).toHaveTextContent(text);
    expect(alert.textContent).toBe(text);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(onReady).not.toHaveBeenCalled();
    const asked = polls(calls).length;
    await act(() => vi.advanceTimersByTimeAsync(5 * ARCHIVE_RELOAD_POLL_MS));
    expect(polls(calls)).toHaveLength(asked);

    // Asked again: a new reload, followed from the start.
    server.post = () => jsonResponse(202, { ...ARCHIVE_CREATED, id: 4 });
    server.reloads = [{ ...ARCHIVE_READY, id: 4 }];
    await confirmReload('Yeniden dene');
    await settle();
    await nextPoll();
    expect(posts(calls)).toHaveLength(2);
    expect(polls(calls).at(-1)?.path).toBe('/api/v1/archive-reloads/4');
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(within(card()).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('expired: the days were removed again, the reload is offered again', async () => {
    serve(() => jsonResponse(202, ARCHIVE_CREATED), { ...ARCHIVE_READY, status: 'expired' });
    const { onReady } = renderCard();
    await confirmReload();
    await settle();
    expect(card()).toHaveTextContent(
      'Yüklenen günlerin süresi doldu; veritabanından yeniden kaldırıldılar.',
    );
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(within(card()).getByRole('button', { name: 'Yeniden dene' })).toBeVisible();
    expect(onReady).not.toHaveBeenCalled();
  });
});

describe('ArchiveReloadCard — English', () => {
  it('the texts, the refusal and the progress in English', async () => {
    setLanguage('en');
    let refuse = true;
    serve(
      () => (refuse ? refusal(ARCHIVE_ERRORS.too_large) : jsonResponse(202, ARCHIVE_CREATED)),
      ARCHIVE_LOADING,
      { ...ARCHIVE_FAILED, id: 1, error_code: 'archive_day_not_in_vault' },
    );
    renderCard({ message: LOOKUP_409_EN });
    const region = screen.getByRole('region', { name: 'Days in the sealed archive' });
    expect(region).toHaveTextContent(LOOKUP_409_EN);
    await user.click(within(region).getByRole('button', { name: 'Reload from the archive' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reload the days from the archive?' });
    expect(dialog).toHaveAccessibleDescription(DIALOG_EN);
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: 'Reload' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      ARCHIVE_ERRORS.too_large.body.error.message_en,
    );

    refuse = false;
    await user.click(within(dialog).getByRole('button', { name: 'Reload' }));
    await settle();
    expect(screen.getByRole('progressbar', { name: 'Archive reload progress' })).toHaveAttribute(
      'aria-valuenow',
      '50',
    );
    const status = screen.getByTestId('archive-reload-status');
    expect(status).toHaveTextContent('Loading…');
    expect(screen.getByTestId('archive-reload-bytes')).toHaveTextContent(
      '31.7 KB of 63.5 KB loaded (50%)',
    );

    await nextPoll();
    expect(within(region).getByRole('alert')).toHaveTextContent(
      'The reload failed: one of the days is no longer in the sealed archive (removed by retention while the request waited). That day cannot be reloaded.',
    );
    expect(within(region).getByRole('button', { name: 'Try again' })).toBeVisible();
  });

  it('the search text, the clipped note, ready and expired in English', async () => {
    setLanguage('en');
    serve(() => jsonResponse(202, ARCHIVE_CREATED), {
      ...ARCHIVE_READY,
      reason: 'search',
    });
    const { onReady } = renderCard({
      reason: 'search',
      message: null,
      range: { first_day: '2026-09-01', last_day: '2026-10-05' },
    });
    expect(card()).toHaveTextContent(
      'The days 01.09.2026 – 05.10.2026 of this search are not in the database; their records are in the sealed archive. Reload them from the archive and the search covers them too.',
    );
    expect(screen.getByTestId('archive-reload-clipped')).toHaveTextContent(
      'One reload may ask for at most 14 days: the 14 days nearest the window (22.09.2026 – 05.10.2026) are reloaded. For older days, narrow the range.',
    );
    await user.click(screen.getByRole('button', { name: 'Reload from the archive' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Reload' }));
    await settle();
    expect(onReady).toHaveBeenCalledTimes(1);
    const status = screen.getByTestId('archive-reload-status');
    expect(status).toHaveTextContent(
      'The days are loaded; they stay in the database until 20.10.2026 07:01:26.',
    );
    expect(status).toHaveTextContent('Running the search again…');
  });

  it('an unknown failure and expired in English', async () => {
    setLanguage('en');
    serve(() => jsonResponse(202, ARCHIVE_CREATED), {
      ...ARCHIVE_FAILED,
      id: 1,
      error_code: 'archive_other',
    });
    renderCard({ message: LOOKUP_409_EN });
    await user.click(screen.getByRole('button', { name: 'Reload from the archive' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reload' }),
    );
    await settle();
    expect(within(card()).getByRole('alert').textContent).toBe(
      'The reload failed (archive_other).',
    );

    server.post = () => jsonResponse(202, { ...ARCHIVE_CREATED, id: 5 });
    server.reloads = [{ ...ARCHIVE_READY, id: 5, status: 'expired' }];
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reload' }),
    );
    await settle();
    await nextPoll();
    expect(card()).toHaveTextContent(
      'The reloaded days have expired and were removed from the database again.',
    );
  });
});
