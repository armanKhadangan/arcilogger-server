/**
 * The mocked archive reload of the E2E tests (Wave 5 contract §5, G4 to G10): `POST
 * /archive-reloads` and `GET /archive-reloads/{id}`, and the 409 `archive_reload_required` of
 * `POST /lookup/5651` and `POST /search` for days outside the database window. The answers are
 * the captured cases of W5-G through the fixtures of the unit tests (`src/test/archive-fixtures.ts`,
 * hand-built from W5-G's `archive.json` until #121 merges), re-exported here.
 *
 * Installed after `mockApi` and `mockSearch` (`page.route`: the newest route wins), it shares the
 * main mock's session and recorded calls (`background` is the `X-Arci-Background: 1` of a poll).
 * A reload is asked for with the permission of its reason (`lookup` → `lookup.run`, `search` →
 * `search.read`) and the accepted KVKK notice; it is read with either permission, by the person
 * who asked or an administrator. Its reads answer {@link ArchiveMock.steps} one after the other
 * (default: `loading`, then `ready`; the last one stays). Until a reload is ready, a lookup answers
 * the captured 409 and a search the captured 409 or the search mock's page with the captured
 * `archived` days, as the options say; once one is ready, both go on to the records and search
 * mocks (`route.fallback()`), which then answer as if the days were in the database.
 */

import type { Page, Request, Route } from '@playwright/test';

import type {
  ArchiveReload,
  ArchiveReloadRequest,
  ArchiveReloadStatus,
} from '../src/lib/archive-reload-types';
import {
  ARCHIVE_CREATED,
  ARCHIVE_ERRORS,
  ARCHIVE_EXISTING,
  ARCHIVE_FAILED,
  ARCHIVE_FAILURE_CODES,
  ARCHIVE_LIST,
  ARCHIVE_LOADING,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_ARCHIVED,
  ARCHIVE_SEARCH_REQUIRED,
  ARCHIVE_SITE_TODAY,
  type ArchiveErrorSample,
} from '../src/test/archive-fixtures';

import type { MockApi, MockUser, RecordedCall } from './mock-api';
import type { SearchMock } from './mock-search';
import { copy } from './samples';

export {
  ARCHIVE_CREATED,
  ARCHIVE_ERRORS,
  ARCHIVE_EXISTING,
  ARCHIVE_FAILED,
  ARCHIVE_FAILURE_CODES,
  ARCHIVE_LIST,
  ARCHIVE_LOADING,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_ARCHIVED,
  ARCHIVE_SEARCH_REQUIRED,
  ARCHIVE_SITE_TODAY,
};

/** The URL patterns this mock answers (`page.route`). */
const RELOADS = '**/api/v1/archive-reloads**';
const LOOKUP = '**/api/v1/lookup/5651';
const SEARCH_RUN = '**/api/v1/search';

/** At most this many days in one reload (G5; `MAX_RELOAD_DAYS` of the web app). */
const MAX_DAYS = 14;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The permission a reload of each reason is asked for with (G6). */
const PERMISSION: Record<string, string> = { lookup: 'lookup.run', search: 'search.read' };

/** The captured reload of each status: the fields that are not the request's own. */
const BY_STATUS: Record<ArchiveReloadStatus, ArchiveReload> = {
  queued: ARCHIVE_CREATED,
  loading: ARCHIVE_LOADING,
  ready: ARCHIVE_READY,
  failed: ARCHIVE_FAILED,
  expired: ARCHIVE_LIST.items.find((item) => item.status === 'expired') ?? ARCHIVE_READY,
};

/** An answer: status, body and the headers of a refusal (`Retry-After` of the search mock's 429). */
type Answer = [number, unknown] | [number, unknown, { headers: Record<string, string> }];

/** A refusal of `POST /archive-reloads` the next request answers (captured). */
export type ArchiveRefusal = keyof typeof ARCHIVE_ERRORS;

export interface ArchiveOptions {
  /** `POST /lookup/5651` answers the captured 409 until a reload is ready. */
  lookup?: boolean;
  /**
   * `POST /search` until a reload is ready: the captured 409 (`required`: the whole range is
   * outside the window), or the answer of `searchMock` with the captured `archived` days
   * (`archived`: a part of it is).
   */
  search?: 'required' | 'archived' | null;
  /** The search mock whose answer gets the `archived` days (needed for `archived`). */
  searchMock?: SearchMock;
  /** What the reads of a reload answer, one after the other (default `loading`, `ready`). */
  steps?: ArchiveReloadStatus[];
  /** The next `POST /archive-reloads` answers this captured refusal. */
  refuseNext?: ArchiveRefusal | null;
}

