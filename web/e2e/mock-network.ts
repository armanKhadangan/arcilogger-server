/**
 * The mocked internet-exit endpoints of the E2E tests (Wave 3 contract 2.12, ADR-0016), on top of
 * `mock-api.ts` (`page.route`: the newest route wins, so install it after `mockApi`):
 *
 * - `GET /settings/fortigate/observed` (`settings.manage`): the observed NAT addresses and srcmac
 *   interfaces as the API answered them (`samples/settings-observed.json`), with what the API
 *   computes on every request taken from the settings the main mock stores, as
 *   `observed_answer` does: each row's `egress` from `nat.egresses` (the captured
 *   `observed_exits_invalid` when the exit rules refuse that value, as `parse_egresses` does),
 *   `trandisp_known` from `nat.snat_trandisp` and each interface's `configured` from
 *   `lookup.srcmac_interfaces`; its captured refusals (the 504 of `failNext`, `hours` above 72);
 * - `PATCH /settings` refused once with the captured `setting_invalid_egress` (`refuseNext`: a
 *   rule of the server that the browser's mirror cannot see); every other change goes to the
 *   main mock, which checks the body against the API's model and the value against the exit
 *   rules, and stores it.
 */

import { readFileSync } from 'node:fs';

import type { Page, Request, Route } from '@playwright/test';

import type { FortigateObserved } from '../src/lib/api-types';
import type { SettingValue } from '../src/lib/settings-types';
import { validateEgresses } from '../src/pages/settings/egress-model';

import { bodyProblems, type MockApi, type RecordedCall } from './mock-api';
import { copy, SETTINGS, type ErrorSample, type SampleMeta } from './samples';

/** `samples/settings-observed.json`, read as `samples.ts` reads the others (it does not load it). */
const OBSERVED = JSON.parse(
  readFileSync(new URL('./samples/settings-observed.json', import.meta.url), 'utf8'),
) as {
  meta: SampleMeta;
  observed: FortigateObserved;
  observed_exits_invalid: FortigateObserved;
  errors: Record<string, ErrorSample>;
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
  public: string[];
  transips: string[];
  interfaces: string[];
}

/** The exits of a stored `nat.egresses`, `null` when the exit rules refuse it. */
function exitsOf(value: unknown): Record<string, StoredExit> | null {
  return validateEgresses(value).value ?? null;
}

/**
 * `_claim`: the exit whose public or NAT addresses hold `transip` (`internal` exits included);
 * of several, the one whose interfaces hold `dstintf`, else the first by name.
 */
function claim(
  exits: Record<string, StoredExit>,
  transip: string,
  dstintf: string | null,
): string | null {
  const claims = Object.entries(exits)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .filter(([, exit]) =>
      [...exit.public, ...exit.transips].some((entry) => holds(entry, transip)),
    );
  const onLine = claims.find(([, exit]) => dstintf !== null && exit.interfaces.includes(dstintf));
  return (onLine ?? claims[0])?.[0] ?? null;
}

/** `observed_answer` on the captured counts and the given settings. */
function observedAnswer(settings: (key: string) => SettingValue | undefined): FortigateObserved {
  const exits = exitsOf(settings('nat.egresses') ?? {});
  const answer = copy(exits === null ? OBSERVED.observed_exits_invalid : OBSERVED.observed);
  const snat = (settings('nat.snat_trandisp') as string[] | undefined) ?? ['snat'];
  const listed = (settings('lookup.srcmac_interfaces') as string[] | undefined) ?? [];
  const rows = answer.nat.rows.map((row) => ({
    ...row,
    egress: exits === null ? null : claim(exits, row.transip, row.dstintf),
    trandisp_known: row.trandisp !== null && snat.includes(row.trandisp),
  }));
  answer.nat = {
    ...answer.nat,
    rows,
    unmapped_transips: [
      ...new Set(rows.filter((row) => row.egress === null).map((row) => row.transip)),
    ],
    unknown_trandisp: [
      ...new Set(
        rows.flatMap((row) => (row.trandisp !== null && !row.trandisp_known ? [row.trandisp] : [])),
      ),
    ].sort(),
    exits_invalid: exits === null,
  };
  answer.srcmac.interfaces = answer.srcmac.interfaces.map((item) => ({
    ...item,
    configured: listed.includes(item.srcintf),
  }));
  return answer;
}

