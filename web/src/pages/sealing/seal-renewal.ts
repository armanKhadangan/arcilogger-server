/**
 * Re-stamping and revocation on the day page (Wave 5 contract 2.3.1 "Day detail", 2.6, C4):
 * `GET /seals/{day}` tells the day token's long-term status (`renewal`, decision 6) and whether
 * a revocation list names its TSA certificate (`revocation`, decision 10). The shapes are W5-C's
 * (`lib/seal-types.ts`); this file maps them to locale keys and tones. Relative imports only, so
 * the E2E specs can use it.
 */

import type { RenewalStatus } from '../../lib/seal-types';

/** `renewal_status` of the core (`arcilogger_core.renewal`, contract 2.1.2). */
export const RENEWAL_STATUSES: readonly RenewalStatus[] = [
  'not_due',
  'due',
  'covered',
  'late',
  'uncovered',
];

/** The locale key of each status (the API's underscores, camel case in the locale files). */
export const RENEWAL_KEY: Record<RenewalStatus, string> = {
  not_due: 'notDue',
  due: 'due',
  covered: 'covered',
  late: 'late',
  uncovered: 'uncovered',
};

/**
 * Badge tone of each status: `due`, `late` and `uncovered` are warnings (decision 6), a covered
 * token is fine, `not_due` is information — as the verifier's `renewal-*` levels (2.1.4).
 */
export const RENEWAL_TONE: Record<RenewalStatus, 'info' | 'success' | 'warning'> = {
  not_due: 'info',
  due: 'warning',
  covered: 'success',
  late: 'warning',
  uncovered: 'warning',
};

/**
 * Alert tone of a revocation level (C4): `fail` is danger, `warn` a warning, `ok` fine and
 * `info` neutral. A level this version does not know is shown as neutral information.
 */
export function revocationTone(level: string): 'danger' | 'warning' | 'success' | 'info' {
  if (level === 'fail') return 'danger';
  if (level === 'warn') return 'warning';
  if (level === 'ok') return 'success';
  return 'info';
}
