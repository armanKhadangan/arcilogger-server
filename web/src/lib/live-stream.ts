/**
 * The Canlı Akış connection (contract 2.14.4–2.14.6): one live tail stream from opening to
 * closing, with pause, resume and reconnects.
 *
 * - `POST /live/streams` opens the stream (a user action: it counts as activity; the server
 *   audits `live.open`). Its refusals are ordinary API errors (`lib/api.ts`, with the app's
 *   global reactions: sign-in, KVKK notice).
 * - `GET /live/streams/{id}` reads the server-sent events with `fetch` and a `ReadableStream`
 *   (an `EventSource` can send neither `X-Arci-Background` nor `Last-Event-ID` on its first
 *   request). Every GET — the first, a reconnect, a resume — sends `X-Arci-Background: 1`: an
 *   open stream and its keep-alives never keep a session alive, so an unattended screen is
 *   signed out at `auth.idle_minutes` like every other page.
 * - After a network error (or a body that ends without `end`, or 3 keep-alive periods of silence)
 *   it waits 1, 2, 5, 10, then 30 s and asks again with `Last-Event-ID`; the server sends the
 *   missed lines from its buffer or a `gap`. No reconnect after 401, 403, 429 or an `end` event —
 *   except `end server_stopping`, whose text promises one. A 404 (the stream was closed, lingered
 *   out or the API restarted) opens one new stream on its own (a new `live.open`) and reports
 *   `reopened`: the lines in between may be missing.
 * - Pause aborts the GET (the server keeps the stream 120 s); resume asks again with
 *   `Last-Event-ID`. Close aborts it and sends `DELETE` (audited `live.close`). A stream that
 *   replaces another (a changed filter) is opened once that `DELETE` is answered: the server
 *   counts a user's open streams when it opens one (`live.max_streams_per_user`).
 *
 * The SSE frame parser ({@link SseParser}) and the waits ({@link reconnectDelay}) are separate,
 * so `live-stream.test.ts` checks them on their own.
 */

import { ApiError, BACKGROUND_HEADER, api, buildUrl, reactGlobally } from './api';
import i18n from './i18n';
import type {
  LiveDropped,
  LiveEnd,
  LiveGap,
  LiveHello,
  LiveLine,
  LiveStreamIn,
  LiveStreamOut,
} from './live-types';

// -- server-sent events ---------------------------------------------------------------------------

/** One dispatched event: its type (`message` when none was given), data and the last event id. */
export interface SseFrame {
  event: string;
  data: string;
  /** The last event id after this frame (`id:` persists across events, as in the standard). */
  id: string | null;
}

/**
 * The `text/event-stream` format (WHATWG HTML 9.2.6), fed with decoded text in chunks of any size:
 * lines end with CRLF, LF or CR (a CR at the end of a chunk may be the first half of a CRLF),
 * `data:` lines join with a line feed, `:` starts a comment, a blank line dispatches the event.
 */
export class SseParser {
  /** The `id:` of the newest event that had one. */
  lastEventId: string | null = null;
  /** The server's `retry:` in milliseconds (read, not used: the waits are fixed). */
  retry: number | null = null;
  /** Comment lines seen (the server's `: ka` keep-alives). */
  comments = 0;
  private buffer = '';
  private started = false;
  private skipLineFeed = false;
  private event = '';
  private data: string[] = [];

  push(chunk: string): SseFrame[] {
    let text = chunk;
    if (!this.started && text.length > 0) {
      this.started = true;
      if (text.startsWith('﻿')) text = text.slice(1);
    }
    if (this.skipLineFeed && text.startsWith('\n')) text = text.slice(1);
    this.skipLineFeed = false;
    this.buffer += text;
    const frames: SseFrame[] = [];
    let start = 0;
    for (let index = 0; index < this.buffer.length; index += 1) {
      const char = this.buffer[index];
      if (char !== '\n' && char !== '\r') continue;
      const frame = this.line(this.buffer.slice(start, index));
      if (frame) frames.push(frame);
      if (char === '\r') {
        if (index + 1 === this.buffer.length) this.skipLineFeed = true;
        else if (this.buffer[index + 1] === '\n') index += 1;
      }
      start = index + 1;
    }
    this.buffer = this.buffer.slice(start);
    return frames;
  }

  private line(line: string): SseFrame | null {
    if (line === '') return this.dispatch();
    if (line.startsWith(':')) {
      this.comments += 1;
      return null;
    }
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') this.data.push(value);
    else if (field === 'id' && !value.includes('\u0000')) this.lastEventId = value;
    else if (field === 'retry' && /^\d+$/.test(value)) this.retry = Number(value);
    return null;
  }