{
  // The claims the mock computes must be the API's: on the settings of the capture (the
  // converted `wan` claims 100.64.12.9), and with an unreadable value (nothing is claimed).
  // `configured` is left out: the two answers were captured on another site's srcmac settings.
  const captured = new Map(
    SETTINGS.list.groups.flatMap((group) => group.settings).map((item) => [item.key, item.value]),
  );
  const checks: [FortigateObserved, FortigateObserved][] = [
    [observedAnswer((key) => captured.get(key)), OBSERVED.observed],
    [
      observedAnswer((key) =>
        key === 'nat.egresses' ? { wan: { mode: 'x' } } : captured.get(key),
      ),
      OBSERVED.observed_exits_invalid,
    ],
  ];
  for (const [computed, sample] of checks)
    if (JSON.stringify(computed.nat) !== JSON.stringify(sample.nat))
      throw new Error('mock-network: the computed claims differ from the captured answer');
}

export class NetworkMock {
  /** The next `PATCH /settings` that changes `nat.egresses` is refused (the captured 422). */
  refuseNext = false;
  /** The next `GET /settings/fortigate/observed` is refused with this captured error. */
  failNext: 'fortigate_observed_timeout' | null = null;

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

  private captured(sample: ErrorSample | undefined): [number, unknown] {
    if (!sample) throw new Error('mock-network: the captured refusal is missing');
    return [sample.status, copy(sample.body)];
  }

  /** A setting as the main mock stores it now. */
  private setting(key: string): SettingValue | undefined {
    for (const group of this.api.admin.settings) {
      const found = group.settings.find((item) => item.key === key);
      if (found) return found.value;
    }
    return undefined;
  }

  private async observedRoute(route: Route, request: Request): Promise<void> {
    const call = this.record(request);
    this.api.calls.push(call);
    const account = this.api.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return this.fulfill(route, account);
    if (call.method !== 'GET')
      return this.fulfill(route, this.api.error(405, 'method_not_allowed'));
    const hours = Number(call.query.get('hours') ?? 24);
    if (Number.isInteger(hours) && hours > 72)
      return this.fulfill(route, this.captured(OBSERVED.errors.validation_error));
    if (!Number.isInteger(hours) || hours < 1)
      return this.fulfill(route, this.api.error(422, 'validation_error'));
    if (this.failNext !== null) {
      const code = this.failNext;
      this.failNext = null;
      return this.fulfill(route, this.captured(OBSERVED.errors[code]));
    }
    const answer = observedAnswer((key) => this.setting(key));
    answer.nat.hours = hours;
    return this.fulfill(route, [200, answer]);
  }

  private async patchRoute(route: Route, request: Request): Promise<void> {
    const call = this.record(request);
    const body = call.body as { changes?: { key: string }[] } | undefined;
    const exitsChange = body?.changes?.some((change) => change.key === 'nat.egresses') ?? false;
    if (call.method !== 'PATCH' || !exitsChange || !this.refuseNext) {
      await route.fallback();
      return;
    }
    this.api.calls.push(call);
    const problems = bodyProblems('PATCH /settings', call.body);
    if (problems.length > 0)
      return this.fulfill(route, this.api.error(422, 'validation_error', problems));
    const account = this.api.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return this.fulfill(route, account);
    this.refuseNext = false;
    return this.fulfill(route, this.captured(SETTINGS.errors.setting_invalid_egress));
  }
}

/** Install the exit endpoints on top of `api` (call after `mockApi`). */
export async function mockNetwork(page: Page, api: MockApi): Promise<NetworkMock> {
  const network = new NetworkMock(api);
  await network.install(page);
  return network;
}
