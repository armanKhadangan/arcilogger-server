/**
 * The mocked browser administration of the E2E tests (ADR-0014; Wave 1 contract 2.9-2.13): the
 * machine configuration (`/admin/config`), the administration service's status, change requests
 * that go through their states in real time (queued → received → probing → applying → applied →
 * active, or a failure the test asks for), held changes, uploads, the actions ("Şimdi mühürle",
 * "Kontör sorgula", the stamp, replica and e-mail tests, the lease rebuild) and the database
 * settings (`/settings`).
 *
 * It starts from the answers captured from the real API (`samples/admin.json`, `settings.json`,
 * the seal-now and credit samples): the configuration, the locked paths and the policy as
 * installed, the status of each state of the service, the settings with their labels and bounds,
 * the inspection of an upload, the result of each action and the texts of every captured refusal
 * — the tables below hold only what no capture shows, and every captured text is compared with
 * them when the mock loads. The request bodies are checked against the API's models
 * (`mock-api.ts`). Requests report the steps of the real agent and API in their order (the agent's
 * steps, then the services' reloads and the system task), changes the API's paths and change
 * lists (a source as its whole table), refusals the first problem's code with every field.
 *
 * Simplifications (the real rules are tested in core/tests and api/tests): the rules of 2.6 only
 * as far as the specs need them; one step of a request every {@link AdminOptions.stepMs}; a
 * secret is accepted when it is not empty and never stored, only marked as set.
 */

import { createHash } from 'node:crypto';

import type { Calendar, CreditCheck, Credits } from '../src/lib/api-types';
import type { SealDayDetail } from '../src/lib/seal-types';
import type {
  AdminAccepted,
  AdminCheck,
  AdminConfig,
  AdminRequestOut,
  AdminSection,
  AdminStatus,
  AgentState,
  BackupBody,
  Change,
  CodeMessage,
  ConfigVersion,
  CreditActionResult,
  FieldProblem,
  GeneralBody,
  LeaseRebuildBody,
  LeaseRebuildResult,
  LegalRootBody,
  ReadinessItem,
  ReplicaBody,
  ReplicaTestResult,
  RequestKind,
  RequestState,
  RequestStep,
  RetentionBody,
  RetentionPreview,
  RootBody,
  SealActionResult,
  SettingGroup,
  SettingOut,
  SettingProblem,
  SettingsPatch,
  SettingsPatchResult,
  SettingValue,
  SmtpBody,
  SmtpCaBody,
  SmtpCaClear,
  SmtpSection,
  SourceCheckBody,
  SourceConfig,
  SourceOut,
  StepState,
  TestEmailBody,
  TestEmailResult,
  TsaBody,
  TsaSection,
  TsaTestBody,
  TsaTestResult,
  UploadPreview,
  ZamaneBody,
} from '../src/lib/settings-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { ADMIN, copy, CREDITS, SEALS, SETTINGS } from './samples';

/**
 * HTTP refusals of the administration API no capture shows (contract 2.12, `admin_channel.TEXTS`,
 * `arcilogger_core.admin.messages`): code -> [status, tr, en].
 */
const HTTP: Record<string, [number, string, string]> = {
  admin_channel_key_missing: [
    503,
    'Yönetim kanalının anahtarı bulunamadı; kurulumu onarın (Install-Arcilogger.ps1).',
    'The administration channel key is missing; repair the installation (Install-Arcilogger.ps1).',
  ],
  admin_queue_full: [
    429,
    'Bekleyen değişiklik sayısı sınıra ulaştı; öncekilerin bitmesini bekleyip tekrar deneyin.',
    'Too many changes are waiting; wait for the earlier ones to finish and try again.',
  ],
  config_unsupported_format: [
    409,
    'arcilogger.toml dosyasında web arayüzünün güvenle düzenleyemediği bir yazım var (satır içi tablo, noktalı anahtar ya da çok satırlı metin); bu değişikliği sunucuda elle yapın.',
    'arcilogger.toml contains syntax the web interface cannot edit safely (an inline table, a dotted key or a multi-line string); make this change on the server by hand.',
  ],
  config_invalid: [
    422,
    'Ayarlar geçersiz; işaretli alanları düzeltin.',
    'The settings are invalid; correct the marked fields.',
  ],
  config_key_locked: [
    422,
    'Bu ayar güvenlik nedeniyle yalnızca sunucu konsolundan değiştirilebilir.',
    'For security reasons this setting can only be changed on the server console.',
  ],
  clock_servers_required: [
    422,
    'En az bir saat sunucusu gerekir (yerel kaynak ya da yedek sunucu).',
    'At least one time server is required (the local source or the fallback).',
  ],
  source_invalid: [
    422,
    'Kaynak bilgileri geçersiz; işaretli alanları düzeltin.',
    'The source is invalid; correct the marked fields.',
  ],
  source_not_found: [404, 'Kaynak bulunamadı.', 'Source not found.'],
  source_name_taken: [
    409,
    'Bu kaynak adı zaten kullanılıyor.',
    'This source name is already in use.',
  ],
  source_overlap: [
    409,
    'Bu ağ başka bir kaynağın ağıyla çakışıyor.',
    'This network overlaps the network of another source.',
  ],
  source_rename_not_allowed: [
    422,
    'Kaynak adı sonradan değiştirilemez (ham log klasörünün adıdır); yeni bir kaynak ekleyin.',
    'A source name cannot be changed (it names the raw log folder); add a new source instead.',
  ],
  source_ipv6_unsupported: [
    422,
    'Şimdilik yalnızca IPv4 adresleri ve ağları desteklenir.',
    'Only IPv4 addresses and networks are supported for now.',
  ],
  source_reserved_address: [
    422,
    'Bu adres bir log kaynağı olamaz (geri döngü, çok noktaya yayın, yerel bağlantı, yayın ya da ayrılmış adres).',
    'This address cannot be a log source (loopback, multicast, link-local, broadcast or reserved).',
  ],
  source_own_address: [
    422,
    'Bu adres ARCILOGGER sunucusunun kendi adresi.',
    "This is the ARCILOGGER server's own address.",
  ],
  source_prefix_too_short: [
    422,
    'Ağ izin verilenden geniş; daha dar bir ağ girin.',
    'The network is wider than allowed; enter a narrower network.',
  ],
  source_limit: [
    422,
    'Kaynak sayısı üst sınırına ulaşıldı.',
    'The maximum number of sources has been reached.',
  ],
  secret_invalid: [
    422,
    'Girilen gizli bilgi geçersiz (boş, çok uzun ya da kontrol karakteri içeriyor).',
    'The secret is invalid (empty, too long or containing control characters).',
  ],
  secret_rebind_required: [
    422,
    'Sunucu, kullanıcı ya da bağlantı türü değişti; kayıtlı parola yeni hedefe gönderilmez. Parolayı yeniden girin.',
    'The server, user or connection type changed; the stored password is not sent to a new destination. Enter the password again.',
  ],
  smtp_plaintext_auth_refused: [
    422,
    'Şifresiz bağlantıda parola yalnızca bu sunucudaki bir aktarıcıya gönderilebilir; STARTTLS ya da SSL seçin.',
    'Without encryption a password can only be sent to a relay on this server; choose STARTTLS or SSL.',
  ],
  smtp_test_recipient_required: [
    422,
    'Kaydetmeden önce deneme e-postası için bir alıcı seçin.',
    'Choose a recipient for the trial e-mail before saving.',
  ],
  replica_user_invalid: [
    422,
    'Kullanıcı SUNUCU\\kullanıcı, ETKİALANI\\kullanıcı ya da kullanıcı@etkialanı biçiminde olmalı.',
    'The user must look like SERVER\\user, DOMAIN\\user or user@domain.',
  ],
  replica_password_required: [
    422,
    'Replika kullanıcısı için parola gerekir.',
    'A password is required for the replica user.',
  ],
  tsa_server_invalid: [
    422,
    'Zaman damgası sunucusu "test" ya da "production" olmalı.',
    'The time-stamp server must be "test" or "production".',
  ],
  tsa_not_ready: [
    409,
    'Üretim sunucusuna geçmeden önce eksikleri tamamlayın: kimlik bilgileri, onaylı Zamane istemcisi, Java ve en az bir kök sertifika.',
    'Complete the missing items before switching to the production server: credentials, an approved Zamane client, Java and at least one root certificate.',
  ],
  tsa_confirm_required: [
    422,
    'Üretimden test sunucusuna geçiş üretim damgalarını durdurur; onaylayın.',
    'Switching from production to the test server stops production stamps; confirm to continue.',
  ],
  java_not_detected: [
    422,
    'Seçilen Java sunucuda bulunamadı; listeden birini seçin.',
    'The selected Java was not found on the server; choose one from the list.',
  ],
  root_exists: [
    409,
    'Bu kök sertifika zaten kurulu.',
    'This root certificate is already installed.',
  ],
  root_not_installed: [
    422,
    'Yasal kök olarak yalnızca kurulu bir kök sertifika işaretlenebilir.',
    'Only an installed root certificate can be marked as a legal root.',
  ],
  zamane_archive_invalid: [
    422,
    'Dosya bir Zamane istemcisi değil: bir .jar ya da tek bir tss-client-console-*.jar içeren .zip olmalı.',
    'The file is not a Zamane client: it must be a .jar, or a .zip holding exactly one tss-client-console-*.jar.',
  ],
  zamane_sha256_mismatch: [
    422,
    'Dosyanın SHA-256 değeri girdiğiniz değerle aynı değil; dosya kullanılmadı.',
    "The file's SHA-256 does not match the value you entered; the file was not used.",
  ],
  upload_too_large: [413, 'Dosya izin verilen boyuttan büyük.', 'The file is larger than allowed.'],
  upload_not_found: [
    404,
    'Yüklenen dosya bulunamadı ya da süresi doldu (30 dakika); yeniden yükleyin.',
    'The uploaded file was not found or has expired (30 minutes); upload it again.',
  ],
  seal_not_applicable: [
    409,
    'Bu gün mühürlenmez (veri yok, atlandı ya da imha edildi).',
    'This day is not sealed (no data, skipped or deleted).',
  ],
};

/** Outcome codes of the agent and the tasks (`arcilogger_core.admin.messages.MESSAGES`). */
const MESSAGES: Record<string, [string, string]> = {
  signature_invalid: [
    'İstek imzası doğrulanamadı; istek reddedildi (olası kurcalama, uyarı oluşturuldu).',
    'The request signature could not be verified; it was rejected (possible tampering, an alert was raised).',
  ],
  request_expired: [
    'İstek zamanında işlenemedi ve süresi doldu; hiçbir şey değişmedi.',
    'The request was not processed in time and expired; nothing changed.',
  ],
  request_replayed: [
    'Bu istek daha önce işlendi; tekrarı reddedildi.',
    'This request was already processed; the repeat was rejected.',
  ],
  installation_mismatch: [
    'İstek başka bir kurulum için hazırlanmış; reddedildi.',
    'The request was made for another installation; it was rejected.',
  ],
  payload_mismatch: [
    'Ekli dosya istekteki özetle uyuşmuyor; reddedildi.',
    'An attached file does not match the digest in the request; it was rejected.',
  ],
  schema_invalid: [
    'İstek biçimi geçersiz; reddedildi.',
    'The request format is invalid; it was rejected.',
  ],
  reauth_missing: [
    'İstek yakın zamanda parola onayı olmadan hazırlanmış; reddedildi.',
    'The request was made without a recent password confirmation; it was rejected.',
  ],
  conflict: [
    'Ayar dosyası bu istek hazırlandıktan sonra değişti; hiçbir şey değişmedi. Sayfayı yenileyip tekrar deneyin.',
    'The configuration changed after this request was made; nothing changed. Reload the page and try again.',
  ],
  validation_failed: [
    'Değişiklik sunucudaki son denetimden geçmedi; hiçbir şey değişmedi.',
    'The change failed the final check on the server; nothing changed.',
  ],
  probe_failed: [
    'Yeni değerlerle bağlantı denemesi başarısız; hiçbir şey değişmedi.',
    'The connection test with the new values failed; nothing changed.',
  ],
  ntp_unreachable: ['Saat sunucusu yanıt vermedi.', 'The time server did not answer.'],
  smtp_test_failed: ['Deneme e-postası gönderilemedi.', 'The trial e-mail could not be sent.'],
  replica_test_failed: [
    'Replika paylaşımına bağlanılamadı.',
    'The replica share could not be reached.',
  ],
  credit_query_failed: [
    'Kamu SM kontör sorgusu başarısız (müşteri numarası, parola ya da bağlantı).',
    'The Kamu SM credit query failed (customer number, password or connection).',
  ],
  write_failed: [
    'Ayar dosyası yazılamadı; önceki ayarlar yerinde.',
    'The configuration could not be written; the previous settings are in place.',
  ],
  verify_failed: [
    'Yazılan ayarlar doğrulanamadı; önceki ayarlar geri yüklendi.',
    'The written settings could not be verified; the previous settings were restored.',
  ],
  rollback_failed: [
    'Değişiklik geri alınamadı; elle müdahale gerekiyor. Yedek sürüm ve adımlar aşağıda.',
    'The change could not be undone; manual action is needed. The backup version and the steps are below.',
  ],
  interrupted: [
    'Yönetim hizmeti değişiklik sırasında durdu; önceki ayarlar geri yüklendi.',
    'The administration service stopped during the change; the previous settings were restored.',
  ],
  acl_drift: [
    'Yönetim hizmeti ayar dosyalarına yazma iznini kaybetmiş (çoğunlukla konsoldan bir secret set sonrası); sunucuda yönetici olarak Set-ArciVaultAcl.ps1 betiğini çalıştırın.',
    'The administration service has lost write access to the configuration files (most often after a console secret set); run Set-ArciVaultAcl.ps1 on the server as an administrator.',
  ],
  system_sync_failed: [
    "Güvenlik duvarı, saat ya da Zamane adımı uygulanamadı; önceki kurallar geçerli. Ayrıntılar Olay Görüntüleyicisi'nde (ARCILOGGER-Admin).",
    'The firewall, time or Zamane step could not be applied; the previous rules are in force. Details are in Event Viewer (ARCILOGGER-Admin).',
  ],
  system_sync_timeout: [
    'Sistem görevi (\\ARCILOGGER\\ApplySystem) 5 dakika içinde çalışmadı; görev kapalı olabilir.',
    'The system task (\\ARCILOGGER\\ApplySystem) did not run within 5 minutes; it may be disabled.',
  ],
  system_plan_failed: [
    'Sistem planı hesaplanamadı ya da geçersiz bir değer içeriyor; hiçbir kural değişmedi.',
    'The system plan could not be computed or contains an invalid value; no rule was changed.',
  ],
  system_plan_invalid: [
    'Sistem planı hesaplanamadı ya da geçersiz bir değer içeriyor; hiçbir kural değişmedi.',
    'The system plan could not be computed or contains an invalid value; no rule was changed.',
  ],
  task_timeout: [
    'Görev 15 dakika içinde sonuç vermedi; \\ARCILOGGER\\Seal ya da \\ARCILOGGER\\Requests görevi kapalı olabilir.',
    'The task gave no result within 15 minutes; \\ARCILOGGER\\Seal or \\ARCILOGGER\\Requests may be disabled.',
  ],
  tsa_test_failed: [
    'Zaman damgası alınamadı ya da doğrulanamadı.',
    'No time stamp was obtained, or it could not be verified.',
  ],
  seal_failed: [
    'Mühürleme denemesi başarısız; hata gün ayrıntısında.',
    'The sealing attempt failed; the error is on the day page.',
  ],
  executable_refused: [
    'Bu program yolu güvenilir bir konumda değil; görev çalıştırmadı.',
    'This program path is not in a trusted location; the task did not run it.',
  ],
};