  private dispatch(): SseFrame | null {
    const event = this.event || 'message';
    const data = this.data;
    this.event = '';
    this.data = [];
    if (data.length === 0) return null;
    return { event, data: data.join('\n'), id: this.lastEventId };
  }
}

// -- reconnecting ---------------------------------------------------------------------------------

/** The waits before the 1st, 2nd, … reconnect (the last one repeats). */
export const RECONNECT_DELAYS_MS: readonly number[] = [1_000, 2_000, 5_000, 10_000, 30_000];
/** Silence (no event, no keep-alive) after which the connection counts as lost: 3 periods. */
export const STALE_KEEPALIVES = 3;
const DEFAULT_KEEPALIVE_SECONDS = 15;

/** The wait before reconnect number `attempt` (0 = the first after the connection was lost). */
export function reconnectDelay(attempt: number): number {
  const last = RECONNECT_DELAYS_MS.length - 1;
  return RECONNECT_DELAYS_MS[Math.min(Math.max(attempt, 0), last)] ?? 30_000;
}

/** `end` codes that mean the session ended: the app's sign-in page explains them. */
export const SESSION_END_CODES: readonly string[] = [
  'session_idle',
  'session_expired',
  'session_revoked',
  'session_invalid',
  'user_inactive',
  'mfa_required',
];
/** `end` codes of the account that the app's own flows handle (KVKK notice, password change). */
const ACCOUNT_END_CODES: readonly string[] = [
  'privacy_notice_required',
  'password_change_required',
];
/** The one `end` after which the stream comes back on its own (the API is restarting). */
export const RECONNECTING_END_CODE = 'server_stopping';

/**
 * - `connecting`: opened or resumed, no `hello` yet; `connected`: events arrive;
 * - `reconnecting`: the connection was lost, waiting for the next try;
 * - `paused`: the user paused (the server keeps the stream for 120 s);
 * - `stopped`: ended by the server (`end`), refused, or closed.
 */
export type LiveConnectionState =
  'connecting' | 'connected' | 'reconnecting' | 'paused' | 'stopped';

export interface LiveConnectionEvents {
  state: (state: LiveConnectionState) => void;
  hello: (hello: LiveHello) => void;
  line: (line: LiveLine) => void;
  dropped: (dropped: LiveDropped) => void;
  gap: (gap: LiveGap) => void;
  /** The server ended the stream (`time_limit`, `slow_client`, a session or permission code). */
  end: (end: LiveEnd) => void;
  /** The stream was gone (404) and a new one opened on its own: lines may be missing between. */
  reopened: () => void;
  /** A refusal (the POST, or the GET's 401, 403, 429, a 404 again): no reconnect. */
  failed: (error: ApiError) => void;
}

export interface LiveConnectionOptions {
  q: string;
  /** Matching lines from before the stream opened; absent: the server's `live.backfill_lines`. */
  backfill?: number;
  on: Partial<LiveConnectionEvents>;
  /** Injected by the tests; `window.fetch` otherwise. */
  fetch?: typeof fetch;
}

/** Marks the abort of a connection that went silent (it reconnects; a pause does not). */
const STALE = 'stale';

function fixedTexts(key: string, options?: Record<string, unknown>): [string, string] {
  return [i18n.getFixedT('tr')(key, options), i18n.getFixedT('en')(key, options)];
}

/** The API error of a refused GET (the API's error body, else a generic HTTP text). */
async function refusalOf(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null; // e.g. the reverse proxy's own error page while the API restarts
  }
  const error = (body as { error?: Record<string, unknown> } | null)?.error;
  if (
    error &&
    typeof error.code === 'string' &&
    typeof error.message_tr === 'string' &&
    typeof error.message_en === 'string'
  ) {
    return new ApiError({
      status: response.status,
      code: error.code,
      messageTr: error.message_tr,
      messageEn: error.message_en,
      details: error.details,
    });
  }
  const [messageTr, messageEn] = fixedTexts('errors.http', { status: response.status });
  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    messageTr,
    messageEn,
  });
}

