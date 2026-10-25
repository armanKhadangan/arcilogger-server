/**
 * Types of the ARCILOGGER API (`/api/v1`), mirroring the Pydantic models field by field.
 *
 * Each interface has the name of its OpenAPI schema; `api-types.test.ts` compares them with the
 * generated snapshot `src/generated/api-schemas.json` (names, optional vs. required, `null`,
 * enums, nested types). Times are ISO 8601 strings with offset (UTC from the server).
 */

/** ISO 8601 date-time with offset, e.g. `2026-10-12T06:44:07.123456Z`. */
export type DateTimeString = string;

/**
 * A JSON number that may have a fraction (a rate, a share, milliseconds). Every other `number`
 * of these types is an integer, which `api-samples.test.ts` checks in the captured answers.
 */
export type Float = number;

export type Role = 'admin' | 'auditor' | 'hr' | 'viewer';

/** What the web app shows next after a sign-in step (`arcilogger_api.auth.accounts.Step`). */
export type SignInStep = 'totp' | 'totp_setup' | 'password_change' | 'privacy_notice' | 'done';

/** Error body of every endpoint: `{"error": {...}}` (`arcilogger_api.errors`). */
export interface ApiErrorBody {
  error: {
    code: string;
    message_tr: string;
    message_en: string;
    details?: unknown;
  };
}

/** One rule a rejected password broke (`details` of 422 `password_rejected`). */
export interface PasswordProblem {
  rule: string;
  message_tr: string;
  message_en: string;
}

// -- auth ---------------------------------------------------------------------------------------

export interface UserSummary {
  id: number;
  username: string;
  display_name: string;
  role: string;
  role_label_tr: string;
  role_label_en: string;
}

export interface SignInState {
  next: SignInStep;
  mfa_passed: boolean;
  must_change_password: boolean;
  user: UserSummary;
}

export interface MfaStatus {
  passed: boolean;
  enabled: boolean;
  required: boolean;
  recovery_codes_left: number;
}

export interface PrivacyStatus {
  version: string;
  accepted: boolean;
  required: boolean;
}

export interface SessionInfo {
  created_at: DateTimeString;
  expires_at: DateTimeString;
  reauthenticated_at: DateTimeString | null;
}

export interface Me {
  user: UserSummary;
  email: string | null;
  /** Empty until the second factor is passed. */
  permissions: string[];
  must_change_password: boolean;
  mfa: MfaStatus;
  privacy_notice: PrivacyStatus;
  idle_timeout_minutes: number;
  session: SessionInfo;
  next: SignInStep;
  /** The organisation's name (`site_name` of the server's configuration); "" while not set. */
  site_name: string;
}

export interface LoginBody {
  username: string;
  password: string;
}

export interface CodeBody {
  code: string;
}

export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
  /** Standalone SVG document of the QR code (rendered as an image, never as markup). */
  qr_svg: string;
  issuer: string;
  account: string;
  digits: number;
  period: number;
  algorithm: string;
}

export interface TotpEnrolled {
  /** Shown once; only their hashes are stored. */
  recovery_codes: string[];
  state: SignInState;
}

export interface RecoveryCodes {
  recovery_codes: string[];
}

export interface PasswordBody {
  current_password: string;
  new_password: string;
}

export interface ReauthBody {
  password: string;
  /** Required when TOTP is enrolled. */
  code?: string | null;
}

export interface Reauthenticated {
  reauthenticated_at: DateTimeString;
  valid_minutes: number;
}

export interface PrivacyBody {
  version: string;
}

export interface SetupStatusOut {
  needs_setup: boolean;
  token_active: boolean;
  /** `auth.idle_minutes`: the sign-in page's "Oturum N dk hareketsizlikte kapanır". */
  idle_timeout_minutes: number;
}

export interface SetupAdminBody {
  token: string;
  username: string;
  display_name: string;
  password: string;
}

export interface SetupAdminOut {
  user: UserSummary;
  next: string;
}

// -- users --------------------------------------------------------------------------------------

export interface UserOut extends UserSummary {
  email: string | null;
  active: boolean;
  must_change_password: boolean;
  totp_enabled: boolean;
  locked: boolean;
  locked_until: DateTimeString | null;
  failed_attempts: number;
  last_login: DateTimeString | null;
  password_changed_at: DateTimeString | null;
  created_at: DateTimeString;
}

export interface UserPage {
  items: UserOut[];
  next_cursor: string | null;
}

export interface UserCreate {
  username: string;
  display_name: string;
  email?: string | null;
  role: Role;
}

/** Only the fields sent are changed; `email: null` removes the address. */
export interface UserUpdate {
  display_name?: string | null;
  email?: string | null;
  role?: Role | null;
  active?: boolean | null;
}

export interface CreatedUser {
  user: UserOut;
  /** Shown once; the user must change it at the first sign-in. */
  temporary_password: string;
}

export interface TemporaryPassword {
  user: UserOut;
  temporary_password: string;
}

// -- audit --------------------------------------------------------------------------------------

export interface AuditEntry {
  id: number;
  time: DateTimeString;
  actor_id: number | null;
  actor: string | null;
  action: string;
  target: string | null;
  /**
   * `target` shows a usage subject by its pseudonym token (`usage:p:…`), not as stored: the reader
   * sees pseudonyms on the Kullanım pages. The stored hash covers the real target.
   */
  target_masked: boolean;
  details: Record<string, unknown>;
  ip: string | null;
  /**
   * `null` on every entry for a reader who sees pseudonyms on the Kullanım pages: with the hashes
   * a guess of a masked target could be tested. The server still verifies the chain.
   */
  prev_hash: string | null;
  hash: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  next_cursor: number | null;
}

export interface ChainStatus {
  ok: boolean;
  /** Number of entries checked (the whole log). */
  entries: number;
  first_bad_id: number | null;
  last_hash: string | null;
  errors: string[];
  verified_at: DateTimeString;
}

