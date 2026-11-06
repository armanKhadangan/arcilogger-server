/** Mirror of api/src/arcilogger_api/legalstatus/models.py (Wave 5 contract 2.3.1, verbatim). */
export type LegalLevel = 'ok' | 'warning' | 'danger';
export type LegalReasonCode =
  | 'provider_not_legal' // mock, rfc3161 or a test server
  | 'credentials_in_clear' // turktrust over plain HTTP (tsa.allow_plain_http)
  | 'legal_root_missing' // legal-grade provider but no legal root fingerprint listed
  | 'unstamped_days' // a closed day older than seal.late_after_hours is not stamped
  | 'renewal_due' // tokens due for re-stamping
  | 'renewal_uncovered' // tokens whose certificate expired with no counting renewal
  | 'certificate_revoked' // a TSA certificate in use is on a CRL (crl-revoked)
  | 'crl_stale'; // no verified CRL newer than tsa.crl_max_age_days
export interface LegalReason {
  code: LegalReasonCode;
  level: Exclude<LegalLevel, 'ok'>;
  message_tr: string;
  message_en: string;
  since: string | null; // ISO time the condition was first seen, if known
  count: number | null; // days or tokens concerned, if countable
  link: 'sealing' | 'settings-tsa' | null; // nav key; the web hides it without permission
}
export interface LegalStatus {
  level: LegalLevel; // the worst reason's level, 'ok' when none
  provider: string; // tsa.provider
  provider_label: string; // "Kamu SM", "TÜRKTRUST", "Test sunucusu", "Deneme (mock)"
  reasons: LegalReason[]; // sorted danger first, then by code
  checked_at: string;
}
