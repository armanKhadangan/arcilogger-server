/**
 * Canlı Durum's API (`/activity/devices` W5-M, `/activity/wan` W5-N; contract W5 §6 M2, M3,
 * N6) for the e2e mock, answering the captured samples: W5-M's `activity.json` (the windows
 * 1, 5 and 15 on the golden usage site, and the pseudonymous viewer's) and W5-N's
 * `activity-wan.json` (W5-P-9).
 */

import type {
  ActivityDevices,
  ActivityWindow,
  WanRange,
  WanRates,
} from '../src/lib/activity-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { ACTIVITY, ACTIVITY_WAN, copy } from './samples';

/** What the activity mock needs from the main mock. */
export interface ActivityHost extends MockHost {
  privacyRequired(account: MockUser): boolean;
  permissionsOf(account: MockUser): readonly string[];
}

export interface ActivityOptions {
  /** No source has SNMP on: `/activity/wan` answers the captured `empty`. */
  noLines?: boolean;
  /** The worker's snapshot is not complete or is old: `warming_up` (W5-M-2). */
  warmingUp?: boolean;
  /** `usage.pseudonymize_anonymous_role` (from the usage options): pseudonyms for viewers. */
  pseudonymize?: boolean;
}

const WINDOWS: readonly ActivityWindow[] = [1, 5, 15];

/** The captured answer of a window (`devices_1`, `devices_5`, `devices_15`). */
function captured(window: ActivityWindow): ActivityDevices {
  const key = `devices_${window}` as const;
  return copy(ACTIVITY[key]);
}

/**
 * The pseudonymous viewer's answer: captured for window 5; for 1 and 15 the captured rows with
 * the subjects of that capture (the same persons, by key).
 */
function pseudonymous(window: ActivityWindow): ActivityDevices {
  if (window === 5) return copy(ACTIVITY.viewer_devices_pseudonymous);
  const named = ACTIVITY.devices_5.rows;
  const hidden = ACTIVITY.viewer_devices_pseudonymous.rows;
  const answer = captured(window);
  for (const row of answer.rows) {
    const index = named.findIndex((item) => item.subject.key === row.subject.key);
    const subject = hidden[index]?.subject;
    if (!subject) throw new Error(`no captured pseudonym for ${row.subject.key}`);
    row.subject = copy(subject);
  }
  return answer;
}

export class ActivityMock {
  constructor(readonly options: ActivityOptions = {}) {}

  answer(call: RecordedCall, host: ActivityHost): Answer | null {
    if (call.method !== 'GET') return null;
    if (call.path === '/activity/devices') return this.devices(call, host);
    if (call.path === '/activity/wan') return this.wan(call, host);
    return null;
  }

  /** `viewer_for`: pseudonyms for a viewer with `usage.anonymous` but not `usage.person`. */
  private pseudonymize(account: MockUser, host: ActivityHost): boolean {
    const permissions = host.permissionsOf(account);
    return (
      Boolean(this.options.pseudonymize) &&
      permissions.includes('usage.anonymous') &&
      !permissions.includes('usage.person')
    );
  }

  /** `usage.person` or `usage.anonymous`, after the KVKK notice (M3). */
  private devices(call: RecordedCall, host: ActivityHost): Answer {
    let denied: Answer | null = null;
    for (const permission of ['usage.person', 'usage.anonymous']) {
      const account = host.requirePermission(call, permission);
      if (Array.isArray(account)) {
        denied = account;
        continue;
      }
      if (host.privacyRequired(account) && !account.privacy_accepted)
        return host.error(403, 'privacy_notice_required');
      const window = Number(call.query.get('window') ?? '5');
      if (!(WINDOWS as readonly number[]).includes(window))
        return host.error(422, 'activity_window_invalid');
      const answer = this.pseudonymize(account, host)
        ? pseudonymous(window as ActivityWindow)
        : captured(window as ActivityWindow);
      if (this.options.warmingUp) answer.warming_up = true;
      return [200, answer];
    }
    return denied ?? host.error(403, 'forbidden');
  }

  /** `dashboard.view` or `system.read`; no person data, not licence-gated (N6). */
  private wan(call: RecordedCall, host: ActivityHost): Answer {
    const allowed = ['dashboard.view', 'system.read'].some(
      (permission) => !Array.isArray(host.requirePermission(call, permission)),
    );
    if (!allowed) return host.error(403, 'forbidden');
    const asked = call.query.get('range') ?? '60m';
    if (asked !== '60m' && asked !== '24h') return host.error(422, 'validation_error');
    const range: WanRange = asked;
    // The captured answers: `hour` for 60m, `day` for 24h (each with its own range), `empty`.
    if (this.options.noLines) return [200, { ...copy(ACTIVITY_WAN.empty), range }];
    const captured: WanRates = range === '60m' ? ACTIVITY_WAN.hour : ACTIVITY_WAN.day;
    return [200, copy(captured)];
  }
}
