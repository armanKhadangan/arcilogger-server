/**
 * The mocked live tail API (`/live`, Wave 2 contract 2.14.4–2.14.6) of the E2E tests, with the
 * part of `GET /search/fields` Canlı Akış's filters read. Answers have the exact shapes of
 * `src/lib/live-types.ts`, the error codes and Turkish/English texts of contract 2.20 and the
 * permission rules of the router: `POST /live/streams` needs `search.read` and the accepted KVKK
 * notice (a strict body, like the API's `extra="forbid"`), `GET` and `DELETE` the stream's owner.
 * Installed after `mockApi` (`page.route`: the newest route wins), it shares the main mock's
 * session, users and recorded calls.
 *
 * The stream itself is real server-sent events: `route.fulfill` can only send a whole body, so the
 * GET is recorded on the mocked route (its `X-Arci-Background` and `Last-Event-ID` headers) and
 * then continued to a small HTTP server of this module that keeps the connection open, like the
 * API does. A test pushes lines and events with `send`/`event`, drops the connection with
 * `drop`, forgets every stream with `restart` (the API restarted: the next GET is a 404) and
 * replays missed lines from the stream's history on a reconnect with `Last-Event-ID`, like the
 * hub's buffer. Lines are the example network's; the first ones are captured raw lines
 * (`samples/raw.json`), so their raw drawer opens. Integration replaces these answers with the
 * captured `samples/live.json`.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

import type { Page, Request, Route } from '@playwright/test';

import type {
  LiveDropped,
  LiveEnd,
  LiveGap,
  LiveHello,
  LiveLimits,
  LiveLine,
  LiveStatus,
  LiveStreamOut,
} from '../src/lib/live-types';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import { RAW, SAMPLE_TODAY } from './samples';

/** Texts of the live tail API (contract 2.20, `arcilogger_api.live.messages`). */
const TEXTS: Record<string, [string, string]> = {
  live_stream_limit: [
    'Aynı anda en fazla 2 canlı akış açabilirsiniz (sunucuda toplam 4); başka bir sekmedeki akışı kapatın.',
    'You can have at most 2 live tails open at a time (server total 4); close the one in another tab.',
  ],
  live_stream_not_found: [
    'Canlı akış bulunamadı (kapatılmış ya da sunucu yeniden başlamış olabilir).',
    'Live tail not found (it was closed or the server restarted).',
  ],
  live_unavailable: [
    'Ham kayıt klasörü okunamıyor; canlı akış başlatılamadı. Sistem yöneticisine bildirin.',
    'The raw spool folder cannot be read; the live tail cannot start. Tell the administrator.',
  ],
  search_unknown_field: [
    '“{field}” diye bir alan yok. Alan listesi için “?” düğmesine basın; metin olarak aramak için tırnak içine alın.',
    'There is no field “{field}”. Press “?” for the list of fields; put it in quotes to search for it as text.',
  ],
};

/** `end` texts (contract 2.20; the session codes reuse the sign-in texts). */
export const END_TEXTS: Record<string, [string, string]> = {
  time_limit: [
    'Canlı akış 60 dakika sonra durduruldu (gözetimsiz ekran). Sürdürmek için “Sürdür”e basın.',
    'The live tail stopped after 60 minutes (unattended screen). Press “Resume” to continue.',
  ],
  slow_client: [
    'Bağlantı satırlara yetişemedi; akış durduruldu. Filtreyi daraltıp yeniden başlatın.',
    'The connection could not keep up; the stream stopped. Narrow the filter and start again.',
  ],
  permission_changed: ['Canlı akış yetkiniz kaldırıldı.', 'Your live tail permission was removed.'],
  server_stopping: [
    'Sunucu yeniden başlatılıyor; canlı akış birazdan yeniden bağlanır.',
    'The server is restarting; the live tail reconnects shortly.',
  ],
  session_idle: [
    'Oturum hareketsizlik nedeniyle kapandı; lütfen yeniden giriş yapın.',
    'The session was closed after inactivity; please sign in again.',
  ],
};

export const GAP_TEXTS: [string, string] = [
  "Aradaki satırlar gösterilemedi; bu aralığı Arama'da açabilirsiniz.",
  'The lines in between could not be shown; you can open this interval in Search.',
];

