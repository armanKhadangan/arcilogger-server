/**
 * A stateful stand-in for the ARCILOGGER API, installed with `page.route`, so the E2E tests run
 * the production build without a server. Every answer has the exact shape of the real endpoint
 * (the types are those of `src/lib/api-types.ts`, which a unit test compares with the API's
 * OpenAPI document), errors use the real codes and Turkish/English texts
 * (`arcilogger_api.errors.MESSAGES`, `arcilogger_api.auth.messages.AUTH_MESSAGES`), and the
 * audit entries are the golden vectors the server's own code produced.
 *
 * Simplifications (the real rules are tested in api/tests): one browser session at a time; the
 * valid TOTP code is {@link VALID_TOTP}; the account locks after {@link LOCKOUT_ATTEMPTS} failures.
 */

import type { Page, Request, Route } from '@playwright/test';

import type {
  AuditEntry,
  ChainStatus,
  Me,
  Role,
  SignInState,
  SignInStep,
  UserOut,
  UserSummary,
} from '../src/lib/api-types';

import { API_SCHEMAS, AUDIT_VECTORS, ROLE_CONTRACT } from './generated';
import { AdminMock, type AdminOptions } from './mock-admin';
import { AlertsMock } from './mock-alerts';
import { DevicesMock } from './mock-devices';
import { LicenseMock, type LicenseOptions } from './mock-license';
import { RecordsMock, type Answer, type RecordsOptions } from './mock-records';
import { ReportsMock } from './mock-reports';
import { UsageMock, type UsageOptions } from './mock-usage';
import { ALERTS, CREDITS, EVIDENCE, LOOKUPS, RAW, REPORTS, SEALS } from './samples';

export const CSRF_TOKEN = 'e2e-csrf-token';
export const VALID_TOTP = '123456';
export const RECOVERY_CODES = [
  'a1b2-c3d4-e5f6-g7h8',
  'h8g7-f6e5-d4c3-b2a1',
  '2345-6789-abcd-efgh',
  'jkmn-pqrs-tvwx-yz01',
  '0123-4567-89ab-cdef',
  'ghjk-mnpq-rstv-wxyz',
  'zyxw-vtsr-qpnm-kjhg',
  'fedc-ba98-7654-3210',
  'q1w2-e3r4-t5y6-z7x8',
  'mnbv-cxz1-2345-6789',
];
export const TOTP_SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
export const TEMPORARY_PASSWORD = 'Gecici-Parola-7Kp2-Wx9q'; // gitleaks:allow — fake test value
export const LOCKOUT_ATTEMPTS = 5;

const TEXTS: Record<string, [string, string]> = {
  not_authenticated: ['Oturum açmanız gerekiyor.', 'You need to sign in.'],
  session_idle: [
    'Oturum hareketsizlik nedeniyle kapandı; lütfen yeniden giriş yapın.',
    'The session was closed after inactivity; please sign in again.',
  ],
  forbidden: ['Bu işlem için yetkiniz yok.', 'You do not have permission for this action.'],
  csrf_failed: [
    'Güvenlik doğrulaması (CSRF) başarısız; sayfayı yenileyip tekrar deneyin.',
    'Security check (CSRF) failed; reload the page and try again.',
  ],
  not_found: ['Kayıt bulunamadı.', 'Not found.'],
  // The report API's texts that the capture does not reach (arcilogger_api.routers.reports).
  usage_engine_unavailable: [
    'Kullanım verileri henüz hesaplanamıyor (kullanım motoru yok).',
    'Usage data cannot be computed yet (the usage engine is not installed).',
  ],
  report_missing: [
    'Rapor dosyası bulunamadı (exports/usage-reports). Raporu yeniden çalıştırın.',
    'The report file is missing (exports/usage-reports). Run the report again.',
  ],
  report_damaged: [
    'Rapor dosyası kayıtlı SHA-256 özetiyle uyuşmuyor (değiştirilmiş olabilir).',
    'The report file does not match its recorded SHA-256 (it may have been changed).',
  ],
  login_failed: [
    'Giriş başarısız: kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli.',
    'Sign-in failed: wrong user name or password, or the account is temporarily locked.',
  ],
  totp_invalid: [
    'Doğrulama kodu hatalı veya süresi geçmiş.',
    'The verification code is wrong or has expired.',
  ],
  account_locked: [
    'Çok fazla hatalı deneme: hesap geçici olarak kilitlendi ve oturum kapatıldı.',
    'Too many failed attempts: the account is temporarily locked and the session was closed.',
  ],
  credentials_invalid: [
    'Parola veya doğrulama kodu hatalı.',
    'Wrong password or verification code.',
  ],
  password_rejected: ['Parola kabul edilmedi.', 'The password was not accepted.'],
  reauth_required: [
    'Bu işlem için parolanızı yeniden girmeniz gerekiyor.',
    'Please confirm your password to continue with this action.',
  ],
  setup_token_invalid: [
    "Kurulum anahtarı geçersiz veya süresi dolmuş. Sunucuda 'arcilogger-api setup-token' komutuyla yeni bir anahtar oluşturun.",
    "The setup token is invalid or has expired. Create a new one on the server with 'arcilogger-api setup-token'.",
  ],
  username_taken: ['Bu kullanıcı adı kullanılıyor.', 'This user name is already in use.'],
  last_admin: [
    'Son etkin yönetici hesabının rolü değiştirilemez ve hesap devre dışı bırakılamaz.',
    'The role of the last active administrator cannot be changed and the account cannot be disabled.',
  ],
  cannot_change_self: [
    'Bu işlem kendi hesabınızda yapılamaz; başka bir yöneticiden isteyin.',
    'This action cannot be applied to your own account; ask another administrator.',
  ],
  mfa_required: ['İki adımlı doğrulama gerekiyor.', 'Two-factor authentication is required.'],
  password_change_required: [
    'Devam etmeden önce parolanızı değiştirmeniz gerekiyor.',
    'You need to change your password before continuing.',
  ],
  totp_already_enabled: [
    'İki adımlı doğrulama bu hesapta zaten kurulu.',
    'Two-factor authentication is already set up for this account.',
  ],
  setup_done: [
    'İlk kurulum tamamlanmış: zaten bir yönetici hesabı var.',
    'Initial setup is complete: an administrator account already exists.',
  ],
  notice_outdated: [
    'Bilgilendirme metninin yeni bir sürümü var; sayfayı yenileyip tekrar okuyun.',
    'There is a newer version of the notice; reload the page and read it again.',
  ],
  validation_error: ['Gönderilen veriler geçersiz.', 'The submitted data is invalid.'],
  privacy_notice_required: [
    'Kişisel veri içeren bu bölümü açmadan önce KVKK bilgilendirme metnini okuyup onaylamanız gerekiyor.',
    'Read and accept the KVKK privacy notice before opening this section with personal data.',
  ],
};

