/**
 * Types of the browser administration API (ADR-0014, Wave 1 contract 2.10-2.13): the machine
 * configuration (`/admin/config`), the administration service's status (`/admin/status`), change
 * requests (`/admin/requests`), uploads, the actions (`/seals/{day}/seal`, `/credits/query`,
 * `/admin/actions/*`) and the database settings (`/settings`).
 *
 * Each interface of an OpenAPI schema has its name; `api-types.test.ts` compares them with the
 * generated snapshot `src/generated/api-schemas.json` like those of `api-types.ts`, and
 * `api-samples.test.ts` with the answers captured from the real API (`web/e2e/samples/admin.json`,
 * `settings.json`; the E2E mock `web/e2e/mock-admin.ts` starts from them). Where the API declares
 * a plain string (or any JSON value) whose values come from a fixed list in the Python code
 * (`arcilogger_core.admin.contract`, `settings_meta`), the field has the list as a union here
 * (`NARROWED` in `api-types.test.ts`).
 *
 * Secrets are never values here: a stored credential is `{set, changed_at, changed_by}`, a secret
 * in a change list `{secret: true, old: "set"|"unset", new: …}`.
 */

import type { DateString, DateTimeString } from './api-types';

// -- names (`arcilogger_core.admin.contract`) -----------------------------------------------------

/** The sections of `arcilogger.toml` the browser may change (contract 2.3). */
export type AdminSection =
  'general' | 'tsa' | 'smtp' | 'replica' | 'retention' | 'backup' | 'sources';

/** `admin_requests.kind`: agent requests, and the worker's non-secret actions. */
export type RequestKind =
  | 'config.apply'
  | 'config.revert'
  | 'config.cancel'
  | 'action.seal'
  | 'action.credit'
  | 'action.tsa_test'
  | 'action.replica_test'
  | 'worker.test_email'
  | 'worker.lease_rebuild';

/** Request states (contract 2.10); labels come from the API (`label_tr`, `label_en`). */
export type RequestState =
  | 'queued'
  | 'waiting_for_agent'
  | 'expired'
  | 'received'
  | 'rejected'
  | 'conflict'
  | 'probing'
  | 'probe_failed'
  | 'pending'
  | 'cancelled'
  | 'applying'
  | 'applied'
  | 'active'
  | 'partially_active'
  | 'failed'
  | 'needs_attention'
  | 'dispatched'
  | 'running'
  | 'succeeded'
  | 'timed_out';

export type RequestStep =
  | 'received'
  | 'validate'
  | 'probe'
  | 'hold'
  | 'backup'
  | 'write_secrets'
  | 'write_config'
  | 'install_files'
  | 'verify'
  | 'reload.collector'
  | 'reload.ingest'
  | 'reload.worker'
  | 'reload.api'
  | 'system.firewall'
  | 'system.time'
  | 'jar.promotion'
  | 'dispatch'
  | 'task'
  | 'journal'
  | 'rollback';

export type StepState = 'pending' | 'running' | 'done' | 'skipped' | 'warning' | 'failed';

/** The secrets the browser may set (write-only fields). */
export type BrowserSecret =
  'kamusm.customer' | 'kamusm.password' | 'smtp.password' | 'replica.password';

export type UploadKind = 'kamusm_root' | 'zamane_client' | 'smtp_ca';

/** A code with the API's texts (warnings, step messages). */
export interface CodeMessage {
  code: string;
  message_tr: string;
  message_en: string;
}

/** A field the API refused (`details` of 422 `config_invalid`, `source_invalid`, …). */
export interface FieldProblem extends CodeMessage {
  /** The body path, e.g. `site_name`, `clock.ntp_server`, `source.address`. */
  field: string;
}

// -- GET /admin/config (2.11.1) -------------------------------------------------------------------

/** A stored credential: whether it is set, never its value. */
export interface SecretMeta {
  set: boolean;
  changed_at: DateTimeString | null;
  /** `web:<username>`, `cli` or `installer`. */
  changed_by: string | null;
}

