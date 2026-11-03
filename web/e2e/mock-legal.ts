/**
 * The mocked legal-validity status of the E2E tests: `GET /legal-status` (Wave 5 contract 2.3.1),
 * open to any signed-in user, not audited, the same answer for every role. The answers are the
 * captured `samples/legal-status.json` through the fixtures of the unit tests
 * (`src/test/w5-fixtures.ts`), re-exported here.
 *
 * Installed after `mockApi` (`page.route`: the newest route wins), it shares the main mock's
 * session and recorded calls (`background` is the `X-Arci-Background: 1` of a refresh). The
 * default is {@link LEGAL_OK}, so no banner shows and the specs that expect no alert on a page
 * keep passing; a spec that does not install it gets the main mock's 404, which shows nothing
 * either. Change {@link LegalMock.status} for the next answer; other methods are answered 405.
 */

import type { Page, Request, Route } from '@playwright/test';

import type { LegalStatus } from '../src/lib/legal-status-types';
import {
  LEGAL_MIXED,
  LEGAL_OK,
  LEGAL_PROVIDER_NOT_LEGAL,
  LEGAL_REASONS,
  LEGAL_REVOKED,
  LEGAL_WARNING_ONLY,
  legalStatusWith,
} from '../src/test/w5-fixtures';

import type { MockApi, RecordedCall } from './mock-api';
import { copy } from './samples';

export {
  LEGAL_MIXED,
  LEGAL_OK,
  LEGAL_PROVIDER_NOT_LEGAL,
  LEGAL_REASONS,
  LEGAL_REVOKED,
  LEGAL_WARNING_ONLY,
  legalStatusWith,
};

/** The URL pattern this mock answers (`page.route`). */
const PATTERN = '**/api/v1/legal-status**';

export interface LegalOptions {
  /** The status at start (default {@link LEGAL_OK}). */
  status?: LegalStatus;
}

export class LegalMock {
  /** The status the next `GET /legal-status` answers (a test may change it). */
  status: LegalStatus;

  constructor(
    private readonly api: MockApi,
    options: LegalOptions = {},
  ) {
    this.status = copy(options.status ?? LEGAL_OK);
  }

  async install(page: Page): Promise<void> {
    await page.route(PATTERN, (route, request) => this.handle(route, request));
  }

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const headers = request.headers();
    const call: RecordedCall = {
      method: request.method(),
      path: url.pathname.replace(/^\/api\/v1/, ''),
      query: url.searchParams,
      body: undefined,
      raw: null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.api.calls.push(call);
    const [status, payload] = this.answer(call);
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify(payload),
    });
  }

  private answer(call: RecordedCall): [number, unknown] {
    if (call.path !== '/legal-status') return this.api.error(404, 'not_found');
    if (call.method !== 'GET') return this.api.error(405, 'method_not_allowed');
    const account = this.api.signedIn(call);
    if (Array.isArray(account)) return account;
    return [200, copy(this.status)];
  }
}

/** Install the legal-status mock on `page` (after `mockApi`). */
export async function mockLegal(
  page: Page,
  api: MockApi,
  options: LegalOptions = {},
): Promise<LegalMock> {
  const legal = new LegalMock(api, options);
  await legal.install(page);
  return legal;
}
