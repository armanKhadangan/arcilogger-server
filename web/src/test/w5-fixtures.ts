/**
 * Fixtures of the Wave 5 web tests: answers of `GET /legal-status`, hand-built from contract
 * §2.3.1/§2.6 until W5-E captures `samples/legal-status.json`. The sentences are the §2.6 texts
 * verbatim with their fields filled (`{count}`, `{hours}`, `{days}`, `{name}`), as W5-C's API words
 * them; the levels, the order (danger first, then by code) and the links follow §2.3.1. They tell
 * the story of one site at the captured samples' clock (13.10.2026 07:00): the mock provider since
 * installation, or Kamu SM (`zamane`) with two closed days late, 14 tokens due for re-stamping
 * within the 180-day window and no revocation list newer than 7 days.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-legal.ts`), so it imports nothing with the
 * `@/` alias and reads no file. Copy a value before changing it.
 */

import type { LegalReason, LegalReasonCode, LegalStatus } from '../lib/legal-status-types';

/** When the API computed the status (07:00 at the site, +03:00). */
export const LEGAL_CHECKED_AT = '2026-10-13T04:00:00Z';

/** One reason of each code, worded as §2.6. */
export const LEGAL_REASONS: Record<LegalReasonCode, LegalReason> = {
  provider_not_legal: {
    code: 'provider_not_legal',
    level: 'danger',
    message_tr:
      "Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu damgaların yasal geçerliliği yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim sunucusunu seçin.",
    message_en:
      'Time stamps come from a trial or test provider and have no legal validity. Choose the Kamu SM production server under Settings → Time stamping.',
    since: '2026-10-05T00:12:00Z',
    count: null,
    link: 'settings-tsa',
  },
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
  legal_root_missing: {
    code: 'legal_root_missing',
    level: 'danger',
    message_tr: 'Kök sertifika parmak izi tanımlı değil; hiçbir gün yasal nitelikli gösterilemez.',
    message_en: 'No legal root fingerprint is set; no day can be shown as legal grade.',
    since: '2026-10-05T00:12:00Z',
    count: null,
    link: 'settings-tsa',
  },
  unstamped_days: {
    code: 'unstamped_days',
    level: 'danger',
    message_tr: '2 kapanmış gün 48 saatten uzun süredir damgalanmadı.',
    message_en: '2 closed day(s) have not been time-stamped for more than 48 hours.',
    since: '2026-10-12T21:00:00Z',
    count: 2,
    link: 'sealing',
  },
  renewal_due: {
    code: 'renewal_due',
    level: 'warning',
    message_tr: '14 damganın sertifikası 180 gün içinde sona eriyor; yeniden damgalama bekleniyor.',
    message_en: '14 stamp certificate(s) expire within 180 days; re-stamping is due.',
    since: '2026-10-11T23:30:00Z',
    count: 14,
    link: 'sealing',
  },
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
  certificate_revoked: {
    code: 'certificate_revoked',
    level: 'danger',
    message_tr:
      'Zaman damgası sertifikası iptal listesinde: Deneme TSA Sertifikası 1. Zaman damgası sağlayıcısıyla görüşün; hukuki inceleme önerilir.',
    message_en:
      'A time-stamp certificate is on a revocation list: Deneme TSA Sertifikası 1. Contact the provider; legal review is recommended.',
    since: '2026-10-13T03:10:12Z',
    count: null,
    link: 'sealing',
  },
  crl_stale: {
    code: 'crl_stale',
    level: 'warning',
    message_tr: '7 günden yeni doğrulanmış bir iptal listesi (SİL) yok.',
    message_en: 'No verified revocation list newer than 7 days.',
    since: '2026-10-13T03:10:12Z',
    count: null,
    link: null,
  },
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

/** Kamu SM production, nothing to report: no banner. */
export const LEGAL_OK: LegalStatus = legalStatusWith([]);

/** The mock provider (the captured samples' site): one danger reason, the banner on every page. */
export const LEGAL_PROVIDER_NOT_LEGAL: LegalStatus = legalStatusWith(['provider_not_legal'], {
  provider: 'mock',
  provider_label: 'Deneme (mock)',
});

/** Warnings only (can be hidden for 24 h): re-stamping due and no current revocation list. */
export const LEGAL_WARNING_ONLY: LegalStatus = legalStatusWith(['renewal_due', 'crl_stale']);

/** Two danger and two warning reasons: the worst one shown, "ve 3 uyarı daha" opens the rest. */
export const LEGAL_MIXED: LegalStatus = legalStatusWith([
  'renewal_due',
  'unstamped_days',
  'crl_stale',
  'legal_root_missing',
]);