export interface ClockSection {
  ntp_server: string;
  fallback_server: string;
  alert_offset_ms: number;
}

export interface GeneralSection {
  site_name: string;
  clock: ClockSection;
}

export type TsaServer = 'test' | 'production';

export interface StagedJar {
  sha256: string;
  effective_at: DateTimeString | null;
}

export interface ZamaneClient {
  present: boolean;
  sha256: string | null;
  /** A known Kamu SM release (`tsa.zamane_releases`), else `null`. */
  release: string | null;
  /** Why the client may run (`execpolicy`), `false` when it may not. */
  trusted: 'installer' | 'known' | 'approved' | false;
  staged: StagedJar[];
}

export interface RootCertificate {
  sha256: string;
  subject: string;
  not_after: DateTimeString;
  /** Listed in `tsa.legal_root_fingerprints`. */
  legal: boolean;
  source: 'installer' | 'web';
}

export type ReadinessItem = 'credentials' | 'zamane_jar' | 'java' | 'roots';

export interface Readiness {
  /** Everything the production server needs is in place. */
  production: boolean;
  missing: ReadinessItem[];
}

/** `tsa.preflight.seal_preflight`: the sealing causes (`seal.CONFIG_CAUSES`) that stop the Seal task. */
export interface SealReady {
  ok: boolean;
  causes: string[];
}

export interface TsaSection {
  /** `mock`, `rfc3161` or `zamane` (only Zamane can be chosen in the browser). */
  provider: string;
  /** `null` while the provider is not Zamane. */
  server: TsaServer | null;
  credit_alert_threshold: number;
  timeout_seconds: number;
  java: string | null;
  /** The Java 8 installations the administration service found (`agent.json`). */
  java_choices: string[];
  zamane: ZamaneClient;
  roots: RootCertificate[];
  secrets: Partial<Record<BrowserSecret, SecretMeta>>;
  readiness: Readiness;
  seal_ready: SealReady;
}

export type SmtpSecurity = 'starttls' | 'ssl' | 'none';

/** The CA file the e-mail server is checked with (`smtp.ca_file`), `null`: the system's. */
export interface SmtpCa {
  sha256: string | null;
}

export interface SmtpSection {
  enabled: boolean;
  host: string | null;
  port: number;
  security: SmtpSecurity;
  username: string | null;
  from_address: string | null;
  timeout_seconds: number;
  ca: SmtpCa;
  secrets: Partial<Record<BrowserSecret, SecretMeta>>;
}

export interface ReplicaSection {
  enabled: boolean;
  /** `\\host\share[\path]`. */
  target: string | null;
  user: string | null;
  secrets: Partial<Record<BrowserSecret, SecretMeta>>;
}

/** `retention.plan(layout, candidate, today)`. */
export interface RetentionPreview {
  cutoff: DateString;
  days_to_delete: number;
  oldest: DateString | null;
  newest: DateString | null;
  /** The next 02:00 site time after `effective_at` (the Retention task's start). */
  first_run_at?: DateTimeString | null;
}

export interface RetentionSection {
  enabled: boolean;
  days: number;
  minimum_days: number;
  preview: RetentionPreview;
}

export interface BackupSection {
  enabled: boolean;
  keep_daily: number;
  keep_weekly: number;
  keep_monthly: number;
  min_free_gb: number;
}

export type SourceType = 'fortigate' | 'mikrotik' | 'generic';
export type SourceTransport = 'any' | 'udp' | 'tcp';
export type FirewallState = 'open' | 'closed' | 'pending' | 'not_managed';

/** A `[[sources]]` table as the browser edits it (every field of {@link SourceIn}). */
export interface SourceConfig {
  name: string;
  type: SourceType;
  /** An IPv4 address or network (`172.20.10.0/24`). */
  address: string;
  heartbeat_minutes: number;
  expected: boolean;
  transport: SourceTransport;
  enabled: boolean;
}