interface Reload {
  request: ArchiveReloadRequest;
  owner: MockUser;
  /** Reads answered so far: the index into {@link ArchiveMock.steps}. */
  reads: number;
  status: ArchiveReloadStatus;
}

/** The search mock's own answer to a call: its `answer` is private to it, read through this view. */
interface Answering {
  answer(call: RecordedCall): Answer;
}

export class ArchiveMock {
  /** The reloads asked for, by id. */
  readonly reloads = new Map<number, Reload>();
  lookup: boolean;
  search: 'required' | 'archived' | null;
  /** What the reads of a reload answer (a test may change it; the last one stays). */
  steps: ArchiveReloadStatus[];
  /** The next `POST /archive-reloads` answers this captured refusal. */
  refuseNext: ArchiveRefusal | null;
  /** A reload has been read as `ready`: lookups and searches go on to the other mocks. */
  ready = false;
  private nextId = ARCHIVE_CREATED.id;
  private readonly searchMock: SearchMock | null;

  constructor(
    private readonly api: MockApi,
    options: ArchiveOptions = {},
  ) {
    this.lookup = options.lookup ?? false;
    this.search = options.search ?? null;
    this.searchMock = options.searchMock ?? null;
    this.steps = [...(options.steps ?? ['loading', 'ready'])];
    this.refuseNext = options.refuseNext ?? null;
    if (this.search === 'archived' && !this.searchMock)
      throw new Error('mockArchive: search "archived" needs the searchMock');
  }

  async install(page: Page): Promise<void> {
    await page.route(RELOADS, (route, request) =>
      this.handle(route, request, (call) => this.reloadAnswer(call)),
    );
    await page.route(LOOKUP, (route, request) =>
      this.handle(route, request, (call) => this.lookupAnswer(call)),
    );
    await page.route(SEARCH_RUN, (route, request) =>
      this.handle(route, request, (call) => this.searchAnswer(call)),
    );
  }

  /** The calls of `GET /archive-reloads/{id}` (the polls). */
  reads(id: number): RecordedCall[] {
    return this.api.callsTo('GET', `/archive-reloads/${String(id)}`);
  }

