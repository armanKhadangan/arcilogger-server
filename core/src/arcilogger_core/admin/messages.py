"""Turkish and English texts of the administration channel (ADR-0014; contract 2.10, 2.12).

The agent's status files, its journal and the tasks' results carry codes only; the API adds the
texts from here (``{"code", "message_tr", "message_en"}``), Turkish first. Three catalogues:

* :data:`MESSAGES` — why a request ended as it did (``status.error.code``, a step's ``code``);
* :data:`WARNINGS` — what a request warns about (``warnings[].code``, a step in ``warning``);
* :data:`STATE_LABELS` — the request states of :data:`arcilogger_core.admin.contract.STATES`.

:data:`MESSAGES` also holds the codes of the section rules (:mod:`arcilogger_core.admin.rules`)
that the agent's own, authoritative check can report in a status (``site_name_required``,
``source_overlap``, …; contract Day 0 note D0-9), with the texts the API answers them with. The
API's own HTTP errors (``admin_agent_unavailable``, ``config_conflict``, the typed confirmations
such as ``retention_confirm_mismatch``, …) are listed in the API's error tables. No text claims
legal compliance.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

#: code -> (message_tr, message_en): the outcome codes of the agent and the tasks.
MESSAGES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "signature_invalid": (
            "İstek imzası doğrulanamadı; istek reddedildi (olası kurcalama, uyarı oluşturuldu).",
            "The request signature could not be verified; it was rejected (possible tampering, "
            "an alert was raised).",
        ),
        "request_expired": (
            "İstek zamanında işlenemedi ve süresi doldu; hiçbir şey değişmedi.",
            "The request was not processed in time and expired; nothing changed.",
        ),
        "request_replayed": (
            "Bu istek daha önce işlendi; tekrarı reddedildi.",
            "This request was already processed; the repeat was rejected.",
        ),
        "installation_mismatch": (
            "İstek başka bir kurulum için hazırlanmış; reddedildi.",
            "The request was made for another installation; it was rejected.",
        ),
        "payload_mismatch": (
            "Ekli dosya istekteki özetle uyuşmuyor; reddedildi.",
            "An attached file does not match the digest in the request; it was rejected.",
        ),
        "schema_invalid": (
            "İstek biçimi geçersiz; reddedildi.",
            "The request format is invalid; it was rejected.",
        ),
        "reauth_missing": (
            "İstek yakın zamanda parola onayı olmadan hazırlanmış; reddedildi.",
            "The request was made without a recent password confirmation; it was rejected.",
        ),
        "conflict": (
            "Ayar dosyası bu istek hazırlandıktan sonra değişti; hiçbir şey değişmedi. Sayfayı "
            "yenileyip tekrar deneyin.",
            "The configuration changed after this request was made; nothing changed. Reload "
            "the page and try again.",
        ),
        "validation_failed": (
            "Değişiklik sunucudaki son denetimden geçmedi; hiçbir şey değişmedi.",
            "The change failed the final check on the server; nothing changed.",
        ),
        "probe_failed": (
            "Yeni değerlerle bağlantı denemesi başarısız; hiçbir şey değişmedi.",
            "The connection test with the new values failed; nothing changed.",
        ),
        "ntp_unreachable": (
            "Saat sunucusu yanıt vermedi.",
            "The time server did not answer.",
        ),
        "smtp_test_failed": (
            "Deneme e-postası gönderilemedi.",
            "The trial e-mail could not be sent.",
        ),
        # The worker channel (``admin_requests``, contract Day 0 note WP3-5).
        "smtp_not_configured": (
            "SMTP sunucusu ve gönderen adresi ayarlanmamış.",
            "The SMTP server and sender address are not set.",
        ),
        "lease_rebuild_failed": (
            "Kira zaman çizelgesi bazı günler için yeniden oluşturulamadı; o günlerin hatası "
            "iş kayıtlarında (leases.rebuild). Diğer günler yeniden oluşturuldu.",
            "The lease timeline could not be rebuilt for some days; their error is in the job "
            "runs (leases.rebuild). The other days were rebuilt.",
        ),
        "replica_test_failed": (
            "Replika paylaşımına bağlanılamadı.",
            "The replica share could not be reached.",
        ),
        "credit_query_failed": (
            "Kamu SM kontör sorgusu başarısız (müşteri numarası, parola ya da bağlantı).",
            "The Kamu SM credit query failed (customer number, password or connection).",
        ),
        "write_failed": (
            "Ayar dosyası yazılamadı; önceki ayarlar yerinde.",
            "The configuration could not be written; the previous settings are in place.",
        ),
        "verify_failed": (
            "Yazılan ayarlar doğrulanamadı; önceki ayarlar geri yüklendi.",
            "The written settings could not be verified; the previous settings were restored.",
        ),
        "rollback_failed": (
            "Değişiklik geri alınamadı; elle müdahale gerekiyor. Yedek sürüm ve adımlar aşağıda.",
            "The change could not be undone; manual action is needed. The backup version and "
            "the steps are below.",
        ),
        "interrupted": (
            "Yönetim hizmeti değişiklik sırasında durdu; önceki ayarlar geri yüklendi.",
            "The administration service stopped during the change; the previous settings were "
            "restored.",
        ),
        "acl_drift": (
            "Yönetim hizmeti ayar dosyalarına yazma iznini kaybetmiş (çoğunlukla konsoldan bir "
            "secret set sonrası); sunucuda yönetici olarak Set-ArciVaultAcl.ps1 betiğini "
            "çalıştırın.",
            "The administration service has lost write access to the configuration files "
            "(most often after a console secret set); run Set-ArciVaultAcl.ps1 on the server as "
            "an administrator.",
        ),
        "system_sync_failed": (
            "Güvenlik duvarı, saat ya da Zamane adımı uygulanamadı; önceki kurallar geçerli. "
            "Ayrıntılar Olay Görüntüleyicisi'nde (ARCILOGGER-Admin).",
            "The firewall, time or Zamane step could not be applied; the previous rules are in "
            "force. Details are in Event Viewer (ARCILOGGER-Admin).",
        ),
        "system_sync_timeout": (
            "Sistem görevi (\\ARCILOGGER\\ApplySystem) 5 dakika içinde çalışmadı; görev kapalı "
            "olabilir.",
            "The system task (\\ARCILOGGER\\ApplySystem) did not run within 5 minutes; it may "
            "be disabled.",
        ),
        "system_plan_failed": (
            "Sistem planı hesaplanamadı ya da geçersiz bir değer içeriyor; hiçbir kural değişmedi.",
            "The system plan could not be computed or contains an invalid value; no rule was "
            "changed.",
        ),
        "system_plan_invalid": (
            "Sistem planı hesaplanamadı ya da geçersiz bir değer içeriyor; hiçbir kural değişmedi.",
            "The system plan could not be computed or contains an invalid value; no rule was "
            "changed.",
        ),
        "task_timeout": (
            "Görev 15 dakika içinde sonuç vermedi; \\ARCILOGGER\\Seal ya da "
            "\\ARCILOGGER\\Requests görevi kapalı olabilir.",
            "The task gave no result within 15 minutes; \\ARCILOGGER\\Seal or "
            "\\ARCILOGGER\\Requests may be disabled.",
        ),
        "tsa_test_failed": (
            "Zaman damgası alınamadı ya da doğrulanamadı.",
            "No time stamp was obtained, or it could not be verified.",
        ),
        "seal_failed": (
            "Mühürleme denemesi başarısız; hata gün ayrıntısında.",
            "The sealing attempt failed; the error is on the day page.",
        ),
        "executable_refused": (
            "Bu program yolu güvenilir bir konumda değil; görev çalıştırmadı.",
            "This program path is not in a trusted location; the task did not run it.",
        ),
        # -- the agent's own refusals ---------------------------------------------------------
        "admin_disabled": (
            "Tarayıcıdan sunucu yönetimi bu kurulumda kapalı; değişikliği sunucu konsolunda yapın.",
            "Browser administration is switched off on this installation; make the change on "
            "the server console.",
        ),
        "request_not_cancellable": (
            "Bu istek artık iptal edilemez: uygulanmaya başlandı ya da bitti.",
            "This request can no longer be cancelled: it has started or finished.",
        ),
        "request_not_revertible": (
            "Bu değişiklik geri alınamaz: yalnızca sürümü saklanan, uygulanmış yapılandırma "
            "değişiklikleri geri alınabilir.",
            "This change cannot be reverted: only applied configuration changes whose version "
            "is still kept can be reverted.",
        ),
        # -- the section rules (arcilogger_core.admin.rules), when the agent's check refuses ---
        "config_unsupported_format": (
            "arcilogger.toml dosyasında web arayüzünün güvenle düzenleyemediği bir yazım var "
            "(satır içi tablo, noktalı anahtar ya da çok satırlı metin); bu değişikliği "
            "sunucuda elle yapın.",
            "arcilogger.toml contains syntax the web interface cannot edit safely (an inline "
            "table, a dotted key or a multi-line string); make this change on the server by "
            "hand.",
        ),
        "config_invalid": (
            "Ayarlar geçersiz; işaretli alanları düzeltin.",
            "The settings are invalid; correct the marked fields.",
        ),
        "config_key_locked": (
            "Bu ayar güvenlik nedeniyle yalnızca sunucu konsolundan değiştirilebilir.",
            "For security reasons this setting can only be changed on the server console.",
        ),
        "site_name_required": (
            "Kuruluş adı boş olamaz.",
            "The organisation name cannot be empty.",
        ),
        "host_invalid": (
            "Geçerli bir sunucu adı ya da IPv4 adresi girin.",
            "Enter a valid host name or IPv4 address.",
        ),
        "clock_servers_required": (
            "En az bir saat sunucusu gerekir (yerel kaynak ya da yedek sunucu).",
            "At least one time server is required (the local source or the fallback).",
        ),
        "retention_below_minimum": (
            "Saklama süresi 730 günden (iki yıl) kısa olamaz.",
            "The retention period cannot be shorter than 730 days (two years).",
        ),
        "source_invalid": (
            "Kaynak bilgileri geçersiz; işaretli alanları düzeltin.",
            "The source is invalid; correct the marked fields.",
        ),
        "source_not_found": (
            "Kaynak bulunamadı.",
            "Source not found.",
        ),
        "source_name_taken": (
            "Bu kaynak adı zaten kullanılıyor.",
            "This source name is already in use.",
        ),
        "source_address_taken": (
            "Bu adres başka bir kaynağa ait.",
            "This address belongs to another source.",
        ),
        "source_overlap": (
            "Bu ağ başka bir kaynağın ağıyla çakışıyor.",
            "This network overlaps the network of another source.",
        ),
        "source_rename_not_allowed": (
            "Kaynak adı sonradan değiştirilemez (ham log klasörünün adıdır); yeni bir kaynak "
            "ekleyin.",
            "A source name cannot be changed (it names the raw log folder); add a new source "
            "instead.",
        ),
        "source_ipv6_unsupported": (
            "Şimdilik yalnızca IPv4 adresleri ve ağları desteklenir.",
            "Only IPv4 addresses and networks are supported for now.",
        ),
        "source_reserved_address": (
            "Bu adres bir log kaynağı olamaz (geri döngü, çok noktaya yayın, yerel bağlantı, yayın "
            "ya da ayrılmış adres).",
            "This address cannot be a log source (loopback, multicast, link-local, broadcast or "
            "reserved).",
        ),
        "source_own_address": (
            "Bu adres ARCILOGGER sunucusunun kendi adresi.",
            "This is the ARCILOGGER server's own address.",
        ),
        "source_network_not_allowed": (
            "Adres, kurulumda izin verilen kaynak ağlarının dışında; bu liste yalnızca sunucu "
            "konsolundan değiştirilebilir.",
            "The address is outside the source networks allowed at installation; that list can "
            "only be changed on the server console.",
        ),
        "source_prefix_too_short": (
            "Ağ izin verilenden geniş; daha dar bir ağ girin.",
            "The network is wider than allowed; enter a narrower network.",
        ),
        "source_limit": (
            "Kaynak sayısı üst sınırına ulaşıldı.",
            "The maximum number of sources has been reached.",
        ),
        "secret_invalid": (
            "Girilen gizli bilgi geçersiz (boş, çok uzun ya da kontrol karakteri içeriyor).",
            "The secret is invalid (empty, too long or containing control characters).",
        ),
        "secret_rebind_required": (
            "Sunucu, kullanıcı ya da bağlantı türü değişti; kayıtlı parola yeni hedefe "
            "gönderilmez. Parolayı yeniden girin.",
            "The server, user or connection type changed; the stored password is not sent to a "
            "new destination. Enter the password again.",
        ),
        "smtp_password_required": (
            "Kullanıcı adı girildiğinde SMTP parolası da gerekir.",
            "An SMTP password is required when a user name is set.",
        ),
        "smtp_plaintext_auth_refused": (
            "Şifresiz bağlantıda parola yalnızca bu sunucudaki bir aktarıcıya gönderilebilir; "
            "STARTTLS ya da SSL seçin.",
            "Without encryption a password can only be sent to a relay on this server; choose "
            "STARTTLS or SSL.",
        ),
        "smtp_test_recipient_required": (
            "Kaydetmeden önce deneme e-postası için bir alıcı seçin.",
            "Choose a recipient for the trial e-mail before saving.",
        ),
        "replica_target_invalid": (
            "Replika hedefi \\\\sunucu\\paylaşım biçiminde bir ağ paylaşımı olmalı.",
            "The replica target must be a network share like \\\\server\\share.",
        ),
        "replica_user_invalid": (
            "Kullanıcı SUNUCU\\kullanıcı, ETKİALANI\\kullanıcı ya da kullanıcı@etkialanı "
            "biçiminde olmalı.",
            "The user must look like SERVER\\user, DOMAIN\\user or user@domain.",
        ),
        "replica_password_required": (
            "Replika kullanıcısı için parola gerekir.",
            "A password is required for the replica user.",
        ),
        "tsa_server_invalid": (
            'Zaman damgası sunucusu "test" ya da "production" olmalı.',
            'The time-stamp server must be "test" or "production".',
        ),
        "tsa_mock_refused": (
            "Deneme (mock) zaman damgası web arayüzünden seçilemez.",
            "The trial (mock) time stamp cannot be chosen from the web interface.",
        ),
        "tsa_not_ready": (
            "Üretim sunucusuna geçmeden önce eksikleri tamamlayın: kimlik bilgileri, onaylı "
            "Zamane istemcisi, Java ve en az bir kök sertifika.",
            "Complete the missing items before switching to the production server: "
            "credentials, an approved Zamane client, Java and at least one root certificate.",
        ),
        "tsa_would_break": (
            "Bu değişiklik şu an çalışan mühürlemeyi durdururdu; uygulanmadı. Nedenler aşağıda.",
            "This change would stop sealing, which works now; it was not applied. The causes "
            "are below.",
        ),
        "java_not_detected": (
            "Seçilen Java sunucuda bulunamadı; listeden birini seçin.",
            "The selected Java was not found on the server; choose one from the list.",
        ),
        "root_certificate_invalid": (
            "Dosya geçerli bir kök sertifika değil (tek, kendinden imzalı bir CA sertifikası "
            "olmalı).",
            "The file is not a valid root certificate (it must be a single self-signed CA "
            "certificate).",
        ),
        "root_exists": (
            "Bu kök sertifika zaten kurulu.",
            "This root certificate is already installed.",
        ),
        "root_not_installed": (
            "Yasal kök olarak yalnızca kurulu bir kök sertifika işaretlenebilir.",
            "Only an installed root certificate can be marked as a legal root.",
        ),
        "zamane_archive_invalid": (
            "Dosya bir Zamane istemcisi değil: bir .jar ya da tek bir tss-client-console-*.jar "
            "içeren .zip olmalı.",
            "The file is not a Zamane client: it must be a .jar, or a .zip holding exactly one "
            "tss-client-console-*.jar.",
        ),
    }
)

#: code -> (message_tr, message_en): the warnings of a request.
WARNINGS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "agent_stale": (
            "Yönetim hizmeti geç yanıt veriyor; değişiklik gecikebilir.",
            "The administration service is answering late; the change may be delayed.",
        ),
        "firewall_not_managed": (
            "Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor; 514 kuralını elle güncelleyin.",
            "The firewall is not managed by ARCILOGGER; update the 514 rule by hand.",
        ),
        "legal_roots_empty": (
            "Yasal kök parmak izi yok: hiçbir gün yasal nitelikte gösterilmez.",
            "No legal root fingerprint: no day is shown as legal grade.",
        ),
        "production_not_tested": (
            "Üretim sunucusunda henüz başarılı bir damga testi yok.",
            "No successful stamp test on the production server yet.",
        ),
        "production_stamps_stop": (
            "Üretim zaman damgaları duracak.",
            "Production time stamps will stop.",
        ),
        "root_expired": (
            "Kök sertifikanın süresi dolmuş; geçmiş günlerin doğrulanması için yine de kurulur.",
            "The root certificate has expired; it is still installed so past days can be verified.",
        ),
        "unknown_jar_held": (
            "Bu Zamane sürümü bilinen sürümler arasında değil: ancak bir yönetici sunucu "
            "konsolunda onaylayınca (en erken bekletme süresi sonunda) etkinleşir.",
            "This Zamane release is not a known release: it becomes active only when an "
            "administrator approves it on the server console (at the earliest when the hold "
            "ends).",
        ),
        "probe_skipped": (
            "Bağlantı denemesi atlandı.",
            "The connection test was skipped.",
        ),
        "credit_query_skipped": (
            "Kontör sorgusu yapılmadı: Zamane istemcisi henüz çalıştırılamıyor (onay bekliyor ya "
            'da Java veya istemci güvenilir bir konumda değil); istemci etkinleşince "Kontör '
            'sorgula" ile deneyin.',
            "The credit was not queried: the Zamane client cannot run yet (it awaits approval, "
            "or Java or the client is not in a trusted location); once the client is active, "
            'use "Query credit".',
        ),
        "write_access_lost": (
            "Değişiklik uygulandı, ancak yönetim hizmeti ayar dosyalarına artık yazamıyor: "
            "sonraki değişiklikten önce sunucuda yönetici olarak Set-ArciVaultAcl.ps1 betiğini "
            "çalıştırın.",
            "The change is applied, but the administration service can no longer write the "
            "configuration files: before the next change, run Set-ArciVaultAcl.ps1 on the "
            "server as an administrator.",
        ),
        "service_not_reloaded": (
            "Bir hizmet yeni ayarları henüz yüklemedi; ayrıntı adım listesinde.",
            "A service has not loaded the new settings yet; see the step list.",
        ),
        "restart_required": (
            "Sunucuda elle yapılan bir değişiklik ancak hizmet yeniden başlatılınca etkinleşir.",
            "A change made by hand on the server takes effect only after a service restart.",
        ),
        "source_outside_policy": (
            "Kaynak adresi yönetim politikasındaki ağların dışında ya da izin verilenden geniş; "
            "güvenlik duvarı kuralına eklenmedi.",
            "The source address lies outside the networks of the administration policy or is "
            "wider than allowed; it was not added to the firewall rule.",
        ),
        "outbound_host_invalid": (
            "Bir sunucu adı güvenlik duvarı kuralı için geçerli değil; sıkı giden trafik "
            "kuralına eklenmedi.",
            "A server name is not valid for a firewall rule; it was not added to the strict "
            "outbound rule.",
        ),
        "ntp_server_empty": (
            "Yerel saat kaynağı yok; saat yalnızca yedek sunucuyla eşitlenir.",
            "No local time source; the clock follows only the fallback server.",
        ),
        "source_disable_gaps": (
            "Kapatılan kaynak, mühürlenmemiş günlerin boşluk listesinde artık beklenmez.",
            "Once disabled, the source is no longer expected in the gap list of days not yet "
            "sealed.",
        ),
        "smtp_plaintext": (
            "E-posta şifresiz gönderilecek.",
            "E-mail will be sent unencrypted.",
        ),
        "smtp_disabled": (
            "Uyarı e-postaları durdu.",
            "Alert e-mails have stopped.",
        ),
        "backup_disabled": (
            "Veritabanı yedeği kapatıldı.",
            "The database backup is switched off.",
        ),
        "change_held": (
            "Değişiklik bekletme süresi sonunda uygulanacak; o zamana kadar iptal edilebilir.",
            "The change will be applied when the hold ends; it can be cancelled until then.",
        ),
    }
)

#: state -> (label_tr, label_en) of every request state.
STATE_LABELS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "queued": ("Sırada", "Queued"),
        "waiting_for_agent": (
            "Yönetim hizmeti bekleniyor",
            "Waiting for the administration service",
        ),
        "expired": ("Süresi doldu, hiçbir şey değişmedi", "Expired, nothing changed"),
        "received": ("Alındı", "Received"),
        "rejected": ("Reddedildi", "Rejected"),
        "conflict": ("Çakışma: ayarlar başka yerden değişti", "Conflict: changed elsewhere"),
        "probing": ("Bağlantı deneniyor", "Testing the connection"),
        "probe_failed": (
            "Bağlantı denemesi başarısız, hiçbir şey değişmedi",
            "Connection test failed, nothing changed",
        ),
        "pending": ("Bekletiliyor", "Held"),
        "cancelled": ("İptal edildi", "Cancelled"),
        "applying": ("Uygulanıyor", "Applying"),
        "applied": ("Yazıldı, hizmetler yüklüyor", "Written, services loading"),
        "active": ("Uygulandı", "Applied"),
        "partially_active": ("Kısmen uygulandı", "Partly applied"),
        "failed": (
            "Uygulanmadı, önceki ayarlar geçerli",
            "Not applied, the previous settings are in use",
        ),
        "needs_attention": ("Elle müdahale gerekiyor", "Manual action needed"),
        "dispatched": ("Göreve iletildi", "Passed to the task"),
        "running": ("Çalışıyor", "Running"),
        "succeeded": ("Tamamlandı", "Done"),
        "timed_out": ("Zaman aşımı", "Timed out"),
    }
)


def texts(code: str) -> tuple[str, str]:
    """``(message_tr, message_en)`` of an outcome or warning code; ``KeyError`` when the code
    is in neither catalogue."""
    found = MESSAGES.get(code) or WARNINGS.get(code)
    if found is None:
        raise KeyError(code)
    return found