/** A source in a change body: a field left out takes the `SourceConfig` default of the server. */
export interface SourceIn {
  name: string;
  type: SourceType;
  address: string;
  heartbeat_minutes?: number;
  expected?: boolean;
  transport?: SourceTransport;
  enabled?: boolean;
}

export interface SourceOut extends SourceConfig {
  firewall: FirewallState;
  last_seen: DateTimeString | null;
}

export interface AdminSections {
  general: GeneralSection;
  tsa: TsaSection;
  smtp: SmtpSection;
  replica: ReplicaSection;
  retention: RetentionSection;
  backup: BackupSection;
  sources: SourceOut[];
}

/** Why a path is console only (`config_key_locked`). */
export type LockReason = 'security' | 'service' | 'evidence' | 'expert' | 'collector';

export interface LockedPath {
  path: string;
  reason: LockReason;
}

/** The installer's guard rails (`admin\policy.json`), read only. */
export interface AdminPolicyOut {
  enabled: boolean;
  source_networks: string[];
  min_prefix_v4: number;
  max_sources: number;
  hold_hours: number;
  jar_hold_hours: number;
  tsa_test_per_day: number;
}

/** A held change (`pending\<id>.json`). */
export interface PendingChange {
  request_id: string;
  section: AdminSection | null;
  effective_at: DateTimeString | null;
}

/** `setup_problems` of the configuration, the "Türkçe [English]" text split. */
export interface SetupProblem {
  level: 'warning' | 'error';
  key: string;
  message_tr: string;
  message_en: string;
}

export interface AdminConfig {
  config_sha256: string;
  /** `"<section>:<sha256>"` per section: sent back with a change (409 `config_conflict`). */
  etags: Record<string, string>;
  sections: AdminSections;
  locked: LockedPath[];
  policy: AdminPolicyOut;
  pending: PendingChange[];
  problems: SetupProblem[];
  /** A hand edit of a key the services take only at a restart was not loaded yet. */
  restart_required: boolean;
}

// -- GET /admin/status ----------------------------------------------------------------------------

/** `ok` < 30 s, `stale` 30–120 s, `missing` > 120 s or no heartbeat, `disabled` by the policy. */
export type AgentState = 'ok' | 'stale' | 'missing' | 'disabled';

export interface SystemTaskCheck {
  ok: boolean;
  checked_at: DateTimeString | null;
  error: string | null;
}

/** A held change the administration service waits with. */
export interface PendingRequestOut {
  id: string;
  effective_at: DateTimeString;
}

export interface AgentOut {
  state: AgentState;
  version: string | null;
  heartbeat_at: DateTimeString | null;
  queue: number;
  pending: PendingRequestOut[];
  java_candidates: string[];
  seal_ready: SealReady | null;
  system_task: SystemTaskCheck | null;
}

/** What ApplySystem last did to the Windows firewall. */
export interface FirewallOut {
  managed: boolean;
  strict_outbound: boolean;
  syslog_addresses: string[];
}

/** The time peers ApplySystem set (`w32tm` notation). */
export interface TimeOut {
  peers: string;
}

export interface SystemOut {
  state: 'ok' | 'error' | 'never' | 'stale';
  at: DateTimeString | null;
  applied_config_sha256: string | null;
  firewall: FirewallOut | null;
  time: TimeOut | null;
  code: string | null;
}

/** The `config` block of a service's status file (2.4.12). */
export interface ServiceConfigOut {
  sha256: string | null;
  loaded_at: DateTimeString | null;
  error: string | null;
  restart_keys: string[];
  /** Its status file is younger than 2 minutes (the API itself always). */
  running: boolean;
}

export type ServiceName = 'ArciCollector' | 'ArciIngest' | 'ArciWorker' | 'ArciApi';

export interface AdminStatus {
  agent: AgentOut;
  system: SystemOut;
  services: Partial<Record<ServiceName, ServiceConfigOut>>;
  config_sha256: string;
  /** `outside`: the file was last written by someone other than the agent (a hand edit). */
  config_origin: 'browser' | 'outside';
  restart_required: boolean;
}

