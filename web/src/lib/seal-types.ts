/**
 * `GET /seals/{day}` — everything the sealing page shows about one day (spec 12.3 page 9).
 *
 * The API returns a plain object (`arcilogger_api.sealinfo.VaultView.detail`): the calendar
 * cell's fields plus the manifest, the files exactly as sealed, the time-stamp token, the chain
 * link, the verifications and the replica. Read from the vault and the job state files, never
 * from the database. `api-samples.test.ts` checks these interfaces against answers captured from
 * the real API.
 */

import type { DateString, DateTimeString } from './api-types';

/** A verification of the day: scheduled task (`state/verify`) or "Doğrula" (database). */
export interface SealVerification {
  at: DateTimeString;
  ok: boolean;
  deep: boolean;
  /** `scheduled` | `on_demand`. */
  source: string;
  by: string;
  /** English: the technical text, as stored. */
  errors: string[];
  warnings: string[];
  /** The same lists in Turkish (spec 12.4; unknown messages marked "teknik ayrıntı"). */
  errors_tr: string[];
  warnings_tr: string[];
}

/** The newest "Doğrula" attempt, also inconclusive ones (`timeout`, `error`). */
export interface OnDemandVerification {
  at: DateTimeString;
  started_at: DateTimeString;
  /** `ok` | `failed` | `timeout` | `error`. */
  outcome: string;
  ok: boolean;
  deep: boolean;
  by: string;
  errors: string[];
  warnings: string[];
  errors_tr: string[];
  warnings_tr: string[];
  details: Record<string, unknown>;
}

export interface SealState {
  status: string | null;
  attempts: number | null;
  last_attempt_at: string | null;
  /** The technical (English) text of the last failure. */
  last_error: string | null;
  /** Stable code of the last failure (`tsa_unavailable`, …; older state files: none). */
  last_error_code?: string | null;
  /** The Turkish sentence for the last failure (spec 12.4). */
  last_error_tr?: string | null;
  next_attempt_at: string | null;
  sealed_at: string | null;
}

export interface ReplicaInfo {
  /** `ok`, `failed`, `missing` (enabled, not copied yet), `disabled`, `unreadable`. */
  status: string;
  verified_at?: string | null;
  target?: string | null;
  files?: number | null;
  message?: string;
}

export interface DeletionInfo {
  record: string;
  created_at: string | null;
  manifest_sha256: string | null;
  token_sha256: string | null;
  serial: string | null;
}

export interface ManifestInfo {
  path: string;
  sha256: string;
  bytes: number;
  schema: string | null;
  created_at: string | null;
  window: { from: string; to: string; partition: string } | null;
  system: { name: string; version: string; host: string; site: string } | null;
  tsa: { provider: string; legal_grade: boolean; digest: string } | null;
  stamped_late: boolean;
  late_reason: string | null;
}

/** One sealed file, values exactly as in the manifest. */
export interface SealFile {
  source: string;
  path: string;
  bytes: number;
  bytes_uncompressed: number;
  lines: number;
  first_received: string | null;
  last_received: string | null;
  sha256: string;
  sha256_uncompressed: string;
  sender_ips: string[];
}

export interface SealTotals {
  files: number;
  lines: number;
  bytes: number;
  bytes_uncompressed: number;
}

export interface SealGap {
  source: string;
  from: string;
  to: string;
  reason: string;
}

export interface ClockInfo {
  ntp_server: string | null;
  samples: number;
  max_offset_us: number | null;
  status: string | null;
}

export interface AuditLogFile {
  path: string;
  sha256: string;
  entries: number;
  last_entry_hash: string | null;
}

/** The link to the day before and whether it still matches the vault (or the chain anchor). */
export interface PreviousLink {
  day: string | null;
  manifest_sha256: string | null;
  token_sha256: string | null;
  /** `null`: the previous day is neither in the vault nor the chain anchor (deleted). */
  matches: boolean | null;
  /** `vault` | `chain_anchor` | `null`. */
  target: string | null;
}

export interface TokenCheck {
  /** `null`: no trusted root certificates to check against. */
  ok: boolean | null;
  errors: string[];
  /** `errors` in Turkish (spec 12.4). */
  errors_tr: string[];
  root: string | null;
}

/** The RFC 3161 token of the manifest (`manifest-<day>.json.zd`). */
export interface TokenInfo {
  path: string;
  sha256: string;
  bytes: number;
  provider: string | null;
  /**
   * Legal grade from the verified chain (decision 5): the manifest claims the Kamu SM production
   * server, the token verifies and its root is listed in `tsa.legal_root_fingerprints`.
   */
  legal_grade: boolean;
  /** The manifest's own claim (Kamu SM production server); absent in older answers. */
  legal_grade_claimed?: boolean;
  /** A stamp of ARCILOGGER's test (mock) TSA (provider, policy or root); absent in older answers. */
  test_stamp?: boolean;
  container?: string;
  gen_time?: string;
  serial?: string;
  policy?: string | null;
  tsa_name?: string | null;
  digest_alg?: string | null;
  tsa_cert_not_before?: string | null;
  tsa_cert_not_after?: string | null;
  check: TokenCheck;
}

export interface SealDayDetail {
  day: DateString;
  state: string;
  label_tr: string;
  label_en: string;
  tone: string;
  message_tr: string | null;
  message_en: string | null;
  error_detail: string | null;
  overdue: boolean;
  lines: number | null;
  stamped_late: boolean;
  sealed_at: string | null;
  serial: string | null;
  gen_time: string | null;
  verification: SealVerification | null;
  replica: ReplicaInfo | null;
  sealed: boolean;
  seal_state: SealState | null;
  deleted: DeletionInfo | null;
  verifications: {
    scheduled: SealVerification | null;
    on_demand: OnDemandVerification | null;
  };
  manifest: ManifestInfo | null;
  files: SealFile[];
  totals: SealTotals | null;
  audit_log: AuditLogFile | null;
  gaps: SealGap[];
  clock: ClockInfo | null;
  previous: PreviousLink | null;
  deletions: Record<string, unknown>[];
  chain_check: Record<string, unknown> | null;
  token: TokenInfo | null;
  /** State files that could not be read. */
  problems: string[];
}