const LIMITS: LiveLimits = {
  max_lines_per_second: 100,
  keepalive_seconds: 15,
  buffer_lines: 5000,
  max_minutes: 60,
};

/** Fields the mocked parser knows (a query naming another one is refused, as by the API). */
const FIELDS = new Set([
  'source',
  'sender',
  'type',
  'subtype',
  'host',
  'program',
  'severity',
  'error',
  'srcip',
  'dstip',
  'ip',
  'srcport',
  'dstport',
  'proto',
  'action',
  'domain',
  'user',
  'mac',
  'vlan',
  'department',
  'text',
]);

/** The parts of the field catalogue the filters read (`GET /search/fields`). */
export const CATALOGUE = {
  fields: [],
  kinds: [
    { key: 'traffic', label_tr: 'Trafik', label_en: 'Traffic' },
    { key: 'web', label_tr: 'Web filtresi', label_en: 'Web filter' },
    { key: 'dns', label_tr: 'DNS', label_en: 'DNS' },
    { key: 'event', label_tr: 'FortiGate olayı', label_en: 'FortiGate event' },
    { key: 'dhcp', label_tr: 'DHCP', label_en: 'DHCP' },
    { key: 'arp', label_tr: 'ARP', label_en: 'ARP' },
    { key: 'account', label_tr: 'Oturum açma', label_en: 'Logins' },
    { key: 'syslog', label_tr: 'Genel syslog', label_en: 'Generic syslog' },
    { key: 'unknown', label_tr: 'Tanınmayan', label_en: 'Unrecognised' },
  ],
  sources: [
    { name: 'fortigate', type: 'fortigate', network: false },
    { name: 'core-r1', type: 'mikrotik', network: false },
    { name: 'ap', type: 'generic', network: true },
  ],
  limits: {
    max_chars: 1000,
    max_clauses: 16,
    max_values: 20,
    max_terms: 5,
    max_days: 31,
    page_size_max: 500,
  },
};

/** 15:00 site time on the sample day, as an ISO time `seconds` later. */
function received(seconds: number): string {
  return new Date(Date.parse(`${SAMPLE_TODAY}T15:00:00+03:00`) + seconds * 1000).toISOString();
}

/** The captured raw lines of `core-r1`, in order: the first lines of every stream. */
const CAPTURED = Object.values(RAW.lines)
  .filter((line) => line.source === 'core-r1' && line.day === '2026-10-09')
  .sort((a, b) => a.line - b.line);

/**
 * Line number `n` (from 1) of the mocked spool: the captured core-r1 lines first (their raw
 * drawer opens), then FortiGate traffic of the example network, one denied in five.
 */
export function liveLine(n: number, overrides: Partial<LiveLine> = {}): Omit<LiveLine, 'seq'> {
  const captured = CAPTURED[n - 1];
  if (captured) {
    return {
      received: captured.received,
      source: captured.source,
      sender: captured.sender,
      day: captured.day,
      line: captured.line,
      offset: captured.offset,
      kind: 'dhcp',
      type: 'dhcp',
      subtype: 'assigned',
      summary: { srcip: '172.20.42.57', mac: '00:00:5e:00:53:57' },
      text: captured.text.split(' ').slice(2).join(' '),
      truncated: false,
      parse_error: null,
      parsed: true,
      parse_skipped: false,
      ...overrides,
    };
  }
  const client = `172.20.42.${(n % 200) + 20}`;
  const action = n % 5 === 0 ? 'deny' : 'accept';
  return {
    received: received(n),
    source: 'fortigate',
    sender: '172.31.255.1',
    day: SAMPLE_TODAY,
    line: n,
    offset: n * 400,
    kind: 'traffic',
    type: 'traffic',
    subtype: 'forward',
    summary: { srcip: client, dstip: '203.0.113.10', dstport: 443, action, proto: 'tcp' },
    text: `<189>date=${SAMPLE_TODAY} type="traffic" subtype="forward" srcip=${client} dstip=203.0.113.10 dstport=443 proto=6 action="${action}" n=${n}`,
    truncated: false,
    parse_error: null,
    parsed: true,
    parse_skipped: false,
    ...overrides,
  };
}

interface Stream {
  id: string;
  owner: number;
  q: string;
  backfill: number | null;
  closed: boolean;
  /** Lines sent to the stream, for a resume with `Last-Event-ID` (the hub's buffer). */
  history: { seq: number; frame: string }[];
}

