/**
 * The mocked browser administration of the E2E tests (ADR-0014; Wave 1 contract 2.9-2.13): the
 * machine configuration (`/admin/config`), the administration service's status, change requests
 * that go through their states in real time (queued → received → probing → applying → applied →
 * active, or a failure the test asks for), held changes, uploads, the actions ("Şimdi mühürle",
 * "Kontör sorgula", the stamp, replica and e-mail tests, the lease rebuild) and the database
 * settings (`/settings`).
 *
 * Written by hand from the contract while the API is being built: every answer has the shape of
 * `src/lib/settings-types.ts`, refusals use the contract's codes and Turkish/English texts
 * (2.12, `arcilogger_core.admin.messages`), and a body with a field the endpoint does not know or
 * without a required one is refused like the API's `extra="forbid"` models. Once the API exists,
 * the captured samples (`samples/admin.json`, `settings.json`) replace the hand-written state.
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
  Change,
  CodeMessage,
  ConfigVersion,
  ReadinessItem,
  RequestKind,
  RequestState,
  RequestStep,
  RetentionPreview,
  SettingGroup,
  SettingOut,
  SettingValue,
  SourceConfig,
  SourceOut,
  StepState,
  TsaSection,
  UploadPreview,
} from '../src/lib/settings-types';

import type { MockUser, RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { copy, CREDITS, SEALS } from './samples';

/** HTTP refusals of the administration API (contract 2.12): code -> [status, tr, en]. */
const HTTP: Record<string, [number, string, string]> = {
  admin_agent_unavailable: [
    503,
    'Yönetim hizmeti (ArciAdmin) yanıt vermiyor; değişiklik kuyruğa alınmadı. Sunucuda "ARCILOGGER Yönetim" hizmetini başlatın.',
    'The administration service (ArciAdmin) is not responding; the change was not queued. Start the "ARCILOGGER Yönetim" service on the server.',
  ],
  admin_disabled: [
    503,
    'Tarayıcıdan sunucu yönetimi bu kurulumda kapalı; değişikliği sunucu konsolunda yapın.',
    'Browser administration is switched off on this installation; make the change on the server console.',
  ],
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
  admin_rate_limited: [
    429,
    'Bu işlem kısa süre önce yapıldı; biraz sonra tekrar deneyin.',
    'This action ran a moment ago; try again shortly.',
  ],
  admin_section_busy: [
    409,
    'Bu bölüm için bekleyen bir değişiklik var; bitmesini bekleyin ya da iptal edin.',
    'A change to this section is still open; wait for it to finish or cancel it.',
  ],
  admin_request_not_found: [404, 'Değişiklik isteği bulunamadı.', 'Change request not found.'],
  request_not_cancellable: [
    409,
    'Bu istek artık iptal edilemez: uygulanmaya başlandı ya da bitti.',
    'This request can no longer be cancelled: it has started or finished.',
  ],
  request_not_revertible: [
    409,
    'Bu değişiklik geri alınamaz: yalnızca sürümü saklanan, uygulanmış yapılandırma değişiklikleri geri alınabilir.',
    'This change cannot be reverted: only applied configuration changes whose version is still kept can be reverted.',
  ],
  config_conflict: [
    409,
    'Bu ayarlar siz düzenlerken başka bir yerden değiştirildi; sayfayı yenileyip tekrar deneyin.',
    'These settings were changed elsewhere while you were editing; reload the page and try again.',
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
  site_name_required: [422, 'Kuruluş adı boş olamaz.', 'The organisation name cannot be empty.'],
  host_invalid: [
    422,
    'Geçerli bir sunucu adı ya da IPv4 adresi girin.',
    'Enter a valid host name or IPv4 address.',
  ],
  clock_servers_required: [
    422,
    'En az bir saat sunucusu gerekir (yerel kaynak ya da yedek sunucu).',
    'At least one time server is required (the local source or the fallback).',
  ],
  retention_below_minimum: [
    422,
    'Saklama süresi 730 günden (iki yıl) kısa olamaz.',
    'The retention period cannot be shorter than 730 days (two years).',
  ],
  retention_confirm_mismatch: [
    422,
    'Onaylamak için önizlemedeki silinecek gün sayısını aynen yazın.',
    'To confirm, type exactly the number of days to be deleted shown in the preview.',
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
  source_address_taken: [
    409,
    'Bu adres başka bir kaynağa ait.',
    'This address belongs to another source.',
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
  source_network_not_allowed: [
    422,
    'Adres, kurulumda izin verilen kaynak ağlarının dışında; bu liste yalnızca sunucu konsolundan değiştirilebilir.',
    'The address is outside the source networks allowed at installation; that list can only be changed on the server console.',
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
  source_confirm_mismatch: [
    422,
    'Silmeyi onaylamak için kaynağın adını aynen yazın.',
    "Type the source's name exactly to confirm the removal.",
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
  smtp_password_required: [
    422,
    'Kullanıcı adı girildiğinde SMTP parolası da gerekir.',
    'An SMTP password is required when a user name is set.',
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
  test_email_recipient_not_allowed: [
    422,
    'Deneme e-postası yalnızca uyarı alıcılarına ya da kendi e-posta adresinize gönderilebilir.',
    'A trial e-mail can only go to the alert recipients or to your own address.',
  ],
  smtp_not_configured: [
    409,
    'SMTP sunucusu ve gönderen adresi ayarlanmamış.',
    'The SMTP server and sender address are not set.',
  ],
  replica_target_invalid: [
    422,
    'Replika hedefi \\\\sunucu\\paylaşım biçiminde bir ağ paylaşımı olmalı.',
    'The replica target must be a network share like \\\\server\\share.',
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
  tsa_mock_refused: [
    422,
    'Deneme (mock) zaman damgası web arayüzünden seçilemez.',
    'The trial (mock) time stamp cannot be chosen from the web interface.',
  ],
  tsa_not_ready: [
    409,
    'Üretim sunucusuna geçmeden önce eksikleri tamamlayın: kimlik bilgileri, onaylı Zamane istemcisi, Java ve en az bir kök sertifika.',
    'Complete the missing items before switching to the production server: credentials, an approved Zamane client, Java and at least one root certificate.',
  ],
  tsa_would_break: [
    409,
    'Bu değişiklik şu an çalışan mühürlemeyi durdururdu; uygulanmadı. Nedenler aşağıda.',
    'This change would stop sealing, which works now; it was not applied. The causes are below.',
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
  root_certificate_invalid: [
    422,
    'Dosya geçerli bir kök sertifika değil (tek, kendinden imzalı bir CA sertifikası olmalı).',
    'The file is not a valid root certificate (it must be a single self-signed CA certificate).',
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
  legal_root_confirm_required: [
    422,
    "Parmak izini Kamu SM'nin yayımladığı değerle karşılaştırdığınızı onaylayın.",
    'Confirm that you compared the fingerprint with the value Kamu SM publishes.',
  ],
  zamane_archive_invalid: [
    422,
    'Dosya bir Zamane istemcisi değil: bir .jar ya da tek bir tss-client-console-*.jar içeren .zip olmalı.',
    'The file is not a Zamane client: it must be a .jar, or a .zip holding exactly one tss-client-console-*.jar.',
  ],
  zamane_sha256_required: [
    422,
    'Bu sürüm bilinen sürümler arasında değil; Kamu SM indirme sayfasındaki SHA-256 değerini girin.',
    'This is not a known release; enter the SHA-256 shown on the Kamu SM download page.',
  ],
  zamane_sha256_mismatch: [
    422,
    'Dosyanın SHA-256 değeri girdiğiniz değerle aynı değil; dosya kullanılmadı.',
    "The file's SHA-256 does not match the value you entered; the file was not used.",
  ],
  upload_too_large: [413, 'Dosya izin verilen boyuttan büyük.', 'The file is larger than allowed.'],
  upload_kind_invalid: [422, 'Bilinmeyen yükleme türü.', 'Unknown upload kind.'],
  upload_not_found: [
    404,
    'Yüklenen dosya bulunamadı ya da süresi doldu (30 dakika); yeniden yükleyin.',
    'The uploaded file was not found or has expired (30 minutes); upload it again.',
  ],
  seal_not_due: [
    409,
    'Şimdi yalnızca sıradaki (en eski) mühürlenmemiş kapalı gün mühürlenebilir; bu gün henüz sırada değil ya da gece yarısından hemen sonraki beklemede.',
    'Only the next (oldest) closed unsealed day can be sealed now; this day is not next yet, or it is in the wait just after midnight.',
  ],
  seal_day_sealed: [409, 'Bu gün zaten mühürlü.', 'This day is already sealed.'],
  seal_not_applicable: [
    409,
    'Bu gün mühürlenmez (veri yok, atlandı ya da imha edildi).',
    'This day is not sealed (no data, skipped or deleted).',
  ],
  seal_request_pending: [
    409,
    'Bu gün için bir mühürleme isteği zaten bekliyor.',
    'A sealing request for this day is already waiting.',
  ],
  tsa_test_confirm_required: [
    422,
    'Damga testi 1 kontör harcar; onaylayın.',
    'The stamp test uses 1 credit; confirm to continue.',
  ],
  tsa_test_limit: [
    429,
    'Bugünkü damga testi sınırına ulaşıldı.',
    "Today's stamp test limit has been reached.",
  ],
  lease_rebuild_range: [
    422,
    'Geçersiz aralık: en çok 31 gün, gelecekte değil ve ilk logdan önce değil.',
    'Invalid range: at most 31 days, not in the future and not before the first log.',
  ],
  setting_invalid: [
    422,
    'Bazı ayar değerleri geçersiz; işaretli ayarları düzeltin.',
    'Some setting values are invalid; correct the marked settings.',
  ],
  setting_unknown: [422, 'Bilinmeyen ayar.', 'Unknown setting.'],
  setting_locked: [
    422,
    'Bu ayar web arayüzünden değiştirilemez (sunucudaki bir dosya yolu).',
    'This setting cannot be changed from the web interface (a file path on the server).',
  ],
  setting_conflict: [
    409,
    'Ayar siz düzenlerken başka biri tarafından değiştirildi; sayfayı yenileyin.',
    'The setting was changed by someone else while you were editing; reload the page.',
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
    'Yönetim hizmeti ayar dosyasına yazma iznini kaybetmiş; kurulumu onarın.',
    'The administration service has lost write access to the configuration; repair the installation.',
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

export interface AdminOptions {
  /** The administration service's heartbeat (default `ok`). */
  agent?: AgentState;
  /** `policy.hold_hours` (default 0: retention and replica changes apply at once). */
  holdHours?: number;
  /** Milliseconds per step of a request (default 250). */
  stepMs?: number;
  /** The mock's "now" at start (default: the real time), e.g. the browser clock of a spec. */
  now?: number;
  /** The Kamu SM section starts empty (the first setup in the wizard). */
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
  /** The step that runs (the ones before it are done). */
  running: number;
}

interface Final {
  state: RequestState;
  error?: string;
  /** Step states of the end that differ from `done`. */
  steps?: Partial<Record<RequestStep, StepState>>;
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
  /** Undoes what accepting it did at once (a staged Zamane client), when it is cancelled. */
  cancel?: () => void;
}

const SEALABLE = new Set(['failed', 'pending']);
const RELOAD_STEPS = new Set<RequestStep>([
  'reload.collector',
  'reload.ingest',
  'reload.worker',
  'reload.api',
  'system.firewall',
  'system.time',
  'jar.promotion',
]);
const APPLYING_STEPS = new Set<RequestStep>([
  'backup',
  'write_secrets',
  'write_config',
  'install_files',
  'verify',
  'rollback',
]);

function sha256(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

function newId(): string {
  return createHash('sha256').update(`${Date.now()}-${Math.random()}`).digest('hex').slice(0, 32);
}

/** `{field, code, message_tr, message_en}` of a refused field (422 `details`). */
function problem(field: string, code: string) {
  const [, tr, en] = HTTP[code] ?? [422, code, code];
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

const TSA_KEYS = [
  'etag',
  'server',
  'credit_alert_threshold',
  'timeout_seconds',
  'java',
  'customer',
  'password',
  'clear_credentials',
  'confirm',
  'probe',
];
const TSA_REQUIRED = [
  'etag',
  'server',
  'credit_alert_threshold',
  'timeout_seconds',
  'java',
  'probe',
];
const SMTP_REQUIRED = [
  'etag',
  'enabled',
  'host',
  'port',
  'security',
  'username',
  'from_address',
  'timeout_seconds',
  'probe',
];
const SMTP_KEYS = [...SMTP_REQUIRED, 'password', 'clear_password', 'test_to'];
const REPLICA_REQUIRED = ['etag', 'enabled', 'target', 'user', 'probe'];
const REPLICA_KEYS = [...REPLICA_REQUIRED, 'password', 'clear_password'];
const RETENTION_REQUIRED = ['etag', 'enabled', 'days'];
const RETENTION_KEYS = [...RETENTION_REQUIRED, 'confirm_delete_days'];
const BACKUP_KEYS = ['etag', 'enabled', 'keep_daily', 'keep_weekly', 'keep_monthly', 'min_free_gb'];

/**
 * Request bodies of the administration API: every field it knows and the required ones (the
 * API's models are `extra="forbid"`).
 */
const BODIES: Record<string, { known: string[]; required: string[] }> = {
  'PUT /admin/config/general': {
    known: ['etag', 'site_name', 'clock', 'probe'],
    required: ['etag', 'site_name', 'clock', 'probe'],
  },
  'POST /admin/config/general/check': {
    known: ['etag', 'site_name', 'clock', 'probe'],
    required: ['etag', 'site_name', 'clock', 'probe'],
  },
  'PUT /admin/config/tsa': { known: TSA_KEYS, required: TSA_REQUIRED },
  'POST /admin/config/tsa/check': { known: TSA_KEYS, required: TSA_REQUIRED },
  'POST /admin/config/tsa/roots': {
    known: ['etag', 'upload_id', 'legal', 'confirmed'],
    required: ['etag', 'upload_id', 'legal', 'confirmed'],
  },
  'POST /admin/config/tsa/legal-roots': {
    known: ['etag', 'sha256', 'confirmed'],
    required: ['etag', 'sha256', 'confirmed'],
  },
  'POST /admin/config/tsa/zamane': {
    known: ['etag', 'upload_id', 'expected_sha256'],
    required: ['etag', 'upload_id'],
  },
  'POST /admin/config/sources': { known: ['etag', 'source'], required: ['etag', 'source'] },
  'PUT /admin/config/smtp': { known: SMTP_KEYS, required: SMTP_REQUIRED },
  'POST /admin/config/smtp/check': { known: SMTP_KEYS, required: SMTP_REQUIRED },
  'POST /admin/config/smtp/ca': { known: ['etag', 'upload_id'], required: ['etag', 'upload_id'] },
  'PUT /admin/config/replica': { known: REPLICA_KEYS, required: REPLICA_REQUIRED },
  'POST /admin/config/replica/check': { known: REPLICA_KEYS, required: REPLICA_REQUIRED },
  'PUT /admin/config/retention': { known: RETENTION_KEYS, required: RETENTION_REQUIRED },
  'POST /admin/config/retention/check': { known: RETENTION_KEYS, required: RETENTION_REQUIRED },
  'PUT /admin/config/backup': { known: BACKUP_KEYS, required: BACKUP_KEYS },
  'POST /admin/config/backup/check': { known: BACKUP_KEYS, required: BACKUP_KEYS },
  'POST /admin/config/sources/check': { known: ['etag', 'source'], required: ['etag', 'source'] },
  'PATCH /settings': { known: ['changes'], required: ['changes'] },
  'POST /admin/actions/tsa-test': { known: ['confirm_credit'], required: [] },
  'POST /admin/actions/replica-test': { known: [], required: [] },
  'POST /admin/actions/test-email': { known: ['to'], required: ['to'] },
  'POST /admin/actions/lease-rebuild': { known: ['from', 'to'], required: ['from', 'to'] },
};

/** Problems of a body, in the API's `details` format (`[]` when none). */
function bodyProblems(
  route: string,
  body: unknown,
): { loc: string[]; type: string; msg: string }[] {
  const spec = BODIES[route];
  if (!spec) return [];
  if (typeof body !== 'object' || body === null || Array.isArray(body))
    return [{ loc: ['body'], type: 'model_attributes_type', msg: 'Input should be an object' }];
  return [
    ...Object.keys(body)
      .filter((key) => !spec.known.includes(key))
      .map((key) => ({
        loc: ['body', key],
        type: 'extra_forbidden',
        msg: 'Extra inputs are not permitted',
      })),
    ...spec.required
      .filter((key) => !Object.hasOwn(body, key))
      .map((key) => ({ loc: ['body', key], type: 'missing', msg: 'Field required' })),
  ];
}

/** The machine configuration every test starts with (the repository's fictional site). */
function startConfig(options: AdminOptions): AdminConfig['sections'] {
  const installed = '2026-09-30T09:12:00+03:00';
  return {
    general: {
      site_name: 'Örnek A.Ş.',
      clock: {
        ntp_server: '172.20.10.1',
        fallback_server: 'time.ume.tubitak.gov.tr',
        alert_offset_ms: 1000,
      },
    },
    tsa: options.freshTsa
      ? {
          provider: 'mock',
          server: null,
          credit_alert_threshold: 500,
          timeout_seconds: 60,
          java: null,
          java_choices: JAVA_CHOICES,
          zamane: { present: false, sha256: null, release: null, trusted: false, staged: [] },
          roots: [],
          secrets: {
            'kamusm.customer': { set: false, changed_at: null },
            'kamusm.password': { set: false, changed_at: null },
          },
          readiness: { production: false, missing: ['credentials', 'zamane_jar', 'java', 'roots'] },
          seal_ready: { ok: true, causes: [] },
        }
      : {
          provider: 'zamane',
          server: 'test',
          credit_alert_threshold: 500,
          timeout_seconds: 60,
          java: JAVA_CHOICES[0] ?? null,
          java_choices: JAVA_CHOICES,
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
          secrets: {
            'kamusm.customer': { set: true, changed_at: installed, changed_by: 'cli' },
            'kamusm.password': { set: true, changed_at: installed, changed_by: 'cli' },
          },
          readiness: { production: true, missing: [] },
          seal_ready: { ok: true, causes: [] },
        },
    smtp: {
      enabled: true,
      host: 'smtp.ornek.example',
      port: 587,
      security: 'starttls',
      username: 'arcilogger',
      from_address: 'arcilogger@ornek.example',
      timeout_seconds: 30,
      ca: { sha256: null },
      secrets: { 'smtp.password': { set: true, changed_at: installed, changed_by: 'cli' } },
    },
    replica: {
      enabled: true,
      target: '\\\\NAS-01\\arcilogger-worm',
      user: 'NAS-01\\arcireplica',
      secrets: {
        'replica.password': { set: true, changed_at: installed, changed_by: 'installer' },
      },
    },
    retention: {
      enabled: false,
      days: 760,
      minimum_days: 730,
      preview: { cutoff: '2024-09-13', days_to_delete: 0, oldest: null, newest: null },
    },
    backup: { enabled: true, keep_daily: 14, keep_weekly: 8, keep_monthly: 12, min_free_gb: 10 },
    sources: [
      source('fortigate', 'fortigate', '172.31.255.1', 10, '2026-10-13T14:59:58+03:00'),
      source('core-r1', 'mikrotik', '172.20.10.1', 15, '2026-10-13T14:59:41+03:00'),
      source('switch-1', 'mikrotik', '172.20.10.21', 15, '2026-10-13T14:52:10+03:00'),
    ],
  };
}

const JAVA_CHOICES = [
  'C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe',
  'C:/Program Files/Java/jre1.8.0_421/bin/java.exe',
];
/** SHA-256 of the installer-recorded Zamane client and root of the fictional site. */
const INSTALLED_JAR = sha256('e2e installer zamane client');
const INSTALLED_ROOT = sha256('e2e kamu sm test root');

function source(
  name: string,
  type: SourceOut['type'],
  address: string,
  heartbeat: number,
  lastSeen: string | null,
): SourceOut {
  return {
    name,
    type,
    address,
    heartbeat_minutes: heartbeat,
    expected: true,
    transport: 'any',
    enabled: true,
    firewall: 'open',
    last_seen: lastSeen,
  };
}

const LOCKED: AdminConfig['locked'] = [
  { path: 'tsa.digest', reason: 'expert' },
  { path: 'tsa.credit_regex', reason: 'expert' },
  { path: 'tsa.zamane_verify_flag', reason: 'expert' },
  { path: 'tsa.require_critical_eku', reason: 'expert' },
  { path: 'tsa.roots_dir', reason: 'security' },
  { path: 'clock.ntp_port', reason: 'service' },
  { path: 'collector.unknown_senders', reason: 'collector' },
  { path: 'seal.quiet_minutes', reason: 'evidence' },
  { path: 'seal.retry_minutes', reason: 'evidence' },
  { path: 'backup.dir', reason: 'security' },
  { path: 'backup.pg_dump', reason: 'security' },
  { path: 'backup.pg_restore', reason: 'security' },
];

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
  readonly versions: ConfigVersion[] = [];
  readonly settings: SettingGroup[] = startSettings();
  readonly uploads = new Map<string, UploadPreview>();
  private lastCredit: number | null = null;
  private lastReplicaTest: number | null = null;
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
  }

  /** The mock's clock (the spec's browser time when given). */
  now(): Date {
    return new Date(this.base + (Date.now() - this.realStart));
  }

  private iso(offsetMs = 0): string {
    return new Date(this.now().getTime() + offsetMs).toISOString();
  }

  refuse(status: number, code: string, details?: unknown): [number, unknown] {
    const [, tr, en] = HTTP[code] ?? [status, code, code];
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

  // -- the configuration ----------------------------------------------------------------------

  /** What the API compares for a section's `etag`. */
  private editable(section: AdminSection): unknown {
    const values = this.sections[section];
    if (section === 'sources')
      return (values as SourceOut[]).map((item) => ({
        name: item.name,
        type: item.type,
        address: item.address,
        heartbeat_minutes: item.heartbeat_minutes,
        expected: item.expected,
        transport: item.transport,
        enabled: item.enabled,
      }));
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
          preview: this.retentionPreview(this.sections.retention.days),
        },
      }),
      locked: copy(LOCKED),
      policy: {
        enabled: this.agent !== 'disabled',
        source_networks: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'],
        min_prefix_v4: 16,
        max_sources: 500,
        hold_hours: this.holdHours,
        jar_hold_hours: 24,
        tsa_test_per_day: 3,
      },
      pending: [...this.requests.values()]
        .filter((item) => item.out.state === 'pending' && item.out.section && item.out.effective_at)
        .map((item) => ({
          request_id: item.out.id,
          section: item.out.section as AdminSection,
          effective_at: item.out.effective_at as string,
        })),
      problems: this.sections.general.clock.ntp_server
        ? []
        : [
            {
              level: 'warning',
              key: 'clock.ntp_server',
              message_tr: WARNINGS.ntp_server_empty?.[0] ?? '',
              message_en: WARNINGS.ntp_server_empty?.[1] ?? '',
            },
          ],
      restart_required: this.restartRequired,
    };
  }

  status(): AdminStatus {
    const sha = sha256(JSON.stringify(this.sections));
    const heartbeat =
      this.agent === 'missing' ? null : this.iso(this.agent === 'stale' ? -75_000 : -4_000);
    const service = (restart: string[] = []) => ({
      sha256: sha,
      loaded_at: this.iso(-60_000),
      error: null,
      restart_keys: restart,
      running: true,
    });
    return {
      agent: {
        state: this.agent,
        version: this.agent === 'missing' ? null : '0.1.0',
        heartbeat_at: heartbeat,
        queue: [...this.requests.values()].filter((item) => item.out.state === 'queued').length,
        pending: [...this.requests.values()]
          .filter((item) => item.out.state === 'pending' && item.out.effective_at)
          .map((item) => ({ id: item.out.id, effective_at: item.out.effective_at as string })),
        java_candidates: JAVA_CHOICES,
        seal_ready: this.sections.tsa.seal_ready,
        system_task: { ok: true, checked_at: this.iso(-120_000), error: null },
      },
      system: {
        state: 'ok',
        at: this.iso(-120_000),
        applied_config_sha256: sha,
        firewall: {
          managed: true,
          strict_outbound: false,
          syslog_addresses: this.sections.sources
            .filter((item) => item.enabled)
            .map((item) => item.address),
        },
        time: {
          peers: `${this.sections.general.clock.ntp_server},0x8 ${this.sections.general.clock.fallback_server},0xa`,
        },
        code: null,
      },
      services: {
        ArciCollector: service(this.restartRequired ? ['collector'] : []),
        ArciIngest: service(),
        ArciWorker: service(),
        ArciApi: service(),
      },
      config_sha256: sha,
      config_origin: this.outside ? 'outside' : 'browser',
      restart_required: this.restartRequired,
    };
  }

  /** 503 while the administration service does not answer or is switched off. */
  private unavailable(): [number, unknown] | null {
    if (this.agent === 'missing') return this.refuse(503, 'admin_agent_unavailable');
    if (this.agent === 'disabled') return this.refuse(503, 'admin_disabled');
    return null;
  }

  private busy(section: AdminSection): boolean {
    return [...this.requests.values()].some(
      (item) =>
        item.out.section === section && item.out.kind === 'config.apply' && !item.out.terminal,
    );
  }

  // -- requests ---------------------------------------------------------------------------------

  /** Advance every request to the stage its age reached. */
  tick(): void {
    for (const item of this.requests.values()) this.advance(item);
  }

  private advance(item: MockRequest): void {
    if (item.out.terminal) return;
    const out = item.out;
    if (item.cancelled) {
      this.finish(item, { state: 'cancelled' });
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
    out.label_tr = STATE_LABELS[stage.state][0];
    out.label_en = STATE_LABELS[stage.state][1];
    out.steps = item.steps.map((step, index) => ({
      step,
      state: index < stage.running ? 'done' : index === stage.running ? 'running' : 'pending',
      ...(index < stage.running ? { at: this.iso() } : {}),
    }));
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
    out.label_tr = STATE_LABELS[final.state][0];
    out.label_en = STATE_LABELS[final.state][1];
    out.steps = item.steps.map((step) => ({
      step,
      state: final.steps?.[step] ?? 'done',
      at: this.iso(),
    }));
    if (final.error) out.error = { ...message(final.error) };
    out.rolled_back = final.rolledBack ?? false;
    if (final.manual) [out.manual_steps_tr, out.manual_steps_en] = final.manual;
    for (const code of final.warnings ?? [])
      if (!out.warnings.some((warning) => warning.code === code)) out.warnings.push(message(code));
    if (['active', 'partially_active', 'succeeded'].includes(final.state)) this.applyOnce(item);
    out.updated_at = this.iso();
  }

  /** Stages of a request through `steps`, and how it ends. */
  private plan(
    steps: RequestStep[],
    outcome: Outcome,
    kind: RequestKind,
  ): { steps: RequestStep[]; stages: Stage[]; final: Final; apply: boolean } {
    const action = kind.startsWith('action.') || kind.startsWith('worker.');
    const stages: Stage[] = [{ at: 0, state: 'queued', running: -1 }];
    let at = this.stepMs;
    const stateOf = (step: RequestStep): RequestState => {
      if (step === 'probe') return 'probing';
      if (step === 'hold') return 'pending';
      if (step === 'dispatch') return 'dispatched';
      if (step === 'task') return 'running';
      if (APPLYING_STEPS.has(step)) return 'applying';
      if (RELOAD_STEPS.has(step)) return 'applied';
      return 'received';
    };
    if (outcome === 'expired')
      return {
        steps: [],
        stages,
        final: { state: 'expired', error: 'request_expired' },
        apply: false,
      };
    if (outcome === 'rejected')
      return {
        steps: ['received'],
        stages,
        final: { state: 'rejected', error: 'signature_invalid', steps: { received: 'failed' } },
        apply: false,
      };
    let list = [...steps];
    let final: Final = { state: action ? 'succeeded' : 'active' };
    let apply = true;
    if (outcome === 'probe_failed' && list.includes('probe')) {
      const cut = list.indexOf('probe');
      const skipped = Object.fromEntries(
        list.slice(cut + 1).map((step): [RequestStep, StepState] => [step, 'skipped']),
      );
      final = {
        state: 'probe_failed',
        error: 'probe_failed',
        steps: { probe: 'failed', ...skipped },
      };
      apply = false;
    } else if (outcome === 'conflict') {
      const skipped = Object.fromEntries(
        list.slice(2).map((step): [RequestStep, StepState] => [step, 'skipped']),
      );
      final = { state: 'conflict', error: 'conflict', steps: { validate: 'failed', ...skipped } };
      apply = false;
    } else if (outcome === 'failed' || outcome === 'needs_attention') {
      const verify = list.indexOf('verify');
      list = [...list.slice(0, verify + 1), 'rollback'];
      final =
        outcome === 'failed'
          ? {
              state: 'failed',
              error: 'verify_failed',
              rolledBack: true,
              steps: { verify: 'failed', rollback: 'done' },
            }
          : {
              state: 'needs_attention',
              error: 'rollback_failed',
              steps: { verify: 'failed', rollback: 'failed' },
              manual: [
                '1. Sunucuda yönetici olarak bir komut istemi açın.\n2. python -I -m arcilogger_core.admin versions list\n3. python -I -m arcilogger_core.admin versions restore 17',
                '1. Open a command prompt as an administrator on the server.\n2. python -I -m arcilogger_core.admin versions list\n3. python -I -m arcilogger_core.admin versions restore 17',
              ],
            };
      apply = false;
    } else if (outcome === 'partially_active') {
      // The last service of the request has not loaded it; a system step failed.
      const reloads = list.filter((step) => step.startsWith('reload.'));
      const system = list.filter((step) => step.startsWith('system.'));
      const steps: Partial<Record<RequestStep, StepState>> = {};
      const last = reloads.at(-1);
      if (last) steps[last] = 'pending';
      for (const step of system) steps[step] = 'failed';
      final = {
        state: 'partially_active',
        ...(system.length > 0 ? { error: 'system_sync_failed' } : {}),
        steps,
        warnings: ['service_not_reloaded'],
      };
    }
    list.forEach((step, index) => {
      stages.push({ at, state: stateOf(step), running: index });
      at += this.stepMs;
    });
    return { steps: list, stages, final, apply };
  }

  private enqueue(init: {
    kind: RequestKind;
    section: AdminSection | null;
    account: MockUser;
    steps: RequestStep[];
    changes?: Record<string, Change>;
    warnings?: string[];
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
    const planned = this.plan(init.steps, outcome, init.kind);
    const held = init.hold !== undefined && init.hold !== null;
    if (held) {
      const hold = planned.steps.indexOf('hold');
      planned.stages = planned.stages.filter((stage) => stage.running <= hold);
    }
    if (init.failure)
      planned.final = {
        state: init.failure.state,
        error: init.failure.error,
        steps: { task: 'failed' },
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
      apply: planned.apply && !init.failure ? (init.apply ?? null) : null,
      applied: false,
      held,
      cancelled: false,
      before: init.before ?? null,
      day: init.day ?? null,
    };
    if (init.result !== undefined) {
      const result = init.result;
      const apply = item.apply;
      item.apply = () => {
        out.result = result;
        apply?.();
      };
    }
    this.requests.set(id, item);
    return item;
  }

  private accepted(item: MockRequest, effects: string[] = []): [number, AdminAccepted] {
    return [
      202,
      {
        request_id: item.out.id,
        short_id: item.out.short_id,
        state: 'queued',
        status_url: `/api/v1/admin/requests/${item.out.id}`,
        guarded: item.held,
        effective_at: item.out.effective_at,
        changes: item.out.changes,
        effects,
        warnings: item.out.warnings,
      },
    ];
  }

  /** A configuration change applied: a new version keeps the pair before it. */
  private recordVersion(item: MockRequest, account: MockUser): void {
    const n = (this.versions[0]?.n ?? 16) + 1;
    this.versions.unshift({
      n,
      at: this.iso(),
      request_id: item.out.id,
      actor: account.username,
      paths: Object.keys(item.out.changes),
      has_secrets: Object.values(item.out.changes).some((change) => 'secret' in change),
    });
    item.out.version = { number: n, from: sha256(`${n}-from`), to: sha256(`${n}-to`) };
  }

  // -- the general section ----------------------------------------------------------------------

  private generalCheck(body: { site_name?: unknown; clock?: unknown }) {
    const problems = [];
    const name = typeof body.site_name === 'string' ? body.site_name.trim() : '';
    if (!name) problems.push(problem('site_name', 'site_name_required'));
    const clock = (body.clock ?? {}) as Record<string, unknown>;
    const ntp = typeof clock.ntp_server === 'string' ? clock.ntp_server.trim() : '';
    const fallback = typeof clock.fallback_server === 'string' ? clock.fallback_server.trim() : '';
    if (ntp && !validHost(ntp)) problems.push(problem('clock.ntp_server', 'host_invalid'));
    if (fallback && !validHost(fallback))
      problems.push(problem('clock.fallback_server', 'host_invalid'));
    if (!ntp && !fallback)
      problems.push(problem('clock.fallback_server', 'clock_servers_required'));
    const offset = clock.alert_offset_ms;
    if (typeof offset !== 'number' || !Number.isInteger(offset) || offset < 1 || offset > 60_000)
      problems.push(problem('clock.alert_offset_ms', 'config_invalid'));
    const current = this.sections.general;
    const next = {
      site_name: name,
      clock: { ntp_server: ntp, fallback_server: fallback, alert_offset_ms: Number(offset) },
    };
    const changes = {
      ...diff('', { site_name: current.site_name }, { site_name: next.site_name }),
      ...diff('clock', current.clock as unknown as Record<string, unknown>, next.clock),
    };
    const clockChanged = Object.keys(changes).some((path) => path.startsWith('clock.'));
    const warnings = !ntp && fallback ? ['ntp_server_empty'] : [];
    const effects = [
      ...(changes.site_name ? ['reload.api', 'reload.worker'] : []),
      ...(clockChanged ? ['system.time', 'task.next_run:ClockCheck'] : []),
    ];
    return { problems, next, changes, clockChanged, warnings, effects };
  }

  private general(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as {
      etag?: string;
      site_name?: unknown;
      clock?: unknown;
      probe?: boolean;
    };
    const checked = this.generalCheck(body);
    if (checked.problems.length > 0) return this.refuse(422, 'config_invalid', checked.problems);
    const check: AdminCheck = {
      changes: checked.changes,
      effects: checked.effects,
      warnings: checked.warnings.map(message),
      guarded: false,
      effective_at: null,
      reauth_required: checked.clockChanged,
    };
    if (dryRun) return [200, check];
    if (checked.clockChanged) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    const down = this.unavailable();
    if (down) return down;
    if (body.etag !== this.etag('general'))
      return this.refuse(409, 'config_conflict', { current_etag: this.etag('general') });
    if (this.busy('general')) return this.refuse(409, 'admin_section_busy');
    const before = copy(this.sections.general);
    const steps: RequestStep[] = [
      'received',
      'validate',
      ...(checked.clockChanged && body.probe !== false ? (['probe'] as RequestStep[]) : []),
      'backup',
      'write_config',
      'verify',
      'reload.api',
      'reload.worker',
      ...(checked.clockChanged ? (['system.time'] as RequestStep[]) : []),
      'journal',
    ];
    const outcome = this.takeOutcome();
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'general',
      account,
      steps,
      changes: checked.changes,
      warnings: [...checked.warnings, ...(this.agent === 'stale' ? ['agent_stale'] : [])],
      outcome,
      before,
      apply: () => {
        this.sections.general = checked.next;
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, checked.effects);
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

  /** A refusal with the status of the contract's catalogue (2.12). */
  private reject(code: string, details?: unknown): [number, unknown] {
    return this.refuse(HTTP[code]?.[0] ?? 422, code, details);
  }

  /** The common refusals of a change: service down, stale `etag`, a change still running. */
  private blocked(section: AdminSection, etag: unknown): [number, unknown] | null {
    const down = this.unavailable();
    if (down) return down;
    if (etag !== this.etag(section))
      return this.refuse(409, 'config_conflict', { current_etag: this.etag(section) });
    if (this.busy(section)) return this.refuse(409, 'admin_section_busy');
    return null;
  }

  // -- uploads (contract 2.4.4, 2.6 "tsa") -----------------------------------------------------

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
    const preview: UploadPreview = {
      upload_id: newId(),
      kind,
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
        kind: zip ? 'zip' : 'jar',
        jar_name: zip ? 'tss-client-console-3.1.4.jar' : file.name,
        jar_sha256: zip ? sha256(`${file.content} jar`) : digest,
        release: known ? 'tss-client-console 3.1.4' : null,
        known,
        accepted: true,
        reason: null,
      };
    } else {
      if (!file.content.includes('BEGIN CERTIFICATE'))
        return this.reject('root_certificate_invalid');
      const expired = file.content.includes('expired');
      const subject =
        /^Subject: (.+)$/m.exec(file.content)?.[1] ??
        'CN=Kamu SM Kök Sertifika Hizmet Sağlayıcısı - Sürüm 2 (örnek)';
      preview.certificate = {
        subject,
        issuer: subject,
        not_before: '2021-06-01T00:00:00+03:00',
        not_after: expired ? '2025-06-01T00:00:00+03:00' : '2041-06-01T00:00:00+03:00',
        is_ca: true,
        self_signed: true,
        key: 'RSA 4096',
        installed: this.sections.tsa.roots.some((root) => root.sha256 === digest),
        warnings: expired ? [message('root_expired')] : [],
      };
    }
    this.uploads.set(preview.upload_id, preview);
    return [201, copy(preview)];
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
    const body = call.body as {
      etag?: string;
      server?: unknown;
      credit_alert_threshold?: unknown;
      timeout_seconds?: unknown;
      java?: unknown;
      customer?: unknown;
      password?: unknown;
      clear_credentials?: unknown;
      confirm?: unknown;
      probe?: unknown;
    };
    if (body.server === 'mock') return this.reject('tsa_mock_refused');
    if (body.server !== 'test' && body.server !== 'production')
      return this.reject('tsa_server_invalid');
    const problems = [];
    const threshold = body.credit_alert_threshold;
    if (
      typeof threshold !== 'number' ||
      !Number.isInteger(threshold) ||
      threshold < 0 ||
      threshold > 1_000_000
    )
      problems.push(problem('credit_alert_threshold', 'config_invalid'));
    const timeout = body.timeout_seconds;
    if (typeof timeout !== 'number' || !Number.isInteger(timeout) || timeout < 5 || timeout > 600)
      problems.push(problem('timeout_seconds', 'config_invalid'));
    if (problems.length > 0) return this.refuse(422, 'config_invalid', problems);
    const java = typeof body.java === 'string' ? body.java : null;
    if (java !== null && !JAVA_CHOICES.includes(java))
      return this.reject('java_not_detected', [problem('java', 'java_not_detected')]);
    const customer = typeof body.customer === 'string' ? body.customer : null;
    const password = typeof body.password === 'string' ? body.password : null;
    if (customer !== null && !/^[^\s\p{Cc}\p{Cf}]{1,64}$/u.test(customer))
      return this.reject('secret_invalid', [problem('customer', 'secret_invalid')]);
    if (password !== null && !/^[^\p{Cc}\p{Cf}]{1,128}$/u.test(password))
      return this.reject('secret_invalid', [problem('password', 'secret_invalid')]);
    const clear = body.clear_credentials === true;
    const current = this.sections.tsa;
    const candidate = copy(current);
    candidate.provider = 'zamane';
    candidate.server = body.server;
    candidate.java = java;
    candidate.credit_alert_threshold = threshold as number;
    candidate.timeout_seconds = timeout as number;
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
    if (body.server === 'production' && !candidate.readiness.production)
      return this.reject('tsa_not_ready', { missing: candidate.readiness.missing });
    if (production && body.server === 'test' && body.confirm !== true)
      return this.reject('tsa_confirm_required');
    if (current.seal_ready.ok && !candidate.seal_ready.ok)
      return this.reject('tsa_would_break', { causes: candidate.seal_ready.causes });
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
    if (body.server === 'production' && !production) {
      if (!candidate.roots.some((root) => root.legal)) warnings.push('legal_roots_empty');
      if (!this.tsaTested()) warnings.push('production_not_tested');
    }
    if (production && body.server === 'test') warnings.push('production_stamps_stop');
    const sealing = reauth;
    const effects = ['reload.worker', 'reload.api', ...(sealing ? ['task.next_run:Seal'] : [])];
    const check: AdminCheck = {
      changes,
      effects,
      warnings: warnings.map(message),
      guarded: false,
      effective_at: null,
      reauth_required: reauth,
      readiness: candidate.readiness,
    };
    if (dryRun) return [200, check];
    if (reauth) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    const refused = this.blocked('tsa', body.etag);
    if (refused) return refused;
    const probed =
      body.probe !== false &&
      (Object.keys(secretChanges).length > 0 ||
        Boolean(changes['tsa.server'] ?? changes['tsa.provider']));
    const steps: RequestStep[] = [
      'received',
      'validate',
      ...(probed ? (['probe'] as RequestStep[]) : []),
      'backup',
      ...(Object.keys(secretChanges).length > 0 ? (['write_secrets'] as RequestStep[]) : []),
      'write_config',
      'verify',
      'reload.worker',
      'reload.api',
      'journal',
    ];
    const before = copy(current);
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'tsa',
      account,
      steps,
      changes,
      warnings,
      outcome: this.takeOutcome(),
      before,
      apply: () => {
        // Uploads applied meanwhile (roots, a client) stay.
        const now = this.sections.tsa;
        this.sections.tsa = this.refreshTsa({
          ...candidate,
          roots: now.roots,
          zamane: now.zamane,
        });
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, effects);
  }

  private addRoot(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    const body = call.body as {
      etag?: string;
      upload_id?: string;
      legal?: unknown;
      confirmed?: unknown;
    };
    const upload = this.uploads.get(body.upload_id ?? '');
    if (!upload?.certificate || upload.kind !== 'kamusm_root')
      return this.reject('upload_not_found');
    if (this.sections.tsa.roots.some((root) => root.sha256 === upload.sha256))
      return this.reject('root_exists');
    const legal = body.legal === true;
    if (legal && body.confirmed !== true) return this.reject('legal_root_confirm_required');
    const refused = this.blocked('tsa', body.etag);
    if (refused) return refused;
    const certificate = upload.certificate;
    const warnings = [
      ...certificate.warnings.map((warning) => warning.code),
      ...(!legal && !this.sections.tsa.roots.some((root) => root.legal)
        ? ['legal_roots_empty']
        : []),
    ];
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'tsa',
      account,
      steps: [
        'received',
        'validate',
        'backup',
        'install_files',
        'write_config',
        'verify',
        'reload.worker',
        'journal',
      ],
      changes: {
        'tsa.roots': { old: null, new: certificate.subject },
        ...(legal ? { 'tsa.legal_root_fingerprints': { old: null, new: upload.sha256 } } : {}),
      },
      warnings,
      outcome: this.takeOutcome(),
      before: copy(this.sections.tsa),
      apply: () => {
        this.sections.tsa.roots.push({
          sha256: upload.sha256,
          subject: certificate.subject,
          not_after: certificate.not_after,
          legal,
          source: 'web',
        });
        this.uploads.delete(upload.upload_id);
        this.refreshTsa();
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, ['reload.worker']);
  }

  private addLegalRoot(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    const body = call.body as { etag?: string; sha256?: string; confirmed?: unknown };
    const root = this.sections.tsa.roots.find((item) => item.sha256 === body.sha256);
    if (!root) return this.reject('root_not_installed');
    if (body.confirmed !== true) return this.reject('legal_root_confirm_required');
    const refused = this.blocked('tsa', body.etag);
    if (refused) return refused;
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'tsa',
      account,
      steps: [
        'received',
        'validate',
        'backup',
        'write_config',
        'verify',
        'reload.worker',
        'journal',
      ],
      changes: { 'tsa.legal_root_fingerprints': { old: null, new: root.sha256 } },
      outcome: this.takeOutcome(),
      before: copy(this.sections.tsa),
      apply: () => {
        const installed = this.sections.tsa.roots.find((entry) => entry.sha256 === root.sha256);
        if (installed) installed.legal = true;
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, ['reload.worker']);
  }

  private stageZamane(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    const body = call.body as { etag?: string; upload_id?: string; expected_sha256?: unknown };
    const upload = this.uploads.get(body.upload_id ?? '');
    const zamane = upload?.zamane;
    if (!upload || !zamane || upload.kind !== 'zamane_client')
      return this.reject('upload_not_found');
    if (!zamane.known) {
      if (typeof body.expected_sha256 !== 'string')
        return this.reject('zamane_sha256_required', [
          problem('expected_sha256', 'zamane_sha256_required'),
        ]);
      if (body.expected_sha256.toLowerCase() !== upload.sha256)
        return this.reject('zamane_sha256_mismatch', [
          problem('expected_sha256', 'zamane_sha256_mismatch'),
        ]);
    }
    const refused = this.blocked('tsa', body.etag);
    if (refused) return refused;
    const hold = zamane.known ? null : 24 * 3_600_000;
    const tsa = this.sections.tsa;
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'tsa',
      account,
      steps: [
        'received',
        'validate',
        ...(hold === null ? [] : (['hold'] as RequestStep[])),
        'install_files',
        'jar.promotion',
        'write_config',
        'verify',
        'reload.worker',
        'journal',
      ],
      changes: { 'tsa.zamane_jar': { old: tsa.zamane.sha256, new: zamane.jar_sha256 } },
      warnings: zamane.known ? [] : ['unknown_jar_held'],
      outcome: this.takeOutcome(),
      hold,
      before: copy(tsa),
      apply: () => {
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
        this.recordVersion(item, account);
      },
    });
    if (hold !== null) {
      tsa.zamane.staged.push({ sha256: zamane.jar_sha256, effective_at: item.out.effective_at });
      item.cancel = () => {
        this.sections.tsa.zamane.staged = this.sections.tsa.zamane.staged.filter(
          (entry) => entry.sha256 !== zamane.jar_sha256,
        );
      };
    }
    return this.accepted(item, ['jar.promotion', 'reload.worker']);
  }

  private tsaTest(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as { confirm_credit?: unknown };
    if (body.confirm_credit !== true) return this.reject('tsa_test_confirm_required');
    const down = this.unavailable();
    if (down) return down;
    const today = this.iso().slice(0, 10);
    const done = [...this.requests.values()].filter(
      (item) => item.out.kind === 'action.tsa_test' && item.out.created_at.slice(0, 10) === today,
    ).length;
    if (done >= 3) return this.reject('tsa_test_limit');
    const tsa = this.sections.tsa;
    const legal = tsa.roots.find((root) => root.legal) ?? null;
    const root = legal ?? tsa.roots[0] ?? null;
    const outcome = this.takeAction();
    const result = {
      provider: tsa.provider,
      legal_grade_claimed: legal !== null,
      legal_grade: legal !== null,
      root_sha256: root?.sha256 ?? null,
      root: root?.subject ?? null,
      verified: true,
      errors: [],
      warnings: [],
      gen_time: this.iso(),
      serial: sha256(`tsa test ${String(done)}`)
        .slice(0, 16)
        .toUpperCase(),
      tsa: 'CN=Kamu SM Zaman Damgası Test Sunucusu (örnek)',
      tsa_cert_not_after: '2031-01-01T00:00:00+03:00',
      container: 'test.zd',
    };
    const item = this.enqueue({
      kind: 'action.tsa_test',
      section: null,
      account,
      steps: ['received', 'dispatch', 'task', 'journal'],
      result: outcome === 'succeeded' ? result : null,
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

  /** The refusal of a source, or `null` (`editing`: the name of the source it replaces). */
  private sourceRefusal(
    source: unknown,
    editing: string | null,
    host: MockHost,
  ): [number, unknown] | null {
    if (typeof source !== 'object' || source === null || Array.isArray(source))
      return host.error(422, 'validation_error', [
        {
          loc: ['body', 'source'],
          type: 'model_attributes_type',
          msg: 'Input should be an object',
        },
      ]);
    const extra = Object.keys(source).filter((key) => !SOURCE_KEYS.includes(key));
    const missing = SOURCE_KEYS.filter((key) => !Object.hasOwn(source, key));
    if (extra.length > 0 || missing.length > 0)
      return host.error(422, 'validation_error', [
        ...extra.map((key) => ({
          loc: ['body', 'source', key],
          type: 'extra_forbidden',
          msg: 'Extra inputs are not permitted',
        })),
        ...missing.map((key) => ({
          loc: ['body', 'source', key],
          type: 'missing',
          msg: 'Field required',
        })),
      ]);
    const value = source as SourceConfig;
    const others = this.sections.sources.filter((item) => item.name !== editing);
    if (editing === null) {
      if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(value.name) || value.name.startsWith('unknown-'))
        return this.refuse(422, 'config_invalid', [problem('source.name', 'config_invalid')]);
      if (others.some((item) => item.name === value.name))
        return this.reject('source_name_taken', [problem('source.name', 'source_name_taken')]);
      if (this.sections.sources.length >= 500) return this.reject('source_limit');
    } else if (value.name !== editing) return this.reject('source_rename_not_allowed');
    const problems = [];
    if (!['fortigate', 'mikrotik', 'generic'].includes(value.type))
      problems.push(problem('source.type', 'config_invalid'));
    if (!['any', 'udp', 'tcp'].includes(value.transport))
      problems.push(problem('source.transport', 'config_invalid'));
    const heartbeat = value.heartbeat_minutes;
    if (!Number.isInteger(heartbeat) || heartbeat < 1 || heartbeat > 1440)
      problems.push(problem('source.heartbeat_minutes', 'config_invalid'));
    if (problems.length > 0) return this.refuse(422, 'config_invalid', problems);
    const address = (code: string) => this.reject(code, [problem('source.address', code)]);
    if (value.address.includes(':')) return address('source_ipv6_unsupported');
    const network = networkOf(value.address);
    if (!network)
      return this.refuse(422, 'config_invalid', [problem('source.address', 'config_invalid')]);
    if (
      RESERVED_NETWORKS.some((reserved) => overlap(reserved, network)) ||
      value.address === '255.255.255.255'
    )
      return address('source_reserved_address');
    if (LOCAL_ADDRESSES.includes(value.address)) return address('source_own_address');
    if (!POLICY_NETWORKS.some((allowed) => inside(allowed, network)))
      return address('source_network_not_allowed');
    if (network.prefix < 32 && network.prefix < 16) return address('source_prefix_too_short');
    if (others.some((item) => item.address === value.address))
      return address('source_address_taken');
    if (
      network.prefix < 32 &&
      others.some((item) => {
        const other = networkOf(item.address);
        return other !== null && other.prefix < 32 && overlap(other, network);
      })
    )
      return address('source_overlap');
    return null;
  }

  private sourceChange(
    call: RecordedCall,
    host: MockHost,
    mode: 'check' | 'add' | 'update',
    path: string | null,
  ): Answer {
    const body = call.body as { etag?: string; source?: unknown };
    const name =
      mode === 'update' ? path : ((body.source as { name?: unknown } | undefined)?.name ?? null);
    const existing = this.sections.sources.find((item) => item.name === name) ?? null;
    if (mode === 'update' && !existing) {
      const account = host.requirePermission(call, 'sources.manage');
      return Array.isArray(account) ? account : this.reject('source_not_found');
    }
    // A check of a known name is the check of its change, else of its addition.
    const editing = mode === 'add' ? null : (existing?.name ?? null);
    const disabling =
      existing !== null &&
      existing.enabled &&
      (body.source as { enabled?: unknown } | undefined)?.enabled === false;
    const account = host.requirePermission(call, 'sources.manage', mode !== 'check' && disabling);
    if (Array.isArray(account)) return account;
    const refusal = this.sourceRefusal(body.source, editing, host);
    if (refusal) return refusal;
    const source = body.source as SourceConfig;
    const fields = (item: SourceConfig | null): Record<string, unknown> =>
      item
        ? {
            type: item.type,
            address: item.address,
            transport: item.transport,
            heartbeat_minutes: item.heartbeat_minutes,
            expected: item.expected,
            enabled: item.enabled,
          }
        : {};
    const changes = diff(
      `sources.${source.name}`,
      fields(editing === null ? null : existing),
      fields(source),
    );
    const warnings = disabling && existing.expected ? ['source_disable_gaps'] : [];
    const effects = ['reload.collector', 'reload.ingest', 'system.firewall'];
    if (mode === 'check')
      return [
        200,
        {
          changes,
          effects,
          warnings: warnings.map(message),
          guarded: false,
          effective_at: null,
          reauth_required: disabling,
        } satisfies AdminCheck,
      ];
    const refused = this.blocked('sources', body.etag);
    if (refused) return refused;
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'sources',
      account,
      steps: SOURCE_STEPS,
      changes,
      warnings,
      outcome: this.takeOutcome(),
      before: copy(this.sections.sources),
      apply: () => {
        const row: SourceOut = {
          ...source,
          firewall: source.enabled ? 'open' : 'closed',
          last_seen: existing?.last_seen ?? null,
        };
        const at = this.sections.sources.findIndex((entry) => entry.name === source.name);
        if (at >= 0) this.sections.sources[at] = row;
        else this.sections.sources.push(row);
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, effects);
  }

  private removeSource(call: RecordedCall, host: MockHost, name: string): Answer {
    const account = host.requirePermission(call, 'sources.manage', true);
    if (Array.isArray(account)) return account;
    const existing = this.sections.sources.find((item) => item.name === name);
    if (!existing) return this.reject('source_not_found');
    if (call.query.get('confirm') !== name) return this.reject('source_confirm_mismatch');
    const refused = this.blocked('sources', call.query.get('etag'));
    if (refused) return refused;
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'sources',
      account,
      steps: SOURCE_STEPS,
      changes: { [`sources.${name}`]: { old: existing.address, new: null } },
      warnings: existing.expected && existing.enabled ? ['source_disable_gaps'] : [],
      outcome: this.takeOutcome(),
      before: copy(this.sections.sources),
      apply: () => {
        this.sections.sources = this.sections.sources.filter((entry) => entry.name !== name);
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, ['reload.collector', 'reload.ingest', 'system.firewall']);
  }

  // -- smtp, replica, retention, backup (contract 2.6) ------------------------------------------

  /**
   * The common part of a section's check and change: the dry run's answer, or the change
   * accepted (re-authentication, the service, the `etag`, a change still running), held for
   * `policy.hold_hours` when `held`.
   */
  private sectionChange(
    call: RecordedCall,
    host: MockHost,
    dryRun: boolean,
    account: MockUser,
    plan: {
      section: Exclude<AdminSection, 'general' | 'tsa' | 'sources'>;
      etag: unknown;
      changes: Record<string, Change>;
      warnings: string[];
      effects: string[];
      reauth: boolean;
      probe: boolean;
      secrets: boolean;
      held: boolean;
      reloads: RequestStep[];
      next: () => void;
      check?: Partial<AdminCheck>;
    },
  ): Answer {
    const hold = plan.held && this.holdHours > 0 ? this.holdHours * 3_600_000 : null;
    if (dryRun)
      return [
        200,
        {
          changes: plan.changes,
          effects: plan.effects,
          warnings: plan.warnings.map(message),
          guarded: hold !== null,
          effective_at: hold === null ? null : this.iso(hold),
          reauth_required: plan.reauth,
          ...plan.check,
        } satisfies AdminCheck,
      ];
    if (plan.reauth) {
      const recent = host.requirePermission(call, 'settings.manage', true);
      if (Array.isArray(recent)) return recent;
    }
    const refused = this.blocked(plan.section, plan.etag);
    if (refused) return refused;
    const steps: RequestStep[] = [
      'received',
      'validate',
      ...(plan.probe ? (['probe'] as RequestStep[]) : []),
      ...(hold === null ? [] : (['hold'] as RequestStep[])),
      'backup',
      ...(plan.secrets ? (['write_secrets'] as RequestStep[]) : []),
      'write_config',
      'verify',
      ...plan.reloads,
      'journal',
    ];
    const item = this.enqueue({
      kind: 'config.apply',
      section: plan.section,
      account,
      steps,
      changes: plan.changes,
      warnings: [...plan.warnings, ...(hold === null ? [] : ['change_held'])],
      outcome: this.takeOutcome(),
      hold,
      before: copy(this.sections[plan.section]),
      apply: () => {
        plan.next();
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, plan.effects);
  }

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

  private smtp(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as Record<string, unknown>;
    const current = this.sections.smtp;
    const text = (key: string) =>
      typeof body[key] === 'string' && body[key].trim() ? body[key].trim() : null;
    const next = {
      enabled: body.enabled === true,
      host: text('host'),
      port: body.port,
      security: body.security,
      username: text('username'),
      from_address: text('from_address'),
      timeout_seconds: body.timeout_seconds,
    };
    const problems = [];
    if (
      typeof next.port !== 'number' ||
      !Number.isInteger(next.port) ||
      next.port < 1 ||
      next.port > 65_535
    )
      problems.push(problem('port', 'config_invalid'));
    if (
      typeof next.timeout_seconds !== 'number' ||
      !Number.isInteger(next.timeout_seconds) ||
      next.timeout_seconds < 1 ||
      next.timeout_seconds > 300
    )
      problems.push(problem('timeout_seconds', 'config_invalid'));
    if (!['starttls', 'ssl', 'none'].includes(String(next.security)))
      problems.push(problem('security', 'config_invalid'));
    if (next.host !== null && !validHost(next.host)) problems.push(problem('host', 'host_invalid'));
    if (next.from_address !== null && !EMAIL.test(next.from_address))
      problems.push(problem('from_address', 'config_invalid'));
    if (next.enabled && next.host === null) problems.push(problem('host', 'config_invalid'));
    if (next.enabled && next.from_address === null)
      problems.push(problem('from_address', 'config_invalid'));
    if (problems.length > 0) return this.refuse(422, 'config_invalid', problems);
    const stored = current.secrets['smtp.password']?.set ?? false;
    const typed = typeof body.password === 'string' && body.password !== '';
    const clear = body.clear_password === true || (next.username === null && stored);
    const endpoint =
      next.host !== current.host ||
      next.port !== current.port ||
      next.security !== current.security ||
      next.username !== current.username;
    if (next.username !== null) {
      if (!typed && (!stored || body.clear_password === true))
        return this.reject('smtp_password_required', [
          problem('password', 'smtp_password_required'),
        ]);
      if (stored && !typed && endpoint)
        return this.reject('secret_rebind_required', [
          problem('password', 'secret_rebind_required'),
        ]);
      if (next.security === 'none' && !['localhost', '127.0.0.1', '::1'].includes(next.host ?? ''))
        return this.reject('smtp_plaintext_auth_refused', [
          problem('security', 'smtp_plaintext_auth_refused'),
        ]);
    }
    const needsTest = next.enabled && (!current.enabled || endpoint);
    if (needsTest) {
      const to = typeof body.test_to === 'string' ? body.test_to : null;
      if (to === null)
        return this.reject('smtp_test_recipient_required', [
          problem('test_to', 'smtp_test_recipient_required'),
        ]);
      if (!this.recipients(account).includes(to))
        return this.reject('test_email_recipient_not_allowed', [
          problem('test_to', 'test_email_recipient_not_allowed'),
        ]);
    }
    const values = (smtp: typeof next) => ({
      enabled: smtp.enabled,
      host: smtp.host,
      port: smtp.port,
      security: smtp.security,
      username: smtp.username,
      from_address: smtp.from_address,
      timeout_seconds: smtp.timeout_seconds,
    });
    const secret = this.secretChange(current.secrets['smtp.password'], body.password, clear);
    const changes: Record<string, Change> = {
      ...diff('smtp', values(current), values(next)),
      ...(secret ? { 'smtp.password': secret } : {}),
    };
    const warnings = [
      ...(current.enabled && !next.enabled ? ['smtp_disabled'] : []),
      ...(next.security === 'none' && next.username === null && next.enabled
        ? ['smtp_plaintext']
        : []),
    ];
    return this.sectionChange(call, host, dryRun, account, {
      section: 'smtp',
      etag: body.etag,
      changes,
      warnings,
      effects: ['reload.worker'],
      reauth: true,
      probe: needsTest && body.probe !== false,
      secrets: secret !== null,
      held: false,
      reloads: ['reload.worker'],
      next: () => {
        const by = { changed_at: this.iso(), changed_by: `web:${account.username}` };
        this.sections.smtp = {
          ...this.sections.smtp,
          ...(next as Omit<typeof current, 'ca' | 'secrets'>),
          secrets: secret
            ? { 'smtp.password': { set: secret.new === 'set', ...by } }
            : this.sections.smtp.secrets,
        };
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

  private smtpCa(call: RecordedCall, host: MockHost, clear: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    const body = (call.body ?? {}) as { etag?: string; upload_id?: string };
    const upload = clear ? null : this.uploads.get(body.upload_id ?? '');
    if (!clear && (!upload || upload.kind !== 'smtp_ca')) return this.reject('upload_not_found');
    const refused = this.blocked('smtp', clear ? call.query.get('etag') : body.etag);
    if (refused) return refused;
    const sha = upload?.sha256 ?? null;
    const item = this.enqueue({
      kind: 'config.apply',
      section: 'smtp',
      account,
      steps: [
        'received',
        'validate',
        'backup',
        'install_files',
        'write_config',
        'verify',
        'reload.worker',
        'journal',
      ],
      changes: { 'smtp.ca_file': { old: this.sections.smtp.ca.sha256, new: sha } },
      outcome: this.takeOutcome(),
      before: copy(this.sections.smtp),
      apply: () => {
        this.sections.smtp.ca = { sha256: sha };
        if (upload) this.uploads.delete(upload.upload_id);
        this.recordVersion(item, account);
      },
    });
    return this.accepted(item, ['reload.worker']);
  }

  private testEmail(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const to = (call.body as { to?: unknown }).to;
    const smtp = this.sections.smtp;
    if (!smtp.host || !smtp.from_address) return this.reject('smtp_not_configured');
    if (typeof to !== 'string' || !this.recipients(account).includes(to))
      return this.reject('test_email_recipient_not_allowed');
    const hour = Date.now() - 3_600_000;
    const sent = this.testEmails.filter((at) => at > hour);
    if (sent.length >= 5) return this.reject('admin_rate_limited', { retry_after: 600 });
    this.testEmails.push(Date.now());
    const outcome = this.takeAction();
    const item = this.enqueue({
      kind: 'worker.test_email',
      section: null,
      account,
      steps: ['received', 'task', 'journal'],
      result:
        outcome === 'succeeded'
          ? { ok: true, error_text: null }
          : { ok: false, error_text: '535 5.7.8 Kimlik doğrulama başarısız (b***@ornek.example)' },
    });
    return this.accepted(item);
  }

  private replica(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as Record<string, unknown>;
    const current = this.sections.replica;
    const target =
      typeof body.target === 'string' && body.target.trim() ? body.target.trim() : null;
    const user = typeof body.user === 'string' && body.user.trim() ? body.user.trim() : null;
    const enabled = body.enabled === true;
    if (target !== null && !UNC.test(target))
      return this.reject('replica_target_invalid', [problem('target', 'replica_target_invalid')]);
    if (enabled && target === null)
      return this.refuse(422, 'config_invalid', [problem('target', 'config_invalid')]);
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
        return this.reject('replica_user_invalid', [problem('user', 'replica_user_invalid')]);
      if (!typed && (!stored || clear))
        return this.reject('replica_password_required', [
          problem('password', 'replica_password_required'),
        ]);
      if (stored && !typed && (hostOf(target) !== hostOf(current.target) || user !== current.user))
        return this.reject('secret_rebind_required', [
          problem('password', 'secret_rebind_required'),
        ]);
    }
    const next = { enabled, target, user };
    const secret = this.secretChange(current.secrets['replica.password'], body.password, clear);
    const changes: Record<string, Change> = {
      ...diff(
        'replica',
        { enabled: current.enabled, target: current.target, user: current.user },
        next,
      ),
      ...(secret ? { 'replica.password': secret } : {}),
    };
    const held = current.enabled && (!enabled || target !== current.target);
    const touched = target !== current.target || user !== current.user || secret !== null;
    return this.sectionChange(call, host, dryRun, account, {
      section: 'replica',
      etag: body.etag,
      changes,
      warnings: [],
      effects: ['task.next_run:Replica'],
      reauth: true,
      probe: enabled && touched && body.probe !== false,
      secrets: secret !== null,
      held,
      reloads: ['reload.worker'],
      next: () => {
        const by = { changed_at: this.iso(), changed_by: `web:${account.username}` };
        this.sections.replica = {
          ...next,
          secrets: secret
            ? { 'replica.password': { set: secret.new === 'set', ...by } }
            : this.sections.replica.secrets,
        };
      },
    });
  }

  private replicaTest(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const down = this.unavailable();
    if (down) return down;
    if (this.lastReplicaTest !== null && Date.now() - this.lastReplicaTest < 60_000) {
      const retry = Math.ceil((60_000 - (Date.now() - this.lastReplicaTest)) / 1000);
      const [status, body] = this.reject('admin_rate_limited', { retry_after: retry });
      return [
        status,
        body,
        {
          body: Buffer.from(JSON.stringify(body)),
          contentType: 'application/json',
          headers: { 'Retry-After': String(retry) },
        },
      ];
    }
    this.lastReplicaTest = Date.now();
    const outcome = this.takeAction();
    const ok = outcome === 'succeeded';
    const item = this.enqueue({
      kind: 'action.replica_test',
      section: null,
      account,
      steps: ['received', 'dispatch', 'task', 'journal'],
      result: ok
        ? {
            ok: true,
            message: 'connected, test file written and removed',
            message_tr: 'Replika hedefine bağlanıldı; deneme dosyası yazılıp silindi.',
            message_en: 'Connected to the replica target; a test file was written and removed.',
          }
        : {
            ok: false,
            message: 'access denied',
            message_tr: 'Replika hedefine bağlanılamadı: erişim reddedildi.',
            message_en: 'Could not connect to the replica target: access denied.',
          },
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
    const body = call.body as {
      etag?: unknown;
      enabled?: unknown;
      days?: unknown;
      confirm_delete_days?: unknown;
    };
    const current = this.sections.retention;
    const days = body.days;
    if (typeof days !== 'number' || !Number.isInteger(days))
      return this.refuse(422, 'config_invalid', [problem('days', 'config_invalid')]);
    if (days < current.minimum_days)
      return this.reject('retention_below_minimum', [problem('days', 'retention_below_minimum')]);
    if (days > 36_500)
      return this.refuse(422, 'config_invalid', [problem('days', 'config_invalid')]);
    const enabled = body.enabled === true;
    const deletes = enabled && (!current.enabled || days < current.days);
    const hold = deletes && this.holdHours > 0 ? this.holdHours * 3_600_000 : 0;
    const preview = this.retentionPreview(days, hold);
    if (!dryRun && deletes && body.confirm_delete_days !== preview.days_to_delete)
      return this.reject('retention_confirm_mismatch');
    return this.sectionChange(call, host, dryRun, account, {
      section: 'retention',
      etag: body.etag,
      changes: diff(
        'retention',
        { enabled: current.enabled, days: current.days },
        { enabled, days },
      ),
      warnings: [],
      effects: ['task.next_run:Retention'],
      reauth: true,
      probe: false,
      secrets: false,
      held: deletes,
      reloads: ['reload.worker'],
      check: { retention_preview: preview },
      next: () => {
        this.sections.retention = { ...this.sections.retention, enabled, days };
      },
    });
  }

  private backup(call: RecordedCall, host: MockHost, dryRun: boolean): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as Record<string, unknown>;
    const ranges: Record<string, [number, number]> = {
      keep_daily: [1, 3660],
      keep_weekly: [0, 520],
      keep_monthly: [0, 1200],
      min_free_gb: [0, 100_000],
    };
    const problems = Object.entries(ranges)
      .filter(([key, [min, max]]) => {
        const value = body[key];
        return typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max;
      })
      .map(([key]) => problem(key, 'config_invalid'));
    if (problems.length > 0) return this.refuse(422, 'config_invalid', problems);
    const current = this.sections.backup;
    const next: typeof current = {
      enabled: body.enabled === true,
      keep_daily: body.keep_daily as number,
      keep_weekly: body.keep_weekly as number,
      keep_monthly: body.keep_monthly as number,
      min_free_gb: body.min_free_gb as number,
    };
    return this.sectionChange(call, host, dryRun, account, {
      section: 'backup',
      etag: body.etag,
      changes: diff(
        'backup',
        current as unknown as Record<string, unknown>,
        next as unknown as Record<string, unknown>,
      ),
      warnings: current.enabled && !next.enabled ? ['backup_disabled'] : [],
      effects: ['task.next_run:Backup'],
      reauth: true,
      probe: false,
      secrets: false,
      held: false,
      reloads: ['reload.worker'],
      next: () => {
        this.sections.backup = next;
      },
    });
  }

  private leaseRebuild(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const body = call.body as { from?: unknown; to?: unknown };
    const day = (value: unknown) =>
      typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
        ? Date.parse(`${value}T00:00:00Z`)
        : NaN;
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
    const item = this.enqueue({
      kind: 'worker.lease_rebuild',
      section: null,
      account,
      steps: ['received', 'task', 'journal'],
      result: {
        done_days: total,
        total_days: total,
        failed_days: outcome === 'succeeded' ? [] : [String(body.from)],
      },
    });
    return this.accepted(item);
  }

  // -- requests: list, one, cancel, revert ------------------------------------------------------

  private listRequests(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    const kind = call.query.get('kind');
    const limit = Number(call.query.get('limit') ?? 50);
    const offset = Number(call.query.get('cursor') ?? 0);
    const items = [...this.requests.values()]
      .map((item) => item.out)
      .filter((out) => !kind || out.kind === kind)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    const page = items.slice(offset, offset + limit);
    return [
      200,
      {
        items: copy(page),
        next_cursor: offset + limit < items.length ? String(offset + limit) : null,
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
    if (!item) return this.refuse(404, 'admin_request_not_found');
    return [200, copy(item.out)];
  }

  private cancelRequest(call: RecordedCall, host: MockHost, id: string): Answer {
    const item = this.requests.get(id);
    const account = host.requirePermission(call, 'settings.manage', item?.out.state === 'pending');
    if (Array.isArray(account)) return account;
    if (!item) return this.refuse(404, 'admin_request_not_found');
    if (item.out.state !== 'queued' && item.out.state !== 'pending')
      return this.refuse(409, 'request_not_cancellable');
    item.cancelled = true;
    item.cancel?.();
    const cancel = this.enqueue({
      kind: 'config.cancel',
      section: item.out.section,
      account,
      steps: ['received', 'validate', 'journal'],
    });
    return this.accepted(cancel);
  }

  private revertRequest(call: RecordedCall, host: MockHost, id: string): Answer {
    const account = host.requirePermission(call, 'settings.manage', true);
    if (Array.isArray(account)) return account;
    const item = this.requests.get(id);
    if (!item) return this.refuse(404, 'admin_request_not_found');
    const out = item.out;
    if (
      !(out.kind === 'config.apply' || out.kind === 'config.revert') ||
      !(out.state === 'active' || out.state === 'partially_active') ||
      out.version === null ||
      out.section === null ||
      item.before === null
    )
      return this.refuse(409, 'request_not_revertible');
    const down = this.unavailable();
    if (down) return down;
    const section = out.section;
    const before = item.before;
    const changes = Object.fromEntries(
      Object.entries(out.changes).map(([path, change]) => [
        path,
        'secret' in change
          ? change
          : { old: (change as { new: unknown }).new, new: (change as { old: unknown }).old },
      ]),
    );
    const revert = this.enqueue({
      kind: 'config.revert',
      section,
      account,
      steps: [
        'received',
        'validate',
        'backup',
        'write_config',
        'verify',
        'reload.api',
        'reload.worker',
        'journal',
      ],
      changes,
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

  private sealNow(call: RecordedCall, host: MockHost, day: string): Answer {
    const account = host.requirePermission(call, 'seal.manage');
    if (Array.isArray(account)) return account;
    const down = this.unavailable();
    if (down) return down;
    const state = this.dayState(day);
    if (state === 'sealed' || state === 'verified' || state === 'late')
      return this.refuse(409, 'seal_day_sealed');
    if (state === null || state === 'no_data' || state === 'deleted')
      return this.refuse(409, 'seal_not_applicable');
    if (!SEALABLE.has(state)) return this.refuse(409, 'seal_not_due');
    const earlier = SEALS.calendar_current.days.find(
      (cell) => cell.day < day && SEALABLE.has(this.dayState(cell.day) ?? ''),
    );
    if (earlier) return this.refuse(409, 'seal_not_due');
    const waiting = [...this.requests.values()].some(
      (item) => item.out.kind === 'action.seal' && !item.out.terminal && item.day === day,
    );
    if (waiting) return this.refuse(409, 'seal_request_pending');
    const outcome = this.takeAction();
    const serial = sha256(`seal ${day}`).slice(0, 16).toUpperCase();
    const genTime = this.iso();
    const item = this.enqueue({
      kind: 'action.seal',
      section: null,
      account,
      steps: ['received', 'dispatch', 'task', 'journal'],
      day,
      result:
        outcome === 'succeeded'
          ? { day, status: 'sealed', message: `${day} sealed`, serial, gen_time: genTime }
          : null,
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

  private creditQuery(call: RecordedCall, host: MockHost): Answer {
    const account = host.requirePermission(call, 'seal.manage');
    if (Array.isArray(account)) return account;
    const down = this.unavailable();
    if (down) return down;
    if (this.lastCredit !== null && Date.now() - this.lastCredit < 60_000) {
      const retry = Math.ceil((60_000 - (Date.now() - this.lastCredit)) / 1000);
      const [status, body] = this.refuse(429, 'admin_rate_limited', { retry_after: retry });
      return [
        status,
        body,
        {
          body: Buffer.from(JSON.stringify(body)),
          contentType: 'application/json',
          headers: { 'Retry-After': String(retry) },
        },
      ];
    }
    this.lastCredit = Date.now();
    const sample = this.creditsSample();
    const outcome = this.takeAction();
    const credits = (this.creditChecks.at(-1)?.credits ?? sample.remaining ?? 9612) - 1;
    const result = {
      credits,
      threshold: sample.threshold,
      below_threshold: credits < sample.threshold,
    };
    const item = this.enqueue({
      kind: 'action.credit',
      section: null,
      account,
      steps: ['received', 'dispatch', 'task', 'journal'],
      result: outcome === 'succeeded' ? result : null,
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

  // -- database settings ------------------------------------------------------------------------

  private setting(key: string): SettingOut | undefined {
    for (const group of this.settings) {
      const found = group.settings.find((item) => item.key === key);
      if (found) return found;
    }
    return undefined;
  }

  private patchSettings(call: RecordedCall, host: MockHost): Answer {
    const body = call.body as {
      changes: { key: string; old: SettingValue; value: SettingValue }[];
    };
    const reauth = body.changes.some((change) => this.setting(change.key)?.reauth);
    const account = host.requirePermission(call, 'settings.manage', reauth);
    if (Array.isArray(account)) return account;
    const problems = [];
    const conflicts = [];
    for (const change of body.changes) {
      const setting = this.setting(change.key);
      if (!setting) return this.refuse(422, 'setting_unknown');
      if (setting.locked) return this.refuse(422, 'setting_locked');
      if (JSON.stringify(change.old) !== JSON.stringify(setting.value))
        conflicts.push({ key: change.key, current: setting.value });
      const refused = settingProblem(setting, change.value);
      if (refused)
        problems.push({
          key: change.key,
          code: 'setting_invalid',
          message_tr: refused[0],
          message_en: refused[1],
        });
    }
    if (problems.length > 0) return this.refuse(422, 'setting_invalid', problems);
    if (conflicts.length > 0) return this.refuse(409, 'setting_conflict', conflicts);
    const changed = [];
    const unchanged = [];
    for (const change of body.changes) {
      const setting = this.setting(change.key);
      if (!setting) continue;
      if (JSON.stringify(setting.value) === JSON.stringify(change.value)) {
        unchanged.push(change.key);
        continue;
      }
      changed.push({ key: change.key, old: setting.value, new: change.value });
      setting.value = copy(change.value);
      setting.is_default = JSON.stringify(setting.value) === JSON.stringify(setting.default);
      setting.updated_by = account.username;
      setting.updated_at = this.iso();
    }
    return [200, { changed, unchanged }];
  }

  private resetSetting(call: RecordedCall, host: MockHost, key: string): Answer {
    const setting = this.setting(key);
    const account = host.requirePermission(call, 'settings.manage', setting?.reauth ?? false);
    if (Array.isArray(account)) return account;
    if (!setting) return this.refuse(422, 'setting_unknown');
    if (setting.locked) return this.refuse(422, 'setting_locked');
    const old = setting.value;
    setting.value = copy(setting.default);
    setting.is_default = true;
    setting.updated_by = account.username;
    setting.updated_at = this.iso();
    return [200, { changed: [{ key, old, new: setting.value }], unchanged: [] }];
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
    const problems = bodyProblems(route, call.body);
    if (problems.length > 0) {
      const account = host.requirePermission(call, 'dashboard.usage');
      if (Array.isArray(account)) return account;
      return host.error(422, 'validation_error', problems);
    }
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
      if (method === 'PUT') {
        const problems = bodyProblems('POST /admin/config/sources', call.body);
        if (problems.length > 0) return host.error(422, 'validation_error', problems);
        return this.sourceChange(call, host, 'update', name);
      }
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

/** The site's day of a moment (UTC+03:00 all year). */
function siteDay(at: Date): string {
  return new Date(at.getTime() + 3 * 3_600_000).toISOString().slice(0, 10);
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

const SOURCE_KEYS = [
  'name',
  'type',
  'address',
  'heartbeat_minutes',
  'expected',
  'transport',
  'enabled',
];
const SOURCE_STEPS: RequestStep[] = [
  'received',
  'validate',
  'backup',
  'write_config',
  'verify',
  'reload.collector',
  'reload.ingest',
  'system.firewall',
  'journal',
];

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
/** `policy.source_networks` of the mock (as `config().policy`). */
const POLICY_NETWORKS = nets('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16');
/** `agent.json.local_addresses`: the server's own address. */
const LOCAL_ADDRESSES = ['172.20.10.5'];

// -- the database settings of the mock (contract 2.13) --------------------------------------------

type Meta = [
  key: string,
  type: SettingOut['type'],
  value: SettingValue,
  tr: string,
  en: string,
  helpTr: string,
  helpEn: string,
  extra?: Partial<SettingOut>,
];

function settingOut([key, type, value, tr, en, helpTr, helpEn, extra]: Meta): SettingOut {
  return {
    key,
    value: copy(value),
    default: copy(value),
    is_default: true,
    type,
    min: null,
    max: null,
    reauth: false,
    locked: false,
    label_tr: tr,
    label_en: en,
    help_tr: helpTr,
    help_en: helpEn,
    updated_by: ':seed',
    updated_at: '2026-09-30T09:12:00+03:00',
    ...extra,
  };
}

function group(id: SettingGroup['id'], tr: string, en: string, items: Meta[]): SettingGroup {
  return { id, label_tr: tr, label_en: en, settings: items.map(settingOut) };
}

/** A representative part of `DEFAULT_SETTINGS` in every group, every kind of value. */
function startSettings(): SettingGroup[] {
  return [
    group('alerts', 'Uyarılar ve e-posta', 'Alerts and e-mail', [
      [
        'alerts.email_recipients',
        'string_list',
        [],
        'Uyarı e-postası alıcıları',
        'Alert e-mail recipients',
        'Uyarı e-postalarını alan adresler, en çok 20; boş liste: e-posta gönderilmez.',
        'The addresses that receive alert e-mails, at most 20; an empty list sends none.',
        { reauth: true },
      ],
      [
        'alerts.email_min_severity',
        'string',
        'high',
        'E-postayla gönderilen en düşük önem',
        'Lowest severity e-mailed',
        'Bu ve daha yüksek önemdeki uyarılar e-postayla gider.',
        'Alerts of this severity and above are e-mailed.',
        { reauth: true },
      ],
      [
        'alerts.email_max_per_hour',
        'integer',
        20,
        'Saatte en çok e-posta',
        'E-mails per hour at most',
        'Bir saatte gönderilen uyarı e-postası sayısının üst sınırı.',
        'The most alert e-mails sent in one hour.',
        { min: 1, max: 1000 },
      ],
      [
        'alerts.email_resolved',
        'boolean',
        true,
        'Çözülen uyarılar için de e-posta',
        'E-mail resolved alerts too',
        'Bir uyarı kendiliğinden çözülünce de e-posta gönderilir.',
        'An e-mail is also sent when an alert resolves itself.',
      ],
      [
        'alerts.disk_used_percent',
        'integer',
        80,
        'Disk doluluk uyarısı (%)',
        'Disk usage alert (%)',
        'Veri diskinin doluluğu bu yüzdeyi geçince uyarı.',
        'Alert when the data disk is fuller than this percentage.',
        { min: 50, max: 99 },
      ],
    ]),
    group('lookup', '5651 sorgu ve kira zaman çizelgesi', '5651 lookup and lease timeline', [
      [
        'nat.public_ips',
        'string_list',
        [],
        'Genel (WAN) IP adresleri',
        'Public (WAN) IP addresses',
        'Güvenlik duvarının genel adresleri; boş: çift NAT, sorgu aday listesi verir.',
        "The firewall's public addresses; empty: double NAT, a lookup gives candidates.",
      ],
      [
        'lookup.tolerance_seconds',
        'integer',
        60,
        'Sorgu toleransı (± saniye)',
        'Lookup tolerance (± seconds)',
        'Talep edilen zamanın çevresinde aranan süre.',
        'How far around the requested time a lookup searches.',
        { min: 0, max: 3600 },
      ],
      [
        'lookup.port_hint',
        'boolean',
        false,
        'Port ipucu',
        'Port hint',
        'Genel porta eşit güvenlik duvarı portu olan adaylar öne alınır.',
        'Candidates whose firewall port equals the public port rank first.',
      ],
      [
        'lease.server_seconds',
        'object',
        {},
        'DHCP sunucusuna göre kira süresi',
        'Lease time per DHCP server',
        'Varsayılandan farklı kira süresi olan DHCP sunucuları (saniye).',
        'DHCP servers whose lease time differs from the default (seconds).',
      ],
      [
        'devices.oui_file',
        'string',
        '',
        'OUI listesi dosyası',
        'OUI list file',
        'Sunucuda yöneticinin koyduğu IEEE OUI listesinin yolu.',
        'The path of an IEEE OUI listing the administrator placed on the server.',
        { locked: true },
      ],
    ]),
    group('usage', 'Kullanım analizi', 'Usage analytics', [
      [
        'usage.work_hours',
        'object',
        { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' },
        'Mesai saatleri',
        'Work hours',
        'Mesai günleri ve saatleri; geri kalanı mesai dışıdır.',
        'The work days and hours; the rest is off hours.',
      ],
      [
        'usage.holidays',
        'string_list',
        ['01-01', '04-23', '05-01', '05-19', '07-15', '08-30', '10-29'],
        'Tatil günleri',
        'Holidays',
        'Tatil günleri mesai dışı sayılır.',
        'Holidays count as off hours.',
      ],
      [
        'usage.background_ports',
        'integer_list',
        [53, 123, 853],
        'Arka plan portları',
        'Background ports',
        'Bu portlardaki trafik arka plan trafiği sayılır.',
        'Traffic on these ports counts as background traffic.',
      ],
      [
        'usage.retention_days',
        'integer',
        400,
        'Kullanım özetlerinin saklama süresi (gün)',
        'Usage rollup retention (days)',
        'Bu süreden eski kullanım özetleri silinir.',
        'Usage rollups older than this are deleted.',
        { min: 1, reauth: true },
      ],
      [
        'usage.psl_file',
        'string',
        '',
        'Genel son ek listesi dosyası',
        'Public suffix list file',
        'Sunucuda yöneticinin koyduğu daha yeni bir liste.',
        'A newer list the administrator placed on the server.',
        { locked: true },
      ],
    ]),
    group('reports', 'Kullanım raporları', 'Usage reports', [
      [
        'reports.keep_days',
        'integer',
        400,
        'Rapor dosyalarının saklama süresi (gün)',
        'Report file retention (days)',
        'Bu süreden eski rapor dosyaları silinir.',
        'Report files older than this are deleted.',
        { min: 1, reauth: true },
      ],
      [
        'reports.catch_up',
        'boolean',
        true,
        'Kaçırılan raporu çalıştır',
        'Run a missed report',
        'Çalışan hizmet durduysa kaçırılan zamanlama bir kez çalıştırılır.',
        'A schedule missed while the worker was stopped runs once.',
      ],
    ]),
    group('evidence', 'Kanıt paketleri', 'Evidence packages', [
      [
        'evidence.keep_days',
        'integer',
        30,
        'Kanıt paketlerinin saklama süresi (gün)',
        'Evidence package retention (days)',
        'Bu süreden eski paketler silinir.',
        'Packages older than this are deleted.',
        { min: 1, reauth: true },
      ],
      [
        'evidence.max_requests_per_hour',
        'integer',
        10,
        'Saatte en çok paket isteği',
        'Package requests per hour at most',
        'Bir kullanıcının bir saatte isteyebileceği paket sayısı.',
        'How many packages one user may request in an hour.',
        { min: 1 },
      ],
    ]),
    group('security', 'Oturum, parola ve gizlilik', 'Sessions, passwords and privacy', [
      [
        'auth.idle_minutes',
        'integer',
        15,
        'Hareketsizlik süresi (dakika)',
        'Idle timeout (minutes)',
        'Bu süre işlem yapılmazsa oturum kapanır.',
        'The session closes after this long without activity.',
        { min: 1, max: 120, reauth: true },
      ],
      [
        'auth.password_min_length',
        'integer',
        12,
        'En kısa parola uzunluğu',
        'Shortest password',
        'Yeni parolaların en az uzunluğu.',
        'The least length of new passwords.',
        { min: 12, max: 128, reauth: true },
      ],
      [
        'auth.totp_required_roles',
        'string_list',
        ['admin', 'auditor'],
        'İki adımlı doğrulama zorunlu roller',
        'Roles that must use two-factor authentication',
        'Yönetici ve denetçi için zorunludur; rol eklenebilir.',
        'Mandatory for administrators and auditors; roles may be added.',
        { reauth: true },
      ],
      [
        'privacy.notice_version',
        'string',
        '1',
        'KVKK bilgilendirme metni sürümü',
        'Privacy notice version',
        'Değişince kullanıcılar metni yeniden onaylar.',
        'When it changes, users accept the notice again.',
        { reauth: true },
      ],
    ]),
    group('database', 'Veritabanı', 'Database', [
      [
        'db.retention_days',
        'integer',
        400,
        'Veritabanı saklama süresi (gün)',
        'Database retention (days)',
        'Veritabanındaki kayıtlar bu süre tutulur; ham kayıtlar kasada kalır.',
        'Database records are kept this long; the raw logs stay in the vault.',
        { min: 1, reauth: true },
      ],
    ]),
  ];
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const HOLIDAY = /^(?:\d{4}-)?\d{2}-\d{2}$/;

/** `validate_setting`'s refusal of a value, Turkish first (`null`: accepted). */
function settingProblem(setting: SettingOut, value: SettingValue): [string, string] | null {
  const kind = Array.isArray(setting.default) ? 'list' : typeof setting.default;
  const actual = Array.isArray(value) ? 'list' : typeof value;
  if (kind !== actual)
    return [`${setting.key}: değer türü geçersiz`, `${setting.key}: wrong kind of value`];
  if (setting.type === 'integer') {
    const number = value as number;
    if (!Number.isInteger(number))
      return [`${setting.key}: tam sayı bekleniyor`, `${setting.key}: a whole number is expected`];
    const low = setting.min ?? 0;
    if (number < low || (setting.max !== null && number > setting.max)) {
      const upper = setting.max === null ? '' : `, en çok ${setting.max}`;
      const upperEn = setting.max === null ? '' : `, at most ${setting.max}`;
      return [
        `${setting.key}: en az ${low}${upper} olmalı`,
        `${setting.key}: must be at least ${low}${upperEn}`,
      ];
    }
  }
  if (setting.key === 'nat.public_ips') {
    const bad = (value as string[]).find((item) => !IPV4.test(item) && !item.includes(':'));
    if (bad !== undefined)
      return [
        `${setting.key}: geçersiz IP adresi '${bad}'`,
        `${setting.key}: invalid IP address '${bad}'`,
      ];
  }
  if (
    setting.key === 'alerts.email_recipients' &&
    (value as string[]).some((item) => !EMAIL.test(item))
  )
    return [`${setting.key}: geçersiz e-posta adresi`, `${setting.key}: invalid e-mail address`];
  if (setting.key === 'usage.holidays' && (value as string[]).some((item) => !HOLIDAY.test(item)))
    return [
      `${setting.key}: geçersiz tarih (AA-GG ya da YYYY-AA-GG)`,
      `${setting.key}: invalid date (MM-DD or YYYY-MM-DD)`,
    ];
  if (
    setting.key === 'auth.totp_required_roles' &&
    !['admin', 'auditor'].every((role) => (value as string[]).includes(role))
  )
    return [
      `${setting.key}: admin, auditor rolü için iki adımlı doğrulama (TOTP) zorunludur`,
      `${setting.key}: two-factor authentication (TOTP) is mandatory for admin, auditor`,
    ];
  if (setting.key === 'usage.work_hours') {
    const hours = value as { days: number[]; start: string; end: string };
    if (!(hours.start < hours.end))
      return [
        `${setting.key}: mesai başlangıcı bitişten önce olmalı`,
        `${setting.key}: work must start before it ends`,
      ];
  }
  return null;
}
