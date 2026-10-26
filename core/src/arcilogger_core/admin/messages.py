"""Turkish and English texts of the administration channel (ADR-0014; contract 2.10, 2.12).

The agent's status files, its journal and the tasks' results carry codes only; the API adds the
texts from here (``{"code", "message_tr", "message_en"}``), Turkish first. Three catalogues:

* :data:`MESSAGES` — why a request ended as it did (``status.error.code``, a step's ``code``);
* :data:`WARNINGS` — what a request warns about (``warnings[].code``, a step in ``warning``);
* :data:`STATE_LABELS` — the request states of :data:`arcilogger_core.admin.contract.STATES`.

The API's own HTTP errors (``admin_agent_unavailable``, ``config_conflict``, the field codes of
the section rules, …) are listed in the API's error tables. No text claims legal compliance.
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
            "Yönetim hizmeti ayar dosyasına yazma iznini kaybetmiş; kurulumu onarın.",
            "The administration service has lost write access to the configuration; repair the "
            "installation.",
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