/** A GET of a stream as the mocked route saw it. */
export interface StreamGet {
  id: string;
  lastEventId: string | null;
  background: boolean;
}

function frame(event: string, data: unknown, id?: string): string {
  return `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export class LiveMock {
  readonly streams = new Map<string, Stream>();
  readonly gets: StreamGet[] = [];
  epoch = 'e2e-epoch-1';
  /** The next `POST /live/streams` is refused with this (e.g. 429 `live_stream_limit`). */
  refuseNext: { status: number; code: string } | null = null;
  /**
   * Milliseconds the API takes to handle a `DELETE`: the stream keeps its slot until then, so a
   * `POST` sent before the answer is counted against the limits with it (as by the real API).
   */
  deleteDelay = 0;
  private seq = 0;
  private nextLine = 1;
  private nextId = 1;
  private server: Server | null = null;
  private port = 0;
  private readonly open = new Map<string, Set<ServerResponse>>();

  constructor(private readonly api: MockApi) {}

  async install(page: Page): Promise<void> {
    const server = createServer((request, response) => {
      this.serve(request, response);
    });
    await new Promise<void>((resolve) => {
      server.listen(0, '127.0.0.1', resolve);
    });
    this.server = server;
    this.port = (server.address() as AddressInfo).port;
    page.on('close', () => {
      this.stop();
    });
    await page.route('**/api/v1/live/**', (route, request) => this.handle(route, request));
    await page.route('**/api/v1/search/fields', (route, request) => this.handle(route, request));
  }

  /** Close the stream server (the page closes it on its own). */
  stop(): void {
    for (const responses of this.open.values())
      for (const response of responses) response.destroy();
    this.open.clear();
    this.server?.closeAllConnections();
    this.server?.close();
    this.server = null;
  }

  /** Connections being read now (one per open stream that is not paused). */
  connections(): number {
    return [...this.open.values()].reduce((sum, responses) => sum + responses.size, 0);
  }

  /** Send `count` new lines (or the given ones) to every stream, as the hub would. */
  send(lines: number | Partial<LiveLine>[]): LiveLine[] {
    const items = typeof lines === 'number' ? Array.from({ length: lines }, () => ({})) : lines;
    const sent = items.map((overrides) => {
      this.seq += 1;
      const line: LiveLine = { ...liveLine(this.nextLine, overrides), seq: this.seq };
      this.nextLine += 1;
      return line;
    });
    for (const stream of this.streams.values()) {
      if (stream.closed) continue;
      const text = sent.map((line) => frame('line', line, `${this.epoch}:${line.seq}`)).join('');
      stream.history.push(
        ...sent.map((line) => ({
          seq: line.seq,
          frame: frame('line', line, `${this.epoch}:${line.seq}`),
        })),
      );
      for (const response of this.open.get(stream.id) ?? []) response.write(text);
    }
    return sent;
  }

  /** Send a `dropped`, `gap` or `end` event to every open connection. */
  event(name: 'dropped', data: LiveDropped): void;
  event(name: 'gap', data: LiveGap): void;
  event(name: 'end', data: LiveEnd): void;
  event(name: string, data: unknown): void {
    for (const responses of this.open.values())
      for (const response of responses) response.write(frame(name, data));
  }

  /** End every stream with `code` (the server closes it; the page does not reconnect). */
  end(code: string): void {
    const [tr, en] = END_TEXTS[code] ?? [code, code];
    this.event('end', { code, message_tr: tr, message_en: en });
    for (const stream of this.streams.values()) stream.closed = code !== 'server_stopping';
    for (const responses of this.open.values()) for (const response of responses) response.end();
  }

  /** Cut every connection without an end (a network error): the page reconnects. */
  drop(): void {
    for (const responses of this.open.values())
      for (const response of responses) response.destroy();
  }

  /** A stream the signed-in user has open in another tab (it takes one of their 2 slots). */
  openElsewhere(): void {
    const owner = this.api.session?.userId;
    if (owner === undefined) throw new Error('nobody is signed in');
    const id = `akis-${this.nextId}`;
    this.nextId += 1;
    this.streams.set(id, { id, owner, q: '', backfill: null, closed: false, history: [] });
  }

  /** The API restarted: a new epoch, every stream forgotten (the next GET is a 404). */
  restart(): void {
    this.epoch = `e2e-epoch-${Number(this.epoch.split('-').pop()) + 1}`;
    this.streams.clear();
    this.drop();
  }

  // -- the mocked routes ------------------------------------------------------------------------

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const headers = request.headers();
    let body: unknown;
    try {
      body = request.postDataJSON() as unknown;
    } catch {
      body = undefined;
    }
    const call: RecordedCall = {
      method: request.method(),
      path: url.pathname.replace(/^\/api\/v1/, ''),
      query: url.searchParams,
      body,
      raw: request.postData(),
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.api.calls.push(call);
    const stream = /^\/live\/streams\/([^/]+)$/.exec(call.path);
    if (call.method === 'GET' && stream?.[1]) {
      const id = decodeURIComponent(stream[1]);
      const account = this.api.requirePermission(call, 'search.read');
      const found = this.streams.get(id);
      if (!Array.isArray(account) && found && !found.closed && found.owner === account.id) {
        this.gets.push({
          id,
          lastEventId: headers['last-event-id'] ?? null,
          background: call.background,
        });
        try {
          await route.continue({ url: `http://127.0.0.1:${this.port}/stream/${id}` });
        } catch {
          // The page aborted the request (a pause) or closed.
        }
        return;
      }
      const [status, payload] = Array.isArray(account)
        ? account
        : this.error(404, 'live_stream_not_found');
      if (!Array.isArray(account))
        this.gets.push({ id, lastEventId: null, background: call.background });
      await this.fulfill(route, status, payload);
      return;
    }
    if (call.method === 'DELETE' && this.deleteDelay > 0)
      await new Promise((resolve) => setTimeout(resolve, this.deleteDelay));
    const [status, payload] = this.answer(call, stream?.[1] ? decodeURIComponent(stream[1]) : null);
    await this.fulfill(route, status, payload);
  }

  private async fulfill(route: Route, status: number, payload: unknown): Promise<void> {
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: status === 204 ? '' : JSON.stringify(payload),
    });
  }

  private error(
    status: number,
    code: string,
    params: Record<string, string> = {},
    details?: unknown,
  ): [number, unknown] {
    const texts = TEXTS[code];
    if (!texts) return this.api.error(status, code, details);
    const fill = (text: string) =>
      text.replace(/\{(\w+)\}/g, (_, name: string) => params[name] ?? `{${name}}`);
    return [
      status,
      {
        error: {
          code,
          message_tr: fill(texts[0]),
          message_en: fill(texts[1]),
          ...(details ? { details } : {}),
        },
      },
    ];
  }

  private answer(call: RecordedCall, id: string | null): [number, unknown] {
    if (call.path === '/search/fields') {
      const account = this.api.requirePermission(call, 'search.read');
      return Array.isArray(account) ? account : [200, CATALOGUE];
    }
    if (call.path === '/live/status') {
      const account = this.api.requirePermission(call, 'search.read');
      if (Array.isArray(account)) return account;
      const status: LiveStatus = {
        streams: [...this.streams.values()].filter((stream) => !stream.closed).length,
        max_streams: 4,
        hub: {
          running: true,
          files: 3,
          lines_per_second: 0,
          parsed_per_second: 0,
          skipped_total: 0,
        },
      };
      return [200, status];
    }
    if (call.method === 'POST' && call.path === '/live/streams') return this.create(call);
    if (call.method === 'DELETE' && id) {
      const account = this.api.requirePermission(call, 'search.read');
      if (Array.isArray(account)) return account;
      const stream = this.streams.get(id);
      if (!stream || stream.owner !== account.id) return this.error(404, 'live_stream_not_found');
      stream.closed = true;
      for (const response of this.open.get(id) ?? []) response.end();
      return [204, null];
    }
    return this.api.error(404, 'not_found');
  }

  private create(call: RecordedCall): [number, unknown] {
    const account: MockUser | [number, unknown] = this.api.requirePermission(call, 'search.read');
    if (Array.isArray(account)) return account;
    if (!account.privacy_accepted) return this.api.error(403, 'privacy_notice_required');
    const body = call.body;
    const problems: unknown[] = [];
    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
      problems.push({
        loc: ['body'],
        type: 'model_attributes_type',
        msg: 'Input should be an object',
      });
    } else {
      for (const key of Object.keys(body))
        if (key !== 'q' && key !== 'backfill')
          problems.push({
            loc: ['body', key],
            type: 'extra_forbidden',
            msg: 'Extra inputs are not permitted',
          });
      const { q, backfill } = body as { q?: unknown; backfill?: unknown };
      if (q !== undefined && (typeof q !== 'string' || q.length > 1000))
        problems.push({
          loc: ['body', 'q'],
          type: 'string_type',
          msg: 'Input should be a valid string',
        });
      if (
        backfill !== undefined &&
        backfill !== null &&
        (typeof backfill !== 'number' ||
          !Number.isInteger(backfill) ||
          backfill < 0 ||
          backfill > 200)
      )
        problems.push({
          loc: ['body', 'backfill'],
          type: 'int_type',
          msg: 'Input should be a valid integer',
        });
    }
    if (problems.length > 0) return this.api.error(422, 'validation_error', problems);
    if (this.refuseNext) {
      const { status, code } = this.refuseNext;
      this.refuseNext = null;
      return this.error(status, code);
    }
    const { q = '', backfill = null } = body as { q?: string; backfill?: number | null };
    for (const match of q.matchAll(/(?:^|\s)-?([A-Za-z_]\w*):/g)) {
      const field = match[1] ?? '';
      if (!FIELDS.has(field.toLowerCase())) {
        const start = match.index + (match[0].length - field.length - 1);
        return this.error(
          422,
          'search_unknown_field',
          { field },
          {
            start,
            end: start + field.length + 1,
            params: { field },
          },
        );
      }
    }
    const mine = [...this.streams.values()].filter(
      (stream) => !stream.closed && stream.owner === account.id,
    );
    const all = [...this.streams.values()].filter((stream) => !stream.closed);
    if (mine.length >= 2 || all.length >= 4) return this.error(429, 'live_stream_limit');
    const id = `akis-${this.nextId}`;
    this.nextId += 1;
    this.streams.set(id, { id, owner: account.id, q, backfill, closed: false, history: [] });
    const answer: LiveStreamOut = {
      stream_id: id,
      epoch: this.epoch,
      expires_at: new Date(Date.now() + 60 * 60_000).toISOString(),
      folders: [
        { folder: 'core-r1', day: SAMPLE_TODAY, start_line: 120 },
        { folder: 'fortigate', day: SAMPLE_TODAY, start_line: 5_400 },
      ],
      limits: LIMITS,
    };
    return [201, answer];
  }

  // -- the stream server ------------------------------------------------------------------------

  private serve(request: IncomingMessage, response: ServerResponse): void {
    const id = decodeURIComponent((request.url ?? '').replace(/^\/stream\//, ''));
    const stream = this.streams.get(id);
    if (!stream || stream.closed) {
      response.writeHead(404, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(this.error(404, 'live_stream_not_found')[1]));
      return;
    }
    response.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Accel-Buffering': 'no',
    });
    const hello: LiveHello = {
      stream_id: id,
      epoch: this.epoch,
      notice_version: '1',
      folders: [
        { folder: 'core-r1', day: SAMPLE_TODAY, start_line: 120 },
        { folder: 'fortigate', day: SAMPLE_TODAY, start_line: 5_400 },
      ],
      limits: LIMITS,
    };
    response.write(frame('hello', hello));
    const last = request.headers['last-event-id'];
    const resume = typeof last === 'string' ? /^(.+):(\d+)$/.exec(last) : null;
    if (resume) {
      const after = Number(resume[2]);
      if (resume[1] !== this.epoch) {
        const gap: LiveGap = {
          reason: 'hub_restarted',
          missed: null,
          message_tr: GAP_TEXTS[0],
          message_en: GAP_TEXTS[1],
        };
        response.write(frame('gap', gap));
      } else {
        response.write(
          stream.history
            .filter((item) => item.seq > after)
            .map((item) => item.frame)
            .join(''),
        );
      }
    }
    const responses = this.open.get(id) ?? new Set<ServerResponse>();
    responses.add(response);
    this.open.set(id, responses);
    response.on('close', () => {
      responses.delete(response);
    });
  }
}

/** Install the live tail mock on top of `api` (call after `mockApi`). */
export async function mockLive(page: Page, api: MockApi): Promise<LiveMock> {
  const live = new LiveMock(api);
  await live.install(page);
  return live;
}