// -- POST /admin/config/{section}/check, 202 answers ------------------------------------------------

export interface ValueChange {
  old: unknown;
  new: unknown;
}

export interface SecretChange {
  secret: true;
  old: 'set' | 'unset';
  new: 'set' | 'unset' | 'deleted';
  changed?: boolean;
}

export type Change = ValueChange | SecretChange;

/** A dry run: what a change would do; nothing is queued. */
export interface AdminCheck {
  changes: Record<string, Change>;
  /** `reload.collector`, `system.firewall`, `task.next_run:Seal`, … */
  effects: string[];
  warnings: CodeMessage[];
  /** Held for `policy.hold_hours` (or a Zamane client for `jar_hold_hours`). */
  guarded: boolean;
  effective_at: DateTimeString | null;
  reauth_required: boolean;
  retention_preview?: RetentionPreview | null;
  readiness?: Readiness | null;
}

/**
 * 202: the request is queued; follow it at `status_url`. `cancelled`: `DELETE /admin/requests/{id}`
 * of a request the administration service had not taken (removed at once, nothing to follow).
 */
export interface AdminAccepted {
  request_id: string;
  short_id: string;
  state: 'queued' | 'cancelled';
  status_url: string;
  guarded: boolean;
  effective_at: DateTimeString | null;
  changes: Record<string, Change>;
  effects: string[];
  warnings: CodeMessage[];
}

// -- uploads (201 UploadPreview) ------------------------------------------------------------------

export interface CertificatePreview {
  subject: string;
  issuer: string;
  not_before: DateTimeString;
  not_after: DateTimeString;
  is_ca: boolean;
  self_signed: boolean;
  /** e.g. `RSA 4096`, `EC P-256`. */
  key: string;
  installed: boolean;
  warnings: CodeMessage[];
}

export interface ZamanePreview {
  kind: 'jar' | 'zip';
  /** The client's name inside a ZIP archive, `null` for a .jar. */
  jar_name: string | null;
  jar_sha256: string;
  release: string | null;
  known: boolean;
  accepted: boolean;
  reason: string | null;
}

export interface UploadPreview {
  upload_id: string;
  kind: UploadKind;
  size: number;
  sha256: string;
  expires_at: DateTimeString;
  certificate?: CertificatePreview | null;
  zamane?: ZamanePreview | null;
}

// -- /admin/requests (2.11.2) ---------------------------------------------------------------------

export interface RequestStepOut {
  step: RequestStep;
  state: StepState;
  at?: DateTimeString | null;
  code?: string | null;
  message_tr?: string | null;
  message_en?: string | null;
}

export interface RequestVersion {
  number: number;
  from: string;
  to: string;
}

export interface RequestError extends CodeMessage {
  field?: string | null;
}

/** `action.seal` (`SealOutcome` of the Seal task). */
export interface SealActionResult {
  day: DateString;
  status: 'sealed' | 'pending' | 'failed' | 'waiting' | 'skipped' | 'deleted';
  message: string;
  serial: string | null;
  gen_time: DateTimeString | null;
}

/** `action.credit` (the Requests task's `check_credit`). */
export interface CreditActionResult {
  credits: number;
  threshold: number;
  below_threshold: boolean;
}

/** `action.tsa_test`: the JSON of `arcilogger-core tsa-test` without the token path. */
export interface TsaTestResult {
  provider: string;
  legal_grade_claimed: boolean;
  legal_grade: boolean;
  root_sha256: string | null;
  root: string | null;
  verified: boolean;
  errors: string[];
  warnings: string[];
  gen_time: DateTimeString | null;
  serial: string | null;
  tsa: string | null;
  tsa_cert_not_after: DateTimeString | null;
  container: string | null;
}

/** `action.replica_test`. */
export interface ReplicaTestResult {
  ok: boolean;
  message: string;
}

/** `worker.test_email` (the address masked, ADR-0010 A4: `b***@ornek.example`). */
export interface TestEmailResult {
  ok: boolean;
  to: string;
  /** The server's reply code to a refused message, `null` otherwise. */
  smtp_code: number | null;
  /** A fixed sentence of the worker, never the exception's text. */
  error_text: string | null;
}

