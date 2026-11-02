/**
 * Re-stamping and revocation on the day page (Wave 5 contract 2.3.1 "Day detail", 2.6, C4):
 * `GET /seals/{day}` tells the day token's long-term status (`renewal`, decision 6) and whether
 * a revocation list names its TSA certificate (`revocation`, decision 10).
 *
 * Interim (C2): the types are declared here, exactly as 2.3.1 gives the JSON, and read through
 * {@link renewalOf} / {@link revocationOf} until W5-C adds the fields to `SealDayDetail`
 * (`lib/seal-types.ts`, optional like `TokenInfo.tsa_cert_not_after`). A server without them,
 * or with a status this version does not know, shows neither. Relative imports only, so the E2E
 * specs can type their hand-built answers with it.
 */

import type { Credits } from '../../lib/api-types';
import type { SealDayDetail } from '../../lib/seal-types';

/** `renewal_status` of the core (`arcilogger_core.renewal`, contract 2.1.2). */
export const RENEWAL_STATUSES = ['not_due', 'due', 'covered', 'late', 'uncovered'] as const;
export type RenewalStatus = (typeof RENEWAL_STATUSES)[number];

export interface SealRenewal {
  status: RenewalStatus;
  /** The token's TSA certificate `notAfter` (UTC). */
  cert_not_after: string | null;
  /** The vault-relative path of the counting renewal record (`renewals/2031-05-04-1.json`). */
  covered_by: string | null;
  /** The counting renewal token's genTime (ISO), or `null` when nothing counts (C4). */
  renewed_at: string | null;
}

/** The verifiers' own revocation levels (decision 10, C4). */
export type RevocationLevel = 'fail' | 'warn' | 'info' | 'ok';

export interface SealRevocation {
  /** The verifier's code (`crl-revoked`, `crl-revoked-later`, `crl-hold`, …, decision 10). */
  code: string;
  level: RevocationLevel;
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

/**
 * The day's re-stamping status, or `null` when the answer has none this version can read. A
 * renewal without `renewed_at` (a server before C4) reads as `null` there.
 */
export function renewalOf(detail: SealDayDetail): SealRenewal | null {
  const renewal = (detail as { renewal?: Partial<SealRenewal> | null }).renewal ?? null;
  if (!renewal?.status || !(RENEWAL_STATUSES as readonly string[]).includes(renewal.status))
    return null;
  return {
    status: renewal.status,
    cert_not_after: renewal.cert_not_after ?? null,
    covered_by: renewal.covered_by ?? null,
    renewed_at: renewal.renewed_at ?? null,
  };
}

/** The day's revocation finding, or `null` when the answer has none. */
export function revocationOf(detail: SealDayDetail): SealRevocation | null {
  return (detail as { revocation?: SealRevocation | null }).revocation ?? null;
}

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

/**
 * A credit status without a credit query (contract 2.4, C3: the Credit task records
 * `{"supported": false}`; C21: today `rfc3161` and TÜRKTRUST). Only the flag decides, never the
 * provider name.
 * Interim (C2): read through a cast until W5-C adds `supported` to `Credits` (`lib/api-types.ts`).
 */
export function noCreditQuery(credits: Credits): boolean {
  return (credits as { supported?: boolean }).supported === false;
}
