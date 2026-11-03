/**
 * Fixtures of the Wave 5 web tests: answers of `GET /legal-status` (contract §2.3.1/§2.6). The
 * named statuses are the captured samples (`e2e/samples/legal-status.json`, written by
 * `web/scripts/capture_api_samples.py` from W5-C's API): {@link LEGAL_OK},
 * {@link LEGAL_PROVIDER_NOT_LEGAL}, {@link LEGAL_WARNING_ONLY}, {@link LEGAL_MIXED} and
 * {@link LEGAL_REVOKED}. {@link LEGAL_REASONS} holds one reason of each code, the captured one
 * where a sample has it; the two codes no captured case reaches (`credentials_in_clear`,
 * `renewal_uncovered`) are hand-built from the §2.6 texts, worded as the API words them.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-legal.ts`), so it imports nothing with the
 * `@/` alias; both run in `web/`, where it reads the file as `src/test/samples.ts` does. Copy a
 * value before changing it.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { LegalReason, LegalReasonCode, LegalStatus } from '../lib/legal-status-types';

/** The captured cases of `samples_legal_status.py` and the refusal without a session. */
interface LegalSamples {
  ok: LegalStatus;
  provider_not_legal: LegalStatus;
  warning_only: LegalStatus;
  mixed: LegalStatus;
  revoked: LegalStatus;
  errors: {
    not_authenticated: {
      status: number;
      headers: Record<string, string>;
      body: { error: { code: string; message_tr: string; message_en: string } };
    };
  };
}

export const LEGAL_SAMPLES = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'legal-status.json'), 'utf8'),
) as LegalSamples;

/** Kamu SM production, nothing to report: no banner. */
export const LEGAL_OK: LegalStatus = LEGAL_SAMPLES.ok;

/** The mock provider: one danger reason, the banner on every page. */
export const LEGAL_PROVIDER_NOT_LEGAL: LegalStatus = LEGAL_SAMPLES.provider_not_legal;

/** Warnings only (can be hidden for 24 h): no current revocation list and re-stamping due. */
export const LEGAL_WARNING_ONLY: LegalStatus = LEGAL_SAMPLES.warning_only;

/** Two danger and two warning reasons: the worst one shown, "ve 3 uyarı daha" opens the rest. */
export const LEGAL_MIXED: LegalStatus = LEGAL_SAMPLES.mixed;

/** A TSA certificate in use is on a revocation list (danger). */
export const LEGAL_REVOKED: LegalStatus = LEGAL_SAMPLES.revoked;

/** When the API computed the status. */
export const LEGAL_CHECKED_AT = LEGAL_OK.checked_at;

/** The first captured reason of each code. */
const CAPTURED = new Map<LegalReasonCode, LegalReason>();
for (const status of [LEGAL_PROVIDER_NOT_LEGAL, LEGAL_WARNING_ONLY, LEGAL_MIXED, LEGAL_REVOKED])
  for (const reason of status.reasons)
    if (!CAPTURED.has(reason.code)) CAPTURED.set(reason.code, reason);

function captured(code: LegalReasonCode): LegalReason {
  const reason = CAPTURED.get(code);
  if (!reason) throw new Error(`legal-status.json has no ${code} reason`);
  return reason;
}

/** One reason of each code, worded as §2.6. */
export const LEGAL_REASONS: Record<LegalReasonCode, LegalReason> = {
  provider_not_legal: captured('provider_not_legal'),
  // Hand-built: no captured case runs TÜRKTRUST over plain HTTP.
  credentials_in_clear: {
    code: 'credentials_in_clear',
    level: 'warning',
    message_tr:
      "TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve parola ağda açık metin olarak gider. Mümkünse HTTPS'e dönün.",
    message_en:
      'The TÜRKTRUST connection uses unencrypted HTTP; the username and password cross the network in clear text. Switch back to HTTPS if you can.',
    since: '2026-10-12T08:30:00Z',
    count: null,
    link: 'settings-tsa',
  },
  legal_root_missing: captured('legal_root_missing'),
  unstamped_days: captured('unstamped_days'),
  renewal_due: captured('renewal_due'),
  // Hand-built: no captured case has a certificate that expired without a counting renewal.
  renewal_uncovered: {
    code: 'renewal_uncovered',
    level: 'warning',
    message_tr:
      '3 damganın sertifikası yeniden damgalanmadan sona erdi; damgalar geçerliliğini korur, ancak uzun süreli kanıt için yeniden damgalama önerilir.',
    message_en:
      '3 stamp certificate(s) expired without re-stamping; the stamps stay valid, but re-stamping is recommended for long-term evidence.',
    since: '2026-10-10T23:30:00Z',
    count: 3,
    link: 'sealing',
  },
  certificate_revoked: captured('certificate_revoked'),
  crl_stale: captured('crl_stale'),
};

/** The production provider of the captured site (`tsa.provider = "zamane"`). */
const KAMUSM = { provider: 'zamane', provider_label: 'Kamu SM' };

/**
 * A status with the reasons of `codes`: the level is the worst reason's, the reasons are sorted
 * danger first, then by code (§2.3.1).
 */
export function legalStatusWith(
  codes: readonly LegalReasonCode[],
  provider: Pick<LegalStatus, 'provider' | 'provider_label'> = KAMUSM,
): LegalStatus {
  const reasons = codes
    .map((code) => structuredClone(LEGAL_REASONS[code]))
    .sort(
      (a, b) =>
        Number(b.level === 'danger') - Number(a.level === 'danger') ||
        (a.code < b.code ? -1 : a.code > b.code ? 1 : 0),
    );
  const level = reasons[0]?.level ?? 'ok';
  return { level, ...provider, reasons, checked_at: LEGAL_CHECKED_AT };
}