/** Query parameters of `GET /audit` (`from` inclusive, `to` exclusive). */
export interface AuditFilters {
  from?: string;
  to?: string;
  actor?: string;
  action?: string;
  target?: string;
}

// -- 5651 lookup (`/lookup`, `/lookups`, `/raw`) ------------------------------------------------

/** ISO 8601 calendar date, e.g. `2026-10-12` (site day, Europe/Istanbul). */
export type DateString = string;

/** Talep bilgileri: all required, never blank (spec 12.3 page 6). */
export interface RequestDetails {
  authority: string;
  file_no: string;
  request_date: DateString;
  /** Line breaks allowed. */
  reason: string;
}

/**
 * What the official request asks. `at` without an offset is a local time in `time_zone`
 * (default Europe/Istanbul); with an offset (`+03:00`, `Z`) it is taken as given.
 */
export interface QueryIn {
  at: string;
  time_zone?: string;
  public_ip?: string | null;
  public_port?: number | null;
  /** IP protocol number (6 TCP, 17 UDP, 1 ICMP). */
  protocol?: number | null;
  dest_ip?: string | null;
  dest_port?: number | null;
  dest_domain?: string | null;
  tolerance_seconds?: number | null;
  internal_ip?: string | null;
  mac?: string | null;
}

export interface LookupIn {
  request: RequestDetails;
  query: QueryIn;
}

export interface RequestOut {
  authority: string;
  file_no: string;
  request_date: DateString;
  reason: string;
}

/** The stored official report (`lookup_reports`), `null` until it was generated. */
export interface ReportOut {
  sha256: string;
  size: number;
  pages: number;
  created_at: DateTimeString;
  username: string;
  version: string;
  /** `not_requested` | `stamped` | `failed`. */
  timestamp_status: string;
  /** Provider, legal grade, serial, genTime, TSA … (`ReportTimestamp` in `lookup-types.ts`). */
  timestamp: Record<string, unknown> | null;
  download_count: number;
}

export interface LookupOut {
  id: number;
  created_at: DateTimeString;
  finished_at: DateTimeString | null;
  user_id: number | null;
  username: string;
  request: RequestOut;
  /** `LookupRequest.to_dict()` + `time_input` (`StoredQuery` in `lookup-types.ts`). */
  query: Record<string, unknown>;
  mode: string | null;
  result_count: number;
  /** `LookupOutcome.to_dict()` (`LookupOutcome` in `lookup-types.ts`). */
  outcome: Record<string, unknown> | null;
  report: ReportOut | null;
  /** Only in the answer to `POST /lookup/5651`. */
  elapsed_ms?: number | null;
}

export interface LookupSummary {
  id: number;
  created_at: DateTimeString;
  user_id: number | null;
  username: string;
  authority: string;
  file_no: string;
  request_date: DateString;
  mode: string | null;
  result_count: number;
  /** SHA-256 of the official report, `null` until it was generated. */
  report_sha256: string | null;
}

export interface LookupPage {
  items: LookupSummary[];
  next_cursor: number | null;
  /** `true` when the list holds every user's lookups (administrators). */
  all_users: boolean;
}

/** Query parameters of `GET /lookups` (`from` inclusive, `to` exclusive). */
export interface LookupFilters {
  mine?: boolean;
  username?: string;
  mode?: string;
  from?: string;
  to?: string;
}

/** One raw log line, byte-exact (`raw_base64`) and as display text (escapes visible). */
export interface RawLineOut {
  source: string;
  day: DateString;
  line: number;
  offset: number;
  /** `spool` (not sealed yet) or `vault` (sealed day). */
  origin: string;
  /** Data-directory relative path of the file the line was read from. */
  file: string;
  /** Bytes of the line without its line feed. */
  length: number;
  sha256: string;
  raw_base64: string;
  text: string;
  received: DateTimeString | null;
  sender: string | null;
}

// -- evidence packages (`/evidence`) -------------------------------------------------------------

/** One raw line of a sealed day (line numbers start at 1). */
export interface RawLine {
  day: DateString;
  source: string;
  line: number;
}

/** Scope: `lookup_id` **or** `from`/`to` (inclusive, at most 31 days). */
export interface EvidenceCreate {
  lookup_id?: number | null;
  from?: DateString | null;
  to?: DateString | null;
  sources?: string[] | null;
  extract?: RawLine[];
  content?: 'full_days' | 'extract';
  password?: boolean;
  /** Request reference written into the package, e.g. "Savcılık 2026/123". */
  reference?: string | null;
  /**
   * Add the days' ARCILOGGER audit log (internal audits; administrators only — 403
   * `audit_log_admin_only` otherwise). Off by default (KVKK data minimisation).
   */
  include_audit_log?: boolean;
}

export interface Scope {
  content: string;
  days: DateString[];
  sources: string[] | null;
  extract_lines: number;
  lookup_id: number | null;
  reference: string | null;
  /** Size estimate made when the package was requested. */
  estimated_bytes?: number | null;
  /** The days' ARCILOGGER audit log is in the package. */
  include_audit_log?: boolean;
}

export interface EvidenceOut {
  id: number;
  created_at: DateTimeString;
  created_by: string;
  user_id: number | null;
  lookup_id: number | null;
  /** `queued` | `building` | `ready` | `failed` | `expired` | `deleted`. */
  status: string;
  status_label_tr: string;
  status_label_en: string;
  error: string | null;
  scope: Scope;
  package_name: string | null;
  file: string | null;
  size: number | null;
  sha256: string | null;
  index_sha256: string | null;
  /** SHA-256 of the package's verify.py: give it to the recipient on a separate channel. */
  verifier_sha256: string | null;
  legal_grade: boolean | null;
  password_protected: boolean;
  expires_at: DateTimeString;
  finished_at: DateTimeString | null;
  download_count: number;
  downloadable: boolean;
}

