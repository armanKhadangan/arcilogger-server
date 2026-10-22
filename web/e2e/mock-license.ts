/**
 * The mocked licence endpoints of the E2E tests (licensing contract 6.1–6.6), stateful like the
 * API: `GET /license` (any signed-in user; `details` only with `settings.manage`), `POST /license`
 * (`settings.manage` after a recent re-authentication; the `.lic` as the multipart field `file`)
 * and `DELETE /license`. While a module is off, its routes (`/usage/*`, `/reports/*`,
 * `/imports/*`) answer 403 `module_not_licensed` after authentication, before anything else; the
 * users limit refuses a new or re-enabled user with 409 `license_limit` (the count is the mock's
 * active users, recounted for every request).
 *
 * Every answer is one the real API gave: the states, uploads, refusals and the gates' and the
 * limit's error bodies are the captured `samples/license.json` (`capture_api_samples.py`; the
 * captured sites' stories are in `src/test/license-samples.ts`). The default state is `trial`
 * (every module on, no limit), so every other spec runs with the trial banner. Uploads are
 * answered by the file name ({@link REFUSED_FILES}; anything else installs the captured licence).
 * The history shows the state's captured past, with this browser session's events on top.
 */

import type { LicenseEventOut, LicenseOut } from '../src/lib/api-types';
import {
  REFUSED_FILES,
  viewerForm,
  type LicenseDocumentName,
  type LicenseErrorName,
  type LicenseErrorSample,
} from '../src/test/license-samples';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { LICENSE } from './samples';

export { REFUSED_FILES };

export interface LicenseOptions {
  /** The state at start (default `trial`). */
  state?: LicenseDocumentName;
}

/** Route prefixes of the licence modules (contract 1.3) and their captured 403 answers. */
const MODULE_ROUTES: readonly [RegExp, string, LicenseErrorName][] = [
  [/^\/usage(\/|$)/, 'usage_analytics', 'module_locked_usage'],
  [/^\/reports(\/|$)/, 'usage_reports', 'module_locked_reports'],
  [/^\/imports(\/|$)/, 'bulk_import', 'module_locked_import'],
];

/** The captured licence the mock installs: the first upload's answer, then "the same again". */
const INSTALLED: LicenseDocumentName = 'upload_ok';
const REINSTALLED: LicenseDocumentName = 'upload_unchanged';

/** What the licence mock needs beyond the other mocks: the session check of `require()`. */
export interface LicenseHost extends MockHost {
  signedIn(call: RecordedCall): MockUser | [number, unknown];
}

function sample(name: LicenseDocumentName): LicenseOut {
  return structuredClone(LICENSE[name]);
}

function errorSample(name: LicenseErrorName): LicenseErrorSample {
  return structuredClone(LICENSE[name]);
}

/** The newest captured event of `kind` (whose details match `matches`), as the API recorded it. */
function capturedEvent(
  kind: LicenseEventOut['kind'],
  matches: (details: Record<string, unknown>) => boolean = () => true,
): LicenseEventOut | undefined {
  for (const name of [REINSTALLED, INSTALLED] as const) {
    const found = LICENSE[name].details?.history.find(
      (item) => item.kind === kind && matches(item.details),
    );
    if (found) return structuredClone(found);
  }
  return undefined;
}

export class LicenseMock {
  status: LicenseOut;
  /** The state's captured history (before this browser session). */
  private past: LicenseEventOut[];
  /** Events of this browser session, newest first. */
  private events: LicenseEventOut[] = [];
  private readonly now: string;

  constructor(
    options: LicenseOptions,
    private readonly activeUsers: () => number,
  ) {
    this.status = sample(options.state ?? 'trial');
    this.past = this.status.details?.history ?? [];
    this.now = this.status.checked_at;
  }

  /** Switch to another captured state (a licence installed by the CLI meanwhile, …). */
  set(state: LicenseDocumentName): void {
    this.status = sample(state);
    this.past = this.status.details?.history ?? [];
  }

