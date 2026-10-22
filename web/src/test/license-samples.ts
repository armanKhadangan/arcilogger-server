/**
 * The names and shape of `e2e/samples/license.json`: the licence answers captured from the real
 * API by `web/scripts/capture_api_samples.py` (licensing contract 8.5). Each document comes from a
 * site whose story the capture controls — a fresh database and data directory, the installation's
 * seed and the worker's `license.check` at chosen moments, licence files signed with the core
 * package's deterministic test key for the server id of `fixed_identity()`, release day pinned to
 * 01.10.2026 — so the samples are the same on every capture:
 *
 * - the main site, installed on 05.10.2026 03:12: `trial` (6 days left on 13.10.2026 07:00), every
 *   refusal of `POST /license`, `upload_ok` / `upload_unchanged` (the licence ARCI-L-20261001-
 *   TEST0001: analytics and reports, 10 sources, 25 users, maintenance until 20.10.2027),
 *   `licensed`, the key no longer trusted (`invalid`), the licence revoked (`revoked`), a licence
 *   for 3 sources and 3 users with 4 of each in use, 2 of the sources unconfigured senders
 *   (`licensed_limits_exceeded`), maintenance ending on 25.10.2026 and ended on 05.10.2026;
 * - `trial_last_day` (installed 29.09.2026 15:30), `trial_expired` (installed 20.09.2026; also the
 *   locked modules' 403 answers and the users limit's 409 with 3 active users),
 *   `trial_clock_inconsistent` (installed with the clock three years ahead), `expired` (a demo
 *   licence valid until 10.10.2026), `version_not_covered` and its base-only variant (maintenance
 *   until 30.09.2026, upgraded to the version of 01.10.2026), `wrong_server` (licensed on another
 *   server, moved to this one).
 *
 * Shared by the unit tests (`src/test/samples.ts`) and the E2E mock (`e2e/samples.ts`), so it
 * imports nothing with the `@/` alias and reads no file.
 */

import type { ApiErrorBody, LicenseOut } from '../lib/api-types';

/** The `GET /license` documents (and `POST /license` answers), administrator's form. */
export const LICENSE_DOCUMENT_NAMES = [
  'trial',
  'trial_viewer',
  'trial_last_day',
  'trial_expired',
  'trial_clock_inconsistent',
  'licensed',
  'licensed_viewer',
  'licensed_limits_exceeded',
  'maintenance_ends_soon',
  'maintenance_expired',
  'invalid',
  'revoked',
  'wrong_server',
  'expired',
  'version_not_covered',
  'version_not_covered_base_only',
  'upload_ok',
  'upload_unchanged',
] as const;

/** The error answers: refusals, re-authentication, the locked modules, the users limit, 503. */
export const LICENSE_ERROR_NAMES = [
  'refuse_too_large',
  'refuse_malformed',
  'refuse_unknown_key',
  'refuse_signature_invalid',
  'refuse_revoked',
  'refuse_other_server',
  'refuse_expired',
  'refuse_version_not_covered',
  'remove_not_installed',
  'reauth_required',
  'unavailable',
  'module_locked_usage',
  'module_locked_reports',
  'module_locked_import',
  'user_limit',
] as const;

export type LicenseDocumentName = (typeof LICENSE_DOCUMENT_NAMES)[number];
export type LicenseErrorName = (typeof LICENSE_ERROR_NAMES)[number];

/** A captured error answer: `{status, headers (Retry-After), body}`. */
export interface LicenseErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

export type LicenseSamples = { meta: { clock: string; site_today: string } } & Record<
  LicenseDocumentName,
  LicenseOut
> &
  Record<LicenseErrorName, LicenseErrorSample>;

/**
 * The document as a user without `settings.manage` receives it: the API sends `details: null`
 * and nothing else differs (`trial_viewer` / `licensed_viewer`; `api-samples.test.ts`).
 */
export function viewerForm(document: LicenseOut): LicenseOut {
  return { ...structuredClone(document), details: null };
}

/**
 * The refusal codes of `POST /license` by the file names the E2E mock refuses (the names the
 * capture uploaded): `bozuk.lic` is refused as `license_malformed`, and so on.
 */
export const REFUSED_FILES = {
  'bozuk.lic': 'refuse_malformed',
  'buyuk.lic': 'refuse_too_large',
  'baska-anahtar.lic': 'refuse_unknown_key',
  'degistirilmis.lic': 'refuse_signature_invalid',
  'iptal.lic': 'refuse_revoked',
  'baska-sunucu.lic': 'refuse_other_server',
  'suresi-dolmus.lic': 'refuse_expired',
  'bakim-disi.lic': 'refuse_version_not_covered',
} as const satisfies Record<string, LicenseErrorName>;