/** One live tail stream (see the module text). */
export class LiveConnection {
  state: LiveConnectionState = 'stopped';
  streamId: string | null = null;
  /** `<epoch>:<seq>` of the newest line received: sent as `Last-Event-ID` on a reconnect. */
  lastEventId: string | null = null;
  private readonly q: string;
  private readonly backfill: number | undefined;
  private readonly on: Partial<LiveConnectionEvents>;
  private readonly fetchImpl: typeof fetch;
  private controller: AbortController | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private keepaliveSeconds = DEFAULT_KEEPALIVE_SECONDS;
  private attempt = 0;
  /** A 404 already opened a new stream since the last `hello` (the next one is a refusal). */
  private reopened = false;
  private closed = false;
  /** The `POST` of this stream (or of a new one after a 404) until it is answered. */
  private opening: Promise<void> | null = null;

  constructor(options: LiveConnectionOptions) {
    this.q = options.q;
    this.backfill = options.backfill;
    this.on = options.on;
    this.fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
  }

  /**
   * Open the stream (`POST`) and start reading it. A refusal is reported as `failed`. `previous`:
   * the stream this one replaces, closed first: the server counts a user's open streams when it
   * opens one, so the `POST` waits until the old stream's `DELETE` is answered.
   */
  open(previous?: LiveConnection | null): Promise<void> {
    if (this.closed) return Promise.resolve();
    const closing = previous?.close();
    this.setState('connecting');
    this.opening = this.begin(closing);
    return this.opening;
  }

  private async begin(closing: Promise<void> | undefined): Promise<void> {
    await closing;
    if (this.isClosed()) return;
    try {
      await this.create(this.backfill, false);
    } catch (error) {
      if (!this.isClosed()) this.fail(error);
      return;
    }
    // Closed while the stream was being opened: `close` deletes it now that its id is known.
    if (this.isClosed()) return;
    if (this.state !== 'paused') void this.connect();
  }

  /** Stop reading; the server keeps the stream (and its slot) for 120 s. */
  pause(): void {
    if (this.closed || this.state === 'paused' || this.state === 'stopped') return;
    this.disconnect();
    this.setState('paused');
  }

  /** Read on from the newest line received (`Last-Event-ID`). */
  resume(): void {
    if (this.closed || this.state !== 'paused') return;
    this.attempt = 0;
    this.setState('connecting');
    void this.connect();
  }

  /**
   * Stop for good and close the stream on the server (`DELETE`, best effort). `background`: not
   * the user's doing (the page was left), so it does not count as activity.
   */
  async close({ background = false }: { background?: boolean } = {}): Promise<void> {
    const open = !this.closed;
    this.closed = true;
    this.disconnect();
    this.setState('stopped');
    if (!open) return;
    // A stream still being opened is deleted once the server has said which one it is.
    await this.opening;
    await this.remove(background);
  }

  private async remove(background: boolean): Promise<void> {
    const id = this.streamId;
    if (id === null) return;
    try {
      await api.delete(`/live/streams/${encodeURIComponent(id)}`, { background, silent: true });
    } catch {
      // Gone already (ended, lingered out, the API restarted): nothing to close.
    }
  }

  private async create(backfill: number | undefined, background: boolean): Promise<void> {
    const body: LiveStreamIn = backfill === undefined ? { q: this.q } : { q: this.q, backfill };
    const created = await api.post<LiveStreamOut>('/live/streams', body, { background });
    this.streamId = created.stream_id;
    this.lastEventId = null;
  }