export interface DownloadOut {
  time: DateTimeString;
  username: string;
  ip: string | null;
}

export interface EvidenceDetail extends EvidenceOut {
  extract: RawLine[];
  downloads: DownloadOut[];
}

export interface EvidencePage {
  items: EvidenceOut[];
  next_cursor: number | null;
}

export interface EvidenceCreated {
  package: EvidenceOut;
  /** Only for `password: true` — shown once, never stored. */
  password: string | null;
  password_notice_tr: string | null;
  password_notice_en: string | null;
}

// -- sealing (`/seals`, `/credits`) --------------------------------------------------------------

export interface CalendarDay {
  day: DateString;
  /** `verified`, `sealed`, `late`, `pending`, `failed`, `open`, `future`, `no_data`, `deleted`. */
  state: string;
  label_tr: string;
  label_en: string;
  /** `ok` | `warning` | `error` | `neutral`. */
  tone: string;
  message_tr: string | null;
  message_en: string | null;
  /** The technical (English) text behind the message, shown apart from it. */
  error_detail: string | null;
  overdue: boolean;
  files: number | null;
  lines: number | null;
  stamped_late: boolean;
  sealed_at: string | null;
  serial: string | null;
  gen_time: string | null;
  /** `{at, ok, source}` of the newest verification. */
  verification: Record<string, unknown> | null;
  replica: string | null;
}

export interface StateLegend {
  state: string;
  label_tr: string;
  label_en: string;
  tone: string;
}

export interface Calendar {
  month: string;
  today: DateString;
  days: CalendarDay[];
  counts: Record<string, number>;
  legend: StateLegend[];
  recent_verify_days: number;
  replica_enabled: boolean;
  /** State files that could not be read (shown as a warning). */
  problems: string[];
}

export interface VerifyResult {
  day: DateString;
  /** `ok` | `failed` | `timeout` | `error`. */
  outcome: string;
  ok: boolean;
  deep: boolean;
  started_at: DateTimeString;
  finished_at: DateTimeString;
  duration_ms: number;
  errors: string[];
  warnings: string[];
  /** `errors` / `warnings` in Turkish (the API translates the core's messages). */
  errors_tr: string[];
  warnings_tr: string[];
  details: Record<string, unknown>;
  message_tr: string;
  message_en: string;
}

export interface CreditCheck {
  time: DateTimeString;
  provider: string | null;
  /** `null`: the provider could not be asked, or it has no credit concept. */
  credits: number | null;
  error: string | null;
}

export interface Credits {
  provider: string;
  legal_grade: boolean;
  threshold: number;
  latest: CreditCheck | null;
  /** The last known number (the newest check that returned one). */
  remaining: number | null;
  below_threshold: boolean;
  history: CreditCheck[];
  unreadable_lines: number;
}

// -- devices and persons (`/devices`, `/persons`, `/departments`, `/vlans`, `/imports`) ----------

/** `arcilogger_db.models.DEVICE_TYPES` (a device without a type is `null`: unknown). */
export type DeviceType =
  'pc' | 'phone' | 'printer' | 'ip_phone' | 'ap' | 'server' | 'camera' | 'other';

export type ImportKind = 'persons' | 'assignments';

export interface PersonRef {
  id: number;
  name: string;
}

export interface DepartmentRef {
  id: number;
  name: string;
}

/** A device by its MAC address (lower case `aa:bb:cc:dd:ee:ff`) with its current owner. */
export interface DeviceOut {
  mac: string;
  name: string | null;
  vendor: string | null;
  /** Locally administered (randomised/private) MAC address, typical of phones (spec 9.2). */
  randomized: boolean;
  hostnames: string[];
  last_ip: string | null;
  last_vlan: number | null;
  vlan_name: string | null;
  /** One of {@link DeviceType}, `null` = unknown. */
  type: string | null;
  infrastructure: boolean;
  notes: string | null;
  tags: string[];
  first_seen: DateTimeString | null;
  last_seen: DateTimeString | null;
  /** Current owner (the open assignment). */
  person: PersonRef | null;
  /** `valid_from` of the open assignment. */
  assigned_since: DateTimeString | null;
  /** Effective department: the person's, else the VLAN's. */
  department: DepartmentRef | null;
  department_source: 'person' | 'vlan' | null;
}

/** One row of a device's assignment history (`valid_to: null` = still valid). */
export interface AssignmentOut {
  id: number;
  mac: string;
  /** `null`: the device belonged to nobody in this interval. */
  person: PersonRef | null;
  valid_from: DateTimeString;
  valid_to: DateTimeString | null;
  assigned_by: string;
  note: string | null;
  /** Taken over from the device's owner of before the history existed. */
  backfilled: boolean;
}

export interface LeaseBrief {
  ip: string;
  vlan: number | null;
  start: DateTimeString;
  end: DateTimeString | null;
  end_reason: string | null;
  confidence: string;
}

/** A device with its assignment history and its newest leases (≤ 20), newest first. */
export interface DeviceDetail extends DeviceOut {
  assignments: AssignmentOut[];
  recent_leases: LeaseBrief[];
}

export interface DevicePage {
  items: DeviceOut[];
  total: number;
  limit: number;
  offset: number;
}

/** Only the fields sent are changed. */
export interface DeviceUpdate {
  name?: string | null;
  type?: DeviceType | null;
  infrastructure?: boolean | null;
  notes?: string | null;
  tags?: string[] | null;
}

/**
 * Closes each device's open assignment at `valid_from` (default: now) and opens a new one;
 * `person_id: null` only closes. All or nothing: 409 `assignment_in_future` /
 * `assignment_overlap` (`details: [{mac, reason, earliest}]` — the history is appended to, never
 * rewritten), 409 `person_inactive`, 404 `device_not_found`. A device the person already holds
 * is left unchanged (not counted in `updated`).
 */