/** Texts of every refusal captured from the real API (`samples.ts`), by code: `failNext` with
 * one of these codes answers exactly what the server says. */
const SAMPLE_TEXTS: Record<string, [string, string]> = Object.fromEntries(
  [
    LOOKUPS.errors,
    RAW.errors,
    EVIDENCE.errors,
    SEALS.errors,
    ALERTS.errors,
    REPORTS.errors,
  ].flatMap((errors) =>
    Object.values(errors).map(({ body }) => [
      body.error.code,
      [body.error.message_tr, body.error.message_en] as [string, string],
    ]),
  ),
);

/**
 * The request model of every API endpoint with a JSON body (its `requestBody` in the OpenAPI
 * document). Every one is `extra="forbid"`: the API answers 422 `validation_error` to a body with a
 * field it does not know or without a required one — and so does the mock, so a field the web app
 * sends but the API does not accept fails the E2E tests instead of passing a lenient mock.
 */
const REQUEST_BODIES: Record<string, string> = {
  'POST /auth/login': 'LoginBody',
  'POST /auth/totp': 'CodeBody',
  'POST /auth/totp/confirm': 'CodeBody',
  'POST /auth/password': 'PasswordBody',
  'POST /auth/reauth': 'ReauthBody',
  'POST /auth/privacy-notice': 'PrivacyBody',
  'POST /auth/setup/admin': 'SetupAdminBody',
  'POST /evidence': 'EvidenceCreate',
  'POST /lookup/5651': 'LookupIn',
  'POST /users': 'UserCreate',
  'PATCH /users/{id}': 'UserUpdate',
  'PATCH /devices/{mac}': 'DeviceUpdate',
  'POST /devices/assign': 'AssignRequest',
  'POST /persons': 'PersonCreate',
  'PATCH /persons/{id}': 'PersonUpdate',
  'POST /departments': 'DepartmentCreate',
  'PATCH /departments/{id}': 'DepartmentUpdate',
  'POST /vlans': 'VlanCreate',
  'PATCH /vlans': 'VlanUpdate',
  'POST /alerts/{id}/state': 'AlertStateRequest',
  'POST /alerts/{id}/notes': 'AlertNoteRequest',
  'POST /reports/schedules': 'ScheduleCreate',
  'PATCH /reports/schedules/{id}': 'ScheduleUpdate',
  'POST /reports/schedules/{id}/run': 'RunRequest',
  'POST /usage/rebuild': 'RebuildRequest',
};
for (const [route, name] of Object.entries(REQUEST_BODIES)) {
  if (API_SCHEMAS[name]?.additionalProperties !== false)
    throw new Error(`${route}: no strict OpenAPI schema ${name} in api-schemas.json`);
}

