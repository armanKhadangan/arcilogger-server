import { errorResponse, installFetch, jsonResponse, setCsrfCookie } from '@/test/api-mock';
import type { RecordedRequest } from '@/test/api-mock';

import { BACKGROUND_HEADER, CSRF_HEADER, setApiEventHandlers, type ApiError } from './api';
import {
  LiveConnection,
  reconnectDelay,
  SseParser,
  type LiveConnectionState,
  type SseFrame,
} from './live-stream';
import type { LiveEnd, LiveHello, LiveLine, LiveStreamOut } from './live-types';

// -- the frame parser -----------------------------------------------------------------------------

function parseAll(chunks: string[]): { frames: SseFrame[]; parser: SseParser } {
  const parser = new SseParser();
  const frames = chunks.flatMap((chunk) => parser.push(chunk));
  return { frames, parser };
}

describe('SseParser', () => {
  it('reads events with their type, data and id', () => {
    const { frames, parser } = parseAll([
      'event: hello\ndata: {"a":1}\n\nid: e1:5\nevent: line\ndata: {"b":2}\n\n',
    ]);
    expect(frames).toEqual([
      { event: 'hello', data: '{"a":1}', id: null },
      { event: 'line', data: '{"b":2}', id: 'e1:5' },
    ]);
    expect(parser.lastEventId).toBe('e1:5');
  });

  it('gives the same events whatever the chunk boundaries', () => {
    const text =
      'event: line\r\nid: e1:7\r\ndata: {"x":"y"}\r\n\r\n: ka\r\nevent: end\r\ndata: {}\r\n\r\n';
    const whole = parseAll([text]).frames;
    for (let size = 1; size <= 7; size += 1) {
      const chunks: string[] = [];
      for (let start = 0; start < text.length; start += size)
        chunks.push(text.slice(start, start + size));
      expect(parseAll(chunks).frames, `chunks of ${size}`).toEqual(whole);
    }
    expect(whole.map((frame) => frame.event)).toEqual(['line', 'end']);
  });

  it('treats a CR at the end of a chunk as one line end with a following LF', () => {
    const { frames } = parseAll(['data: a\r', '\ndata: b\r', '\r', '\n']);
    expect(frames).toEqual([{ event: 'message', data: 'a\nb', id: null }]);
  });

  it('accepts lines ending with CR alone', () => {
    expect(parseAll(['event: gap\rdata: {}\r\r']).frames).toEqual([
      { event: 'gap', data: '{}', id: null },
    ]);
  });

  it('joins multi-line data with a line feed and keeps a space after the first', () => {
    expect(parseAll(['data: one\ndata:two\ndata:  three\n\n']).frames).toEqual([
      { event: 'message', data: 'one\ntwo\n three', id: null },
    ]);
  });

  it('counts comments (keep-alives) and dispatches nothing for them', () => {
    const { frames, parser } = parseAll([': ka\n\n', ':\n', ': ka\n\n']);
    expect(frames).toEqual([]);
    expect(parser.comments).toBe(3);
  });

  it('keeps the last id across events and reads retry', () => {
    const { frames, parser } = parseAll([
      'id: e1:1\ndata: 1\n\n',
      'data: 2\n\n',
      'retry: 2500\nid: bad\u0000id\ndata: 3\n\n',
      'retry: soon\n\n',
    ]);
    expect(frames.map((frame) => frame.id)).toEqual(['e1:1', 'e1:1', 'e1:1']);
    expect(parser.retry).toBe(2500);
  });

  it('ignores a byte order mark at the very start and events without data', () => {
    expect(parseAll(['﻿event: hello\n\nevent: line\ndata: x\n\n']).frames).toEqual([
      { event: 'line', data: 'x', id: null },
    ]);
  });

  it('keeps an unfinished line until its end arrives', () => {
    const parser = new SseParser();
    expect(parser.push('data: {"half":')).toEqual([]);
    expect(parser.push('true}\n')).toEqual([]);
    expect(parser.push('\n')).toEqual([{ event: 'message', data: '{"half":true}', id: null }]);
  });
});

describe('reconnectDelay', () => {
  it('waits 1, 2, 5, 10, then 30 s', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(reconnectDelay)).toEqual([
      1_000, 2_000, 5_000, 10_000, 30_000, 30_000, 30_000,
    ]);
  });
});

// -- the connection -------------------------------------------------------------------------------

