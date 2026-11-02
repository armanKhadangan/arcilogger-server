/**
 * Re-stamping and revocation on the day page (Wave 5 contract 2.3.1 "Day detail", 2.6):
 * `GET /seals/{day}` tells the day token's long-term status (`renewal`, decision 6) and whether
 * a revocation list names its TSA certificate (`revocation`, decision 10).
 *
 * Interim: the types are declared here, exactly as 2.3.1 gives the JSON, and read through
 * {@link renewalOf} / {@link revocationOf} until W5-C adds the fields to `SealDayDetail`
 * (`lib/seal-types.ts`, optional like `TokenInfo.tsa_cert_not_after`). A server without them,
 * or with a status this version does not know, shows neither. Relative imports only, so the E2E
 * specs can type their hand-built answers with it.
 */

import type { SealDayDetail } from '../../lib/seal-types';

/** `renewal_status` of the core (`arcilogger_core.renewal`, contract 2.1.2). */
export const RENEWAL_STATUSES = ['not_due', 'due', 'covered', 'late', 'uncovered'] as const;
export type RenewalStatus = (typeof RENEWAL_STATUSES)[number];

export interface SealRenewal {
  status: RenewalStatus;
  /** The token's TSA certificate `notAfter` (UTC). */
  cert_not_after: string | null;
  /** The path of the renewal that counts (`renewals/<YYYY-MM-DD>-<seq>.json.zd`, decision 1). */
  covered_by: string | null;
}

export interface SealRevocation {
  /** The verifier's code (`crl-revoked`, `crl-revoked-later`, `crl-hold`, …, decision 10). */
  code: string;
  level: string;
  message_tr: string;
  message_en: string;
}

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

/** The day's re-stamping status, or `null` when the answer has none this version can read. */
export function renewalOf(detail: SealDayDetail): SealRenewal | null {
  const renewal = (detail as { renewal?: SealRenewal | null }).renewal ?? null;
  return renewal && (RENEWAL_STATUSES as readonly string[]).includes(renewal.status)
    ? renewal
    : null;
}

/** The day's revocation finding, or `null` when the answer has none. */
export function revocationOf(detail: SealDayDetail): SealRevocation | null {
  return (detail as { revocation?: SealRevocation | null }).revocation ?? null;
}

/**
 * The date a renewal was made, from its file name (`<YYYY-MM-DD>-<seq>.json`, decision 1), for
 * "renewal-late (…)": the answer carries no stamp time of its own. `null` for any other name.
 */
export function renewalDay(coveredBy: string | null): string | null {
  return /(?:^|\/)(\d{4}-\d{2}-\d{2})-\d+\.json(?:\.zd)?$/.exec(coveredBy ?? '')?.[1] ?? null;
}

/**
 * Alert tone of a revocation level. Both vocabularies are accepted until W5-C fixes one: the
 * verifier's (`fail`, `warn`, `info`, `ok`) and the legal status' (`danger`, `warning`).
 */
export function revocationTone(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'fail' || level === 'danger') return 'danger';
  if (level === 'warn' || level === 'warning') return 'warning';
  return 'info';
}

/**
 * A credit status without a credit query (contract 2.4: TÜRKTRUST has none; the Credit task
 * records `{"supported": false}`). Interim: `/credits` has no such field yet, so the provider
 * name decides as well.
 */
export function noCreditQuery(credits: { provider: string }): boolean {
  return (
    (credits as { supported?: boolean }).supported === false || credits.provider === 'turktrust'
  );
}