/**
 * Problems of a request body against the API's model (unknown and missing fields only — the
 * values are the mocked endpoints' business), in the API's `details` format; `[]` when none.
 */
export function bodyProblems(
  route: string,
  body: unknown,
): { loc: string[]; type: string; msg: string }[] {
  const name = REQUEST_BODIES[route];
  const schema = name ? API_SCHEMAS[name] : undefined;
  if (!schema) return [];
  if (typeof body !== 'object' || body === null || Array.isArray(body))
    return [{ loc: ['body'], type: 'model_attributes_type', msg: 'Input should be an object' }];
  const known = schema.properties ?? {};
  return [
    ...Object.keys(body)
      .filter((key) => !Object.hasOwn(known, key))
      .map((key) => ({
        loc: ['body', key],
        type: 'extra_forbidden',
        msg: 'Extra inputs are not permitted',
      })),
    ...(schema.required ?? [])
      .filter((key) => !Object.hasOwn(body, key))
      .map((key) => ({ loc: ['body', key], type: 'missing', msg: 'Field required' })),
  ];
}

const ROLE_INFO = ROLE_CONTRACT.roles;
const PRIVACY_PERMISSIONS = new Set(ROLE_CONTRACT.privacy_permissions);
const TOTP_REQUIRED: readonly Role[] = ['admin', 'auditor'];

export interface MockUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  role: Role;
  password: string;
  active: boolean;
  must_change_password: boolean;
  totp_enabled: boolean;
  recovery_codes: string[];
  failed_attempts: number;
  locked_until: number | null;
  last_login: string | null;
  privacy_accepted: boolean;
}

interface Session {
  userId: number;
  mfaPassed: boolean;
  /** ms since epoch of the last password(+TOTP) proof, `null` = never. */
  reauthAt: number | null;
}

export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  /** The request body as text (multipart uploads), `null` without one. */
  raw: string | null;
  contentType: string | null;
  csrf: string | null;
  background: boolean;
}

export interface MockOptions {
  /** Signed in at start (complete sign-in) as this user name. */
  signedInAs?: string;
  needsSetup?: boolean;
  /** Mutations of /users need a fresh /auth/reauth first. */
  staleReauth?: boolean;
  idleMinutes?: number;
  /**
   * `site_name` of the server's configuration in `/auth/me` (default: the tests' fictional
   * organisation; `''` is a server whose name was not set).
   */
  siteName?: string;
  chainBroken?: boolean;
  privacyVersion?: string;
  /** Lookups, evidence packages, sealing and credits (captured samples). */
  records?: RecordsOptions;
  /**
   * Permissions added to a role for this test, e.g. `{ auditor: ['devices.read'] }`: a role
   * decision the server has not made yet (today only administrators hold `devices.*`).
   */
  grant?: Partial<Record<Role, string[]>>;
  /** Permissions taken from a role for this test, e.g. `{ viewer: ['usage.anonymous'] }`. */
  revoke?: Partial<Record<Role, string[]>>;
  /** Usage analytics: pseudonyms for `usage.anonymous` viewers, no traffic, a failing rebuild. */
  usage?: UsageOptions;
  /** The licence state at start (default: the trial, every module on). */
  license?: LicenseOptions;
  /**
   * Browser administration: the administration service's heartbeat, the hold of guarded
   * changes, the speed of the requests, a fresh Kamu SM section (`mock-admin.ts`).
   */
  admin?: AdminOptions;
}

function user(
  partial: Partial<MockUser> & Pick<MockUser, 'id' | 'username' | 'display_name' | 'role'>,
): MockUser {
  return {
    email: null,
    password: 'Uzun-ve-Gizli-Parola-2026', // gitleaks:allow — fake test credential
    active: true,
    must_change_password: false,
    totp_enabled: TOTP_REQUIRED.includes(partial.role),
    recovery_codes: [...RECOVERY_CODES],
    failed_attempts: 0,
    locked_until: null,
    last_login: '2026-10-12T06:40:00Z',
    privacy_accepted: true,
    ...partial,
  };
}

/** The accounts every test starts with. */
export function defaultUsers(): MockUser[] {
  return [
    user({
      id: 1,
      username: 'tolga',
      display_name: 'Tolga Yılmaz',
      role: 'admin',
      email: 'tolga@ornek.com.tr',
    }),
    user({ id: 2, username: 'ayse.yilmaz', display_name: 'Ayşe Yılmaz', role: 'auditor' }),
    user({ id: 3, username: 'mehmet.kaya', display_name: 'Mehmet Kaya', role: 'hr' }),
    user({
      id: 4,
      username: 'zeynep',
      display_name: 'Zeynep Demir',
      role: 'viewer',
      failed_attempts: 5,
      locked_until: Date.now() + 15 * 60_000,
    }),
  ];
}