const OPENED: LiveStreamOut = {
  stream_id: 'akis-1',
  epoch: 'e1',
  expires_at: '2026-10-13T05:00:00Z',
  folders: [{ folder: 'fortigate', day: '2026-10-13', start_line: 5 }],
  limits: { max_lines_per_second: 100, keepalive_seconds: 15, buffer_lines: 5000, max_minutes: 60 },
};

const HELLO: LiveHello = {
  stream_id: 'akis-1',
  epoch: 'e1',
  notice_version: '1',
  folders: OPENED.folders,
  limits: OPENED.limits,
};

function line(seq: number): LiveLine {
  return {
    seq,
    received: '2026-10-13T07:00:33.822642+03:00',
    source: 'fortigate',
    sender: '172.31.255.1',
    day: '2026-10-13',
    line: seq,
    offset: seq * 100,
    kind: 'traffic',
    type: 'traffic',
    subtype: 'forward',
    summary: { srcip: '172.20.42.57', dstip: '203.0.113.10', action: 'accept' },
    text: 'srcip=172.20.42.57 dstip=203.0.113.10 action="accept"',
    truncated: false,
    parse_error: null,
    parsed: true,
    parse_skipped: false,
  };
}

function frame(event: string, data: unknown, id?: string): string {
  return `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A server-sent event body the test writes into; an abort of the request errors it. */
class Body {
  readonly response: Response;
  private controller!: ReadableStreamDefaultController<Uint8Array>;
  private readonly encoder = new TextEncoder();
  private done = false;

  constructor(signal: AbortSignal | null | undefined) {
    const stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller;
      },
    });
    signal?.addEventListener('abort', () => {
      this.error(new DOMException('aborted', 'AbortError'));
    });
    this.response = new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
    });
  }

  send(text: string | Uint8Array): void {
    if (!this.done)
      this.controller.enqueue(typeof text === 'string' ? this.encoder.encode(text) : text);
  }

  close(): void {
    if (this.done) return;
    this.done = true;
    this.controller.close();
  }

  error(reason: unknown = new TypeError('network')): void {
    if (this.done) return;
    this.done = true;
    this.controller.error(reason);
  }
}

interface Harness {
  calls: RecordedRequest[];
  /** The GETs of the stream, in order, with their bodies. */
  gets: { request: RecordedRequest; body: Body | null }[];
  states: LiveConnectionState[];
  lines: LiveLine[];
  ends: LiveEnd[];
  failures: ApiError[];
  reopened: number;
  connection: LiveConnection;
  /** What the next GET answers: a stream (default) or a refusal. */
  answers: (() => Response | null)[];
}

function harness(): Harness {
  const h = {
    gets: [],
    states: [],
    lines: [],
    ends: [],
    failures: [],
    reopened: 0,
    answers: [],
  } as unknown as Harness;
  let opened = 0;
  h.calls = installFetch((request) => {
    if (request.method === 'POST' && request.path === '/api/v1/live/streams') {
      opened += 1;
      return jsonResponse(201, { ...OPENED, stream_id: `akis-${opened}` });
    }
    if (request.method === 'DELETE') return jsonResponse(204, null);
    return errorResponse(404, 'not_found');
  });
  const streamFetch = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      'http://127.0.0.1',
    );
    const request: RecordedRequest = {
      method: init.method ?? 'GET',
      url: `${url.pathname}${url.search}`,
      path: url.pathname,
      params: url.searchParams,
      headers: new Headers(init.headers),
      body: undefined,
    };
    h.calls.push(request);
    const answer = h.answers.shift()?.() ?? null;
    if (answer) {
      h.gets.push({ request, body: null });
      return Promise.resolve(answer);
    }
    const body = new Body(init.signal);
    h.gets.push({ request, body });
    return Promise.resolve(body.response);
  });
  h.connection = new LiveConnection({
    q: 'source:fortigate',
    fetch: streamFetch,
    on: {
      state: (state) => h.states.push(state),
      line: (item) => h.lines.push(item),
      end: (end) => h.ends.push(end),
      failed: (error) => h.failures.push(error),
      reopened: () => {
        h.reopened += 1;
      },
    },
  });
  return h;
}

function lastBody(h: Harness): Body {
  const body = h.gets.at(-1)?.body;
  if (!body) throw new Error('no open stream');
  return body;
}

async function settle(ms = 0): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

describe('LiveConnection', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setCsrfCookie('csrf-1');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setCsrfCookie(null);
  });

  it('opens with a POST, then reads the stream in the background', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    const post = h.calls[0];
    expect(post?.method).toBe('POST');
    expect(post?.body).toEqual({ q: 'source:fortigate' });
    expect(post?.headers.get(CSRF_HEADER)).toBe('csrf-1');
    expect(post?.headers.get(BACKGROUND_HEADER)).toBeNull();
    const get = h.gets[0]?.request;
    expect(get?.path).toBe('/api/v1/live/streams/akis-1');
    expect(get?.headers.get(BACKGROUND_HEADER)).toBe('1');
    expect(get?.headers.get('Accept')).toBe('text/event-stream');
    expect(get?.headers.get('Last-Event-ID')).toBeNull();
    expect(h.states).toEqual(['connecting']);

    lastBody(h).send(frame('hello', HELLO) + frame('line', line(6), 'e1:6'));
    await settle();
    expect(h.states.at(-1)).toBe('connected');
    expect(h.lines.map((item) => item.seq)).toEqual([6]);
    expect(h.connection.lastEventId).toBe('e1:6');
  });

  it('sends the backfill it was given', async () => {
    const h = harness();
    const connection = new LiveConnection({ q: '', backfill: 20, on: {} });
    void connection.open();
    await settle();
    expect(h.calls[0]?.body).toEqual({ q: '', backfill: 20 });
    await connection.close();
  });

  it('reconnects after 1, 2, 5, 10 and 30 s with Last-Event-ID, in the background', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    lastBody(h).send(frame('hello', HELLO) + frame('line', line(7), 'e1:7'));
    await settle();
    lastBody(h).error();
    await settle();
    expect(h.states.at(-1)).toBe('reconnecting');
    const waits = [1_000, 2_000, 5_000, 10_000, 30_000, 30_000];
    for (const [index, wait] of waits.entries()) {
      await settle(wait - 1);
      expect(h.gets, `before try ${index + 1}`).toHaveLength(index + 1);
      await settle(1);
      expect(h.gets, `try ${index + 1}`).toHaveLength(index + 2);
      const get = h.gets.at(-1)?.request;
      expect(get?.headers.get('Last-Event-ID')).toBe('e1:7');
      expect(get?.headers.get(BACKGROUND_HEADER)).toBe('1');
      lastBody(h).error();
      await settle();
    }
    // A hello starts the waits again from 1 s.
    await settle(30_000);
    lastBody(h).send(frame('hello', HELLO));
    await settle();
    expect(h.states.at(-1)).toBe('connected');
    const tries = h.gets.length;
    lastBody(h).close();
    await settle(1_000);
    expect(h.gets).toHaveLength(tries + 1);
    // Only one POST: a reconnect reads the same stream.
    expect(h.calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });

  it('reconnects when the connection goes silent for three keep-alive periods', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    lastBody(h).send(frame('hello', HELLO));
    await settle(44_000);
    lastBody(h).send(': ka\n\n');
    await settle(44_000);
    expect(h.gets).toHaveLength(1);
    await settle(1_000);
    expect(h.states.at(-1)).toBe('reconnecting');
    await settle(1_000);
    expect(h.gets).toHaveLength(2);
  });

  it('stops at an end event and does not reconnect', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    const end = { code: 'time_limit', message_tr: 'süre', message_en: 'time' };
    lastBody(h).send(frame('hello', HELLO) + frame('end', end));
    await settle(60_000);
    expect(h.ends).toEqual([end]);
    expect(h.states.at(-1)).toBe('stopped');
    expect(h.gets).toHaveLength(1);
    // The server closed it: no DELETE.
    await h.connection.close();
    expect(h.calls.filter((call) => call.method === 'DELETE')).toHaveLength(0);
  });

  it('sends a session end to the sign-in flow', async () => {
    const unauthorized = vi.fn();
    const privacy = vi.fn();
    const remove = setApiEventHandlers({ unauthorized, privacyNoticeRequired: privacy });
    try {
      const h = harness();
      await h.connection.open();
      await settle();
      lastBody(h).send(
        frame('end', { code: 'session_idle', message_tr: 'boşta', message_en: 'idle' }),
      );
      await settle();
      expect(unauthorized).toHaveBeenCalledWith(
        expect.objectContaining({ status: 401, code: 'session_idle' }),
      );
      const other = harness();
      await other.connection.open();
      await settle();
      lastBody(other).send(
        frame('end', { code: 'privacy_notice_required', message_tr: 'k', message_en: 'k' }),
      );
      await settle();
      expect(privacy).toHaveBeenCalledWith(
        expect.objectContaining({ status: 403, code: 'privacy_notice_required' }),
      );
    } finally {
      remove();
    }
  });

  it('comes back after end server_stopping (the API is restarting)', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    lastBody(h).send(
      frame('hello', HELLO) +
        frame('end', { code: 'server_stopping', message_tr: 'y', message_en: 'r' }),
    );
    await settle();
    expect(h.states.at(-1)).toBe('reconnecting');
    await settle(1_000);
    expect(h.gets).toHaveLength(2);
  });

  it.each([
    [401, 'session_idle'],
    [403, 'privacy_notice_required'],
    [429, 'live_stream_limit'],
    [422, 'validation_error'],
  ])('a GET refused with %i is not tried again', async (status, code) => {
    const h = harness();
    h.answers.push(() => errorResponse(status, code));
    await h.connection.open();
    await settle(60_000);
    expect(h.gets).toHaveLength(1);
    expect(h.failures.map((error) => [error.status, error.code])).toEqual([[status, code]]);
    expect(h.states.at(-1)).toBe('stopped');
  });

  it('a 5xx (the proxy while the API restarts) is tried again', async () => {
    const h = harness();
    h.answers.push(() => new Response('<html>Bad gateway</html>', { status: 502 }));
    await h.connection.open();
    await settle();
    expect(h.states.at(-1)).toBe('reconnecting');
    await settle(1_000);
    expect(h.gets).toHaveLength(2);
    expect(h.failures).toEqual([]);
  });

  it('a 404 opens one new stream on its own; a second 404 is a refusal', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    lastBody(h).send(frame('hello', HELLO) + frame('line', line(9), 'e1:9'));
    await settle();
    lastBody(h).error();
    h.answers.push(() => errorResponse(404, 'live_stream_not_found'));
    await settle(1_000);
    const posts = h.calls.filter((call) => call.method === 'POST');
    expect(posts).toHaveLength(2);
    expect(posts[1]?.body).toEqual({ q: 'source:fortigate', backfill: 0 });
    expect(posts[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
    expect(h.reopened).toBe(1);
    const get = h.gets.at(-1)?.request;
    expect(get?.path).toBe('/api/v1/live/streams/akis-2');
    expect(get?.headers.get('Last-Event-ID')).toBeNull();

    // No hello on the new stream before it is gone again (the second try waits 2 s): give up.
    h.answers.push(() => errorResponse(404, 'live_stream_not_found'));
    lastBody(h).error();
    await settle(2_000);
    expect(h.calls.filter((call) => call.method === 'POST')).toHaveLength(2);
    expect(h.failures.map((error) => error.code)).toEqual(['live_stream_not_found']);
  });

  it('pause aborts the GET and waits; resume reads on with Last-Event-ID', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    lastBody(h).send(frame('hello', HELLO) + frame('line', line(3), 'e1:3'));
    await settle();
    h.connection.pause();
    await settle(120_000);
    expect(h.states.at(-1)).toBe('paused');
    expect(h.gets).toHaveLength(1);
    h.connection.resume();
    await settle();
    expect(h.gets).toHaveLength(2);
    expect(h.gets[1]?.request.headers.get('Last-Event-ID')).toBe('e1:3');
    expect(h.gets[1]?.request.headers.get(BACKGROUND_HEADER)).toBe('1');
    expect(h.states.at(-1)).toBe('connecting');
  });

  it('close aborts the GET and deletes the stream', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    await h.connection.close();
    await settle(60_000);
    const removed = h.calls.filter((call) => call.method === 'DELETE');
    expect(removed.map((call) => call.path)).toEqual(['/api/v1/live/streams/akis-1']);
    expect(removed[0]?.headers.get(CSRF_HEADER)).toBe('csrf-1');
    expect(h.gets).toHaveLength(1);
    expect(h.states.at(-1)).toBe('stopped');
  });

  it('a refused POST is reported and nothing is read', async () => {
    const h = harness();
    installFetch(() => errorResponse(429, 'live_stream_limit', { tr: 'en fazla 2' }));
    await h.connection.open();
    expect(h.failures.map((error) => error.localized)).toEqual(['en fazla 2']);
    expect(h.gets).toHaveLength(0);
    expect(h.states.at(-1)).toBe('stopped');
  });

  it('decodes characters split across chunks', async () => {
    const h = harness();
    await h.connection.open();
    await settle();
    const bytes = new TextEncoder().encode(
      frame('line', { ...line(1), text: 'kullanıcı=Ayşe' }, 'e1:1'),
    );
    const cut = bytes.indexOf(0xc5) + 1; // inside "ş"
    lastBody(h).send(bytes.slice(0, cut));
    await settle();
    lastBody(h).send(bytes.slice(cut));
    await settle();
    expect(h.lines[0]?.text).toBe('kullanıcı=Ayşe');
  });
});