/** Warnings of a request (`arcilogger_core.admin.messages.WARNINGS`). */
const WARNINGS: Record<string, [string, string]> = {
  agent_stale: [
    'Yönetim hizmeti geç yanıt veriyor; değişiklik gecikebilir.',
    'The administration service is answering late; the change may be delayed.',
  ],
  firewall_not_managed: [
    'Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor; 514 kuralını elle güncelleyin.',
    'The firewall is not managed by ARCILOGGER; update the 514 rule by hand.',
  ],
  legal_roots_empty: [
    'Yasal kök parmak izi yok: hiçbir gün yasal nitelikte gösterilmez.',
    'No legal root fingerprint: no day is shown as legal grade.',
  ],
  production_not_tested: [
    'Üretim sunucusunda henüz başarılı bir damga testi yok.',
    'No successful stamp test on the production server yet.',
  ],
  production_stamps_stop: ['Üretim zaman damgaları duracak.', 'Production time stamps will stop.'],
  root_expired: [
    'Kök sertifikanın süresi dolmuş; geçmiş günlerin doğrulanması için yine de kurulur.',
    'The root certificate has expired; it is still installed so past days can be verified.',
  ],
  unknown_jar_held: [
    'Bu Zamane sürümü bilinen sürümler arasında değil: ancak bir yönetici sunucu konsolunda onaylayınca (en erken bekletme süresi sonunda) etkinleşir.',
    'This Zamane release is not a known release: it becomes active only when an administrator approves it on the server console (at the earliest when the hold ends).',
  ],
  probe_skipped: ['Bağlantı denemesi atlandı.', 'The connection test was skipped.'],
  credit_query_skipped: [
    'Kontör sorgusu yapılmadı: Zamane istemcisi henüz çalıştırılamıyor (onay bekliyor ya da Java veya istemci güvenilir bir konumda değil); istemci etkinleşince "Kontör sorgula" ile deneyin.',
    'The credit was not queried: the Zamane client cannot run yet (it awaits approval, or Java or the client is not in a trusted location); once the client is active, use "Query credit".',
  ],
  write_access_lost: [
    'Değişiklik uygulandı, ancak yönetim hizmeti ayar dosyalarına artık yazamıyor: sonraki değişiklikten önce sunucuda yönetici olarak Set-ArciVaultAcl.ps1 betiğini çalıştırın.',
    'The change is applied, but the administration service can no longer write the configuration files: before the next change, run Set-ArciVaultAcl.ps1 on the server as an administrator.',
  ],
  service_not_reloaded: [
    'Bir hizmet yeni ayarları henüz yüklemedi; ayrıntı adım listesinde.',
    'A service has not loaded the new settings yet; see the step list.',
  ],
  restart_required: [
    'Sunucuda elle yapılan bir değişiklik ancak hizmet yeniden başlatılınca etkinleşir.',
    'A change made by hand on the server takes effect only after a service restart.',
  ],
  source_outside_policy: [
    'Kaynak adresi yönetim politikasındaki ağların dışında ya da izin verilenden geniş; güvenlik duvarı kuralına eklenmedi.',
    'The source address lies outside the networks of the administration policy or is wider than allowed; it was not added to the firewall rule.',
  ],
  ntp_server_empty: [
    'Yerel saat kaynağı yok; saat yalnızca yedek sunucuyla eşitlenir.',
    'No local time source; the clock follows only the fallback server.',
  ],
  source_disable_gaps: [
    'Kapatılan kaynak, mühürlenmemiş günlerin boşluk listesinde artık beklenmez.',
    'Once disabled, the source is no longer expected in the gap list of days not yet sealed.',
  ],
  smtp_plaintext: ['E-posta şifresiz gönderilecek.', 'E-mail will be sent unencrypted.'],
  smtp_disabled: ['Uyarı e-postaları durdu.', 'Alert e-mails have stopped.'],
  backup_disabled: ['Veritabanı yedeği kapatıldı.', 'The database backup is switched off.'],
  change_held: [
    'Değişiklik bekletme süresi sonunda uygulanacak; o zamana kadar iptal edilebilir.',
    'The change will be applied when the hold ends; it can be cancelled until then.',
  ],
};

/** Request state labels (`arcilogger_core.admin.messages.STATE_LABELS`). */
const STATE_LABELS: Record<RequestState, [string, string]> = {
  queued: ['Sırada', 'Queued'],
  waiting_for_agent: ['Yönetim hizmeti bekleniyor', 'Waiting for the administration service'],
  expired: ['Süresi doldu, hiçbir şey değişmedi', 'Expired, nothing changed'],
  received: ['Alındı', 'Received'],
  rejected: ['Reddedildi', 'Rejected'],
  conflict: ['Çakışma: ayarlar başka yerden değişti', 'Conflict: changed elsewhere'],
  probing: ['Bağlantı deneniyor', 'Testing the connection'],
  probe_failed: [
    'Bağlantı denemesi başarısız, hiçbir şey değişmedi',
    'Connection test failed, nothing changed',
  ],
  pending: ['Bekletiliyor', 'Held'],
  cancelled: ['İptal edildi', 'Cancelled'],
  applying: ['Uygulanıyor', 'Applying'],
  applied: ['Yazıldı, hizmetler yüklüyor', 'Written, services loading'],
  active: ['Uygulandı', 'Applied'],
  partially_active: ['Kısmen uygulandı', 'Partly applied'],
  failed: ['Uygulanmadı, önceki ayarlar geçerli', 'Not applied, the previous settings are in use'],
  needs_attention: ['Elle müdahale gerekiyor', 'Manual action needed'],
  dispatched: ['Göreve iletildi', 'Passed to the task'],
  running: ['Çalışıyor', 'Running'],
  succeeded: ['Tamamlandı', 'Done'],
  timed_out: ['Zaman aşımı', 'Timed out'],
};

/**
 * Every refusal captured from the real API (`samples/admin.json`, `settings.json`, the seal and
 * credit samples): code -> [status, tr, en]. {@link HTTP} keeps only the codes no capture shows.
 */
const CAPTURED: Record<string, [number, string, string]> = Object.fromEntries(
  [ADMIN.errors, SETTINGS.errors, SEALS.errors, { query_limited: CREDITS.query_limited }].flatMap(
    (errors) =>
      Object.values(errors).map(
        ({ status, body }) =>
          [body.error.code, [status, body.error.message_tr, body.error.message_en]] as const,
      ),
  ),
);
for (const code of Object.keys(HTTP))
  if (code in CAPTURED) throw new Error(`mock-admin: ${code} is captured; use the captured text`);

/** The status and texts of a refusal: the captured answer, else the contract's catalogue. */
function texts(code: string): [number, string, string] | undefined {
  return CAPTURED[code] ?? HTTP[code];
}

/**
 * The tables above must say what the real API says: every request, warning and step message the
 * capture shows is compared with them when the mock loads (a changed text fails every spec).
 */
function checkCaptured(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      checkCaptured(item, `${path}[${String(index)}]`);
    });
    return;
  }
  if (typeof value !== 'object' || value === null) return;
  const item = value as Record<string, unknown>;
  if (typeof item.kind === 'string' && typeof item.label_tr === 'string') {
    const label = STATE_LABELS[item.state as RequestState] as [string, string] | undefined;
    if (label?.[0] !== item.label_tr || label[1] !== item.label_en)
      throw new Error(`mock-admin: STATE_LABELS.${String(item.state)} differs from ${path}`);
  }
  if (typeof item.code === 'string' && typeof item.message_tr === 'string') {
    const known = MESSAGES[item.code] ?? WARNINGS[item.code] ?? texts(item.code)?.slice(1);
    if (known?.[0] !== item.message_tr || known[1] !== item.message_en)
      throw new Error(`mock-admin: the text of ${item.code} differs from ${path}`);
  }
  for (const [key, child] of Object.entries(item)) checkCaptured(child, `${path}.${key}`);
}
checkCaptured({ ...ADMIN, errors: null, meta: null }, 'admin');
checkCaptured([SEALS.seal_now, SEALS.seal_now_queued, SEALS.seal_now_done], 'seals');
checkCaptured([CREDITS.query, CREDITS.query_done], 'credits');

export interface AdminOptions {
  /** The administration service's heartbeat (default `ok`). */
  agent?: AgentState;
  /** `policy.hold_hours` (default 0: retention and replica changes apply at once). */
  holdHours?: number;
  /** Milliseconds per step of a request (default 250). */
  stepMs?: number;
  /** The mock's "now" at start (default: the real time), e.g. the browser clock of a spec. */
  now?: number;
  /** The Kamu SM section of a new installation (the captured one: the first setup). */
  freshTsa?: boolean;
  /** A hand edit on the server waits for a restart (`restart_required`). */
  restartRequired?: boolean;
  /** The configuration was last written outside the administration service. */
  outside?: boolean;
}

/** How the next configuration change ends (default `active`). */
export type Outcome =
  | 'active'
  | 'partially_active'
  | 'failed'
  | 'needs_attention'
  | 'probe_failed'
  | 'conflict'
  | 'rejected'
  | 'expired';

/** How the next action ends (default `succeeded`). */
export type ActionOutcome = 'succeeded' | 'failed' | 'timed_out';

interface Stage {
  /** Milliseconds after the request was accepted. */
  at: number;
  state: RequestState;
  /** The step that runs (the ones before it are done; `-1`: none yet). */
  running: number;
}

interface StepEnd {
  state: StepState;
  code?: string;
}

interface Final {
  state: RequestState;
  error?: string;
  /** The steps the request ends with (`done` unless named in `ends`). */
  steps: RequestStep[];
  ends?: Partial<Record<RequestStep, StepEnd>>;
  rolledBack?: boolean;
  manual?: [string, string];
  warnings?: string[];
}

interface MockRequest {
  out: AdminRequestOut;
  /** Real time it was accepted (ms). */
  started: number;
  stages: Stage[];
  final: Final;
  steps: RequestStep[];
  /** Steps a change does not need (`hold`, `write_secrets`, `install_files`): `skipped`. */
  skipped: ReadonlySet<RequestStep>;
  /** Steps listed already while the request waits (a held client's `jar.promotion`). */
  ahead: RequestStep[];
  /** Applied once the request passes `applied` (configuration) or succeeds (action). */
  apply: (() => void) | null;
  applied: boolean;
  /** Stays `pending` until it is cancelled (a hold of `policy.hold_hours`). */
  held: boolean;
  cancelled: boolean;
  /** The section's values before it was applied (for a revert). */
  before: unknown;
  /** The day of a seal request. */
  day: string | null;
  /** The effects it was accepted with (a revert takes them over). */
  effects: string[];
  /** The outcome of an action, reported once it ends. */
  result: Record<string, unknown> | null;
  /** Undoes what accepting it did at once (a staged Zamane client), when it is cancelled. */
  cancel?: () => void;
}

const SEALABLE = new Set(['failed', 'pending']);
/**
 * The agent's steps of a configuration change (`admin/agent.py`): every change reports all of
 * them, the ones it does not need `skipped` (`probe` only when the change is tried first).
 */
const AGENT_STEPS: readonly RequestStep[] = [
  'received',
  'validate',
  'probe',
  'hold',
  'backup',
  'write_secrets',
  'write_config',
  'install_files',
  'verify',
  'journal',
];
/** The API's steps after them, from the change's effects (`admin_channel._activation`). */
const ACTIVATION_STEPS: readonly RequestStep[] = [
  'reload.collector',
  'reload.ingest',
  'reload.worker',
  'reload.api',
  'system.firewall',
  'system.time',
];
const APPLYING_STEPS = new Set<RequestStep>([
  'backup',
  'write_secrets',
  'write_config',
  'install_files',
  'verify',
  'journal',
  'rollback',
]);
/** The steps of an action the agent hands to a task (the captured seal and credit requests). */
const ACTION_STEPS: readonly RequestStep[] = ['received', 'dispatch', 'task'];