export interface AssignRequest {
  macs: string[];
  person_id: number | null;
  valid_from?: DateTimeString | null;
  note?: string | null;
}

export interface AssignResult {
  updated: number;
  devices: DeviceOut[];
}

export interface PersonOut {
  id: number;
  name: string;
  department: DepartmentRef | null;
  title: string | null;
  email: string | null;
  employee_no: string | null;
  /** Persons are never deleted: a deactivated person keeps the assignment history. */
  active: boolean;
  notes: string | null;
  /**
   * Left out of every person-level usage view and report (the company and department totals
   * still count the traffic); 5651 lookups never read it.
   */
  usage_excluded: boolean;
  device_count: number;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

export interface PersonDetail extends PersonOut {
  devices: DeviceOut[];
  assignments: AssignmentOut[];
}

export interface PersonPage {
  items: PersonOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface PersonCreate {
  name: string;
  department_id?: number | null;
  title?: string | null;
  email?: string | null;
  employee_no?: string | null;
  /** Default `true`. */
  active?: boolean;
  notes?: string | null;
  /** Default `false`. */
  usage_excluded?: boolean;
}

/**
 * Only the fields sent are changed (`name`, `active` and `usage_excluded` never `null`;
 * `active: false` deactivates — there is no delete, and the person's current devices stay
 * assigned).
 */
export interface PersonUpdate {
  name?: string | null;
  department_id?: number | null;
  title?: string | null;
  email?: string | null;
  employee_no?: string | null;
  active?: boolean | null;
  notes?: string | null;
  usage_excluded?: boolean | null;
}

export interface DepartmentOut {
  id: number;
  name: string;
  notes: string | null;
  person_count: number;
  vlan_ids: number[];
}

export interface DepartmentCreate {
  name: string;
  notes?: string | null;
}

export interface DepartmentUpdate {
  name?: string | null;
  notes?: string | null;
}

export interface VlanOut {
  /** The network with its prefix length (`10.0.20.0/24`): names the VLAN. */
  subnet: string;
  vlan_id: number | null;
  name_tr: string;
  purpose: string | null;
  /** The DHCP server's name on the router (`dhcp20`). */
  dhcp_server: string | null;
  department: DepartmentRef | null;
  guest: boolean;
  notes: string | null;
}

/** `POST /vlans`: only `subnet` (host bits zero) and `name_tr` are required. */
export interface VlanCreate {
  subnet: string;
  vlan_id?: number | null;
  name_tr: string;
  purpose?: string | null;
  dhcp_server?: string | null;
  department_id?: number | null;
  guest?: boolean;
  notes?: string | null;
}

/**
 * `PATCH /vlans`: `subnet` names the VLAN, only the other fields sent are changed (`null`
 * clears one; not `name_tr`, `guest` or `new_subnet`). `new_subnet` moves the VLAN to another
 * network; `{subnet, department_id}` changes only its department.
 */
export interface VlanUpdate {
  subnet: string;
  new_subnet?: string | null;
  vlan_id?: number | null;
  name_tr?: string | null;
  purpose?: string | null;
  dhcp_server?: string | null;
  department_id?: number | null;
  guest?: boolean | null;
  notes?: string | null;
}

/** A refused CSV line (the header is line 1). */
export interface ImportLineError {
  line: number;
  message_tr: string;
  message_en: string;
}

/**
 * Answer of `POST /imports/{kind}`; with `dry_run` nothing was written, and an apply writes every
 * line or — when a line has an error — none. Problems of the whole file (encoding, header, size)
 * are API errors, not lines.
 */
export interface ImportResult {
  kind: ImportKind;
  dry_run: boolean;
  rows: number;
  created: number;
  updated: number;
  unchanged: number;
  /** At most 200, then one "… and n more" entry. */
  errors: ImportLineError[];
  /** ≤ 50 rows of what would change (or changed): {@link PersonPreview}, {@link AssignmentPreview}. */
  preview: Record<string, unknown>[];
}

/** A preview row of a persons import (`ImportResult.preview`, untyped in the API). */
export interface PersonPreview {
  line: number;
  action: 'create' | 'update';
  person_id: number | null;
  name: string;
  employee_no: string | null;
  /** Fields that would change (`name`, `department_id`, `title`, …). */
  changes: string[];
  /** Another person has the same name (a hint, not an error). */
  duplicate_name: boolean;
}

/** A preview row of an assignments import. */
export interface AssignmentPreview {
  line: number;
  action: 'assign' | 'reassign';
  mac: string;
  person_id: number;
  person_name: string;
  previous_person_id: number | null;
  previous_person_name: string | null;
  valid_from: DateTimeString;
}

/** Query parameters of `GET /devices`. */
export interface DeviceFilters {
  /** Part of a MAC address, host name, IP address or device name (any case). */
  q?: string;
  /** "Bilinmeyen cihazlar": no current owner and not infrastructure. */
  unknown?: boolean;
  type?: DeviceType;
  person_id?: number;
  vlan?: number;
  randomized?: boolean;
  infrastructure?: boolean;
  sort?: DeviceSort;
  order?: SortOrder;
  limit?: number;
  offset?: number;
}

export type DeviceSort = 'last_seen' | 'first_seen' | 'mac' | 'last_ip' | 'name';
export type SortOrder = 'asc' | 'desc';

/** Query parameters of `GET /persons`. */
export interface PersonFilters {
  /** Part of the name, e-mail address, employee number or title (any case). */
  q?: string;
  active?: boolean;
  department_id?: number;
  limit?: number;
  offset?: number;
}

// -- security alerts (`/alerts`) -----------------------------------------------------------------

/** Bilgi, Düşük, Orta, Yüksek, Kritik (spec 11). */
export type AlertSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical';

/** Spec 11: Yeni (`open`), İnceleniyor (`acknowledged`), Kapandı (`resolved`), Yanlış alarm. */
export type AlertState = 'open' | 'acknowledged' | 'resolved' | 'dismissed';

export interface AlertOut {
  id: number;
  /** When the problem was detected. */
  time: DateTimeString;
  created_at: DateTimeString;
  updated_at: DateTimeString;
  resolved_at: DateTimeString | null;
  /** Rule id, e.g. `R15`, `R20-seal`. Names and runbooks come from the API's catalogue. */
  rule: string;
  rule_name_tr: string;
  rule_name_en: string;
  severity: AlertSeverity;
  state: AlertState;
  title: string;
  summary_tr: string;
  summary_en: string;
  /** Closed by the system because the problem went away. */
  auto_resolved: boolean;
  notes_count: number;
}

/**
 * One entry of an alert's history (oldest first): a state change or a note. Automatic changes
 * are `by: "system"` with a reason code as `text` (`messages_again`, `credit_ok`, …); a note sent
 * with a state change is its own `note` event right after it.
 */
export interface AlertEvent {
  id: number;
  kind: 'state' | 'note';
  from_state: AlertState | null;
  to_state: AlertState | null;
  text: string | null;
  by: string;
  at: DateTimeString;
}

/** An e-mail about the alert (the address masked, e.g. `a***@ornek.com.tr`). */
export interface AlertDeliveryOut {
  kind: 'new' | 'resolved';
  /** `suppressed`: not sent because of the hourly limit (counted in the next summary). */
  status: 'queued' | 'sent' | 'failed' | 'suppressed';
  attempts: number;
  sent_at: DateTimeString | null;
  recipient_masked: string;
}

export interface AlertDetail extends AlertOut {
  /** The detector's facts, sanitised by the API (no secrets). */
  details: Record<string, unknown>;
  /** Oldest first. */
  events: AlertEvent[];
  runbook_tr: string;
  runbook_en: string;
  deliveries: AlertDeliveryOut[];
}

export interface AlertPage {
  items: AlertOut[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * `POST /alerts/{id}/state`; 409 `alert_state_unchanged`, `alert_transition_not_allowed` or
 * `alert_key_active` (`details.alert_id`: the active alert of the same problem).
 */
export interface AlertStateRequest {
  state: AlertState;
  note?: string | null;
}

/** `POST /alerts/{id}/notes` (1 … 2000 characters). */
export interface AlertNoteRequest {
  text: string;
}

export interface OpenBySeverity {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

/** "Güvenlik Özeti": open alerts by severity, those being investigated, mean time to close. */
export interface AlertSummary {
  open_by_severity: OpenBySeverity;
  acknowledged: number;
  /** The newest open or acknowledged alerts (≤ 5). */
  latest: AlertOut[];
  /** Over the alerts closed in the last 30 days; `null` when none was closed. */
  mean_time_to_close_seconds_30d: number | null;
}

export type AlertSort = 'time' | 'created_at' | 'updated_at' | 'severity' | 'state' | 'rule';

/** Query parameters of `GET /alerts`. */
export interface AlertFilters {
  state?: AlertState;
  severity?: AlertSeverity;
  rule?: string;
  /** Part of the title or summary (any case). */
  q?: string;
  /** Inclusive start, exclusive end (ISO 8601 with offset). */
  from?: string;
  to?: string;
  sort?: AlertSort;
  order?: SortOrder;
  limit?: number;
  offset?: number;
}

// -- dashboard (`/dashboard/summary`) -------------------------------------------------------------

/** A source that sent nothing for longer than its heartbeat threshold. */
export interface SilentSource {
  name: string;
  last_seen: DateTimeString | null;
}

export interface MinuteCount {
  minute: DateTimeString;
  count: number;
}

/** "Log alımı" (`sources.read` or `dashboard.view`). */
export interface DashboardIngest {
  sources_total: number;
  sources_active: number;
  silent: SilentSource[];
  messages_per_minute: Float;
  /** The last 60 minutes, oldest first. */
  sparkline: MinuteCount[];
}

export type SealDayState = 'sealed' | 'pending' | 'failed' | 'no_data';

export interface SealYesterday {
  day: DateString;
  status: SealDayState;
  sealed_at: DateTimeString | null;
  /** The first characters of the time-stamp serial number. */
  serial_short: string | null;
  /** `null`: not verified yet. */
  verified: boolean | null;
}

export interface SealCalendarDay {
  day: DateString;
  status: SealDayState;
}

/** "Dünkü mühür" and the 30 days ending yesterday (`seal.read`). */
export interface DashboardSeal {
  yesterday: SealYesterday;
  calendar: SealCalendarDay[];
}

/** "Kontör" (`seal.read`). */
export interface DashboardCredit {
  remaining: number | null;
  /** How long the credit lasts at the current use, in days. */
  estimated_days: number | null;
  threshold: number | null;
}

/** "Saat sapması" (`system.read` or `seal.read`); `ok: null` when the sample is too old. */
export interface DashboardClock {
  offset_ms: Float | null;
  ntp_server: string | null;
  checked_at: DateTimeString | null;
  ok: boolean | null;
}

/** Open alerts (`alerts.read`). */
export interface DashboardAlerts {
  open_by_severity: OpenBySeverity;
  latest: AlertOut[];
}

/** `GET /dashboard/summary`: each block is `null` when the user lacks its permission. */
export interface DashboardSummary {
  generated_at: DateTimeString;
  ingest: DashboardIngest | null;
  seal: DashboardSeal | null;
  credit: DashboardCredit | null;
  clock: DashboardClock | null;
  alerts: DashboardAlerts | null;
}

// -- usage, company totals (`GET /usage/company`, read by the Panel) -----------------------------
// The schemas the Panel needs, field for field as in the generated contract (area U).

/** Site days (Europe/Istanbul), inclusive; `hours`: all, work hours or off hours. */
export interface UsagePeriod {
  from: DateString;
  to: DateString;
  hours: 'all' | 'work' | 'off';
}

/** Usage figures: estimates computed from connection logs, never exact screen time. */
export interface Metrics {
  online_seconds: number;
  active_seconds: number;
  download_bytes: number;
  upload_bytes: number;
  sessions: number;
  blocked: number;
  off_hours_active_seconds: number;
  first_activity: DateTimeString | null;
  last_activity: DateTimeString | null;
}

export interface HourPoint {
  hour: DateTimeString;
  active_users: number;
  active_seconds: number;
  download_bytes: number;
  upload_bytes: number;
}

export interface SiteRow {
  /** A registrable domain, a service name or a bare address (`kind`). */
  domain: string;
  kind: 'domain' | 'service' | 'ip';
  category: string | null;
  seconds: number;
  raw_seconds: number;
  /** 0 … 1 of the active time. */
  share: Float;
  download_bytes: number;
  upload_bytes: number;
  sessions: number;
  users: number;
  background: boolean;
}

export interface CategoryRow {
  /** "Bilinmiyor" when unknown. */
  category: string;
  seconds: number;
  share: Float;
  download_bytes: number;
  upload_bytes: number;
  users: number;
}

export interface AppRow {
  app: string;
  appcat: string | null;
  seconds: number;
  bytes: number;
}

export interface DepartmentRow {
  department: string;
  source: 'person' | 'vlan';
  active_users: number;
  metrics: Metrics;
}

/** `GET /usage/company` (`dashboard.usage`, `usage.person` or `usage.anonymous`). */
export interface CompanyUsage {
  period: UsagePeriod;
  estimated: boolean;
  /** How far the rollups have processed traffic. */
  computed_until: DateTimeString | null;
  /** Locale key of the method note (`usage.method_note`). */
  notice_key: string;
  active_users: number;
  metrics: Metrics;
  hourly: HourPoint[];
  top_sites: SiteRow[];
  top_categories: CategoryRow[];
  top_apps: AppRow[];
  departments: DepartmentRow[];
}

// -- scheduled usage reports (`/reports`, group 2 contract section 4, MVP-CONTRACTS §13) ---------

/**
 * `end_of_day` 00:00 → `time` of the day, `full_day` the previous day, `weekly` the previous ISO
 * week (on `weekday`), `monthly` the previous month (on `monthday`).
 */
export type ReportKind = 'end_of_day' | 'full_day' | 'weekly' | 'monthly';
/** A run with a period given by hand is `custom`. */
export type ReportRunKind = ReportKind | 'custom';
/** A run without e-mail (`send: false`) also ends `sent` (its deliveries `skipped`). */
export type ReportRunStatus = 'queued' | 'running' | 'sent' | 'partial' | 'failed';

export interface ReportFormats {
  email_html: boolean;
  pdf: boolean;
  xlsx: boolean;
}

/** `[from, to)`: the end is exclusive. */
export interface ReportPeriod {
  from: DateTimeString;
  to: DateTimeString;
}

export interface ReportFile {
  name: string;
  format: 'pdf' | 'xlsx' | 'html';
  size: number;
  sha256: string;
}

/**
 * The tried recipients only (one not tried yet is left out); a delivery waiting for its retry is
 * `failed` with the last error while the run is `running`.
 */
export interface ReportDelivery {
  recipient: string;
  status: 'sent' | 'failed' | 'skipped';
  error: string | null;
  sent_at: DateTimeString | null;
}

export interface RunOut {
  id: number;
  /** `null` once the schedule was deleted (its runs and files are kept). */
  schedule_id: number | null;
  kind: ReportRunKind;
  period: ReportPeriod;
  status: ReportRunStatus;
  /** When the run was queued. */
  started_at: DateTimeString;
  finished_at: DateTimeString | null;
  error: string | null;
  files: ReportFile[];
  deliveries: ReportDelivery[];
  triggered_by: 'schedule' | 'manual';
  requested_by: string | null;
}

export interface RunPage {
  items: RunOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface ScheduleOut {
  id: number;
  name: string;
  kind: ReportKind;
  enabled: boolean;
  /** `HH:MM`, site time (Europe/Istanbul). */
  time: string;
  /** 1 = Monday … 7 = Sunday (weekly only). */
  weekday: number | null;
  /** 1–28 (monthly only). */
  monthday: number | null;
  recipients: string[];
  formats: ReportFormats;
  /** Person-level detail (owner decision 2026-10-20: on by default). */
  person_level: boolean;
  pseudonymize: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
  updated_by: string;
  next_run_at: DateTimeString | null;
  last_run: RunOut | null;
}

export interface ScheduleCreate {
  name: string;
  kind: ReportKind;
  enabled?: boolean;
  time: string;
  weekday?: number | null;
  monthday?: number | null;
  recipients: string[];
  formats: ReportFormats;
  person_level?: boolean;
  pseudonymize?: boolean;
}

/** Only the fields sent are changed. */
export interface ScheduleUpdate {
  name?: string | null;
  kind?: ReportKind | null;
  enabled?: boolean | null;
  time?: string | null;
  weekday?: number | null;
  monthday?: number | null;
  recipients?: string[] | null;
  formats?: ReportFormats | null;
  person_level?: boolean | null;
  pseudonymize?: boolean | null;
}

/**
 * Body of `POST /reports/schedules/{id}/run` (optional): the schedule's own period unless
 * `from`/`to` are given (then `custom`, at most 92 days, `to` cut at now; a time without an offset
 * is site time); `send` e-mails it.
 */
export interface RunRequest {
  from?: DateTimeString | null;
  to?: DateTimeString | null;
  send?: boolean;
}

/** Query parameters of `GET /reports/runs`. */
export interface RunFilters {
  schedule_id?: number;
  status?: ReportRunStatus;
  limit?: number;
  offset?: number;
}

// -- usage analytics (`/usage`; group 2 contract section 2, spec 10) -----------------------------

/** `all` = every hour, `work` = within the work-hours setting, `off` = outside it. */
export type UsageHours = UsagePeriod['hours'];
/** A person (if linked), else a device (MAC), else an IP address (spec 10.1). */
export type SubjectKind = 'person' | 'device' | 'ip';
export type BlockedSource = 'web' | 'dns' | 'app';
export type RebuildState = 'queued' | 'running' | 'done' | 'failed';

/**
 * Who a usage row belongs to. `key` is `person:<id>`, `device:<mac>` or `ip:<addr>` — or, for a
 * viewer who gets pseudonyms (`usage.pseudonymize_anonymous_role`), an opaque `p:…` / `d:…` that
 * the API accepts back; `label` is then the pseudonym and `pseudonymous` true.
 */
export interface SubjectRef {
  kind: SubjectKind;
  key: string;
  label: string;
  pseudonymous: boolean;
  /** The device's MAC, IP address and host name (`null` for a pseudonymous viewer). */
  mac?: string | null;
  ip?: string | null;
  hostname?: string | null;
}

/** A blocked attempt (web filter, DNS filter or application control). */
export interface BlockedRow {
  time: DateTimeString;
  domain: string | null;
  category: string | null;
  action: string;
  source: BlockedSource;
  /** The full host name and the path (web filter), the application (application control). */
  hostname?: string | null;
  url?: string | null;
  app?: string | null;
}

/** Merged active minutes of one day. */
export interface TimelineSpan {
  start: DateTimeString;
  end: DateTimeString;
  category: string | null;
}

/** Active minutes of one hour (0…23) of one site day. */
export interface HeatCell {
  day: DateString;
  hour: number;
  active_minutes: number;
}

export interface TopSite {
  domain: string;
  seconds: number;
}

export interface TopCategory {
  category: string;
  seconds: number;
}

/** A person or device of a list (`top_sites` ≤ 10, `top_categories` ≤ 5). */
export interface SubjectRow {
  subject: SubjectRef;
  department: string | null;
  metrics: Metrics;
  top_sites: TopSite[];
  top_categories: TopCategory[];
}

/** `GET /usage/subjects?kind=person|device` (one page; the web app reads every page). */
export interface SubjectPage {
  items: SubjectRow[];
  total: number;
  limit: number;
  offset: number;
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
}

/** `GET /usage/subjects?kind=department` (one page). */
export interface DepartmentPage {
  items: DepartmentRow[];
  total: number;
  limit: number;
  offset: number;
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
}

/** A host name of a subject's traffic (`domain`: its registrable domain). */
export interface HostnameRow {
  hostname: string;
  domain: string;
  seconds: number;
  bytes: number;
  sessions: number;
}

/** A URL from the FortiGate web filter log (`fg_web.url`: the path, `hostname` its host). */
export interface UrlRow {
  url: string;
  hostname: string | null;
  hits: number;
  first_seen: DateTimeString;
  last_seen: DateTimeString;
  category: string | null;
  /** The web filter blocked it. */
  blocked: boolean;
}

/** One hour of a subject (`top_domain`: the domain with the most time in it). */
export interface SubjectHour {
  hour: DateTimeString;
  active_seconds: number;
  online_seconds: number;
  download_bytes: number;
  upload_bytes: number;
  top_domain: string | null;
}

/** One device of a person. */
export interface DeviceUsage {
  device: SubjectRef;
  metrics: Metrics;
  top_sites: TopSite[];
}

/** `GET /usage/subjects/{key}`. */
export interface SubjectDetail {
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
  subject: SubjectRef;
  department: string | null;
  /** A person's devices (empty for a device). */
  devices: SubjectRef[];
  metrics: Metrics;
  /** Only for a single day (`from == to`), else empty. */
  timeline: TimelineSpan[];
  sites: SiteRow[];
  categories: CategoryRow[];
  apps: AppRow[];
  hostnames: HostnameRow[];
  /** ≤ 1000, newest first. */
  urls: UrlRow[];
  /** ≤ 5000, newest first. */
  blocked: BlockedRow[];
  /** Every hour of the period. */
  hourly: SubjectHour[];
  per_device: DeviceUsage[];
  /** The 30 days ending `period.to`. */
  heatmap: HeatCell[];
}

/** `GET /usage/sites` (one page). */
export interface SitePage {
  items: SiteRow[];
  total: number;
  limit: number;
  offset: number;
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
}

export interface SiteHost {
  hostname: string;
  seconds: number;
  bytes: number;
  sessions: number;
}

/**
 * `GET /usage/sites/{domain}`: `users` are the subjects' figures on this site (online = the
 * site's raw time; no blocked attempts or first/last activity, not kept per site).
 */
export interface SiteDetail {
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
  domain: string;
  category: string | null;
  totals: SiteRow;
  /** ≤ 50. */
  hostnames: SiteHost[];
  /** ≤ 200, most time first (the exports hold them all). */
  users: SubjectRow[];
  /** Every user of the site (`users` may show fewer). */
  users_total: number;
}

/** `GET /usage/categories`. */
export interface CategoryList {
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
  items: CategoryRow[];
}

/**
 * `GET /usage/categories/{category}`: `users` are the subjects' figures in this category (no
 * online time, blocked attempts or first/last activity, not kept per category).
 */
export interface CategoryDetail {
  period: UsagePeriod;
  estimated: boolean;
  computed_until: DateTimeString | null;
  notice_key: string;
  category: string;
  totals: CategoryRow;
  /** ≤ 100, most time first (the exports hold them all). */
  sites: SiteRow[];
  /** Every site of the category (`sites` may show fewer). */
  sites_total: number;
  /** ≤ 200, most time first (the exports hold them all). */
  users: SubjectRow[];
  /** Every user of the category (`users` may show fewer). */
  users_total: number;
}

/** `POST /usage/rebuild` (`settings.manage`): recompute the rollups of these site days. */
export interface RebuildRequest {
  from: DateString;
  to: DateString;
}

/** A rebuild job (`POST /usage/rebuild` answers it queued; the worker runs it). */
export interface RebuildJob {
  job_id: number;
  from: DateString;
  to: DateString;
  status: RebuildState;
  /** Share of the days done, 0…1. */
  progress: Float;
  error: string | null;
  requested_by: string;
  created_at: DateTimeString;
  started_at: DateTimeString | null;
  finished_at: DateTimeString | null;
}

/** `GET /usage/pseudonyms` (administrators; audited): who is behind a pseudonym. */
export interface PseudonymRow {
  pseudonym: string;
  kind: SubjectKind;
  subject: SubjectRef;
}

/** Parameters every usage endpoint takes (`department`: only that department's rows). */
export interface UsageParams {
  from: DateString;
  to: DateString;
  hours: UsageHours;
  department?: string;
}

// -- licence (`/license`; licensing contract section 6.1) ---------------------------------------

/**
 * The installation's licence state (`arcilogger_core.license.state.LicenseStateName`);
 * `trial_expired` is shown as "Lisanssız / Unlicensed".
 */
export type LicenseState =
  | 'trial'
  | 'trial_expired'
  | 'licensed'
  | 'invalid'
  | 'wrong_server'
  | 'expired'
  | 'version_not_covered';

/** Which floor of the trusted time won (`arcilogger_core.license.trusted_time.TimeSource`). */
export type LicenseTimeSource = 'system' | 'tsa' | 'vault_day' | 'release_date' | 'license_issued';

/** What a licence event records (`arcilogger_db.models.LICENSE_EVENT_KINDS`). */
export type LicenseEventKind = 'install' | 'remove' | 'refuse' | 'limit' | 'state';

/** Where a licence event came from (`arcilogger_db.models.LICENSE_SOURCES`). */
export type LicenseEventSource = 'web' | 'cli' | 'worker' | 'seed';

/** One registry module (in registry order); names and descriptions come from the API. */
export interface LicenseModuleOut {
  key: string;
  name_tr: string;
  name_en: string;
  description_tr: string;
  description_en: string;
  /** The one answer to "is module X enabled" (the server's `LicenseStatus.module_enabled`). */
  enabled: boolean;
  /** `core_5651`: in every licence and every state. */
  always: boolean;
  /** Listed in a verified licence document (false without one). */
  licensed: boolean;
}

/** Present in the states `trial` and `trial_expired`. */
export interface LicenseTrialOut {
  started_at: DateTimeString;
  ends_at: DateTimeString;
  days_left: number;
}

/** With a verified licence document: annual maintenance (upgrades and support only). */
export interface LicenseMaintenanceOut {
  until: DateString;
  /** Negative once over. */
  days_left: number;
  expired: boolean;
  ends_soon: boolean;
  covers_version: boolean;
}

/** A limit: `max` `null` = unlimited; `unconfigured` only for log sources. */
export interface LicenseLimitOut {
  max: number | null;
  used: number;
  unconfigured: number;
  exceeded: boolean;
  full: boolean;
}

export interface LicenseLimitsOut {
  log_sources: LicenseLimitOut;
  users: LicenseLimitOut;
}

export interface LicenseProductOut {
  name: string;
  version: string;
  release_date: DateString;
}

/** The rights holder the request code is e-mailed to. */
export interface LicensorOut {
  name: string;
  email: string;
}

/** The trusted time the licence decisions use (licensing contract section 3.3). */
export interface LicenseTimeOut {
  now: DateTimeString;
  source: LicenseTimeSource;
  tsa_time: DateTimeString | null;
  vault_day_end: DateTimeString | null;
  system_time: DateTimeString;
  clock_behind: boolean;
}

/** One `license_events` row (the history of the licence page). */
export interface LicenseEventOut {
  at: DateTimeString;
  kind: LicenseEventKind;
  source: LicenseEventSource;
  actor: string;
  license_id: string | null;
  /** As recorded: `install` `{license_id, …, unchanged}`, `refuse` `{code, …}`, `limit`
   * `{limit, exceeded, max, used}`, `state` `{from, to, …, problem}`, `remove` `{license_id, sha256}`. */
  details: Record<string, unknown>;
}

/** Only for holders of `settings.manage`; the licence fields come from the re-verified file. */
export interface LicenseDetailsOut {
  license_id: string | null;
  key_id: string | null;
  issued_at: DateTimeString | null;
  server_ids: string[] | null;
  installed_at: DateTimeString | null;
  installed_by: string | null;
  /** This server's id, `null` when it cannot be read (then `server_id_error`). */
  server_id: string | null;
  server_id_error: string | null;
  installation_id: string;
  /** `site_name`; may be empty. */
  organization: string;
  /** `null` when the server id cannot be read. */
  request_code: string | null;
  time: LicenseTimeOut;
  /** Newest 20 events, newest first. */
  history: LicenseEventOut[];
}

/**
 * `GET /license` (any signed-in user; `details` only with `settings.manage`), also the answer of
 * `POST /license`. Times in site time with offset, dates `YYYY-MM-DD`.
 */
export interface LicenseOut {
  status: LicenseState;
  /** The code of the state's problem (`license_revoked`, `trial_clock_inconsistent`, …). */
  problem: string | null;
  modules: LicenseModuleOut[];
  trial: LicenseTrialOut | null;
  licensee: string | null;
  maintenance: LicenseMaintenanceOut | null;
  valid_until: DateString | null;
  limits: LicenseLimitsOut;
  product: LicenseProductOut;
  licensor: LicensorOut;
  checked_at: DateTimeString;
  details: LicenseDetailsOut | null;
}