export class MockApi {
  users: MockUser[] = defaultUsers();
  session: Session | null = null;
  needsSetup: boolean;
  setupToken = 'kurulum-anahtari-2026'; // gitleaks:allow — fake test value
  staleReauth: boolean;
  idleMinutes: number;
  siteName: string;
  chainBroken: boolean;
  privacyVersion: string;
  /** 401 code returned for the next authenticated request (e.g. `session_idle`). */
  expireWith: string | null = null;
  /** One refusal for the next matching request (`METHOD /path` → status + code). */
  failNext: { route: string; status: number; code: string } | null = null;
  calls: RecordedCall[] = [];
  readonly entries: AuditEntry[] = AUDIT_VECTORS.entries.map((vector) => vector.api).reverse();
  /** 5651 lookups, raw lines, evidence packages, sealing, credits. */
  readonly records: RecordsMock;
  /** Devices, persons, departments, VLANs, CSV imports and exports (captured samples). */
  readonly devices: DevicesMock;
  /** Security alerts, the dashboard summary and today's company usage (contract samples). */
  readonly alerts: AlertsMock;
  /** Scheduled reports, runs, files and previews (contract samples). */
  readonly reports: ReportsMock;
  /** Usage analytics (`/usage/*`): the captured day and a generated dataset. */
  readonly usage: UsageMock;
  /** The licence (`/license`), the module gates and the users limit. */
  readonly license: LicenseMock;
  /**
   * Browser administration (`/admin/*`, `/settings`, "Şimdi mühürle", "Kontör sorgula"): asked
   * before the captured seal and credit samples, which it changes once a request succeeded.
   */
  readonly admin: AdminMock;
  readonly grant: Partial<Record<Role, string[]>>;
  readonly revoke: Partial<Record<Role, string[]>>;

  constructor(options: MockOptions = {}) {
    this.license = new LicenseMock(
      options.license ?? {},
      () => this.users.filter((item) => item.active).length,
    );
    this.records = new RecordsMock(options.records);
    this.admin = new AdminMock(options.admin, () => CREDITS[this.records.credits]);
    this.devices = new DevicesMock();
    this.alerts = new AlertsMock();
    this.reports = new ReportsMock();
    this.usage = new UsageMock(options.usage);
    this.grant = options.grant ?? {};
    this.revoke = options.revoke ?? {};
    this.needsSetup = options.needsSetup ?? false;
    this.staleReauth = options.staleReauth ?? false;
    this.idleMinutes = options.idleMinutes ?? 15;
    this.siteName = options.siteName ?? 'Örnek A.Ş.';
    this.chainBroken = options.chainBroken ?? false;
    this.privacyVersion = options.privacyVersion ?? '1';
    if (this.needsSetup) this.users = [];
    if (options.signedInAs) {
      const found = this.byName(options.signedInAs);
      if (!found) throw new Error(`no mock user ${options.signedInAs}`);
      this.session = {
        userId: found.id,
        mfaPassed: true,
        reauthAt: this.staleReauth ? null : Date.now(),
      };
    }
  }

  byName(username: string): MockUser | undefined {
    return this.users.find((item) => item.username === username);
  }

  /** The account `username` (the test fails when it does not exist). */
  get(username: string): MockUser {
    const found = this.byName(username);
    if (!found) throw new Error(`no mock user ${username}`);
    return found;
  }

  callsTo(method: string, path: string): RecordedCall[] {
    return this.calls.filter((call) => call.method === method && call.path === path);
  }

  async install(page: Page): Promise<void> {
    await page
      .context()
      .addCookies([
        { name: 'arci_csrf', value: CSRF_TOKEN, url: 'http://127.0.0.1:4173', sameSite: 'Strict' },
      ]);
    await page.route('**/api/v1/**', (route, request) => this.handle(route, request));
  }

  // -- plumbing ---------------------------------------------------------------------------------