  /** The answer of `GET /license` now (admin form); the users count is recounted. */
  current(): LicenseOut {
    const status = structuredClone(this.status);
    const used = this.activeUsers();
    const max = status.limits.users.max;
    status.limits.users = {
      ...status.limits.users,
      used,
      full: max !== null && used >= max,
      exceeded: max !== null && used > max,
    };
    if (status.details) status.details.history = [...this.events, ...this.past].slice(0, 20);
    return status;
  }

  /** An event of this session, shaped like the captured one of the same kind (and code). */
  private record(
    actor: MockUser,
    template: LicenseEventOut | undefined,
    fallback: Pick<LicenseEventOut, 'kind' | 'license_id' | 'details'>,
  ): void {
    this.events.unshift({
      ...(template ?? { ...fallback, source: 'web' }),
      at: this.now,
      source: 'web',
      actor: actor.username,
    });
  }

  /** 403 `module_not_licensed` for a route of a module that is off, else `null`. */
  gate(call: RecordedCall, host: LicenseHost): Answer | null {
    const route = MODULE_ROUTES.find(([pattern]) => pattern.test(call.path));
    if (!route) return null;
    const [, module, refusal] = route;
    const account = host.signedIn(call);
    if (Array.isArray(account)) return account;
    if (this.status.modules.find((item) => item.key === module)?.enabled) return null;
    // The captured body; the state is this mock's (the texts name only the module).
    const { status, body } = errorSample(refusal);
    body.error.details = { module, license_status: this.status.status };
    return [status, body];
  }

  /** 409 `license_limit` when no user may be added or re-enabled (contract 6.6), else `null`. */
  usersRefusal(): [number, unknown] | null {
    const max = this.status.limits.users.max;
    const used = this.activeUsers();
    if (max === null || used < max) return null;
    // The captured refusal (3 of 3 users), with this mock's numbers.
    const { status, body } = errorSample('user_limit');
    const captured = body.error.details as { max: number; used: number };
    const numbers = (text: string) =>
      text.replace(`(${captured.used}/${captured.max})`, `(${used}/${max})`);
    body.error.message_tr = numbers(body.error.message_tr);
    body.error.message_en = numbers(body.error.message_en);
    body.error.details = { limit: 'users', max, used };
    return [status, body];
  }

  answer(call: RecordedCall, host: LicenseHost): Answer | null {
    if (call.path !== '/license') return null;
    if (call.method === 'GET') {
      const account = host.signedIn(call);
      if (Array.isArray(account)) return account;
      const status = this.current();
      const admin = host.permissionsOf(account).includes('settings.manage');
      return [200, admin ? status : viewerForm(status)];
    }
    if (call.method === 'POST') {
      const account = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(account)) return account;
      const name = /filename="([^"]*)"/.exec(call.raw ?? '')?.[1];
      if (name === undefined) return host.error(422, 'upload_invalid');
      const refusal = (REFUSED_FILES as Record<string, LicenseErrorName | undefined>)[name];
      if (refusal) {
        const { status, body } = errorSample(refusal);
        const code = body.error.code;
        this.record(
          account,
          capturedEvent('refuse', (details) => details.code === code),
          { kind: 'refuse', license_id: null, details: { code } },
        );
        return [status, body];
      }
      const installed = this.status.details?.license_id ?? null;
      const next = sample(INSTALLED);
      const unchanged = installed !== null && installed === next.details?.license_id;
      this.status = next;
      this.record(
        account,
        capturedEvent('install', (details) => details.unchanged === unchanged),
        { kind: 'install', license_id: next.details?.license_id ?? null, details: { unchanged } },
      );
      return [200, this.current()];
    }
    if (call.method === 'DELETE') {
      const account = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(account)) return account;
      if (this.status.status === 'trial' || this.status.status === 'trial_expired') {
        const { status, body } = errorSample('remove_not_installed');
        return [status, body];
      }
      const removed = this.status.details?.license_id ?? null;
      // The installation's trial is long over (the captured unlicensed site).
      this.status = sample('trial_expired');
      this.record(account, undefined, {
        kind: 'remove',
        license_id: removed,
        details: { license_id: removed },
      });
      return [204, null];
    }
    return null;
  }
}