  private async connect(): Promise<void> {
    const id = this.streamId;
    if (this.closed || id === null) return;
    const controller = new AbortController();
    this.controller = controller;
    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
      [BACKGROUND_HEADER]: '1',
    };
    if (this.lastEventId) headers['Last-Event-ID'] = this.lastEventId;
    let response: Response;
    try {
      response = await this.fetchImpl(buildUrl(`/live/streams/${encodeURIComponent(id)}`), {
        method: 'GET',
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
      });
    } catch {
      if (this.controller === controller && !this.aborted(controller)) this.retry();
      return;
    }
    if (this.controller !== controller) return;
    if (!response.ok) {
      await this.refused(response);
      return;
    }
    let ended = false;
    try {
      ended = await this.read(response, controller);
    } catch {
      // A network error while reading, or the abort of a pause or close.
    }
    this.clearStale();
    if (ended || this.controller !== controller || this.aborted(controller)) return;
    // The body ended without `end` (the server went away) or the connection went silent.
    this.retry();
  }

  /** `true` when the controller was aborted by a pause or close (not because it went silent). */
  private aborted(controller: AbortController): boolean {
    return controller.signal.aborted && controller.signal.reason !== STALE;
  }

  /** Read events until the body ends; `true` when an `end` event closed the stream. */
  private async read(response: Response, controller: AbortController): Promise<boolean> {
    const body = response.body;
    if (!body) return false;
    const reader = body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    this.watch(controller);
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) return false;
      this.watch(controller);
      for (const frame of parser.push(decoder.decode(chunk.value, { stream: true }))) {
        if (this.dispatch(frame)) {
          controller.abort();
          return true;
        }
      }
    }
  }

  /** Handle one event; `true` when it ended the stream. */
  private dispatch(frame: SseFrame): boolean {
    let data: unknown;
    try {
      data = JSON.parse(frame.data);
    } catch {
      return false; // not one of the API's events
    }
    switch (frame.event) {
      case 'hello': {
        const hello = data as LiveHello;
        this.keepaliveSeconds = hello.limits.keepalive_seconds || DEFAULT_KEEPALIVE_SECONDS;
        this.attempt = 0;
        this.reopened = false;
        this.setState('connected');
        this.on.hello?.(hello);
        return false;
      }
      case 'line':
        if (frame.id) this.lastEventId = frame.id;
        this.on.line?.(data as LiveLine);
        return false;
      case 'dropped':
        this.on.dropped?.(data as LiveDropped);
        return false;
      case 'gap':
        this.on.gap?.(data as LiveGap);
        return false;
      case 'end':
        this.ended(data as LiveEnd);
        return true;
      default:
        return false;
    }
  }

  private ended(end: LiveEnd): void {
    if (end.code === RECONNECTING_END_CODE) {
      // The API is restarting: the next try finds the stream gone (404) and opens a new one.
      this.on.end?.(end);
      this.retry();
      return;
    }
    this.closed = true;
    this.disconnect();
    this.setState('stopped');
    this.on.end?.(end);
    const status = SESSION_END_CODES.includes(end.code)
      ? 401
      : ACCOUNT_END_CODES.includes(end.code)
        ? 403
        : null;
    if (status !== null)
      reactGlobally(
        new ApiError({
          status,
          code: end.code,
          messageTr: end.message_tr,
          messageEn: end.message_en,
        }),
      );
  }

  private async refused(response: Response): Promise<void> {
    const error = await refusalOf(response);
    if (this.closed) return;
    if (response.status === 404 && !this.reopened) {
      this.reopened = true;
      this.opening = this.reopen();
      await this.opening;
      return;
    }
    if (response.status >= 500) {
      // The reverse proxy while the API restarts, or a passing failure: try again.
      this.retry();
      return;
    }
    if (response.status === 401 || response.status === 403) reactGlobally(error);
    this.fail(error);
  }

  /** The stream was gone (404): open a new one, as `begin` does. */
  private async reopen(): Promise<void> {
    try {
      // Not the user's doing; lines from before are not sent again (they are on screen or in
      // the gap the page reports).
      await this.create(0, true);
    } catch (cause) {
      this.fail(cause);
      return;
    }
    if (this.isClosed()) return;
    this.on.reopened?.();
    if (this.state !== 'paused') void this.connect();
  }

  /** Read after an `await`: a pause or close may have happened meanwhile. */
  private isClosed(): boolean {
    return this.closed;
  }

  private fail(error: unknown): void {
    this.closed = true;
    this.disconnect();
    this.setState('stopped');
    if (error instanceof ApiError) this.on.failed?.(error);
    else {
      const [messageTr, messageEn] = fixedTexts('errors.network');
      this.on.failed?.(new ApiError({ status: 0, code: 'network_error', messageTr, messageEn }));
    }
  }

  private retry(): void {
    if (this.closed) return;
    this.disconnect();
    const delay = reconnectDelay(this.attempt);
    this.attempt += 1;
    this.setState('reconnecting');
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.connect();
    }, delay);
  }

  /** (Re)start the silence watch of the connection read by `controller`. */
  private watch(controller: AbortController): void {
    this.clearStale();
    this.staleTimer = setTimeout(
      () => {
        this.staleTimer = null;
        controller.abort(STALE);
      },
      this.keepaliveSeconds * STALE_KEEPALIVES * 1000,
    );
  }

  private clearStale(): void {
    if (this.staleTimer !== null) clearTimeout(this.staleTimer);
    this.staleTimer = null;
  }

  private disconnect(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.clearStale();
    const controller = this.controller;
    this.controller = null;
    controller?.abort();
  }

  private setState(state: LiveConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.on.state?.(state);
  }
}