  private async handle(route: Route, request: Request): Promise<void> {
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, '');
    const method = request.method();
    let body: unknown;
    try {
      body = request.postDataJSON() as unknown;
    } catch {
      body = undefined;
    }
    const headers = request.headers();
    const call: RecordedCall = {
      method,
      path,
      query: url.searchParams,
      body,
      raw: request.postDataBuffer()?.toString('utf8') ?? null,
      contentType: headers['content-type'] ?? null,
      csrf: headers['x-csrf-token'] ?? null,
      background: headers['x-arci-background'] === '1',
    };
    this.calls.push(call);
    const [status, payload, file] = this.answer(call);
    if (file) {
      await route.fulfill({
        status,
        contentType: file.contentType,
        headers: file.headers,
        body: file.body,
      });
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: status === 204 ? '' : JSON.stringify(payload),
    });
  }

  error(status: number, code: string, details?: unknown): [number, unknown] {
    const [tr, en] = TEXTS[code] ?? SAMPLE_TEXTS[code] ?? [code, code];
    return [
      status,
      { error: { code, message_tr: tr, message_en: en, ...(details ? { details } : {}) } },
    ];
  }

  private current(): MockUser | null {
    return this.session
      ? (this.users.find((item) => item.id === this.session?.userId) ?? null)
      : null;
  }

  private summary(account: MockUser): UserSummary {
    const info = ROLE_INFO.find((item) => item.id === account.role);
    return {
      id: account.id,
      username: account.username,
      display_name: account.display_name,
      role: account.role,
      role_label_tr: info?.label_tr ?? account.role,
      role_label_en: info?.label_en ?? account.role,
    };
  }

  userOut(account: MockUser): UserOut {
    const locked = account.locked_until !== null && account.locked_until > Date.now();
    return {
      ...this.summary(account),
      email: account.email,
      active: account.active,
      must_change_password: account.must_change_password,
      totp_enabled: account.totp_enabled,
      locked,
      locked_until:
        locked && account.locked_until ? new Date(account.locked_until).toISOString() : null,
      failed_attempts: account.failed_attempts,
      last_login: account.last_login,
      password_changed_at: '2026-10-01T08:00:00Z',
      created_at: '2026-09-30T08:00:00Z',
    };
  }

  privacyRequired(account: MockUser): boolean {
    const info = ROLE_INFO.find((item) => item.id === account.role);
    return (info?.permissions ?? []).some((permission) => PRIVACY_PERMISSIONS.has(permission));
  }

  private nextStep(account: MockUser, mfaPassed: boolean): SignInStep {
    if (!mfaPassed) return account.totp_enabled ? 'totp' : 'totp_setup';
    if (account.must_change_password) return 'password_change';
    if (this.privacyRequired(account) && !account.privacy_accepted) return 'privacy_notice';
    return 'done';
  }

  private state(account: MockUser): SignInState {
    const mfaPassed = this.session?.mfaPassed ?? false;
    return {
      next: this.nextStep(account, mfaPassed),
      mfa_passed: mfaPassed,
      must_change_password: account.must_change_password,
      user: this.summary(account),
    };
  }

  private me(account: MockUser): Me {
    const mfaPassed = this.session?.mfaPassed ?? false;
    const permissions = this.permissionsOf(account);
    return {
      user: this.summary(account),
      email: account.email,
      permissions: mfaPassed ? [...permissions].sort() : [],
      must_change_password: account.must_change_password,
      mfa: {
        passed: mfaPassed,
        enabled: account.totp_enabled,
        required: TOTP_REQUIRED.includes(account.role),
        recovery_codes_left: account.totp_enabled ? account.recovery_codes.length : 0,
      },
      privacy_notice: {
        version: this.privacyVersion,
        accepted: account.privacy_accepted,
        required: this.privacyRequired(account),
      },
      idle_timeout_minutes: this.idleMinutes,
      session: {
        created_at: '2026-10-12T07:00:00Z',
        expires_at: '2026-10-12T19:00:00Z',
        reauthenticated_at: this.session?.reauthAt
          ? new Date(this.session.reauthAt).toISOString()
          : null,
      },
      next: this.nextStep(account, mfaPassed),
      site_name: this.siteName,
    };
  }

  /** Authentication like `security._authenticate` (+ CSRF on mutations). */
  private authenticate(call: RecordedCall, allowPending = false): MockUser | [number, unknown] {
    if (this.expireWith) {
      const code = this.expireWith;
      this.expireWith = null;
      this.session = null;
      return this.error(401, code);
    }
    const account = this.current();
    if (!account || !this.session) return this.error(401, 'not_authenticated');
    if (!this.session.mfaPassed && !allowPending) return this.error(401, 'mfa_required');
    if (call.method !== 'GET' && call.csrf !== CSRF_TOKEN) return this.error(403, 'csrf_failed');
    return account;
  }

  /** Any signed-in user (`require()` without a permission, e.g. `GET /license`). */
  signedIn(call: RecordedCall): MockUser | [number, unknown] {
    const account = this.authenticate(call);
    if (Array.isArray(account)) return account;
    if (account.must_change_password) return this.error(403, 'password_change_required');
    return account;
  }

  requirePermission(
    call: RecordedCall,
    permission: string,
    recent = false,
  ): MockUser | [number, unknown] {
    const account = this.authenticate(call);
    if (Array.isArray(account)) return account;
    if (account.must_change_password) return this.error(403, 'password_change_required');
    if (!this.permissionsOf(account).includes(permission)) return this.error(403, 'forbidden');
    if (
      recent &&
      (this.session?.reauthAt === null || Date.now() - (this.session?.reauthAt ?? 0) > 5 * 60_000)
    )
      return this.error(403, 'reauth_required');
    return account;
  }

  private fail(account: MockUser): boolean {
    account.failed_attempts += 1;
    if (account.failed_attempts % LOCKOUT_ATTEMPTS === 0) {
      account.locked_until = Date.now() + 15 * 60_000;
      return true;
    }
    return false;
  }

  // -- endpoints --------------------------------------------------------------------------------

  permissionsOf(account: MockUser): readonly string[] {
    const base = ROLE_INFO.find((item) => item.id === account.role)?.permissions ?? [];
    const taken = this.revoke[account.role] ?? [];
    return [...new Set([...base, ...(this.grant[account.role] ?? [])])].filter(
      (permission) => !taken.includes(permission),
    );
  }

  private answer(call: RecordedCall): Answer {
    const { method, path } = call;
    if (this.failNext?.route === `${method} ${path}`) {
      const { status, code } = this.failNext;
      this.failNext = null;
      return this.error(status, code);
    }
    const route = `${method} ${path
      .replace(/\/\d+(?=\/|$)/, '/{id}')
      .replace(/^\/devices\/(?!assign$|export\.csv$)[^/]+$/, '/devices/{mac}')}`;
    // A locked module's routes refuse after authentication, before any validation (6.4).
    const locked = this.license.gate(call, this);
    if (locked) return locked;
    const problems = bodyProblems(route, call.body);
    if (problems.length > 0) return this.error(422, 'validation_error', problems);
    switch (route) {
      case 'GET /health':
        return [200, { status: 'ok' }];
      case 'GET /auth/setup/status':
        return [
          200,
          {
            needs_setup: this.needsSetup,
            token_active: this.needsSetup,
            idle_timeout_minutes: this.idleMinutes,
          },
        ];
      case 'POST /auth/setup/admin':
        return this.setupAdmin(call);
      case 'POST /auth/login':
        return this.login(call);
      case 'GET /auth/me': {
        const account = this.authenticate(call, true);
        return Array.isArray(account) ? account : [200, this.me(account)];
      }
      case 'POST /auth/totp':
        return this.totp(call);
      case 'GET /auth/totp/setup':
        return this.totpSetup(call);
      case 'POST /auth/totp/confirm':
        return this.totpConfirm(call);
      case 'POST /auth/password':
        return this.password(call);
      case 'POST /auth/reauth':
        return this.reauth(call);
      case 'POST /auth/privacy-notice':
        return this.privacy(call);
      case 'POST /auth/recovery-codes': {
        const account = this.requirePermission(call, 'dashboard.usage', true);
        if (Array.isArray(account)) return account;
        return [200, { recovery_codes: RECOVERY_CODES.map((code) => code.replace(/^./, 'n')) }];
      }
      case 'POST /auth/logout': {
        this.session = null;
        return [204, null];
      }
      case 'GET /users':
        return this.listUsers(call);
      case 'POST /users':
        return this.createUser(call);
      case 'PATCH /users/{id}':
        return this.updateUser(call);
      case 'POST /users/{id}/reset-password':
        return this.resetPassword(call);
      case 'POST /users/{id}/reset-totp':
        return this.resetTotp(call);
      case 'POST /users/{id}/unlock':
        return this.unlock(call);
      case 'GET /audit':
        return this.audit(call);
      case 'GET /audit/verify':
        return this.verify(call);
      default:
        return (
          this.license.answer(call, this) ??
          this.admin.answer(call, this) ??
          this.records.answer(call, this) ??
          this.devices.answer(call, this) ??
          this.usage.answer(call, this) ??
          this.alerts.answer(call, this) ??
          this.reports.answer(call, this) ??
          this.error(404, 'not_found')
        );
    }
  }

  private setupAdmin(call: RecordedCall): [number, unknown] {
    const body = call.body as {
      token: string;
      username: string;
      display_name: string;
      password: string;
    };
    if (!this.needsSetup) return this.error(409, 'setup_done');
    if (body.token !== this.setupToken) return this.error(403, 'setup_token_invalid');
    const account = user({
      id: 1,
      username: body.username,
      display_name: body.display_name,
      role: 'admin',
      password: body.password,
      totp_enabled: false,
      privacy_accepted: false,
      last_login: null,
    });
    this.users = [account, ...defaultUsers().slice(1)];
    this.needsSetup = false;
    return [201, { user: this.summary(account), next: 'login' }];
  }

  private login(call: RecordedCall): [number, unknown] {
    const body = call.body as { username: string; password: string };
    const account = this.byName(body.username.trim().toLowerCase());
    if (!account) return this.error(401, 'login_failed');
    const locked = account.locked_until !== null && account.locked_until > Date.now();
    if (locked || !account.active) return this.error(401, 'login_failed');
    if (body.password !== account.password) {
      this.fail(account);
      return this.error(401, 'login_failed');
    }
    const second = account.totp_enabled || TOTP_REQUIRED.includes(account.role);
    this.session = { userId: account.id, mfaPassed: !second, reauthAt: second ? null : Date.now() };
    if (!second) account.failed_attempts = 0;
    return [200, this.state(account)];
  }

  private totp(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call, true);
    if (Array.isArray(account)) return account;
    const code = (call.body as { code: string }).code;
    const index = account.recovery_codes.findIndex(
      (item) => item.replace(/-/g, '') === code.replace(/[-\s]/g, '').toLowerCase(),
    );
    const ok = code === VALID_TOTP || index >= 0;
    if (!ok) {
      if (this.fail(account)) {
        this.session = null;
        return this.error(401, 'account_locked');
      }
      return this.error(401, 'totp_invalid');
    }
    if (index >= 0) account.recovery_codes.splice(index, 1);
    account.failed_attempts = 0;
    account.last_login = new Date().toISOString();
    this.session = { userId: account.id, mfaPassed: true, reauthAt: Date.now() };
    return [200, this.state(account)];
  }

  private totpSetup(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call, true);
    if (Array.isArray(account)) return account;
    if (account.totp_enabled) return this.error(409, 'totp_already_enabled');
    const uri = `otpauth://totp/ARCILOGGER:${account.username}?secret=${TOTP_SECRET}&issuer=ARCILOGGER`;
    // A tiny but valid SVG document, like segno's (title, no scripts).
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="58" height="58" class="segno"><title>TOTP QR</title>' +
      '<path fill="#ffffff" d="M0 0h58v58H0z"/><path stroke="#000000" d="M4 4.5h7m2 0h7M4 10.5h1m5 0h1"/></svg>';
    return [
      200,
      {
        secret: TOTP_SECRET,
        otpauth_uri: uri,
        qr_svg: svg,
        issuer: 'ARCILOGGER',
        account: account.username,
        digits: 6,
        period: 30,
        algorithm: 'SHA1',
      },
    ];
  }

  private totpConfirm(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call, true);
    if (Array.isArray(account)) return account;
    if ((call.body as { code: string }).code !== VALID_TOTP) return this.error(400, 'totp_invalid');
    account.totp_enabled = true;
    account.recovery_codes = [...RECOVERY_CODES];
    this.session = { userId: account.id, mfaPassed: true, reauthAt: Date.now() };
    return [200, { recovery_codes: [...RECOVERY_CODES], state: this.state(account) }];
  }

  private password(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call);
    if (Array.isArray(account)) return account;
    const body = call.body as { current_password: string; new_password: string };
    // Like the API: a locked account is refused without counting; a wrong current password
    // counts, and the one that locks the account also ends the session.
    if (account.locked_until !== null && account.locked_until > Date.now())
      return this.error(403, 'credentials_invalid');
    if (body.current_password !== account.password) {
      if (this.fail(account)) {
        this.session = null;
        return this.error(401, 'account_locked');
      }
      return this.error(403, 'credentials_invalid');
    }
    if (/galatasaray/i.test(body.new_password))
      return this.error(422, 'password_rejected', [
        {
          rule: 'common',
          message_tr: 'Bu parola çok yaygın veya kolay tahmin edilir.',
          message_en: 'This password is too common or easy to guess.',
        },
      ]);
    account.password = body.new_password;
    account.must_change_password = false;
    account.failed_attempts = 0;
    // The API rotates the session without a recent re-authentication.
    if (this.session) this.session.reauthAt = null;
    return [200, this.state(account)];
  }

  private reauth(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call);
    if (Array.isArray(account)) return account;
    const body = call.body as { password: string; code?: string | null };
    if (body.password !== account.password || (account.totp_enabled && body.code !== VALID_TOTP))
      return this.error(403, 'credentials_invalid');
    if (this.session) this.session.reauthAt = Date.now();
    return [200, { reauthenticated_at: new Date().toISOString(), valid_minutes: 5 }];
  }

  private privacy(call: RecordedCall): [number, unknown] {
    const account = this.authenticate(call);
    if (Array.isArray(account)) return account;
    if ((call.body as { version: string }).version !== this.privacyVersion)
      return this.error(409, 'notice_outdated');
    account.privacy_accepted = true;
    return [
      200,
      { version: this.privacyVersion, accepted: true, required: this.privacyRequired(account) },
    ];
  }

  private listUsers(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage');
    if (Array.isArray(account)) return account;
    const role = call.query.get('role');
    const active = call.query.get('active');
    const items = this.users
      .filter((item) => !role || item.role === role)
      .filter((item) => active === null || String(item.active) === active)
      .sort((a, b) => a.username.localeCompare(b.username))
      .map((item) => this.userOut(item));
    return [200, { items, next_cursor: null }];
  }

  private target(call: RecordedCall): MockUser | undefined {
    const id = Number(/\/users\/(\d+)/.exec(call.path)?.[1]);
    return this.users.find((item) => item.id === id);
  }

  private createUser(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage', true);
    if (Array.isArray(account)) return account;
    const limit = this.license.usersRefusal();
    if (limit) return limit;
    const body = call.body as {
      username: string;
      display_name: string;
      email?: string | null;
      role: Role;
    };
    if (this.byName(body.username)) return this.error(409, 'username_taken');
    const created = user({
      id: Math.max(...this.users.map((item) => item.id)) + 1,
      username: body.username,
      display_name: body.display_name,
      email: body.email ?? null,
      role: body.role,
      password: TEMPORARY_PASSWORD,
      must_change_password: true,
      totp_enabled: false,
      privacy_accepted: false,
      last_login: null,
    });
    this.users.push(created);
    return [201, { user: this.userOut(created), temporary_password: TEMPORARY_PASSWORD }];
  }

  private updateUser(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage', true);
    if (Array.isArray(account)) return account;
    const found = this.target(call);
    if (!found) return this.error(404, 'not_found');
    const body = call.body as Partial<Pick<MockUser, 'display_name' | 'email' | 'role' | 'active'>>;
    if ((body.role !== undefined || body.active !== undefined) && found.id === account.id)
      return this.error(409, 'cannot_change_self');
    const admins = this.users.filter(
      (item) => item.role === 'admin' && item.active && item.id !== found.id,
    );
    const losingAdmin =
      found.role === 'admin' && ((body.role && body.role !== 'admin') || body.active === false);
    if (losingAdmin && admins.length === 0) return this.error(409, 'last_admin');
    if (body.active === true && !found.active) {
      const limit = this.license.usersRefusal();
      if (limit) return limit;
    }
    Object.assign(found, body);
    return [200, this.userOut(found)];
  }

  private resetPassword(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage', true);
    if (Array.isArray(account)) return account;
    const found = this.target(call);
    if (!found) return this.error(404, 'not_found');
    if (found.id === account.id) return this.error(409, 'cannot_change_self');
    Object.assign(found, {
      password: TEMPORARY_PASSWORD,
      must_change_password: true,
      failed_attempts: 0,
      locked_until: null,
    });
    return [200, { user: this.userOut(found), temporary_password: TEMPORARY_PASSWORD }];
  }

  private resetTotp(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage', true);
    if (Array.isArray(account)) return account;
    const found = this.target(call);
    if (!found) return this.error(404, 'not_found');
    if (found.id === account.id) return this.error(409, 'cannot_change_self');
    Object.assign(found, { totp_enabled: false, recovery_codes: [] });
    return [200, this.userOut(found)];
  }

  private unlock(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'users.manage');
    if (Array.isArray(account)) return account;
    const found = this.target(call);
    if (!found) return this.error(404, 'not_found');
    Object.assign(found, { failed_attempts: 0, locked_until: null });
    return [200, this.userOut(found)];
  }

  private audit(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'audit.read');
    if (Array.isArray(account)) return account;
    const q = call.query;
    const from = q.get('from');
    const to = q.get('to');
    const limit = Number(q.get('limit') ?? 100);
    const cursor = q.get('cursor');
    const rows = this.entries
      .filter((entry) => !from || Date.parse(entry.time) >= Date.parse(from))
      .filter((entry) => !to || Date.parse(entry.time) < Date.parse(to))
      .filter((entry) => !q.get('actor') || entry.actor === q.get('actor'))
      .filter((entry) => !q.get('action') || entry.action.startsWith(q.get('action') ?? ''))
      .filter((entry) => !q.get('target') || entry.target === q.get('target'))
      .filter((entry) => cursor === null || entry.id < Number(cursor));
    const page = rows.slice(0, limit);
    return [
      200,
      { items: page, next_cursor: rows.length > limit ? (page.at(-1)?.id ?? null) : null },
    ];
  }

  private verify(call: RecordedCall): [number, unknown] {
    const account = this.requirePermission(call, 'audit.read');
    if (Array.isArray(account)) return account;
    const status: ChainStatus = this.chainBroken
      ? {
          ok: false,
          entries: this.entries.length,
          first_bad_id: 37,
          last_hash: this.entries[0]?.hash ?? null,
          errors: [
            'entry 37: hash does not match its content',
            'entry 38: prev_hash does not link',
          ],
          verified_at: new Date().toISOString(),
        }
      : {
          ok: true,
          entries: this.entries.length,
          first_bad_id: null,
          last_hash: this.entries[0]?.hash ?? null,
          errors: [],
          verified_at: new Date().toISOString(),
        };
    return [200, status];
  }
}

/** Install a mock API for this page; returns it for state changes and request assertions. */
export async function mockApi(page: Page, options: MockOptions = {}): Promise<MockApi> {
  const api = new MockApi(options);
  await api.install(page);
  return api;
}