/** `worker.lease_rebuild`, also while it runs. */
export interface LeaseRebuildResult {
  done_days: number;
  total_days: number;
  failed_days: DateString[];
}

export interface AdminRequestOut {
  id: string;
  /** The first 8 characters, for support. */
  short_id: string;
  kind: RequestKind;
  section: AdminSection | null;
  state: RequestState;
  terminal: boolean;
  label_tr: string;
  label_en: string;
  requested_by: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
  effective_at: DateTimeString | null;
  version: RequestVersion | null;
  steps: RequestStepOut[];
  changes: Record<string, Change>;
  /** One of the `*Result` types by `kind`, `null` until there is one. */
  result: Record<string, unknown> | null;
  error: RequestError | null;
  rolled_back: boolean;
  warnings: CodeMessage[];
  manual_steps_tr: string | null;
  manual_steps_en: string | null;
}

export interface AdminRequestPage {
  items: AdminRequestOut[];
  next_cursor: string | null;
}

/** `GET /admin/config/versions` [M4]: version *n* is the pair before request *n*. */
export interface ConfigVersion {
  n: number;
  at: DateTimeString;
  request_id: string | null;
  actor: string;
  paths: string[];
  has_secrets: boolean;
}

// -- request bodies (2.11.1, 2.11.3) --------------------------------------------------------------
//
// A field left out of a section's change stays as it is (the API's models default every field);
// the pages send the whole form. A secret is sent once and never read back.

/** The time servers of a general change (a server sent as `null` stays as it is). */
export interface ClockBody {
  ntp_server?: string | null;
  fallback_server?: string | null;
  alert_offset_ms?: number | null;
}

export interface GeneralBody {
  etag: string;
  site_name?: string | null;
  clock?: ClockBody | null;
  /** Test a changed time server first (SNTP); `true` when left out. */
  probe?: boolean;
}

export interface TsaBody {
  etag: string;
  /** `test` or `production` (`mock`: 422 `tsa_mock_refused`). */
  server?: string | null;
  credit_alert_threshold?: number | null;
  timeout_seconds?: number | null;
  java?: string | null;
  customer?: string | null;
  password?: string | null;
  clear_credentials?: boolean;
  /** Production to test stops production stamps: the user said so. */
  confirm?: boolean;
  probe?: boolean;
}

export interface SmtpBody {
  etag: string;
  enabled?: boolean | null;
  host?: string | null;
  port?: number | null;
  security?: SmtpSecurity | null;
  username?: string | null;
  from_address?: string | null;
  timeout_seconds?: number | null;
  password?: string | null;
  clear_password?: boolean;
  /** The probe's test e-mail goes here (an alert recipient or one's own address). */
  test_to?: string | null;
  probe?: boolean;
}

export interface ReplicaBody {
  etag: string;
  enabled?: boolean | null;
  target?: string | null;
  user?: string | null;
  password?: string | null;
  clear_password?: boolean;
  probe?: boolean;
}

export interface RetentionBody {
  etag: string;
  enabled?: boolean | null;
  days?: number | null;
  /** The number of sealed days the change deletes (`retention_preview.days_to_delete`). */
  confirm_delete_days?: number | null;
}

export interface BackupBody {
  etag: string;
  enabled?: boolean | null;
  keep_daily?: number | null;
  keep_weekly?: number | null;
  keep_monthly?: number | null;
  min_free_gb?: number | null;
}

export interface SourceBody {
  etag: string;
  source: SourceIn;
}

/**
 * `POST /admin/config/sources/check`: the body of the addition (`POST`) or of the change
 * (`PUT /sources/{name}`) it checks, and which of the two (contract gap WP5-1: "as the PUT"
 * does not say; a check of a known name as an addition would refuse every change).
 */
export interface SourceCheckBody extends SourceBody {
  operation: 'add' | 'update';
}