function sha256(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

function newId(): string {
  return createHash('sha256').update(`${Date.now()}-${Math.random()}`).digest('hex').slice(0, 32);
}

/** `{field, code, message_tr, message_en}` of a refused field (422 `details`). */
function problem(field: string, code: string): FieldProblem {
  const [, tr, en] = texts(code) ?? [422, code, code];
  return { field, code, message_tr: tr, message_en: en };
}

function message(code: string): CodeMessage {
  const [tr, en] = MESSAGES[code] ?? WARNINGS[code] ?? [code, code];
  return { code, message_tr: tr, message_en: en };
}

const HOST = /^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function validHost(text: string): boolean {
  return /^[\d.]+$/.test(text) ? IPV4.test(text) : HOST.test(text);
}

/** A text field of a change body: trimmed, `null` when empty. */
function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function integer(value: unknown, low: number, high: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high;
}

/**
 * The Java 8 installations the administration service finds on the fictional site, and its
 * installer-recorded Zamane client and Kamu SM test root (the capture runs without Java and
 * without Kamu SM).
 */
const JAVA_CHOICES = [
  'C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe',
  'C:/Program Files/Java/jre1.8.0_421/bin/java.exe',
];
const INSTALLED_JAR = sha256('e2e installer zamane client');
const INSTALLED_ROOT = sha256('e2e kamu sm test root');
/** When the installer recorded the site's credentials. */
const INSTALLED_AT = '2026-09-30T09:12:00+03:00';

/**
 * The machine configuration every test starts with: the installation the capture made
 * (`ADMIN.config`: the site name and clock, retention, backup and the two sources) and the
 * replica share as the capture set it up (`ADMIN.sections.config`). What the capture cannot set
 * up is added in the captured shapes: the Kamu SM provider with Java, the installer's Zamane
 * client, test root and credentials (`freshTsa`: the captured section of a new installation,
 * with the site's Java found), and an SMTP account with a stored password (the capture's mail
 * server takes no login).
 */
function startConfig(options: AdminOptions): AdminConfig['sections'] {
  const sections = copy(ADMIN.config.sections);
  const stored = { set: true, changed_at: INSTALLED_AT, changed_by: 'cli' };
  sections.tsa = options.freshTsa
    ? { ...sections.tsa, java_choices: [...JAVA_CHOICES] }
    : {
        ...sections.tsa,
        provider: 'zamane',
        server: 'test',
        java: JAVA_CHOICES[0] ?? null,
        java_choices: [...JAVA_CHOICES],
        zamane: {
          present: true,
          sha256: INSTALLED_JAR,
          release: null,
          trusted: 'installer',
          staged: [],
        },
        roots: [
          {
            sha256: INSTALLED_ROOT,
            subject: 'CN=Kamu SM Test Kök Sertifikası (örnek)',
            not_after: '2036-01-01T00:00:00+03:00',
            legal: false,
            source: 'installer',
          },
        ],
        secrets: { 'kamusm.customer': { ...stored }, 'kamusm.password': { ...stored } },
      };
  sections.smtp = {
    ...sections.smtp,
    enabled: true,
    host: 'smtp.ornek.example',
    port: 587,
    security: 'starttls',
    username: 'arcilogger',
    from_address: 'arcilogger@ornek.example',
    timeout_seconds: 30,
    secrets: { 'smtp.password': { ...stored } },
  };
  sections.replica = copy(ADMIN.sections.config.sections.replica);
  return sections;
}

/** Values as the API compares them for an `etag` (secrets only set/unset). */
function etagOf(section: string, values: unknown): string {
  return `${section}:${sha256(JSON.stringify(values))}`;
}

/** `{path: {old, new}}` of two flat value maps. */
function diff(prefix: string, before: Record<string, unknown>, after: Record<string, unknown>) {
  const changes: Record<string, Change> = {};
  for (const [key, value] of Object.entries(after)) {
    const old = before[key];
    if (JSON.stringify(old) !== JSON.stringify(value))
      changes[prefix ? `${prefix}.${key}` : key] = { old: old ?? null, new: value };
  }
  return changes;
}

/** The `[[sources]]` table of a source (what a change list holds: the whole table). */
function sourceTable(source: SourceConfig): SourceConfig {
  return {
    name: source.name,
    type: source.type,
    address: source.address,
    heartbeat_minutes: source.heartbeat_minutes,
    expected: source.expected,
    transport: source.transport,
    enabled: source.enabled,
  };
}

export class AdminMock {
  readonly sections: AdminConfig['sections'];
  agent: AgentState;
  holdHours: number;
  stepMs: number;
  restartRequired: boolean;
  outside: boolean;
  readonly requests = new Map<string, MockRequest>();
  /** The next configuration change ends like this (then back to `active`). */
  nextOutcome: Outcome | null = null;
  /** The next action ends like this (then back to `succeeded`). */
  nextAction: ActionOutcome | null = null;
  /** Days "Şimdi mühürle" sealed, with the Seal task's outcome. */
  readonly sealed = new Map<string, { serial: string; gen_time: string; sealed_at: string }>();
  /** Checks "Kontör sorgula" added to the credit history. */
  readonly creditChecks: CreditCheck[] = [];
  /**
   * The kept configurations, newest first (contract 2.4.10): version *n* is the pair from before
   * its request was applied (16: before a restore on the server console). The captured list
   * names the capture's own requests, so the mock keeps its own.
   */
  readonly versions: ConfigVersion[] = [
    {
      n: 16,
      at: INSTALLED_AT,
      request_id: null,
      actor: 'console',
      paths: [],
      has_secrets: true,
    },
  ];
  /** Diagnostics packages handed out. */
  diagnostics = 0;
  /** The database settings as the capture's API lists them (`settings.json`). */
  readonly settings: SettingGroup[] = copy(SETTINGS.list.groups);
  readonly uploads = new Map<string, UploadPreview>();
  /** When each action was last accepted (its rate limit, contract 2.11.3). */
  private readonly lastAction = new Map<RequestKind, number>();
  /** When test e-mails were sent (5 an hour). */
  private readonly testEmails: number[] = [];
  private readonly realStart = Date.now();
  private readonly base: number;
  private readonly creditsSample: () => Credits;

  constructor(options: AdminOptions = {}, creditsSample: () => Credits = () => CREDITS.kamusm) {
    this.sections = startConfig(options);
    this.agent = options.agent ?? 'ok';
    this.holdHours = options.holdHours ?? 0;
    this.stepMs = options.stepMs ?? 250;
    this.restartRequired = options.restartRequired ?? false;
    this.outside = options.outside ?? false;
    this.base = options.now ?? Date.now();
    this.creditsSample = creditsSample;
    this.refreshTsa();
  }

  /** The mock's clock (the spec's browser time when given). */
  now(): Date {
    return new Date(this.base + (Date.now() - this.realStart));
  }

  private iso(offsetMs = 0): string {
    return new Date(this.now().getTime() + offsetMs).toISOString();
  }

  refuse(status: number, code: string, details?: unknown): [number, unknown] {
    const [, tr, en] = texts(code) ?? [status, code, code];
    return [
      status,
      {
        error: {
          code,
          message_tr: tr,
          message_en: en,
          ...(details === undefined ? {} : { details }),
        },
      },
    ];
  }

  /** A refusal with the status of the API (the captured answer, else the catalogue, 2.12). */
  private reject(code: string, details?: unknown): [number, unknown] {
    return this.refuse(texts(code)?.[0] ?? 422, code, details);
  }

  /**
   * A refused check (WP4-2): the first problem's code and status; `details` lists every field,
   * or carries the first problem's own facts (`missing`, `causes`).
   */
  private refused(problems: FieldProblem[], facts?: Record<string, unknown>): [number, unknown] {
    return this.reject(problems[0]?.code ?? 'config_invalid', facts ?? problems);
  }

  /** A 429 with its `Retry-After` header (seconds). */
  private limited(code: string, retryAfter: number): Answer {
    const [status, body] = this.reject(code, { retry_after: retryAfter });
    return [
      status,
      body,
      {
        body: Buffer.from(JSON.stringify(body)),
        contentType: 'application/json',
        headers: { 'Retry-After': String(retryAfter) },
      },
    ];
  }

  // -- the configuration ----------------------------------------------------------------------

  /** What the API compares for a section's `etag`. */
  private editable(section: AdminSection): unknown {
    const values = this.sections[section];
    if (section === 'sources') return (values as SourceOut[]).map(sourceTable);
    return values;
  }

  etag(section: AdminSection): string {
    return etagOf(section, this.editable(section));
  }

  config(): AdminConfig {
    const sections: AdminSection[] = [
      'general',
      'tsa',
      'smtp',
      'replica',
      'retention',
      'backup',
      'sources',
    ];
    return {
      config_sha256: sha256(JSON.stringify(this.sections)),
      etags: Object.fromEntries(sections.map((section) => [section, this.etag(section)])),
      sections: copy({
        ...this.sections,
        retention: {
          ...this.sections.retention,
          // The configuration's preview has no first run (that is the check's, with its hold).
          preview: { ...this.retentionPreview(this.sections.retention.days), first_run_at: null },
        },
      }),
      locked: copy(ADMIN.config.locked),
      policy: {
        ...copy(ADMIN.config.policy),
        enabled: this.agent !== 'disabled',
        hold_hours: this.holdHours,
      },
      pending: [...this.requests.values()]
        .filter((item) => item.out.state === 'pending' && item.out.section && item.out.effective_at)
        .map((item) => ({
          request_id: item.out.id,
          section: item.out.section,
          effective_at: item.out.effective_at,
        })),
      // `setup_problems` of the captured installation: no local time source.
      problems: copy(ADMIN.config.problems).filter(
        (item) => item.key !== 'clock.ntp_server' || !this.sections.general.clock.ntp_server,
      ),
      restart_required: this.restartRequired,
    };
  }

  /** The captured status, with this mock's heartbeat, requests and configuration. */
  status(): AdminStatus {
    const sha = sha256(JSON.stringify(this.sections));
    const captured =
      this.agent === 'missing'
        ? ADMIN.status_agent_missing
        : this.agent === 'disabled'
          ? ADMIN.status_disabled
          : ADMIN.status;
    const agent = copy(captured.agent);
    agent.state = this.agent;
    if (this.agent !== 'missing') {
      agent.heartbeat_at = this.iso(this.agent === 'stale' ? -75_000 : -4_000);
      agent.java_candidates = [...JAVA_CHOICES];
      agent.seal_ready = copy(this.sections.tsa.seal_ready);
      if (agent.system_task) agent.system_task.checked_at = this.iso(-120_000);
    }
    agent.queue = [...this.requests.values()].filter((item) => item.out.state === 'queued').length;
    agent.pending = [...this.requests.values()]
      .filter((item) => item.out.state === 'pending' && item.out.effective_at)
      .map((item) => ({ id: item.out.id, effective_at: item.out.effective_at ?? '' }));
    const { ntp_server: local, fallback_server: fallback } = this.sections.general.clock;
    const services = Object.fromEntries(
      Object.entries(copy(ADMIN.status.services)).map(([name, service]) => [
        name,
        {
          ...service,
          // Every service of the site runs and has the configuration in force.
          sha256: sha,
          loaded_at: this.iso(-60_000),
          running: true,
          restart_keys: name === 'ArciCollector' && this.restartRequired ? ['collector'] : [],
        },
      ]),
    );
    return {
      agent,
      system: {
        ...copy(ADMIN.status.system),
        at: this.iso(-120_000),
        applied_config_sha256: sha,
        firewall: {
          managed: true,
          strict_outbound: false,
          syslog_addresses: this.sections.sources
            .filter((item) => item.enabled)
            .map((item) => item.address),
        },
        // `systemplan.peer_list`: the local source in client mode, the fallback as fallback.
        time: {
          peers:
            local && fallback && local !== fallback
              ? `${local},0x8 ${fallback},0xA`
              : `${local || fallback},0x8`,
        },
      },
      services,
      config_sha256: sha,
      config_origin: this.outside ? 'outside' : 'browser',
      restart_required: this.restartRequired,
    };
  }

  /** 503 while browser administration is switched off or the service does not answer. */
  private unavailable(): [number, unknown] | null {
    if (this.agent === 'disabled') return this.reject('admin_disabled');
    if (this.agent === 'missing') return this.reject('admin_agent_unavailable');
    return null;
  }

  /**
   * One open configuration request per section; a Zamane client held for the console's
   * approval counts only for another client (`client`), as `Channel.check_section_free`.
   */
  private busy(section: AdminSection, client = false): boolean {
    return [...this.requests.values()].some(
      (item) =>
        item.out.section === section &&
        (item.out.kind === 'config.apply' || item.out.kind === 'config.revert') &&
        !item.out.terminal &&
        (client ||
          item.out.state !== 'pending' ||
          !item.out.warnings.some((warning) => warning.code === 'unknown_jar_held')),
    );
  }

  // -- requests ---------------------------------------------------------------------------------

  /** Advance every request to the stage its age reached. */
  tick(): void {
    for (const item of this.requests.values()) this.advance(item);
  }

  private stepOut(step: RequestStep, end: StepEnd): AdminRequestOut['steps'][number] {
    const text = end.code ? message(end.code) : null;
    return {
      step,
      state: end.state,
      at: end.state === 'pending' ? null : this.iso(),
      code: end.code ?? null,
      message_tr: text?.message_tr ?? null,
      message_en: text?.message_en ?? null,
    };
  }

  private advance(item: MockRequest): void {
    if (item.out.terminal) return;
    const out = item.out;
    if (item.cancelled) {
      // A held change keeps the steps the agent took (its hold `skipped`); a queued one has none.
      this.finish(item, {
        state: 'cancelled',
        steps: item.held
          ? item.steps.filter((step) => ['received', 'validate', 'hold'].includes(step))
          : [],
        ends: { hold: { state: 'skipped' } },
      });
      return;
    }
    const age = Date.now() - item.started;
    const stage =
      [...item.stages].reverse().find((candidate) => candidate.at <= age) ?? item.stages[0];
    if (!stage) return;
    const done = age >= (item.stages.at(-1)?.at ?? 0) + this.stepMs && !item.held;
    if (done) {
      this.finish(item, item.final);
      return;
    }
    out.state = stage.state;
    [out.label_tr, out.label_en] = STATE_LABELS[stage.state];
    // The agent lists a step once it reaches it; the API the reloads once the file is written.
    // The worker reports no steps.
    const shown = out.kind.startsWith('worker.') ? [] : item.steps.slice(0, stage.running + 1);
    out.steps = [
      ...shown.map((step, index) =>
        this.stepOut(
          step,
          index < stage.running
            ? { state: item.skipped.has(step) ? 'skipped' : 'done' }
            : { state: 'running' },
        ),
      ),
      ...(stage.state === 'pending'
        ? item.ahead.map((step) => this.stepOut(step, { state: 'pending' }))
        : []),
    ];
    if (stage.state === 'applied' || stage.state === 'succeeded') this.applyOnce(item);
    out.updated_at = this.iso();
  }

  private applyOnce(item: MockRequest): void {
    if (item.applied || !item.apply) return;
    item.applied = true;
    item.apply();
  }

  private finish(item: MockRequest, final: Final): void {
    const out = item.out;
    out.state = final.state;
    out.terminal = true;
    [out.label_tr, out.label_en] = STATE_LABELS[final.state];
    out.steps = (out.kind.startsWith('worker.') ? [] : final.steps).map((step) =>
      this.stepOut(
        step,
        final.ends?.[step] ?? { state: item.skipped.has(step) ? 'skipped' : 'done' },
      ),
    );
    if (final.error) {
      const { code, message_tr, message_en } = message(final.error);
      out.error = { code, message_tr, message_en, field: null };
    }
    // A task or the worker reports its outcome also when it failed (`{ok: false, …}`).
    if (item.result) out.result = copy(item.result);
    out.rolled_back = final.rolledBack ?? false;
    if (final.manual) [out.manual_steps_tr, out.manual_steps_en] = final.manual;
    for (const code of final.warnings ?? [])
      if (!out.warnings.some((warning) => warning.code === code)) out.warnings.push(message(code));
    if (['active', 'partially_active', 'succeeded'].includes(final.state)) this.applyOnce(item);
    out.updated_at = this.iso();
  }

  /** The state of a request while `step` runs (a hold that does not hold is passed through). */
  private stateOf(step: RequestStep, held: boolean): RequestState {
    if (step === 'probe') return 'probing';
    if (step === 'hold') return held ? 'pending' : 'received';
    if (step === 'dispatch') return 'dispatched';
    if (step === 'task') return 'running';
    if (APPLYING_STEPS.has(step)) return 'applying';
    if (ACTIVATION_STEPS.includes(step)) return 'applied';
    return 'received';
  }

  /** Stages of a request through `steps`, and how it ends. */
  private plan(
    steps: RequestStep[],
    outcome: Outcome,
    kind: RequestKind,
    held: boolean,
  ): { steps: RequestStep[]; stages: Stage[]; final: Final; apply: boolean } {
    const action = kind.startsWith('action.') || kind.startsWith('worker.');
    const stages: Stage[] = [{ at: 0, state: 'queued', running: -1 }];
    const staged = (list: RequestStep[]) => {
      let at = this.stepMs;
      list.forEach((step, index) => {
        stages.push({ at, state: this.stateOf(step, held), running: index });
        at += this.stepMs;
      });
    };
    if (outcome === 'expired')
      return {
        steps: [],
        stages,
        final: { state: 'expired', error: 'request_expired', steps: [] },
        apply: false,
      };
    if (outcome === 'rejected')
      return {
        steps: ['received'],
        stages,
        final: {
          state: 'rejected',
          error: 'signature_invalid',
          steps: ['received'],
          ends: { received: { state: 'failed', code: 'signature_invalid' } },
        },
        apply: false,
      };
    let list = [...steps];
    let final: Final = { state: action ? 'succeeded' : 'active', steps: list };
    let apply = true;
    if (outcome === 'probe_failed' && list.includes('probe')) {
      // The agent stops at the probe: nothing after it runs.
      list = list.slice(0, list.indexOf('probe') + 1);
      final = {
        state: 'probe_failed',
        error: 'probe_failed',
        steps: list,
        ends: { probe: { state: 'failed', code: 'probe_failed' } },
      };
      apply = false;
    } else if (outcome === 'conflict') {
      list = list.slice(0, 2);
      final = {
        state: 'conflict',
        error: 'conflict',
        steps: list,
        ends: { validate: { state: 'failed', code: 'conflict' } },
      };
      apply = false;
    } else if (outcome === 'failed' || outcome === 'needs_attention') {
      list = [...list.slice(0, list.indexOf('verify') + 1), 'rollback', 'journal'];
      final =
        outcome === 'failed'
          ? {
              state: 'failed',
              error: 'verify_failed',
              rolledBack: true,
              steps: list,
              ends: { verify: { state: 'failed', code: 'verify_failed' } },
            }
          : {
              state: 'needs_attention',
              error: 'rollback_failed',
              steps: list,
              ends: {
                verify: { state: 'failed', code: 'verify_failed' },
                rollback: { state: 'failed', code: 'rollback_failed' },
              },
              // `admin_channel.manual_steps` of the real API, one command a line.
              manual: [
                '1. Sunucuda yönetici olarak bir PowerShell penceresi açın.\n2. Önceki ayarları geri yükleyin (yedek sürüm 17):\n   & "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin versions restore 17\n3. Durumu denetleyin:\n   & "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin status',
                '1. Open a PowerShell window as an administrator on the server.\n2. Put the previous settings back (backup version 17):\n   & "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin versions restore 17\n3. Check the state:\n   & "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin status',
              ],
            };
      apply = false;
    } else if (outcome === 'partially_active') {
      // The last service of the request has not loaded it in time; a system step failed.
      const reloads = list.filter((step) => step.startsWith('reload.'));
      const ends: Partial<Record<RequestStep, StepEnd>> = {};
      const last = reloads.at(-1);
      if (last) ends[last] = { state: 'warning', code: 'service_not_reloaded' };
      for (const step of list.filter((item) => item.startsWith('system.')))
        ends[step] = { state: 'failed', code: 'system_sync_failed' };
      final = { state: 'partially_active', steps: list, ends, warnings: ['service_not_reloaded'] };
    }
    staged(list);
    return { steps: list, stages, final, apply };
  }

  private enqueue(init: {
    kind: RequestKind;
    section: AdminSection | null;
    account: MockUser;
    steps: RequestStep[];
    skipped?: RequestStep[];
    ahead?: RequestStep[];
    changes?: Record<string, Change>;
    warnings?: string[];
    effects?: string[];
    apply?: () => void;
    before?: unknown;
    outcome?: Outcome;
    hold?: number | null;
    result?: Record<string, unknown> | null;
    failure?: { state: RequestState; error: string };
    day?: string;
  }): MockRequest {
    const id = newId();
    const outcome = init.outcome ?? 'active';
    const held = init.hold !== undefined && init.hold !== null;
    const planned = this.plan(init.steps, outcome, init.kind, held);
    if (held) {
      const hold = planned.steps.indexOf('hold');
      planned.stages = planned.stages.filter((stage) => stage.running <= hold);
    }
    if (init.failure)
      planned.final = {
        state: init.failure.state,
        error: init.failure.error,
        steps: planned.steps,
        ends: { task: { state: 'failed', code: init.failure.error } },
      };
    const out: AdminRequestOut = {
      id,
      short_id: id.slice(0, 8),
      kind: init.kind,
      section: init.section,
      state: 'queued',
      terminal: false,
      label_tr: STATE_LABELS.queued[0],
      label_en: STATE_LABELS.queued[1],
      requested_by: init.account.username,
      created_at: this.iso(),
      updated_at: this.iso(),
      effective_at: held ? this.iso(init.hold ?? 0) : null,
      version: null,
      steps: [],
      changes: init.changes ?? {},
      result: null,
      error: null,
      rolled_back: false,
      warnings: (init.warnings ?? []).map(message),
      manual_steps_tr: null,
      manual_steps_en: null,
    };
    const item: MockRequest = {
      out,
      started: Date.now(),
      stages: planned.stages,
      final: planned.final,
      steps: planned.steps,
      skipped: new Set([...(init.skipped ?? []), ...(held ? [] : (['hold'] as RequestStep[]))]),
      ahead: init.ahead ?? [],
      apply: planned.apply && !init.failure ? (init.apply ?? null) : null,
      applied: false,
      held,
      cancelled: false,
      before: init.before ?? null,
      day: init.day ?? null,
      effects: init.effects ?? [],
      result: init.result ?? null,
    };
    this.requests.set(id, item);
    return item;
  }

  private accepted(item: MockRequest): [number, AdminAccepted] {
    return [
      202,
      {
        request_id: item.out.id,
        short_id: item.out.short_id,
        state: 'queued',
        status_url: `/api/v1/admin/requests/${item.out.id}`,
        guarded: item.held,
        effective_at: item.out.effective_at,
        changes: copy(item.out.changes),
        effects: [...item.effects],
        warnings: copy(item.out.warnings),
      },
    ];
  }

  /**
   * A configuration change applied: a new version keeps the pair from before it. The newest 20
   * configurations are kept, and `secrets.json` only with the newest 5 (`has_secrets`).
   */
  private recordVersion(item: MockRequest, account: MockUser): void {
    const n = (this.versions[0]?.n ?? 16) + 1;
    this.versions.unshift({
      n,
      at: this.iso(),
      request_id: item.out.id,
      actor: account.username,
      paths: Object.keys(item.out.changes),
      has_secrets: true,
    });
    this.versions.splice(20);
    for (const version of this.versions.slice(5)) version.has_secrets = false;
    item.out.version = { number: n, from: sha256(`${n}-from`), to: sha256(`${n}-to`) };
  }

  private takeOutcome(): Outcome {
    const outcome = this.nextOutcome ?? 'active';
    this.nextOutcome = null;
    return outcome;
  }

  private takeAction(): ActionOutcome {
    const outcome = this.nextAction ?? 'succeeded';
    this.nextAction = null;
    return outcome;
  }

  // -- the common path of a change (contract 2.11.1, `routers/admin_config.py`) -----------------

  /**
   * The check of a change before its rules: the `PUT` first needs browser administration on
   * (503 `admin_disabled`); both need the section's current `etag` (409 `config_conflict`).
   */
  private stale(section: AdminSection, etag: unknown, dryRun: boolean): [number, unknown] | null {
    if (!dryRun && this.agent === 'disabled') return this.reject('admin_disabled');
    if (etag !== this.etag(section))
      return this.reject('config_conflict', { current_etag: this.etag(section) });
    return null;
  }

  /**
   * The answer of a checked change: the dry run's `AdminCheck`, or the change queued after the
   * API's last refusals in their order (nothing changes: 422 `config_unchanged`; the password
   * confirmation; the administration service; a change of the section still open).
   */
  private change(
    call: RecordedCall,
    host: MockHost,
    dryRun: boolean,
    account: MockUser,
    plan: {
      section: AdminSection;
      changes: Record<string, Change>;
      warnings: string[];
      effects: string[];
      reauth: boolean;
      probe: boolean;
      secrets?: boolean;
      installs?: boolean;
      /** Held for `policy.hold_hours` (a guarded change, contract 2.3). */
      held?: boolean;
      /** Held for `hold` ms instead (a Zamane client for `jar_hold_hours`). */
      hold?: number | null;
      ahead?: RequestStep[];
      check?: Partial<AdminCheck>;
      next: () => void;
      cancel?: () => void;
    },
  ): Answer {
    const hold =
      plan.hold !== undefined
        ? plan.hold
        : plan.held && this.holdHours > 0
          ? this.holdHours * 3_600_000
          : null;
    const effects = [...plan.effects].sort();
    if (dryRun)
      return [
        200,
        {
          changes: plan.changes,
          effects,
          warnings: plan.warnings.map(message),
          guarded: hold !== null,
          effective_at: hold === null ? null : this.iso(hold),
          reauth_required: plan.reauth,
          retention_preview: null,
          readiness: null,
          ...plan.check,
        } satisfies AdminCheck,
      ];
    if (Object.keys(plan.changes).length === 0) return this.reject('config_unchanged');
    if (plan.reauth) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    if (this.agent === 'missing') return this.reject('admin_agent_unavailable');
    if (this.busy(plan.section, 'tsa.zamane_jar' in plan.changes))
      return this.reject('admin_section_busy');
    const skipped: RequestStep[] = [
      ...(plan.secrets ? [] : (['write_secrets'] as RequestStep[])),
      ...(plan.installs ? [] : (['install_files'] as RequestStep[])),
    ];
    const steps = [
      ...AGENT_STEPS.filter((step) => step !== 'probe' || plan.probe),
      ...ACTIVATION_STEPS.filter((step) => effects.includes(step)),
    ];
    const item = this.enqueue({
      kind: 'config.apply',
      section: plan.section,
      account,
      steps,
      skipped,
      ahead: plan.ahead ?? [],
      changes: plan.changes,
      effects,
      warnings: [
        ...plan.warnings,
        ...(hold !== null && plan.hold === undefined ? ['change_held'] : []),
        ...(this.agent === 'stale' ? ['agent_stale'] : []),
      ],
      outcome: this.takeOutcome(),
      hold,
      before: copy(this.sections[plan.section]),
      apply: () => {
        plan.next();
        this.recordVersion(item, account);
      },
    });
    if (plan.cancel) item.cancel = plan.cancel;
    // The agent reports the check's warnings at its validation step.
    item.final.ends = {
      ...(plan.warnings.length > 0 ? { validate: { state: 'warning' } } : {}),
      ...item.final.ends,
    };
    return this.accepted(item);
  }

  // -- the general section ----------------------------------------------------------------------

  private general(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as GeneralBody;
    const refusal = this.stale('general', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.general;
    const problems: FieldProblem[] = [];
    // A field left out stays as it is; a clock server sent as `null` too (`GeneralBody.form`).
    const name = body.site_name === undefined ? current.site_name : (text(body.site_name) ?? '');
    if (body.site_name !== undefined && !name)
      problems.push(problem('site_name', 'site_name_required'));
    const clock = body.clock ?? {};
    const ntp =
      typeof clock.ntp_server === 'string' ? clock.ntp_server.trim() : current.clock.ntp_server;
    const fallback =
      typeof clock.fallback_server === 'string'
        ? clock.fallback_server.trim()
        : current.clock.fallback_server;
    const offset = clock.alert_offset_ms ?? current.clock.alert_offset_ms;
    if (ntp && !validHost(ntp)) problems.push(problem('clock.ntp_server', 'host_invalid'));
    if (fallback && !validHost(fallback))
      problems.push(problem('clock.fallback_server', 'host_invalid'));
    if (!ntp && !fallback)
      problems.push(problem('clock.fallback_server', 'clock_servers_required'));
    if (!integer(offset, 1, 60_000))
      problems.push(problem('clock.alert_offset_ms', 'config_invalid'));
    if (problems.length > 0) return this.refused(problems);
    const next = {
      site_name: name,
      clock: { ntp_server: ntp, fallback_server: fallback, alert_offset_ms: offset },
    };
    const changes = {
      ...diff('', { site_name: current.site_name }, { site_name: next.site_name }),
      ...diff('clock', { ...current.clock }, next.clock),
    };
    const clockChanged = Object.keys(changes).some((path) => path.startsWith('clock.'));
    return this.change(call, host, dryRun, account, {
      section: 'general',
      changes,
      warnings: !ntp && fallback && clockChanged ? ['ntp_server_empty'] : [],
      effects: [
        ...(changes.site_name ? ['reload.api', 'reload.worker'] : []),
        ...(clockChanged ? ['system.time', 'task.next_run:ClockCheck'] : []),
      ],
      reauth: clockChanged,
      probe: clockChanged && body.probe !== false && Boolean(ntp),
      next: () => {
        this.sections.general = next;
      },
    });
  }

  // -- uploads (contract 2.4.4, 2.6 "tsa") -----------------------------------------------------

  /**
   * `POST /admin/uploads?kind=`: the captured inspection of an upload of its kind
   * (`ADMIN.kamusm.upload_root`, `upload_zamane`, `ADMIN.sections.upload_smtp_ca`), for the file
   * the spec sent: its size and SHA-256, a subject the PEM names, an expired certificate (the
   * text `expired`), a known Zamane release (the text `known-release`).
   */
  private upload(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const kind = call.query.get('kind');
    if (kind !== 'kamusm_root' && kind !== 'zamane_client' && kind !== 'smtp_ca')
      return this.reject('upload_kind_invalid');
    const file = multipartFile(call);
    if (!file)
      return host.error(422, 'validation_error', [
        { loc: ['body', 'file'], type: 'missing', msg: 'Field required' },
      ]);
    const size = Buffer.byteLength(file.content);
    if (size > UPLOAD_LIMITS[kind]) return this.reject('upload_too_large');
    const digest = sha256(file.content);
    const captured =
      kind === 'zamane_client'
        ? ADMIN.kamusm.upload_zamane
        : kind === 'smtp_ca'
          ? ADMIN.sections.upload_smtp_ca
          : ADMIN.kamusm.upload_root;
    const preview: UploadPreview = {
      ...copy(captured),
      upload_id: newId(),
      size,
      sha256: digest,
      expires_at: this.iso(30 * 60_000),
    };
    if (kind === 'zamane_client') {
      const zip = file.name.toLowerCase().endsWith('.zip');
      if ((!zip && !file.name.toLowerCase().endsWith('.jar')) || file.content.includes('broken'))
        return this.reject('zamane_archive_invalid');
      const known = file.content.includes('known-release');
      preview.zamane = {
        ...copy(ADMIN.kamusm.upload_zamane.zamane ?? null),
        kind: zip ? 'zip' : 'jar',
        jar_name: zip ? 'tss-client-console-3.1.4.jar' : null,
        jar_sha256: zip ? sha256(`${file.content} jar`) : digest,
        release: known ? '3.1.4' : null,
        known,
        accepted: true,
        reason: known ? null : 'unknown_jar_held',
      };
    } else {
      if (!file.content.includes('BEGIN CERTIFICATE'))
        return this.reject('root_certificate_invalid');
      const expired = file.content.includes('expired');
      const certificate = copy(captured.certificate);
      if (!certificate) throw new Error('mock-admin: the captured upload has no certificate');
      const subject = /^Subject: (.+)$/m.exec(file.content)?.[1] ?? certificate.subject;
      preview.certificate = {
        ...certificate,
        subject,
        issuer: subject,
        ...(expired
          ? { not_before: '2021-06-01T00:00:00Z', not_after: '2025-06-01T00:00:00Z' }
          : {}),
        installed:
          kind === 'kamusm_root' && this.sections.tsa.roots.some((root) => root.sha256 === digest),
        warnings: expired ? [message('root_expired')] : [],
      };
    }
    this.uploads.set(preview.upload_id, preview);
    return [201, copy(preview)];
  }

  /** An upload of `kind` of this mock (404 `upload_not_found` otherwise, like another's). */
  private staged(id: unknown, kind: UploadPreview['kind']): UploadPreview | null {
    const upload = this.uploads.get(typeof id === 'string' ? id : '');
    return upload?.kind === kind ? upload : null;
  }

  // -- the Kamu SM section ----------------------------------------------------------------------

  /** `readiness` and `seal_ready` of a Kamu SM section (`tsa.preflight`, simplified). */
  private refreshTsa(tsa: TsaSection = this.sections.tsa): TsaSection {
    tsa.readiness = { production: false, missing: tsaMissing(tsa) };
    tsa.readiness.production = tsa.readiness.missing.length === 0;
    const causes = tsa.provider === 'zamane' ? tsa.readiness.missing.map(causeOf) : [];
    tsa.seal_ready = { ok: causes.length === 0, causes };
    return tsa;
  }

  private tsaTested(): boolean {
    return [...this.requests.values()].some(
      (item) => item.out.kind === 'action.tsa_test' && item.out.state === 'succeeded',
    );
  }

  private tsa(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as TsaBody;
    const refusal = this.stale('tsa', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.tsa;
    const server = body.server ?? current.server;
    if (server === 'mock') return this.refused([problem('tsa.provider', 'tsa_mock_refused')]);
    if (server !== 'test' && server !== 'production')
      return this.refused([problem('tsa.provider', 'tsa_server_invalid')]);
    const problems: FieldProblem[] = [];
    const threshold = body.credit_alert_threshold ?? current.credit_alert_threshold;
    if (!integer(threshold, 0, 1_000_000))
      problems.push(problem('tsa.credit_alert_threshold', 'config_invalid'));
    const timeout = body.timeout_seconds ?? current.timeout_seconds;
    if (!integer(timeout, 5, 600)) problems.push(problem('tsa.timeout_seconds', 'config_invalid'));
    const java = body.java === undefined ? current.java : body.java;
    if (java !== null && !JAVA_CHOICES.includes(java))
      problems.push(problem('tsa.java', 'java_not_detected'));
    const customer = typeof body.customer === 'string' ? body.customer : null;
    const password = typeof body.password === 'string' ? body.password : null;
    if (customer !== null && !/^[^\s\p{Cc}\p{Cf}]{1,64}$/u.test(customer))
      problems.push(problem('kamusm.customer', 'secret_invalid'));
    if (password !== null && !/^[^\p{Cc}\p{Cf}]{1,128}$/u.test(password))
      problems.push(problem('kamusm.password', 'secret_invalid'));
    if (problems.length > 0) return this.refused(problems);
    const clear = body.clear_credentials === true;
    const candidate = copy(current);
    candidate.provider = 'zamane';
    candidate.server = server;
    candidate.java = java;
    candidate.credit_alert_threshold = threshold;
    candidate.timeout_seconds = timeout;
    const by = `web:${account.username}`;
    const secretChanges: Record<string, Change> = {};
    for (const [name, value] of [
      ['kamusm.customer', customer],
      ['kamusm.password', password],
    ] as const) {
      const was = current.secrets[name]?.set ? 'set' : 'unset';
      if (clear && was === 'set') {
        candidate.secrets[name] = { set: false, changed_at: this.iso(), changed_by: by };
        secretChanges[name] = { secret: true, old: was, new: 'deleted', changed: true };
      } else if (!clear && value !== null) {
        candidate.secrets[name] = { set: true, changed_at: this.iso(), changed_by: by };
        secretChanges[name] = { secret: true, old: was, new: 'set', changed: true };
      }
    }
    this.refreshTsa(candidate);
    const production = current.provider === 'zamane' && current.server === 'production';
    if (server === 'production' && !candidate.readiness.production)
      return this.refused([problem('tsa.provider', 'tsa_not_ready')], {
        missing: candidate.readiness.missing,
      });
    if (production && server === 'test' && body.confirm !== true)
      return this.refused([problem('tsa.provider', 'tsa_confirm_required')]);
    if (current.seal_ready.ok && !candidate.seal_ready.ok)
      return this.refused([problem('tsa.provider', 'tsa_would_break')], {
        causes: candidate.seal_ready.causes,
      });
    const pick = (tsa: TsaSection) => ({
      provider: tsa.provider,
      server: tsa.server,
      credit_alert_threshold: tsa.credit_alert_threshold,
      timeout_seconds: tsa.timeout_seconds,
      java: tsa.java,
    });
    const changes: Record<string, Change> = {
      ...diff('tsa', pick(current), pick(candidate)),
      ...secretChanges,
    };
    const reauth = Object.keys(changes).some(
      (path) => path !== 'tsa.credit_alert_threshold' && path !== 'tsa.timeout_seconds',
    );
    const warnings: string[] = [];
    if (server === 'production' && !production) {
      if (!candidate.roots.some((root) => root.legal)) warnings.push('legal_roots_empty');
      if (!this.tsaTested()) warnings.push('production_not_tested');
    }
    if (production && server === 'test') warnings.push('production_stamps_stop');
    const secrets = Object.keys(secretChanges).length > 0;
    return this.change(call, host, dryRun, account, {
      section: 'tsa',
      changes,
      warnings,
      effects: ['reload.api', 'reload.worker', ...(reauth ? ['task.next_run:Seal'] : [])],
      reauth,
      probe:
        body.probe !== false &&
        (secrets || Boolean(changes['tsa.server'] ?? changes['tsa.provider'])),
      secrets,
      check: { readiness: candidate.readiness },
      next: () => {
        // Uploads applied meanwhile (roots, a client) stay.
        const now = this.sections.tsa;
        this.sections.tsa = this.refreshTsa({ ...candidate, roots: now.roots, zamane: now.zamane });
      },
    });
  }

  /** `tsa.legal_root_fingerprints` before and after `sha` is added. */
  private legalRoots(sha: string): Change {
    const old = this.sections.tsa.roots.filter((root) => root.legal).map((root) => root.sha256);
    return { old, new: [...old, sha] };
  }

  private addRoot(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as RootBody;
    const refusal = this.stale('tsa', body.etag, false);
    if (refusal) return refusal;
    const upload = this.staged(body.upload_id, 'kamusm_root');
    const certificate = upload?.certificate;
    if (!upload || !certificate) return this.reject('upload_not_found');
    if (this.sections.tsa.roots.some((root) => root.sha256 === upload.sha256))
      return this.refused([problem('tsa.roots_dir', 'root_exists')]);
    const legal = body.legal === true;
    if (legal && body.confirmed !== true)
      return this.refused([problem('tsa.legal_root_fingerprints', 'legal_root_confirm_required')]);
    const warnings = [
      ...certificate.warnings.map((warning) => warning.code),
      ...(!legal && !this.sections.tsa.roots.some((root) => root.legal)
        ? ['legal_roots_empty']
        : []),
    ];
    return this.change(call, host, false, account, {
      section: 'tsa',
      changes: {
        ...(legal ? { 'tsa.legal_root_fingerprints': this.legalRoots(upload.sha256) } : {}),
        [`tsa.roots.${upload.sha256}`]: { old: null, new: 'installed' },
      },
      warnings,
      effects: ['reload.api', 'reload.worker', 'task.next_run:Seal'],
      reauth: true,
      probe: false,
      installs: true,
      next: () => {
        this.sections.tsa.roots.push({
          sha256: upload.sha256,
          subject: certificate.subject,
          not_after: certificate.not_after,
          legal,
          source: 'web',
        });
        this.uploads.delete(upload.upload_id);
        this.refreshTsa();
      },
    });
  }

  private addLegalRoot(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as LegalRootBody;
    const refusal = this.stale('tsa', body.etag, false);
    if (refusal) return refusal;
    const root = this.sections.tsa.roots.find((item) => item.sha256 === body.sha256);
    if (!root) return this.refused([problem('tsa.legal_root_fingerprints', 'root_not_installed')]);
    if (body.confirmed !== true)
      return this.refused([problem('tsa.legal_root_fingerprints', 'legal_root_confirm_required')]);
    return this.change(call, host, false, account, {
      section: 'tsa',
      changes: { 'tsa.legal_root_fingerprints': this.legalRoots(root.sha256) },
      warnings: [],
      effects: ['reload.api', 'reload.worker', 'task.next_run:Seal'],
      reauth: true,
      probe: false,
      next: () => {
        const installed = this.sections.tsa.roots.find((entry) => entry.sha256 === root.sha256);
        if (installed) installed.legal = true;
      },
    });
  }

  private stageZamane(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as ZamaneBody;
    const refusal = this.stale('tsa', body.etag, false);
    if (refusal) return refusal;
    const upload = this.staged(body.upload_id, 'zamane_client');
    const zamane = upload?.zamane;
    if (!upload || !zamane) return this.reject('upload_not_found');
    if (!zamane.known) {
      if (typeof body.expected_sha256 !== 'string')
        return this.refused([problem('tsa.zamane_jar', 'zamane_sha256_required')]);
      if (body.expected_sha256.toLowerCase() !== upload.sha256)
        return this.refused([problem('tsa.zamane_jar', 'zamane_sha256_mismatch')]);
    }
    const hold = zamane.known ? null : this.config().policy.jar_hold_hours * 3_600_000;
    const tsa = this.sections.tsa;
    // A known release is promoted at once (`zamane\web\`), another after the console approves it.
    const path = `zamane/${zamane.known ? 'web' : 'approved'}/${zamane.jar_sha256}.jar`;
    const staged = {
      sha256: zamane.jar_sha256,
      effective_at: hold === null ? null : this.iso(hold),
    };
    const answer = this.change(call, host, false, account, {
      section: 'tsa',
      changes: {
        'tsa.zamane_jar': {
          old: tsa.zamane.present ? `zamane/${tsa.zamane.sha256 ?? ''}.jar` : null,
          new: path,
        },
      },
      warnings: zamane.known ? [] : ['unknown_jar_held'],
      effects: ['jar.promotion', 'reload.api', 'reload.worker', 'task.next_run:Seal'],
      reauth: true,
      probe: false,
      installs: true,
      hold,
      ahead: ['jar.promotion'],
      next: () => {
        this.sections.tsa.zamane = {
          present: true,
          sha256: zamane.jar_sha256,
          release: zamane.release,
          trusted: 'known',
          staged: this.sections.tsa.zamane.staged.filter(
            (entry) => entry.sha256 !== zamane.jar_sha256,
          ),
        };
        this.uploads.delete(upload.upload_id);
        this.refreshTsa();
      },
      cancel: () => {
        this.sections.tsa.zamane.staged = this.sections.tsa.zamane.staged.filter(
          (entry) => entry.sha256 !== zamane.jar_sha256,
        );
      },
    });
    if (answer[0] === 202 && hold !== null) tsa.zamane.staged.push(staged);
    return answer;
  }

  private tsaTest(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as TsaTestBody;
    if (body.confirm_credit !== true) return this.reject('tsa_test_confirm_required');
    if (this.agent === 'disabled') return this.reject('admin_disabled');
    // One every 5 minutes, then `policy.tsa_test_per_day` a site day (WP4-3).
    const last = this.lastAction.get('action.tsa_test');
    if (last !== undefined && Date.now() - last < 5 * 60_000)
      return this.limited(
        'admin_rate_limited',
        Math.ceil((5 * 60_000 - (Date.now() - last)) / 1000),
      );
    const today = siteDay(this.now());
    const done = [...this.requests.values()].filter(
      (item) =>
        item.out.kind === 'action.tsa_test' && siteDay(new Date(item.out.created_at)) === today,
    ).length;
    if (done >= this.config().policy.tsa_test_per_day) {
      const midnight = Date.parse(`${today}T00:00:00+03:00`) + 86_400_000;
      return this.limited('tsa_test_limit', Math.ceil((midnight - this.now().getTime()) / 1000));
    }
    if (this.agent === 'missing') return this.reject('admin_agent_unavailable');
    this.lastAction.set('action.tsa_test', Date.now());
    const tsa = this.sections.tsa;
    const legal = tsa.roots.find((root) => root.legal) ?? null;
    const root = legal ?? tsa.roots[0] ?? null;
    const outcome = this.takeAction();
    // The captured result of the capture's test stamp, as the site's Kamu SM test server gives it.
    const result: TsaTestResult = {
      ...(copy(ADMIN.kamusm.tsa_test_done.result) as unknown as TsaTestResult),
      provider: tsa.provider,
      legal_grade_claimed: legal !== null,
      legal_grade: legal !== null,
      root_sha256: root?.sha256 ?? null,
      root: root?.subject ?? null,
      gen_time: this.iso(),
      serial: sha256(`tsa test ${String(done)}`)
        .slice(0, 16)
        .toUpperCase(),
      tsa: 'CN=Kamu SM Zaman Damgası Test Sunucusu (örnek)',
      tsa_cert_not_after: '2031-01-01T00:00:00Z',
    };
    const item = this.enqueue({
      kind: 'action.tsa_test',
      section: null,
      account,
      steps: [...ACTION_STEPS],
      result: outcome === 'succeeded' ? { ...result } : null,
      ...(outcome === 'succeeded'
        ? {}
        : {
            failure:
              outcome === 'timed_out'
                ? { state: 'timed_out' as RequestState, error: 'task_timeout' }
                : { state: 'failed' as RequestState, error: 'tsa_test_failed' },
          }),
    });
    return this.accepted(item);
  }

  // -- sources (contract 2.6 "sources") ---------------------------------------------------------

  /** The refusals of a source (`editing`: the name of the source it replaces), `[]` if none. */
  private sourceProblems(value: SourceConfig, editing: string | null): FieldProblem[] {
    const at = (key: string) => `sources.${value.name}.${key}`;
    const others = this.sections.sources.filter((item) => item.name !== editing);
    if (editing === null) {
      if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(value.name) || value.name.startsWith('unknown-'))
        return [problem(at('name'), 'source_invalid')];
      if (others.some((item) => item.name === value.name))
        return [problem(at('name'), 'source_name_taken')];
      if (this.sections.sources.length >= this.config().policy.max_sources)
        return [problem('sources', 'source_limit')];
    }
    const problems: FieldProblem[] = [];
    if (!integer(value.heartbeat_minutes, 1, 1440))
      problems.push(problem(at('heartbeat_minutes'), 'source_invalid'));
    const address = (code: string) => [...problems, problem(at('address'), code)];
    if (value.address.includes(':')) return address('source_ipv6_unsupported');
    const network = networkOf(value.address);
    if (!network) return address('source_invalid');
    if (
      RESERVED_NETWORKS.some((reserved) => overlap(reserved, network)) ||
      value.address === '255.255.255.255'
    )
      return address('source_reserved_address');
    if (LOCAL_ADDRESSES.includes(value.address)) return address('source_own_address');
    if (!nets(...this.config().policy.source_networks).some((allowed) => inside(allowed, network)))
      return address('source_network_not_allowed');
    if (network.prefix < this.config().policy.min_prefix_v4)
      return address('source_prefix_too_short');
    const host = (text: string) => text.replace(/\/32$/, '');
    if (others.some((item) => host(item.address) === host(value.address)))
      return address('source_address_taken');
    if (
      network.prefix < 32 &&
      others.some((item) => {
        const other = networkOf(item.address);
        return other !== null && other.prefix < 32 && overlap(other, network);
      })
    )
      return address('source_overlap');
    return problems;
  }

  private sourceChange(
    call: RecordedCall,
    host: MockHost,
    mode: 'check' | 'add' | 'update',
    path: string | null,
  ): Answer {
    const body = call.body as SourceCheckBody;
    // The check says which change it is (WP5-1): an addition, or the change of a known name.
    const operation = mode === 'check' ? body.operation : mode;
    const name = mode === 'update' ? path : body.source.name;
    const existing = this.sections.sources.find((item) => item.name === name) ?? null;
    const account = host.requirePermission(call, 'sources.manage');
    if (Array.isArray(account)) return account;
    const refusal = this.stale('sources', body.etag, mode === 'check');
    if (refusal) return refusal;
    // A field left out takes the `SourceConfig` default.
    const source: SourceConfig = {
      heartbeat_minutes: 15,
      expected: true,
      transport: 'any',
      enabled: true,
      ...body.source,
    };
    if (operation === 'update') {
      if (!existing) return this.refused([problem('sources', 'source_not_found')]);
      if (source.name !== existing.name)
        return this.refused([
          problem(`sources.${existing.name}.name`, 'source_rename_not_allowed'),
        ]);
    }
    const editing = operation === 'add' ? null : (existing?.name ?? null);
    const problems = this.sourceProblems(source, editing);
    if (problems.length > 0) return this.refused(problems);
    const before = editing === null || !existing ? null : sourceTable(existing);
    const after = sourceTable(source);
    const changes: Record<string, Change> =
      JSON.stringify(before) === JSON.stringify(after)
        ? {}
        : { [`sources.${source.name}`]: { old: before, new: after } };
    const disabling = existing !== null && existing.enabled && !source.enabled;
    return this.change(call, host, mode === 'check', account, {
      section: 'sources',
      changes,
      warnings: disabling && existing.expected ? ['source_disable_gaps'] : [],
      effects: ['reload.api', 'reload.collector', 'reload.ingest', 'system.firewall'],
      reauth: disabling,
      probe: false,
      next: () => {
        const row: SourceOut = {
          ...source,
          firewall: source.enabled ? 'open' : 'closed',
          last_seen: existing?.last_seen ?? null,
        };
        const at = this.sections.sources.findIndex((entry) => entry.name === source.name);
        if (at >= 0) this.sections.sources[at] = row;
        else this.sections.sources.push(row);
      },
    });
  }

  private removeSource(call: RecordedCall, host: MockHost, name: string): Answer {
    const account = host.requirePermission(call, 'sources.manage');
    if (Array.isArray(account)) return account;
    const refusal = this.stale('sources', call.query.get('etag'), false);
    if (refusal) return refusal;
    const existing = this.sections.sources.find((item) => item.name === name);
    if (!existing) return this.refused([problem('sources', 'source_not_found')]);
    if (call.query.get('confirm') !== name)
      return this.refused([problem(`sources.${name}`, 'source_confirm_mismatch')]);
    return this.change(call, host, false, account, {
      section: 'sources',
      changes: { [`sources.${name}`]: { old: sourceTable(existing), new: null } },
      warnings: existing.expected && existing.enabled ? ['source_disable_gaps'] : [],
      effects: ['reload.api', 'reload.collector', 'reload.ingest', 'system.firewall'],
      reauth: true,
      probe: false,
      next: () => {
        this.sections.sources = this.sections.sources.filter((entry) => entry.name !== name);
      },
    });
  }

  // -- smtp, replica, retention, backup (contract 2.6) ------------------------------------------

  /** `{secret: true, old, new}` of a write-only field (`null`: unchanged). */
  private secretChange(
    meta: { set: boolean } | undefined,
    value: unknown,
    clear: boolean,
  ): Change | null {
    const was = meta?.set ? 'set' : 'unset';
    if (clear && was === 'set') return { secret: true, old: was, new: 'deleted', changed: true };
    if (typeof value === 'string' && value !== '')
      return { secret: true, old: was, new: 'set', changed: true };
    return null;
  }

  /**
   * The SMTP rules (contract 2.6 "smtp"): the account, the trust (the CA file is part of the
   * server's identity while a user name is set, WP1-10) and the trial e-mail of a new server.
   */
  private smtpRules(
    body: SmtpBody | SmtpCaBody | SmtpCaClear,
    next: SmtpSection,
    account: MockUser,
  ): { problems: FieldProblem[]; secret: Change | null; probe: boolean; warnings: string[] } {
    const current = this.sections.smtp;
    const problems: FieldProblem[] = [];
    if (!integer(next.port, 1, 65_535)) problems.push(problem('smtp.port', 'config_invalid'));
    if (!integer(next.timeout_seconds, 1, 300))
      problems.push(problem('smtp.timeout_seconds', 'config_invalid'));
    if (next.host !== null && !validHost(next.host))
      problems.push(problem('smtp.host', 'host_invalid'));
    if (next.from_address !== null && !EMAIL.test(next.from_address))
      problems.push(problem('smtp.from_address', 'config_invalid'));
    if (next.enabled && next.host === null) problems.push(problem('smtp.host', 'config_invalid'));
    if (next.enabled && next.from_address === null)
      problems.push(problem('smtp.from_address', 'config_invalid'));
    const stored = current.secrets['smtp.password']?.set ?? false;
    const password = 'password' in body ? body.password : null;
    const typed = typeof password === 'string' && password !== '';
    const clearing = 'clear_password' in body && body.clear_password === true;
    const clear = clearing || (next.username === null && stored);
    const endpoint =
      next.host !== current.host ||
      next.port !== current.port ||
      next.security !== current.security ||
      next.username !== current.username ||
      next.ca.sha256 !== current.ca.sha256;
    if (next.username !== null) {
      if (!typed && (!stored || clearing))
        problems.push(problem('smtp.password', 'smtp_password_required'));
      else if (!typed && endpoint)
        problems.push(problem('smtp.password', 'secret_rebind_required'));
      if (next.security === 'none' && !['localhost', '127.0.0.1', '::1'].includes(next.host ?? ''))
        problems.push(problem('smtp.security', 'smtp_plaintext_auth_refused'));
    }
    const probe = next.enabled && (!current.enabled || endpoint) && body.probe !== false;
    if (probe && problems.length === 0) {
      const to = typeof body.test_to === 'string' ? body.test_to : null;
      if (to === null) problems.push(problem('test_to', 'smtp_test_recipient_required'));
      else if (!this.recipients(account).includes(to))
        problems.push(problem('test_to', 'test_email_recipient_not_allowed'));
    }
    const warnings = [
      ...(current.enabled && !next.enabled ? ['smtp_disabled'] : []),
      ...(next.security === 'none' && next.username === null && next.enabled
        ? ['smtp_plaintext']
        : []),
    ];
    const secret = this.secretChange(current.secrets['smtp.password'], password, clear);
    return { problems, secret, probe, warnings };
  }

  private smtpSecrets(secret: Change | null, account: MockUser): SmtpSection['secrets'] {
    if (!secret || !('secret' in secret)) return this.sections.smtp.secrets;
    const by = { changed_at: this.iso(), changed_by: `web:${account.username}` };
    return { 'smtp.password': { set: secret.new === 'set', ...by } };
  }

  private smtp(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as SmtpBody;
    const refusal = this.stale('smtp', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.smtp;
    // A text field sent as null or empty is removed; a field left out stays as it is.
    const field = <K extends 'host' | 'username' | 'from_address'>(key: K) =>
      body[key] === undefined ? current[key] : text(body[key]);
    const next: SmtpSection = {
      ...current,
      enabled: body.enabled ?? current.enabled,
      host: field('host'),
      port: body.port ?? current.port,
      security: body.security ?? current.security,
      username: field('username'),
      from_address: field('from_address'),
      timeout_seconds: body.timeout_seconds ?? current.timeout_seconds,
    };
    const rules = this.smtpRules(body, next, account);
    if (rules.problems.length > 0) return this.refused(rules.problems);
    const values = (smtp: SmtpSection) => ({
      enabled: smtp.enabled,
      host: smtp.host,
      port: smtp.port,
      security: smtp.security,
      username: smtp.username,
      from_address: smtp.from_address,
      timeout_seconds: smtp.timeout_seconds,
    });
    return this.change(call, host, dryRun, account, {
      section: 'smtp',
      changes: {
        ...diff('smtp', values(current), values(next)),
        ...(rules.secret ? { 'smtp.password': rules.secret } : {}),
      },
      warnings: rules.warnings,
      effects: ['reload.worker'],
      reauth: true,
      probe: rules.probe,
      secrets: rules.secret !== null,
      next: () => {
        this.sections.smtp = { ...next, secrets: this.smtpSecrets(rules.secret, account) };
      },
    });
  }

  /** The addresses a test e-mail may go to: the alert recipients and one's own address. */
  private recipients(account: MockUser): string[] {
    const listed = this.setting('alerts.email_recipients')?.value;
    return [
      ...(Array.isArray(listed)
        ? listed.filter((item): item is string => typeof item === 'string')
        : []),
      ...(account.email ? [account.email] : []),
    ];
  }

  /** `POST /admin/config/smtp/ca` (`clear`: `DELETE …?etag=`, its body optional). */
  private smtpCa(call: RecordedCall, host: MockHost, clear: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = (call.body ?? {}) as SmtpCaBody | SmtpCaClear;
    const refusal = this.stale(
      'smtp',
      clear ? call.query.get('etag') : (body as SmtpCaBody).etag,
      false,
    );
    if (refusal) return refusal;
    const upload = clear ? null : this.staged((body as SmtpCaBody).upload_id, 'smtp_ca');
    if (!clear && !upload) return this.reject('upload_not_found');
    const current = this.sections.smtp;
    const sha = upload?.sha256 ?? null;
    const next: SmtpSection = { ...current, ca: { sha256: sha } };
    const rules = this.smtpRules(body, next, account);
    if (rules.problems.length > 0) return this.refused(rules.problems);
    const path = (digest: string | null) => (digest ? `smtp-ca/${digest}.pem` : null);
    return this.change(call, host, false, account, {
      section: 'smtp',
      changes:
        current.ca.sha256 === sha
          ? {}
          : {
              'smtp.ca_file': { old: path(current.ca.sha256), new: path(sha) },
              ...(rules.secret ? { 'smtp.password': rules.secret } : {}),
            },
      warnings: rules.warnings,
      effects: ['reload.worker'],
      reauth: true,
      probe: rules.probe,
      secrets: rules.secret !== null,
      installs: upload !== null,
      next: () => {
        this.sections.smtp = { ...next, secrets: this.smtpSecrets(rules.secret, account) };
        if (upload) this.uploads.delete(upload.upload_id);
      },
    });
  }

  /** The worker's test e-mail (WP3-2, WP4-4): no agent needed, 5 in any 60 minutes. */
  private testEmail(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const to = (call.body as TestEmailBody).to.trim();
    if (!this.recipients(account).some((item) => item.toLowerCase() === to.toLowerCase()))
      return this.reject('test_email_recipient_not_allowed');
    const smtp = this.sections.smtp;
    if (!smtp.host || !smtp.from_address) return this.reject('smtp_not_configured');
    const hour = Date.now() - 3_600_000;
    const sent = this.testEmails.filter((at) => at > hour);
    if (sent.length >= 5)
      return this.limited('admin_rate_limited', Math.ceil(((sent[0] ?? 0) - hour) / 1000));
    this.testEmails.push(Date.now());
    const outcome = this.takeAction();
    // The captured result (the address masked, ADR-0010 A4).
    const captured = ADMIN.sections.test_email_done.result as unknown as TestEmailResult;
    const result: TestEmailResult =
      outcome === 'succeeded'
        ? { ...captured, to: maskAddress(to) }
        : {
            ...captured,
            ok: false,
            to: maskAddress(to),
            smtp_code: 535,
            error_text: '535 5.7.8 Kimlik doğrulama başarısız',
          };
    const item = this.enqueue({
      kind: 'worker.test_email',
      section: null,
      account,
      steps: [],
      result: { ...result },
      ...(outcome === 'succeeded'
        ? {}
        : { failure: { state: 'failed' as RequestState, error: 'smtp_test_failed' } }),
    });
    return this.accepted(item);
  }

  private replica(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as ReplicaBody;
    const refusal = this.stale('replica', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.replica;
    const target = body.target === undefined ? current.target : text(body.target);
    const user = body.user === undefined ? current.user : text(body.user);
    const enabled = body.enabled ?? current.enabled;
    const problems: FieldProblem[] = [];
    if (target !== null && !UNC.test(target))
      problems.push(problem('replica.target', 'replica_target_invalid'));
    if (enabled && target === null) problems.push(problem('replica.target', 'config_invalid'));
    const stored = current.secrets['replica.password']?.set ?? false;
    const typed = typeof body.password === 'string' && body.password !== '';
    const clear = body.clear_password === true;
    const hostOf = (value: string | null) => value?.slice(2).split('\\')[0]?.toLowerCase() ?? '';
    if (user !== null) {
      if (
        !/^[A-Za-z0-9][A-Za-z0-9.-]*\\[^\\@\s]+$|^[^\\@\s]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(
          user,
        )
      )
        problems.push(problem('replica.user', 'replica_user_invalid'));
      else if (!typed && (!stored || clear))
        problems.push(problem('replica.password', 'replica_password_required'));
      else if (
        stored &&
        !typed &&
        (hostOf(target) !== hostOf(current.target) || user !== current.user)
      )
        problems.push(problem('replica.password', 'secret_rebind_required'));
    }
    if (problems.length > 0) return this.refused(problems);
    const next = { enabled, target, user };
    const secret = this.secretChange(current.secrets['replica.password'], body.password, clear);
    const touched = target !== current.target || user !== current.user || secret !== null;
    return this.change(call, host, dryRun, account, {
      section: 'replica',
      changes: {
        ...diff(
          'replica',
          { enabled: current.enabled, target: current.target, user: current.user },
          next,
        ),
        ...(secret ? { 'replica.password': secret } : {}),
      },
      warnings: [],
      effects: ['reload.worker', 'task.next_run:Replica'],
      reauth: true,
      probe: enabled && touched && body.probe !== false,
      secrets: secret !== null,
      // Switching the replica off or moving it is guarded (contract 2.3).
      held: current.enabled && (!enabled || target !== current.target),
      next: () => {
        const by = { changed_at: this.iso(), changed_by: `web:${account.username}` };
        this.sections.replica = {
          ...next,
          secrets:
            secret && 'secret' in secret
              ? { 'replica.password': { set: secret.new === 'set', ...by } }
              : this.sections.replica.secrets,
        };
      },
    });
  }

  private replicaTest(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    if (this.agent === 'disabled') return this.reject('admin_disabled');
    const last = this.lastAction.get('action.replica_test');
    if (last !== undefined && Date.now() - last < 60_000)
      return this.limited('admin_rate_limited', Math.ceil((60_000 - (Date.now() - last)) / 1000));
    if (this.agent === 'missing') return this.reject('admin_agent_unavailable');
    this.lastAction.set('action.replica_test', Date.now());
    const outcome = this.takeAction();
    const captured = ADMIN.sections.replica_test_done.result as unknown as ReplicaTestResult;
    const result: ReplicaTestResult =
      outcome === 'succeeded' ? { ...captured } : { ok: false, message: 'access denied' };
    const item = this.enqueue({
      kind: 'action.replica_test',
      section: null,
      account,
      steps: [...ACTION_STEPS],
      result: outcome === 'timed_out' ? null : { ...result },
      ...(outcome === 'succeeded'
        ? {}
        : {
            failure:
              outcome === 'timed_out'
                ? { state: 'timed_out' as RequestState, error: 'task_timeout' }
                : { state: 'failed' as RequestState, error: 'replica_test_failed' },
          }),
    });
    return this.accepted(item);
  }

  /** What the Retention task would delete with `days` (`retention.plan`, the mock's data). */
  private retentionPreview(days: number, effectiveInMs = 0): RetentionPreview {
    const today = Date.parse(`${siteDay(this.now())}T00:00:00Z`);
    const cutoff = new Date(today - days * 86_400_000).toISOString().slice(0, 10);
    const oldest = Date.parse(`${OLDEST_DAY}T00:00:00Z`);
    const count = Math.max(
      0,
      Math.round((Date.parse(`${cutoff}T00:00:00Z`) - oldest) / 86_400_000),
    );
    const effective = new Date(this.now().getTime() + effectiveInMs + 3 * 3_600_000);
    const at2 = Date.UTC(
      effective.getUTCFullYear(),
      effective.getUTCMonth(),
      effective.getUTCDate(),
      2,
    );
    const first = at2 > effective.getTime() ? at2 : at2 + 86_400_000;
    return {
      cutoff,
      days_to_delete: count,
      oldest: count > 0 ? OLDEST_DAY : null,
      newest:
        count > 0
          ? new Date(Date.parse(`${cutoff}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10)
          : null,
      first_run_at: `${new Date(first).toISOString().slice(0, 16)}:00+03:00`,
    };
  }

  private retention(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as RetentionBody;
    const refusal = this.stale('retention', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.retention;
    const days = body.days ?? current.days;
    if (!integer(days, -Infinity, Infinity) || days > 36_500)
      return this.refused([problem('retention.days', 'config_invalid')]);
    if (days < current.minimum_days)
      return this.refused([problem('retention.days', 'retention_below_minimum')]);
    const enabled = body.enabled ?? current.enabled;
    const deletes = enabled && (!current.enabled || days < current.days);
    const hold = deletes && this.holdHours > 0 ? this.holdHours * 3_600_000 : 0;
    const preview = this.retentionPreview(days, hold);
    // Without `confirm_delete_days` the dry run takes the preview's number (WP4-4).
    const confirmed = body.confirm_delete_days ?? (dryRun ? preview.days_to_delete : null);
    if (deletes && preview.days_to_delete > 0 && confirmed !== preview.days_to_delete)
      return this.refused([problem('retention.days', 'retention_confirm_mismatch')]);
    return this.change(call, host, dryRun, account, {
      section: 'retention',
      changes: diff(
        'retention',
        { enabled: current.enabled, days: current.days },
        { enabled, days },
      ),
      warnings: [],
      effects: ['reload.api', 'task.next_run:Retention'],
      reauth: true,
      probe: false,
      held: deletes,
      check: { retention_preview: preview },
      next: () => {
        this.sections.retention = { ...this.sections.retention, enabled, days };
      },
    });
  }

  private backup(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as BackupBody;
    const refusal = this.stale('backup', body.etag, dryRun);
    if (refusal) return refusal;
    const current = this.sections.backup;
    const next: typeof current = {
      enabled: body.enabled ?? current.enabled,
      keep_daily: body.keep_daily ?? current.keep_daily,
      keep_weekly: body.keep_weekly ?? current.keep_weekly,
      keep_monthly: body.keep_monthly ?? current.keep_monthly,
      min_free_gb: body.min_free_gb ?? current.min_free_gb,
    };
    const ranges: Record<string, [number, number]> = {
      keep_daily: [1, 3660],
      keep_weekly: [0, 520],
      keep_monthly: [0, 1200],
      min_free_gb: [0, 100_000],
    };
    const problems = Object.entries(ranges)
      .filter(([key, [low, high]]) => !integer(next[key as keyof typeof next], low, high))
      .map(([key]) => problem(`backup.${key}`, 'config_invalid'));
    if (problems.length > 0) return this.refused(problems);
    return this.change(call, host, dryRun, account, {
      section: 'backup',
      changes: diff('backup', { ...current }, { ...next }),
      warnings: current.enabled && !next.enabled ? ['backup_disabled'] : [],
      effects: ['task.next_run:Backup'],
      reauth: true,
      probe: false,
      next: () => {
        this.sections.backup = next;
      },
    });
  }

  /** The worker's lease rebuild (WP3-2): at most 31 past days, not before the first log. */
  private leaseRebuild(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as LeaseRebuildBody;
    const day = (value: string) =>
      /^\d{4}-\d{2}-\d{2}$/.test(value) ? Date.parse(`${value}T00:00:00Z`) : NaN;
    const from = day(body.from);
    const to = day(body.to);
    const today = Date.parse(`${siteDay(this.now())}T00:00:00Z`);
    const total = Math.round((to - from) / 86_400_000) + 1;
    if (
      Number.isNaN(from) ||
      Number.isNaN(to) ||
      total < 1 ||
      total > 31 ||
      to > today ||
      from < Date.parse(`${OLDEST_DAY}T00:00:00Z`)
    )
      return this.reject('lease_rebuild_range');
    const outcome = this.takeAction();
    const result: LeaseRebuildResult = {
      done_days: total,
      total_days: total,
      failed_days: outcome === 'succeeded' ? [] : [body.from],
    };
    const item = this.enqueue({
      kind: 'worker.lease_rebuild',
      section: null,
      account,
      // Its row goes queued → running → done (the worker reports no steps).
      steps: ['task'],
      result: { ...result },
      ...(outcome === 'succeeded'
        ? {}
        : { failure: { state: 'failed' as RequestState, error: 'lease_rebuild_failed' } }),
    });
    return this.accepted(item);
  }

  // -- versions and diagnostics (contract 2.11.1, M4) ------------------------------------------

  private diagnosticsPackage(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    this.diagnostics += 1;
    // A ZIP holding one entry (the status files would be in it; no raw log line ever is).
    const body = Buffer.from(
      'PK\u0003\u0004mock arcilogger diagnostics: status, config without secrets, requests',
      'utf8',
    );
    return [
      200,
      null,
      {
        body,
        contentType: 'application/zip',
        headers: {
          'Content-Disposition': `attachment; filename="arcilogger-diagnostics-${siteDay(this.now()).replaceAll('-', '')}.zip"`,
          'Cache-Control': 'no-store',
        },
      },
    ];
  }

  // -- requests: list, one, cancel, revert ------------------------------------------------------

  /** `GET /admin/requests`: newest first; `cursor` is the id of the last item of the page before. */
  private listRequests(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const kind = call.query.get('kind');
    const limit = Number(call.query.get('limit') ?? 50);
    const cursor = call.query.get('cursor');
    const items = [...this.requests.values()]
      .map((item) => item.out)
      .filter((out) => !kind || out.kind === kind)
      .reverse();
    const start = cursor ? items.findIndex((out) => out.id === cursor) + 1 : 0;
    const page = items.slice(start, start + limit);
    return [
      200,
      {
        items: copy(page),
        next_cursor: start + limit < items.length ? (page.at(-1)?.id ?? null) : null,
      },
    ];
  }

  private oneRequest(call: RecordedCall, host: MockHost, id: string): Answer {
    const item = this.requests.get(id);
    // `seal.manage` is enough for one's own "Şimdi mühürle" and "Kontör sorgula".
    const own =
      item && (item.out.kind === 'action.seal' || item.out.kind === 'action.credit')
        ? 'seal.manage'
        : 'settings.manage';
    const account = host.requirePermission(call, own);
    if (Array.isArray(account)) return account;
    if (!item) return this.reject('admin_request_not_found');
    return [200, copy(item.out)];
  }

  /**
   * `DELETE /admin/requests/{id}` (WP4-5): a request the service has not taken is removed at once
   * (`state: cancelled`, nothing to follow); a held change is cancelled by a signed
   * `config.cancel` (a password confirmation), which the target reports once taken.
   */
  private cancelRequest(call: RecordedCall, host: MockHost, id: string): Answer {
    const item = this.requests.get(id);
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    if (!item) return this.reject('admin_request_not_found');
    const state = item.out.state;
    if (state === 'queued' || state === 'waiting_for_agent') {
      item.cancelled = true;
      item.cancel?.();
      this.advance(item);
      const [, answer] = this.accepted(item);
      return [202, { ...answer, state: 'cancelled' } satisfies AdminAccepted];
    }
    if (state !== 'pending') return this.reject('request_not_cancellable');
    const recent = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(recent)) return recent;
    const cancel = this.enqueue({
      kind: 'config.cancel',
      section: null,
      account,
      steps: ['received', 'validate'],
      apply: () => {
        item.cancelled = true;
        item.cancel?.();
      },
    });
    cancel.final.state = 'succeeded';
    return this.accepted(cancel);
  }

  private revertRequest(call: RecordedCall, host: MockHost, id: string): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const item = this.requests.get(id);
    if (!item) return this.reject('admin_request_not_found');
    const out = item.out;
    if (
      !(out.kind === 'config.apply' || out.kind === 'config.revert') ||
      !['applied', 'active', 'partially_active'].includes(out.state) ||
      out.version === null ||
      out.section === null ||
      item.before === null
    )
      return this.reject('request_not_revertible');
    const recent = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(recent)) return recent;
    const down = this.unavailable();
    if (down) return down;
    const section = out.section;
    if (this.busy(section)) return this.reject('admin_section_busy');
    const before = item.before;
    // The target's changes the other way round (a secret stays `set`/`unset` only).
    const changes = Object.fromEntries(
      Object.entries(out.changes).map(([path, change]) => [
        path,
        { ...change, old: change.new, new: change.old },
      ]),
    );
    const effects = [...item.effects];
    const revert = this.enqueue({
      kind: 'config.revert',
      section,
      account,
      steps: [
        ...AGENT_STEPS.filter((step) => step !== 'probe'),
        ...ACTIVATION_STEPS.filter((step) => effects.includes(step)),
      ],
      skipped: ['write_secrets', 'install_files'],
      changes,
      effects,
      before: copy(this.sections[section]),
      apply: () => {
        (this.sections as Record<AdminSection, unknown>)[section] = copy(before);
        this.recordVersion(revert, account);
      },
    });
    return this.accepted(revert);
  }

  // -- actions ----------------------------------------------------------------------------------

  /** The oldest closed unsealed day of the captured calendar (with the days sealed here). */
  private dayState(day: string): string | null {
    if (this.sealed.has(day)) return 'sealed';
    const cell = SEALS.calendar_current.days.find((item) => item.day === day);
    return cell?.state ?? SEALS.days[day]?.state ?? null;
  }

  /** "Şimdi mühürle" (`routers/seals.py`): the day's 409s first, then the service. */
  private sealNow(call: RecordedCall, host: MockHost, day: string): Answer {
    const account = host.requirePermission(call, 'seal.manage');
    if (Array.isArray(account)) return account;
    const state = this.dayState(day);
    if (state === 'sealed' || state === 'verified' || state === 'late')
      return this.reject('seal_day_sealed');
    if (state === null || state === 'no_data' || state === 'deleted')
      return this.reject('seal_not_applicable');
    if (!SEALABLE.has(state)) return this.reject('seal_not_due');
    const earlier = SEALS.calendar_current.days.find(
      (cell) => cell.day < day && SEALABLE.has(this.dayState(cell.day) ?? ''),
    );
    if (earlier) return this.reject('seal_not_due');
    const waiting = [...this.requests.values()].some(
      (item) => item.out.kind === 'action.seal' && !item.out.terminal && item.day === day,
    );
    if (waiting) return this.reject('seal_request_pending');
    const down = this.unavailable();
    if (down) return down;
    const outcome = this.takeAction();
    const serial = sha256(`seal ${day}`).slice(0, 16).toUpperCase();
    const genTime = this.iso();
    // The captured outcome of the Seal task (`SealOutcome`), for this day.
    const result: SealActionResult = {
      ...(copy(SEALS.seal_now_done.result) as unknown as SealActionResult),
      day,
      serial,
      gen_time: genTime,
    };
    const item = this.enqueue({
      kind: 'action.seal',
      section: null,
      account,
      steps: [...ACTION_STEPS],
      day,
      result: outcome === 'succeeded' ? { ...result } : null,
      ...(outcome === 'succeeded'
        ? {
            apply: () => {
              this.sealed.set(day, { serial, gen_time: genTime, sealed_at: this.iso() });
            },
          }
        : {
            failure:
              outcome === 'timed_out'
                ? { state: 'timed_out' as RequestState, error: 'task_timeout' }
                : { state: 'failed' as RequestState, error: 'seal_failed' },
          }),
    });
    return this.accepted(item);
  }

  /** "Kontör sorgula" (`routers/credits.py`): one a minute, then the service. */
  private creditQuery(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'seal.manage');
    if (Array.isArray(account)) return account;
    const last = this.lastAction.get('action.credit');
    if (last !== undefined && Date.now() - last < 60_000)
      return this.limited('admin_rate_limited', Math.ceil((60_000 - (Date.now() - last)) / 1000));
    const down = this.unavailable();
    if (down) return down;
    this.lastAction.set('action.credit', Date.now());
    const sample = this.creditsSample();
    const outcome = this.takeAction();
    const credits = (this.creditChecks.at(-1)?.credits ?? sample.remaining ?? 9612) - 1;
    const result: CreditActionResult = {
      ...(copy(CREDITS.query_done.result) as unknown as CreditActionResult),
      credits,
      threshold: sample.threshold,
      below_threshold: credits < sample.threshold,
    };
    const item = this.enqueue({
      kind: 'action.credit',
      section: null,
      account,
      steps: [...ACTION_STEPS],
      result: outcome === 'succeeded' ? { ...result } : null,
      ...(outcome === 'succeeded'
        ? {
            apply: () => {
              this.creditChecks.push({
                time: this.iso(),
                provider: 'zamane',
                credits,
                error: null,
              });
            },
          }
        : {
            failure:
              outcome === 'timed_out'
                ? { state: 'timed_out' as RequestState, error: 'task_timeout' }
                : { state: 'failed' as RequestState, error: 'credit_query_failed' },
          }),
    });
    return this.accepted(item);
  }

  /** The captured calendar with the days sealed here (`null`: nothing to change). */
  private calendar(call: RecordedCall, host: MockHost): Answer | null {
    if (this.sealed.size === 0) return null;
    const account = host.requirePermission(call, 'seal.read');
    if (Array.isArray(account)) return account;
    const month = call.query.get('month');
    const calendar: Calendar | undefined = month
      ? copy(SEALS.calendars[month])
      : copy(SEALS.calendar_current);
    if (!calendar) return null;
    const label = calendar.legend.find((item) => item.state === 'sealed');
    for (const cell of calendar.days) {
      const sealed = this.sealed.get(cell.day);
      if (!sealed) continue;
      calendar.counts[cell.state] = (calendar.counts[cell.state] ?? 1) - 1;
      calendar.counts.sealed = (calendar.counts.sealed ?? 0) + 1;
      Object.assign(cell, {
        state: 'sealed',
        label_tr: label?.label_tr ?? 'Mühürlendi',
        label_en: label?.label_en ?? 'Sealed',
        tone: 'warning',
        message_tr: null,
        message_en: null,
        error_detail: null,
        sealed_at: sealed.sealed_at,
        serial: sealed.serial,
        gen_time: sealed.gen_time,
      });
    }
    return [200, calendar];
  }

  /** The captured day page of a day sealed here (`null`: nothing to change). */
  private sealDay(call: RecordedCall, host: MockHost, day: string): Answer | null {
    const sealed = this.sealed.get(day);
    const sample = SEALS.days[day];
    if (!sealed || !sample) return null;
    const account = host.requirePermission(call, 'seal.read');
    if (Array.isArray(account)) return account;
    const label = SEALS.calendar_current.legend.find((item) => item.state === 'sealed');
    const detail: SealDayDetail = {
      ...copy(sample),
      state: 'sealed',
      label_tr: label?.label_tr ?? 'Mühürlendi',
      label_en: label?.label_en ?? 'Sealed',
      tone: 'warning',
      message_tr: null,
      message_en: null,
      error_detail: null,
      sealed: true,
      sealed_at: sealed.sealed_at,
      serial: sealed.serial,
      gen_time: sealed.gen_time,
      seal_state: {
        status: 'sealed',
        attempts: (sample.seal_state?.attempts ?? 0) + 1,
        last_attempt_at: sealed.sealed_at,
        last_error: null,
        last_error_code: null,
        last_error_tr: null,
        next_attempt_at: null,
        sealed_at: sealed.sealed_at,
      },
    };
    return [200, detail];
  }

  /** The credit history with the checks "Kontör sorgula" added (`null`: nothing to change). */
  private credits(call: RecordedCall, host: MockHost): Answer | null {
    if (this.creditChecks.length === 0) return null;
    const account = host.requirePermission(call, 'seal.read');
    if (Array.isArray(account)) return account;
    const credits = copy(this.creditsSample());
    credits.history.push(...copy(this.creditChecks));
    const latest = this.creditChecks.at(-1) ?? null;
    credits.latest = latest;
    credits.remaining = latest?.credits ?? credits.remaining;
    credits.below_threshold = (credits.remaining ?? 0) < credits.threshold;
    return [200, credits];
  }

  // -- database settings (contract 2.13, `routers/settings.py`) --------------------------------

  private setting(key: string): SettingOut | undefined {
    for (const group of this.settings) {
      const found = group.settings.find((item) => item.key === key);
      if (found) return found;
    }
    return undefined;
  }

  /** An unknown or locked key (the API's order: before any value is looked at). */
  private unusable(key: string): [number, unknown] | null {
    const setting = this.setting(key);
    if (!setting) return this.reject('setting_unknown');
    if (setting.locked) return this.reject('setting_locked', [{ key }]);
    return null;
  }

  /** Write the values that differ (`settings_admin.change_setting`, `reset_setting`). */
  private write(account: MockUser, values: [string, SettingValue][]): SettingsPatchResult {
    const changed: SettingsPatchResult['changed'] = [];
    const unchanged: string[] = [];
    for (const [key, value] of values) {
      const setting = this.setting(key);
      if (!setting) continue;
      if (JSON.stringify(setting.value) === JSON.stringify(value)) {
        unchanged.push(key);
        continue;
      }
      changed.push({ key, old: setting.value, new: copy(value) });
      setting.value = copy(value);
      setting.is_default = JSON.stringify(setting.value) === JSON.stringify(setting.default);
      setting.updated_by = account.username;
      setting.updated_at = this.iso();
    }
    return { changed, unchanged };
  }

  /** `PATCH /settings`: keys, values, conflicts, then the password confirmation. */
  private patchSettings(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const { changes } = call.body as SettingsPatch;
    for (const change of changes) {
      const refusal = this.unusable(change.key);
      if (refusal) return refusal;
    }
    const problems: SettingProblem[] = [];
    for (const change of changes) {
      const setting = this.setting(change.key);
      const refused = setting ? settingProblem(setting, change.value) : null;
      if (refused) {
        const [tr, en] = SETTING_ERRORS[refused.code] ?? [refused.code, refused.code];
        problems.push({
          key: change.key,
          code: refused.code,
          message_tr: format(tr, refused.facts),
          message_en: format(en, refused.facts),
        });
      }
    }
    if (problems.length > 0) return this.reject('setting_invalid', problems);
    const conflicts = changes
      .map((change) => ({ change, setting: this.setting(change.key) }))
      .filter(
        ({ change, setting }) => JSON.stringify(change.old) !== JSON.stringify(setting?.value),
      )
      .map(({ change, setting }) => ({ key: change.key, current: setting?.value ?? null }));
    if (conflicts.length > 0) return this.reject('setting_conflict', conflicts);
    if (changes.some((change) => this.setting(change.key)?.reauth)) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    return [
      200,
      this.write(
        account,
        changes.map((change) => [change.key, change.value]),
      ),
    ];
  }

  private resetSetting(call: RecordedCall, host: MockHost, key: string): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const refusal = this.unusable(key);
    if (refusal) return refusal;
    const setting = this.setting(key);
    if (!setting) return this.reject('setting_unknown');
    if (setting.reauth) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    return [200, this.write(account, [[key, setting.default]])];
  }

  // -- routing ----------------------------------------------------------------------------------

  answer(call: RecordedCall, host: MockHost): Answer | null {
    const { method, path } = call;
    let match: RegExpExecArray | null;
    const route = `${method} ${path}`;
    if (
      !path.startsWith('/admin') &&
      !path.startsWith('/settings') &&
      !path.startsWith('/seals') &&
      !path.startsWith('/credits')
    )
      return null;
    this.tick();
    // The request bodies were checked against the API's models (`mock-api.ts` `bodyProblems`).
    if (route === 'GET /admin/status') {
      const account = host.requirePermission(call, 'settings.manage');
      return Array.isArray(account) ? account : [200, this.status()];
    }
    if (route === 'GET /admin/config') {
      const account = host.requirePermission(call, 'settings.manage');
      return Array.isArray(account) ? account : [200, this.config()];
    }
    if (route === 'POST /admin/config/general/check') return this.general(call, host, true);
    if (route === 'PUT /admin/config/general') return this.general(call, host, false);
    if (route === 'POST /admin/uploads') return this.upload(call, host);
    if (route === 'POST /admin/config/tsa/check') return this.tsa(call, host, true);
    if (route === 'PUT /admin/config/tsa') return this.tsa(call, host, false);
    if (route === 'POST /admin/config/tsa/roots') return this.addRoot(call, host);
    if (route === 'POST /admin/config/tsa/legal-roots') return this.addLegalRoot(call, host);
    if (route === 'POST /admin/config/tsa/zamane') return this.stageZamane(call, host);
    if (route === 'POST /admin/actions/tsa-test') return this.tsaTest(call, host);
    if (route === 'POST /admin/config/sources/check')
      return this.sourceChange(call, host, 'check', null);
    if (route === 'POST /admin/config/sources') return this.sourceChange(call, host, 'add', null);
    if ((match = /^\/admin\/config\/sources\/([^/]+)$/.exec(path))) {
      const name = decodeURIComponent(match[1] ?? '');
      if (method === 'PUT') return this.sourceChange(call, host, 'update', name);
      if (method === 'DELETE') return this.removeSource(call, host, name);
    }
    if (route === 'POST /admin/config/smtp/check') return this.smtp(call, host, true);
    if (route === 'PUT /admin/config/smtp') return this.smtp(call, host, false);
    if (route === 'POST /admin/config/smtp/ca') return this.smtpCa(call, host, false);
    if (route === 'DELETE /admin/config/smtp/ca') return this.smtpCa(call, host, true);
    if (route === 'POST /admin/actions/test-email') return this.testEmail(call, host);
    if (route === 'POST /admin/config/replica/check') return this.replica(call, host, true);
    if (route === 'PUT /admin/config/replica') return this.replica(call, host, false);
    if (route === 'POST /admin/actions/replica-test') return this.replicaTest(call, host);
    if (route === 'POST /admin/config/retention/check') return this.retention(call, host, true);
    if (route === 'PUT /admin/config/retention') return this.retention(call, host, false);
    if (route === 'POST /admin/config/backup/check') return this.backup(call, host, true);
    if (route === 'PUT /admin/config/backup') return this.backup(call, host, false);
    if (route === 'POST /admin/actions/lease-rebuild') return this.leaseRebuild(call, host);
    if (route === 'GET /admin/config/versions') {
      const account = host.requirePermission(call, 'settings.manage');
      return Array.isArray(account) ? account : [200, copy(this.versions)];
    }
    if (route === 'GET /admin/diagnostics') return this.diagnosticsPackage(call, host);
    if (route === 'GET /admin/requests') return this.listRequests(call, host);
    if ((match = /^\/admin\/requests\/([0-9a-f]{32})$/.exec(path))) {
      const id = match[1] ?? '';
      if (method === 'GET') return this.oneRequest(call, host, id);
      if (method === 'DELETE') return this.cancelRequest(call, host, id);
    }
    if ((match = /^\/admin\/requests\/([0-9a-f]{32})\/revert$/.exec(path)) && method === 'POST')
      return this.revertRequest(call, host, match[1] ?? '');
    if ((match = /^\/seals\/(\d{4}-\d{2}-\d{2})\/seal$/.exec(path)) && method === 'POST')
      return this.sealNow(call, host, match[1] ?? '');
    if (route === 'POST /credits/query') return this.creditQuery(call, host);
    if (route === 'GET /seals/calendar') return this.calendar(call, host);
    if ((match = /^\/seals\/(\d{4}-\d{2}-\d{2})$/.exec(path)) && method === 'GET')
      return this.sealDay(call, host, match[1] ?? '');
    if (route === 'GET /credits') return this.credits(call, host);
    if (route === 'GET /settings') {
      const account = host.requirePermission(call, 'settings.manage');
      return Array.isArray(account) ? account : [200, { groups: copy(this.settings) }];
    }
    if (route === 'PATCH /settings') return this.patchSettings(call, host);
    if ((match = /^\/settings\/([a-z0-9_.]+)\/reset$/.exec(path)) && method === 'POST')
      return this.resetSetting(call, host, match[1] ?? '');
    return null;
  }
}

// -- smtp, replica, retention and the actions of M3 ---------------------------------------------

/** `\\host\share[\path]` (contract 2.6 "replica", simplified). */
const UNC =
  /^\\\\[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\\[A-Za-z0-9$_.-]{1,80}(?:\\[^\\:*?"<>|/]+)*$/;
/** The oldest day of the mock's data (sealed days, logs). */
const OLDEST_DAY = '2024-03-01';
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** The site's day of a moment (UTC+03:00 all year). */
function siteDay(at: Date): string {
  return new Date(at.getTime() + 3 * 3_600_000).toISOString().slice(0, 10);
}

/** `admin_jobs.mask_address`: `bora@ornek.example` → `b***@ornek.example` (ADR-0010 A4). */
function maskAddress(address: string): string {
  const at = address.trim().lastIndexOf('@');
  const local = address.trim().slice(0, Math.max(at, 0));
  const domain = address.trim().slice(at + 1);
  return at < 0 || !local || !domain ? '***' : `${local.slice(0, 1)}***@${domain}`;
}

// -- uploads, Kamu SM and sources of the mock (contract 2.4.4, 2.6) -------------------------------

/** Largest upload per kind (contract 2.4.4). */
const UPLOAD_LIMITS: Record<UploadPreview['kind'], number> = {
  kamusm_root: 64 * 1024,
  smtp_ca: 256 * 1024,
  zamane_client: 25 * 1024 * 1024,
};

/** The `file` part of a multipart body (the specs upload text files). */
function multipartFile(call: RecordedCall): { name: string; content: string } | null {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(call.contentType ?? '');
  if (!call.raw || !boundary) return null;
  for (const part of call.raw.split(`--${boundary[1] ?? boundary[2] ?? ''}`)) {
    const head = part.indexOf('\r\n\r\n');
    if (head < 0) continue;
    const headers = part.slice(0, head);
    const name = /filename="([^"]*)"/.exec(headers);
    if (!name || !/name="file"/.test(headers)) continue;
    const content = part.slice(head + 4);
    return {
      name: name[1] ?? '',
      content: content.endsWith('\r\n') ? content.slice(0, -2) : content,
    };
  }
  return null;
}

/** What the production server still needs (`readiness.missing`). */
function tsaMissing(tsa: TsaSection): ReadinessItem[] {
  const missing: ReadinessItem[] = [];
  if (!(tsa.secrets['kamusm.customer']?.set && tsa.secrets['kamusm.password']?.set))
    missing.push('credentials');
  if (!tsa.zamane.present || tsa.zamane.trusted === false) missing.push('zamane_jar');
  if (tsa.java === null || !JAVA_CHOICES.includes(tsa.java)) missing.push('java');
  if (tsa.roots.length === 0) missing.push('roots');
  return missing;
}

/** The sealing cause of a missing item (`seal.CONFIG_CAUSES`, WP2's `executable`). */
function causeOf(item: ReadinessItem): string {
  return {
    credentials: 'credentials',
    zamane_jar: 'zamane-jar',
    java: 'executable',
    roots: 'roots',
  }[item];
}

interface Net {
  base: number;
  prefix: number;
}

function networkOf(text: string): Net | null {
  const [address = '', prefixText, ...rest] = text.split('/');
  if (rest.length > 0 || !IPV4.test(address)) return null;
  const value = address.split('.').reduce((sum, part) => sum * 256 + Number(part), 0);
  const prefix = prefixText === undefined ? 32 : Number(prefixText);
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32) return null;
  const size = 2 ** (32 - prefix);
  if (value % size !== 0) return null;
  return { base: value, prefix };
}

function inside(outer: Net, inner: Net): boolean {
  if (inner.prefix < outer.prefix) return false;
  const size = 2 ** (32 - outer.prefix);
  return Math.floor(inner.base / size) * size === outer.base;
}

function overlap(a: Net, b: Net): boolean {
  return inside(a, b) || inside(b, a);
}

function nets(...texts: string[]): Net[] {
  return texts.map((text) => networkOf(text)).filter((net): net is Net => net !== null);
}

const RESERVED_NETWORKS = nets(
  '0.0.0.0/8',
  '127.0.0.0/8',
  '169.254.0.0/16',
  '224.0.0.0/4',
  '240.0.0.0/4',
);
/** `agent.json.local_addresses`: the server's own address. */
const LOCAL_ADDRESSES = ['172.20.10.5'];

// -- the database settings of the mock (contract 2.13) --------------------------------------------

/**
 * The refusals `validate_setting` gives the values the specs send (`arcilogger_db.settings`
 * `SETTING_ERRORS`, Turkish first); `{low}`-style placeholders take the facts.
 */
const SETTING_ERRORS: Record<string, [string, string]> = {
  setting_type: [
    'Değer türü yanlış: {expected_tr} bekleniyor.',
    'Wrong type of value: {expected_en} expected.',
  ],
  setting_below_minimum: ['En az {low} olmalı.', 'Must be at least {low}.'],
  setting_out_of_range: ['{low} ile {high} arasında olmalı.', 'Must be between {low} and {high}.'],
  setting_ip_invalid: ['Geçersiz IP adresi.', 'Invalid IP address.'],
  setting_email_invalid: ['Geçersiz e-posta adresi.', 'Invalid e-mail address.'],
  setting_totp_required: [
    '{roles} rolleri için iki adımlı doğrulama (TOTP) zorunludur (şartname 13): listeye rol eklenebilir, bu roller çıkarılamaz.',
    'Two-step verification (TOTP) is mandatory for the {roles} roles (specification 13): roles may be added, these cannot be removed.',
  ],
  setting_work_hours_order: [
    'Mesai başlangıcı bitişten önce olmalı (gece vardiyası yok).',
    'Work hours must start before they end (no night shift).',
  ],
  setting_holiday_invalid: [
    'Geçersiz tarih (AA-GG ya da YYYY-AA-GG).',
    'Invalid date (MM-DD or YYYY-MM-DD).',
  ],
};
{
  // The captured refusal holds one of these texts: it must still be the API's.
  const captured = SETTINGS.errors.setting_invalid?.body.error.details as SettingProblem[];
  for (const item of captured) {
    const [tr, en] = SETTING_ERRORS[item.code] ?? ['', ''];
    const facts = { low: 1, high: 10_000 };
    if (format(tr, facts) !== item.message_tr || format(en, facts) !== item.message_en)
      throw new Error(`mock-admin: SETTING_ERRORS.${item.code} differs from the capture`);
  }
}

const HOLIDAY = /^(?:\d{4}-)?\d{2}-\d{2}$/;
const TYPE_NAMES: Record<string, [string, string]> = {
  boolean: ['true/false', 'true/false'],
  number: ['tam sayı', 'an integer'],
  string: ['metin', 'a text'],
  list: ['liste', 'a list'],
  object: ['nesne', 'an object'],
};

function format(text: string, facts: Record<string, unknown>): string {
  return text.replace(/\{(\w+)\}/g, (all, name: string) =>
    name in facts ? String(facts[name]) : all,
  );
}

/** `validate_setting`'s refusal of a value (`null`: accepted). */
function settingProblem(
  setting: SettingOut,
  value: SettingValue,
): { code: string; facts: Record<string, unknown> } | null {
  const kind = Array.isArray(setting.default) ? 'list' : typeof setting.default;
  const actual = Array.isArray(value) ? 'list' : typeof value;
  if (kind !== actual || (setting.type === 'integer' && !Number.isInteger(value))) {
    const [tr, en] = TYPE_NAMES[kind] ?? [kind, kind];
    return { code: 'setting_type', facts: { expected_tr: tr, expected_en: en } };
  }
  if (setting.type === 'integer') {
    const number = value as number;
    const low = setting.min ?? 0;
    if (number < low || (setting.max !== null && number > setting.max))
      return setting.max === null
        ? { code: 'setting_below_minimum', facts: { low } }
        : { code: 'setting_out_of_range', facts: { low, high: setting.max } };
  }
  if (setting.key === 'nat.public_ips' && (value as string[]).some((item) => !IPV4.test(item)))
    return { code: 'setting_ip_invalid', facts: {} };
  if (
    setting.key === 'alerts.email_recipients' &&
    (value as string[]).some((item) => !EMAIL.test(item))
  )
    return { code: 'setting_email_invalid', facts: {} };
  if (setting.key === 'usage.holidays' && (value as string[]).some((item) => !HOLIDAY.test(item)))
    return { code: 'setting_holiday_invalid', facts: {} };
  if (
    setting.key === 'auth.totp_required_roles' &&
    !['admin', 'auditor'].every((role) => (value as string[]).includes(role))
  )
    return { code: 'setting_totp_required', facts: { roles: 'admin, auditor' } };
  if (setting.key === 'usage.work_hours') {
    const hours = value as { start: string; end: string };
    if (!(hours.start < hours.end)) return { code: 'setting_work_hours_order', facts: {} };
  }
  return null;
}