  /** Records the call when this mock answers it; `null` leaves it to the older routes. */
  private async handle(
    route: Route,
    request: Request,
    answer: (call: RecordedCall) => Answer | null,
  ): Promise<void> {
    const url = new URL(request.url());
    let body: unknown;
    try {
      body = request.postDataJSON() as unknown;
    } catch {
      body = undefined;
    }
    const headers = request.headers();
    const call: RecordedCall = {
      method: request.method(),
      path: url.pathname.replace(/^\/api\/v1/, ''),
      query: url.searchParams,
      body,
      raw: request.postDataBuffer()?.toString('utf8') ?? null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    const found = answer(call);
    if (!found) {
      await route.fallback();
      return;
    }
    this.api.calls.push(call);
    const [status, payload, file] = found;
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store', ...file?.headers },
      body: JSON.stringify(payload),
    });
  }

  /** A captured refusal. */
  private refusal(sample: ArchiveErrorSample): [number, unknown] {
    return [sample.status, copy(sample.body)];
  }

  /** `permission` and the accepted KVKK notice (`require_privacy_notice`). */
  private person(call: RecordedCall, permission: string): MockUser | [number, unknown] {
    const account = this.api.requirePermission(call, permission);
    if (Array.isArray(account)) {
      const code = (account[1] as { error?: { code?: string } }).error?.code;
      return code === 'forbidden' ? this.refusal(ARCHIVE_ERRORS.forbidden) : account;
    }
    if (!account.privacy_accepted) return this.refusal(ARCHIVE_ERRORS.privacy_notice_required);
    return account;
  }

  // -- the 409 of lookups and searches -----------------------------------------------------------

  private lookupAnswer(call: RecordedCall): Answer | null {
    if (!this.lookup || this.ready || call.method !== 'POST') return null;
    // The permission and the notice come first (`RunnerDep`); the records mock refuses the rest.
    const account = this.person(call, 'lookup.run');
    if (Array.isArray(account)) return null;
    return this.refusal(ARCHIVE_LOOKUP_REQUIRED);
  }

  private searchAnswer(call: RecordedCall): Answer | null {
    if (this.search === null || this.ready || call.method !== 'POST') return null;
    const account = this.person(call, 'search.read');
    if (Array.isArray(account)) return null;
    if (this.search === 'required') return this.refusal(ARCHIVE_SEARCH_REQUIRED);
    const answer = (this.searchMock as unknown as Answering).answer(call);
    const [status, page] = answer;
    if (status !== 200) return answer;
    return [status, { ...(page as object), archived: copy(ARCHIVE_SEARCH_ARCHIVED.archived) }];
  }

  // -- /archive-reloads --------------------------------------------------------------------------

  private reloadAnswer(call: RecordedCall): Answer {
    if (call.path === '/archive-reloads' && call.method === 'POST') return this.create(call);
    if (call.path === '/archive-reloads' && call.method === 'GET') return this.list(call);
    const match = /^\/archive-reloads\/(\d+)$/.exec(call.path);
    if (match && call.method === 'GET') return this.read(call, Number(match[1]));
    return call.path === '/archive-reloads' || match
      ? this.api.error(405, 'method_not_allowed')
      : this.api.error(404, 'not_found');
  }

  /** `lookup.run` or `search.read` (`_reader`): reading needs no KVKK notice. */
  private reader(call: RecordedCall): MockUser | [number, unknown] {
    const account = this.api.requirePermission(call, 'lookup.run');
    return Array.isArray(account) ? this.api.requirePermission(call, 'search.read') : account;
  }

  /** The reload as the API shows it: the captured one of its status, with the request's fields. */
  private shown(id: number, reload: Reload): ArchiveReload {
    return {
      ...copy(BY_STATUS[reload.status]),
      id,
      requested_by: reload.owner.username,
      reason: reload.request.reason,
      lookup_id: reload.request.lookup_id ?? null,
      first_day: reload.request.first_day,
      last_day: reload.request.last_day,
    };
  }

  private create(call: RecordedCall): Answer {
    const body = call.body as Partial<ArchiveReloadRequest> | undefined;
    const reason = body?.reason ?? '';
    const permission = PERMISSION[reason];
    if (!body || !permission || !DAY.test(body.first_day ?? '') || !DAY.test(body.last_day ?? ''))
      return this.api.error(422, 'validation_error', [
        { loc: ['body'], msg: 'reason, first_day and last_day are required', type: 'missing' },
      ]);
    const account = this.person(call, permission);
    if (Array.isArray(account)) return account;
    const refuse = this.refuseNext;
    if (refuse) {
      this.refuseNext = null;
      return this.refusal(ARCHIVE_ERRORS[refuse]);
    }
    const request: ArchiveReloadRequest = {
      reason: body.reason ?? 'lookup',
      first_day: body.first_day ?? '',
      last_day: body.last_day ?? '',
      lookup_id: body.lookup_id ?? null,
    };
    const days = (Date.parse(request.last_day) - Date.parse(request.first_day)) / 86_400_000 + 1;
    if (days < 1)
      return this.api.error(422, 'validation_error', [
        { loc: ['body', 'last_day'], msg: 'last_day is before first_day', type: 'value_error' },
      ]);
    // The captured refusal asked for 15 days; the card never asks for more than 14.
    if (days > MAX_DAYS) return this.refusal(ARCHIVE_ERRORS.too_large);
    // The person's own active request for these days answers again (G9).
    for (const [id, reload] of this.reloads)
      if (
        reload.owner.id === account.id &&
        ['queued', 'loading', 'ready'].includes(reload.status) &&
        reload.request.first_day <= request.first_day &&
        reload.request.last_day >= request.last_day
      )
        return [202, this.shown(id, reload)];
    const id = this.nextId++;
    const reload: Reload = { request, owner: account, reads: 0, status: 'queued' };
    this.reloads.set(id, reload);
    return [202, this.shown(id, reload)];
  }

  private read(call: RecordedCall, id: number): Answer {
    const account = this.reader(call);
    if (Array.isArray(account)) return account;
    const reload = this.reloads.get(id);
    if (!reload || (account.role !== 'admin' && reload.owner.id !== account.id))
      return this.refusal(ARCHIVE_ERRORS.not_found);
    reload.status = this.steps[Math.min(reload.reads, this.steps.length - 1)] ?? 'ready';
    reload.reads += 1;
    if (reload.status === 'ready') this.ready = true;
    return [200, this.shown(id, reload)];
  }

  /** `GET /archive-reloads`: the active reloads first, then the others; newest first in each. */
  private list(call: RecordedCall): Answer {
    const account = this.reader(call);
    if (Array.isArray(account)) return account;
    const active = (reload: Reload) => ['queued', 'loading'].includes(reload.status);
    const items = [...this.reloads]
      .filter(([, reload]) => account.role === 'admin' || reload.owner.id === account.id)
      .sort(([a, one], [b, other]) => Number(active(other)) - Number(active(one)) || b - a)
      .map(([id, reload]) => this.shown(id, reload));
    return [200, { items }];
  }
}

/** Install the archive reload mock on `page` (after `mockApi` and `mockSearch`). */
export async function mockArchive(
  page: Page,
  api: MockApi,
  options: ArchiveOptions = {},
): Promise<ArchiveMock> {
  const archive = new ArchiveMock(api, options);
  await archive.install(page);
  return archive;
}