export interface RootBody {
  etag: string;
  upload_id: string;
  /** Also a legal root (`tsa.legal_root_fingerprints`; needs `confirmed`). */
  legal?: boolean;
  /** "Parmak izini Kamu SM'nin yayımladığı değerle karşılaştırdım". */
  confirmed?: boolean;
}

export interface LegalRootBody {
  etag: string;
  sha256: string;
  confirmed?: boolean;
}

export interface ZamaneBody {
  etag: string;
  upload_id: string;
  expected_sha256?: string | null;
}

/**
 * `POST /admin/config/smtp/ca`: the server is tried with the new trust first (WP4-4), so a
 * recipient is needed while the server is enabled (422 `smtp_test_recipient_required`); with a
 * user name the password comes again (422 `secret_rebind_required`, WP1-10).
 */
export interface SmtpCaBody {
  etag: string;
  upload_id: string;
  test_to?: string | null;
  password?: string | null;
  probe?: boolean;
}

/**
 * The optional JSON body of `DELETE /admin/config/smtp/ca?etag=` (an address never goes into a
 * URL): the same recipient and password as {@link SmtpCaBody}.
 */
export interface SmtpCaClear {
  test_to?: string | null;
  password?: string | null;
  probe?: boolean;
}

export interface TsaTestBody {
  /** The test spends one credit: 422 `tsa_test_confirm_required` unless `true`. */
  confirm_credit?: boolean;
}

export interface TestEmailBody {
  to: string;
}

export interface LeaseRebuildBody {
  from: DateString;
  to: DateString;
}

/** The body of each section's `PUT` and of its `/check` (the sources: of the check only). */
export interface SectionBodies {
  general: GeneralBody;
  tsa: TsaBody;
  smtp: SmtpBody;
  replica: ReplicaBody;
  retention: RetentionBody;
  backup: BackupBody;
  sources: SourceCheckBody;
}

// -- /settings (2.13) -----------------------------------------------------------------------------

/** The JSON kind of a setting's value (a list's items are integers or strings). */
export type SettingType =
  'boolean' | 'integer' | 'string' | 'string_list' | 'integer_list' | 'object';

export type SettingValue =
  boolean | number | string | string[] | number[] | Record<string, unknown>;

export interface SettingOut {
  key: string;
  value: SettingValue;
  default: SettingValue;
  is_default: boolean;
  type: SettingType;
  /** Bounds of an integer (`INTEGER_RANGES`), `null` otherwise or without one. */
  min: number | null;
  max: number | null;
  /** A change needs a recent re-authentication (`REAUTH_KEYS`). */
  reauth: boolean;
  /** Changed on the server console only (`LOCKED_KEYS`: a path on the server). */
  locked: boolean;
  label_tr: string;
  label_en: string;
  help_tr: string;
  help_en: string;
  updated_by: string | null;
  updated_at: DateTimeString | null;
}

export type SettingGroupId =
  'alerts' | 'lookup' | 'usage' | 'reports' | 'evidence' | 'security' | 'database';

export interface SettingGroup {
  id: SettingGroupId;
  label_tr: string;
  label_en: string;
  settings: SettingOut[];
}

export interface SettingsOut {
  groups: SettingGroup[];
}

export interface SettingChange {
  key: string;
  /** The value shown when the edit began (409 `setting_conflict` when it changed since). */
  old: SettingValue;
  value: SettingValue;
}

export interface SettingsPatch {
  changes: SettingChange[];
}

/** A setting a change (or a reset) wrote. */
export interface ChangedSetting {
  key: string;
  old: SettingValue;
  new: SettingValue;
}

/** The answer of `PATCH /settings` and of `POST /settings/{key}/reset`. */
export interface SettingsPatchResult {
  changed: ChangedSetting[];
  unchanged: string[];
}

/** One refused value (`details` of 422 `setting_invalid`). */
export interface SettingProblem extends CodeMessage {
  key: string;
}

/** `details` of 409 `setting_conflict`. */
export interface SettingConflict {
  key: string;
  current: SettingValue;
}
