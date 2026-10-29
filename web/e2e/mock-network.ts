/**
 * The mocked internet-exit endpoints of the E2E tests (Wave 3 contract 2.12, ADR-0016), on top of
 * `mock-api.ts` (`page.route`: the newest route wins, so install it after `mockApi`):
 *
 * - `GET /settings/fortigate/observed` (`settings.manage`): the observed NAT addresses of a
 *   dual-WAN day (`src/test/wave3-mocks.ts`, a HAND MOCK until the endpoint's sample is
 *   captured), each row's `egress` computed from the `nat.egresses` the main mock stores, as the
 *   server claims addresses (`exits_invalid` when the exit rules refuse that value, as the
 *   server's `parse_egresses` does);
 * - `PATCH /settings` refused once with a captured-shape 422 (`refuseNext`, the server's exit
 *   rules that the browser's mirror cannot see); every other change goes to the main mock, which
 *   checks the body against the API's model and stores it.
 */

import type { Page, Request, Route } from '@playwright/test';

import type { FortigateObserved } from '../src/lib/api-types';
import type { SettingProblem } from '../src/lib/settings-types';
import { validateEgresses } from '../src/pages/settings/egress-model';
import { observedSample } from '../src/test/wave3-mocks';

import { bodyProblems, type MockApi, type RecordedCall } from './mock-api';

/** `SETTING_ERRORS` texts of the exit rules a spec makes the server refuse with. */
const EXIT_ERRORS: Record<string, [string, string]> = {
  setting_egress_overlap: [
    'Bir genel adres ya da NAT adresi iki çıkışta birden olamaz; aynı transip yalnızca iki çıkışın arayüzleri ayrıysa kullanılabilir.',
    'A public or NAT address cannot be in two exits; the same transip is allowed only when the two exits have separate interfaces.',
  ],
};

function addressNumber(text: string): number | null {
  const parts = text.split('.');
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return null;
  return parts.reduce((value, part) => value * 256 + Number(part), 0);
}

/** `address` is `entry` or lies in the network `entry`. */
function holds(entry: string, address: string): boolean {
  const [base = '', prefix = '32'] = entry.split('/');
  const first = addressNumber(base);
  const value = addressNumber(address);
  if (first === null || value === null) return false;
  const size = 2 ** (32 - Number(prefix));
  return value >= first && value < first + size;
}

interface StoredExit {
  public?: string[];
  transips?: string[];
  interfaces?: string[];
}

export class NetworkMock {
  /** The observed answer before the claims are computed (a spec may replace it). */
  observed: FortigateObserved = observedSample();
  /** The next `PATCH /settings` that changes `nat.egresses` is refused with this code. */
  refuseNext: string | null = null;

  constructor(private readonly api: MockApi) {}

  async install(page: Page): Promise<void> {
    await page.route('**/api/v1/settings/fortigate/observed**', (route, request) =>
      this.observedRoute(route, request),
    );
    await page.route('**/api/v1/settings', (route, request) => this.patchRoute(route, request));
  }

  private record(request: Request): RecordedCall {
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
      raw: request.postDataBuffer()?.toString('utf8') ?? null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    return call;
  }

  private async fulfill(route: Route, [status, payload]: [number, unknown]): Promise<void> {
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify(payload),
    });
  }

  /** The stored exits (the main mock's `nat.egresses`), `null` when the value is unreadable. */
  private exits(): Record<string, StoredExit> | null {
    for (const group of this.api.admin.settings) {
      const found = group.settings.find((item) => item.key === 'nat.egresses');
      if (!found) continue;
      const value = validateEgresses(found.value).value;
      return value ?? null;
    }
    return {};
  }

  private claim(exits: Record<string, StoredExit>, transip: string, dstintf: string | null) {
    const claims = Object.entries(exits).filter(([, exit]) =>
      [...(exit.public ?? []), ...(exit.transips ?? [])].some((entry) => holds(entry, transip)),
    );
    const onLine = claims.find(
      ([, exit]) => dstintf !== null && exit.interfaces?.includes(dstintf),
    );
    return (onLine ?? claims[0])?.[0] ?? null;
  }

  private async observedRoute(route: Route, request: Request): Promise<void> {
    const call = this.record(request);
    this.api.calls.push(call);
    const account = this.api.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return this.fulfill(route, account);
    if (call.method !== 'GET')
      return this.fulfill(route, this.api.error(405, 'method_not_allowed'));
    const hours = Number(call.query.get('hours') ?? 24);
    if (!Number.isInteger(hours) || hours < 1 || hours > 72)
      return this.fulfill(route, this.api.error(422, 'validation_error'));
    const exits = this.exits();
    const answer = structuredClone(this.observed);
    answer.nat.rows = answer.nat.rows.map((row) => ({
      ...row,
      egress: exits === null ? null : this.claim(exits, row.transip, row.dstintf),
    }));
    answer.nat.unmapped_transips = [
      ...new Set(answer.nat.rows.filter((row) => row.egress === null).map((row) => row.transip)),
    ];
    if (exits === null) answer.nat.exits_invalid = true;
    return this.fulfill(route, [200, answer]);
  }

  private async patchRoute(route: Route, request: Request): Promise<void> {
    const call = this.record(request);
    const body = call.body as { changes?: { key: string }[] } | undefined;
    const exitsChange = body?.changes?.some((change) => change.key === 'nat.egresses') ?? false;
    if (call.method !== 'PATCH' || !exitsChange || this.refuseNext === null) {
      await route.fallback();
      return;
    }
    this.api.calls.push(call);
    const problems = bodyProblems('PATCH /settings', call.body);
    if (problems.length > 0)
      return this.fulfill(route, this.api.error(422, 'validation_error', problems));
    const account = this.api.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return this.fulfill(route, account);
    const code = this.refuseNext;
    this.refuseNext = null;
    const [tr, en] = EXIT_ERRORS[code] ?? [code, code];
    const details: SettingProblem[] = [
      { key: 'nat.egresses', code, message_tr: tr, message_en: en },
    ];
    return this.fulfill(route, this.api.error(422, 'setting_invalid', details));
  }
}

/** Install the exit endpoints on top of `api` (call after `mockApi`). */
export async function mockNetwork(page: Page, api: MockApi): Promise<NetworkMock> {
  const network = new NetworkMock(api);
  await network.install(page);
  return network;
}
