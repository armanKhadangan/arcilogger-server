"""The fixed texts of Uyum Durumu (Wave 4 contract 2.3.1, 2.3.3, 2.3.4), Turkish first.

Every text is a ``(Turkish, English)`` pair, character for character as the contract gives it;
``{…}`` are filled by :mod:`arcilogger_api.compliance.cards`. The page and the PDF show the same
texts (contract D3). No text claims legal conformity: "Uyum Durumu" is the page's name, not a
verdict, and the only wording about the law is the notice below.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from arcilogger_api.compliance.models import ComplianceCardId, ComplianceLinkPage, ComplianceTone

#: (Turkish, English)
Text = tuple[str, str]

NOTICE: Final[Text] = (
    "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Bu kartlar kayıt "
    "toplama, mühürleme ve yedeklemenin teknik denetimlerinin sonucudur; hukuki bir "
    "değerlendirme ya da uygunluk beyanı değildir. Sonuçları hukuk müşavirinizle teyit edin.",
    "ARCILOGGER is designed to support 5651 obligations. These cards are the results of "
    "technical checks of log collection, sealing and backups; they are neither a legal "
    "assessment nor a statement of conformity. Confirm the results with your legal counsel.",
)

#: A card whose computation raised (contract 2.3.2): ``error_code`` is the exception class name.
CHECK_FAILED: Final[Text] = (
    "Bu denetim şu anda yapılamadı: {error_code}",
    "This check could not be run now: {error_code}",
)

#: ``doc_tr`` / ``doc_en`` (W4-E writes the sections).
DOC: Final[Text] = ("UYUM-DURUMU.md bölüm 2.{number}", "COMPLIANCE-STATUS.md section 2.{number}")

#: Tones as text in the PDF (contract 2.3.4), the web app's ``compliance.tone.*`` in capitals.
TONE_LABELS: Final[Mapping[ComplianceTone, Text]] = {
    "ok": ("TAMAM", "OK"),
    "warning": ("DİKKAT", "ATTENTION"),
    "error": ("SORUN", "PROBLEM"),
    "neutral": ("BİLGİ YOK", "NO DATA"),
}

#: The cards in the order of contract 2.3.3: title and the page with the details (nav key).
CARDS: Final[Mapping[ComplianceCardId, tuple[Text, ComplianceLinkPage]]] = {
    "sources": (("Log kaynakları veri gönderiyor", "Log sources are sending"), "sources"),
    "seal_yesterday": (
        ("Dünün kayıtları mühürlendi ve doğrulandı", "Yesterday's records sealed and verified"),
        "sealing",
    ),
    "chain": (("Mühür zinciri bütün", "Seal chain intact"), "sealing"),
    "retention": (("Saklama süresi", "Retention period"), "sealing"),
    "tsa": (
        (
            "Kamu SM kontörü ve zaman damgası sertifikası",
            "Kamu SM credit and time-stamp certificate",
        ),
        "sealing",
    ),
    "clock": (("Sunucu saati", "Server clock"), "system"),
    "content_filter": (("İçerik filtresi kayıtları", "Content filter records"), "search"),
    "lease_evidence": (
        ("DHCP ve ARP kayıtları (erişim kayıtları)", "DHCP and ARP records (access records)"),
        "sources",
    ),
    "replica": (("İkinci kopya (replika)", "Second copy (replica)"), "sealing"),
    "backup": (
        ("Yapılandırma ve veritabanı yedekleri", "Configuration and database backups"),
        "settings",
    ),
}

# -- values shared by several cards ---------------------------------------------------------------

NONE: Final = "—"
ON: Final[Text] = ("açık", "on")
OFF: Final[Text] = ("kapalı", "off")
#: A verification's or check's outcome after its time: "{time}, başarılı".
PASSED: Final[Text] = ("{time}, başarılı", "{time}, passed")
FAILED: Final[Text] = ("{time}, başarısız", "{time}, failed")

#: Time-stamp servers (card 5 labels, also card 2).
SERVER_PRODUCTION: Final[Text] = ("Kamu SM üretim", "Kamu SM production")
SERVER_TEST: Final[Text] = ("Kamu SM test", "Kamu SM test")
SERVER_MOCK: Final[Text] = ("Deneme (mock)", "Trial (mock)")
SERVER_RFC3161: Final[Text] = ("RFC 3161", "RFC 3161")

# -- card 1 sources -------------------------------------------------------------------------------

SOURCES_ALERT: Final[Text] = (
    "{n} kaynak kalp atışı süresinden uzun süredir veri göndermiyor: {names}.",
    "{n} source(s) have sent nothing for longer than their heartbeat: {names}.",
)
SOURCES_SILENT: Final[Text] = (
    "{names} kalp atışı süresinden uzun süredir sessiz; uyarı başlangıç bekleme süresi içinde.",
    "{names} silent for longer than the heartbeat; the alert is still in its start-up grace "
    "period.",
)
SOURCES_NONE: Final[Text] = (
    "İzlenen log kaynağı yok. Kaynakları Ayarlar → Kaynaklar'da tanımlayın.",
    "No log source is watched. Define the sources in Settings → Sources.",
)
SOURCES_OK: Final[Text] = (
    "İzlenen {n} kaynağın tümü kalp atışı süresi içinde veri gönderdi.",
    "All {n} watched sources sent data within their heartbeat.",
)
SOURCES_HEARTBEAT: Final[Text] = ("kaynağa göre, varsayılan 15 dk", "per source, default 15 min")

# -- card 2 seal_yesterday ------------------------------------------------------------------------

SEAL_FAILED: Final[Text] = (
    "Dünün ({day}) mühürlenmesi ya da doğrulaması başarısız: {message}",
    "Sealing or verification of yesterday ({day}) failed: {message}",
)
SEAL_OVERDUE: Final[Text] = (
    "Dün ({day}) saat {hour}:00'a kadar mühürlenmedi.",
    "Yesterday ({day}) was not sealed by {hour}:00.",
)
SEAL_NO_DATA: Final[Text] = (
    "Dün ({day}) için ham kayıt yok; kaynaklar veri göndermemiş olabilir.",
    "There are no raw records for yesterday ({day}); the sources may have sent nothing.",
)
SEAL_PENDING: Final[Text] = (
    "Dünün ({day}) mühürlenmesi bekleniyor (Seal görevi 00:10'dan sonra çalışır).",
    "Yesterday ({day}) is waiting to be sealed (the Seal task runs after 00:10).",
)
SEAL_NOT_VERIFIED: Final[Text] = (
    "Dün ({day}) mühürlendi, henüz doğrulanmadı (VerifyDaily 01:00'de çalışır).",
    "Yesterday ({day}) is sealed but not verified yet (VerifyDaily runs at 01:00).",
)
SEAL_LATE: Final[Text] = ("Dün ({day}) geç damgalandı.", "Yesterday ({day}) was time-stamped late.")
SEAL_NOT_PRODUCTION: Final[Text] = (
    "Dün mühürlendi ve doğrulandı, ancak damga Kamu SM üretim sunucusundan değil.",
    "Yesterday was sealed and verified, but the time stamp is not from the Kamu SM production "
    "server.",
)
SEAL_NO_SOURCES: Final[Text] = (
    "Dün için kayıt yok; henüz izlenen kaynak tanımlanmadı.",
    "No records for yesterday; no watched source is defined yet.",
)
SEAL_OK: Final[Text] = (
    "Dün ({day}) mühürlendi ve doğrulandı.",
    "Yesterday ({day}) was sealed and verified.",
)

# -- card 3 chain ---------------------------------------------------------------------------------

CHAIN_FAILED: Final[Text] = (
    "Son 30 günde {n} gün başarısız: {days}.",
    "{n} day(s) of the last 30 failed: {days}.",
)
CHAIN_FULL_FAILED: Final[Text] = (
    "Tam zincir denetimi ({at}) başarısız.",
    "The full chain check ({at}) failed.",
)
CHAIN_NOT_VERIFIED: Final[Text] = (
    "Son 30 günde {n} mühürlü gün son 35 günde doğrulanmadı.",
    "{n} sealed day(s) of the last 30 were not verified in the last 35 days.",
)
CHAIN_PENDING: Final[Text] = (
    "Son 30 günde {n} gün henüz mühürlenmedi: {days}.",
    "{n} day(s) of the last 30 are not sealed yet: {days}.",
)
CHAIN_FULL_OLD: Final[Text] = (
    "Son tam zincir denetimi {at}; 35 günden eski.",
    "The last full chain check was on {at}, more than 35 days ago.",
)
CHAIN_FULL_NEVER: Final[Text] = (
    "Tam zincir denetimi hiç çalışmadı.",
    "The full chain check has never run.",
)
CHAIN_NONE: Final[Text] = ("Son 30 günde mühürlü gün yok.", "No sealed day in the last 30 days.")
CHAIN_OK: Final[Text] = (
    "Son 30 günün mühürlü {n} günü doğrulandı; tam zincir denetimi {at} tarihinde başarılı.",
    "All {n} sealed days of the last 30 are verified; the full chain check of {at} passed.",
)
CHAIN_OK_FIRST: Final[Text] = (
    "Son 30 günün mühürlü {n} günü doğrulandı; tam zincir denetimi her ayın 1'inde çalışır, "
    "henüz çalışmadı.",
    "All {n} sealed days of the last 30 are verified; the full chain check runs on the 1st of "
    "each month and has not run yet.",
)
CHAIN_TASKS: Final[Text] = (
    "günlük son 2 gün, haftalık son 90 gün, aylık tüm zincir",
    "daily the last 2 days, weekly the last 90 days, monthly the whole chain",
)

# -- card 4 retention -----------------------------------------------------------------------------

RETENTION_ALERT: Final[Text] = (
    "Saklama görevinin açık bir uyarısı var.",
    "The retention task has an open alert.",
)
RETENTION_SHORT: Final[Text] = (
    "Kurulum 730 günden eski, ancak en eski mühürlü gün {oldest} ({age} gün önce).",
    "The installation is older than 730 days, but the oldest sealed day is {oldest} ({age} days "
    "ago).",
)
RETENTION_OFF: Final[Text] = (
    "Saklama kapalı ve {days} günden eski {n} mühürlü gün kasada duruyor (en eskisi {oldest}). "
    "Kişisel verilerin gereğinden uzun saklanmaması için saklamayı açın.",
    "Retention is off and {n} sealed day(s) older than {days} days are in the vault (the oldest "
    "{oldest}). Turn retention on so that personal data is not kept longer than needed.",
)
RETENTION_NONE: Final[Text] = ("Henüz mühürlü gün yok.", "No sealed day yet.")
RETENTION_OK_FULL: Final[Text] = (
    "En eski mühürlü gün {oldest} ({age} gün önce); 730 günlük süre kasada.",
    "The oldest sealed day is {oldest} ({age} days ago); the vault holds the 730 days.",
)
RETENTION_OK_YOUNG: Final[Text] = (
    "En eski mühürlü gün {oldest} ({age} gün önce); kurulum 730 günden genç.",
    "The oldest sealed day is {oldest} ({age} days ago); the installation is younger than 730 "
    "days.",
)
RETENTION_ON: Final[Text] = ("açık, {days} gün", "on, {days} days")

# -- card 5 tsa -----------------------------------------------------------------------------------

TSA_NOT_PRODUCTION: Final[Text] = (
    "Damgalar Kamu SM üretim sunucusundan alınmıyor (sağlayıcı: {provider}). Kamu SM "
    "bağlantısını Ayarlar → Kamu SM'de tamamlayın.",
    "Time stamps are not taken from the Kamu SM production server (provider: {provider}). "
    "Complete the Kamu SM connection in Settings → Kamu SM.",
)
TSA_NO_CREDIT: Final[Text] = ("Kamu SM kontörü bitti.", "The Kamu SM credit is used up.")
TSA_CERT_EXPIRED: Final[Text] = (
    "Son damganın TSA sertifikasının süresi {date} tarihinde doldu.",
    "The TSA certificate of the newest time stamp expired on {date}.",
)
TSA_LOW: Final[Text] = (
    "Kalan kontör {credits}; eşik {threshold}. Tahmini {days} gün yeter.",
    "{credits} credits left; threshold {threshold}. Estimated to last {days} days.",
)
TSA_UNKNOWN: Final[Text] = (
    "Kontör bilinmiyor ya da son okuma ({at}) iki günden eski.",
    "The credit is unknown or the last reading ({at}) is more than two days old.",
)
TSA_CERT_SOON: Final[Text] = (
    "Son damganın TSA sertifikası {date} tarihinde doluyor ({days} gün kaldı).",
    "The TSA certificate of the newest time stamp expires on {date} ({days} days left).",
)
TSA_NONE: Final[Text] = (
    "Henüz Kamu SM ile damgalanmış gün yok.",
    "No day has been stamped with Kamu SM yet.",
)
TSA_OK: Final[Text] = (
    "Kalan kontör {credits} (eşik {threshold}); son damganın TSA sertifikası {days} gün daha "
    "geçerli.",
    "{credits} credits left (threshold {threshold}); the TSA certificate of the newest time "
    "stamp is valid for {days} more days.",
)

# -- card 6 clock ---------------------------------------------------------------------------------

CLOCK_OFFSET: Final[Text] = (
    "Saat farkı {offset} ms; sınır {limit} ms.",
    "Clock offset {offset} ms; limit {limit} ms.",
)
CLOCK_UNREACHABLE: Final[Text] = (
    "Saat sunucusuna ulaşılamadı ya da saat uyarısı açık ({server}).",
    "The time server could not be reached or a clock alert is open ({server}).",
)
CLOCK_STALE: Final[Text] = (
    "Son saat ölçümü {at}; 3 saatten eski (ClockCheck saatte bir çalışır).",
    "The last clock measurement was at {at}, more than 3 hours ago (ClockCheck runs hourly).",
)
CLOCK_NONE: Final[Text] = ("Henüz saat ölçümü yok.", "No clock measurement yet.")
CLOCK_NO_LOCAL: Final[Text] = (
    "Yerel saat kaynağı ayarlanmamış; saat yalnızca yedek sunucuyla ({fallback}) eşitleniyor.",
    "No local time source is set; the clock follows only the fallback server ({fallback}).",
)
CLOCK_OK: Final[Text] = (
    "Saat farkı {offset} ms ({server}, {at}); sınır {limit} ms.",
    "Clock offset {offset} ms ({server}, {at}); limit {limit} ms.",
)
CLOCK_MS: Final[Text] = ("{value} ms", "{value} ms")
CLOCK_LOCAL_SET: Final[Text] = ("ayarlı", "set")
CLOCK_LOCAL_NONE: Final[Text] = ("yok", "none")

# -- card 7 content_filter ------------------------------------------------------------------------

FILTER_NONE: Final[Text] = (
    "FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor.",
    "No FortiGate source is defined; no content filter records are expected.",
)
FILTER_DUE: Final[Text] = (
    "Beklenen günlerde son {hours} saattir web ya da DNS filtresi kaydı gelmedi (son kayıt: "
    "{last}).",
    "No web or DNS filter record in the last {hours} hours of expected days (last record: {last}).",
)
FILTER_QUIET: Final[Text] = (
    "Son {hours} saatte içerik filtresi kaydı yok; bu süre beklenmeyen bir gün içeriyor (son "
    "kayıt: {last}).",
    "No content filter record in the last {hours} hours; this period includes a day on which "
    "none is expected (last record: {last}).",
)
FILTER_SILENT_KIND: Final[Text] = (
    "{kind} filtresi kaydı {hours} saattir gelmiyor; diğer tür geliyor.",
    "No {kind} filter record for {hours} hours; the other kind still arrives.",
)
FILTER_OK: Final[Text] = (
    "Son kayıt {last}; bugün {web} web, {dns} DNS filtresi kaydı.",
    "Last record {last}; today {web} web and {dns} DNS filter records.",
)
#: ``FilterEvidence.silent_kinds`` → ``{kind}``.
FILTER_KINDS: Final[Mapping[str, Text]] = {"web": ("Web", "Web"), "dns": ("DNS", "DNS")}
FILTER_DNS_PROFILE: Final[Text] = (
    "FortiGate DNS filtresi kayıtlarından profil adı okunmuyor (sahada doğrula).",
    "No profile name is read from FortiGate DNS filter records (verify on site).",
)
#: ISO weekdays 1-7.
WEEKDAYS: Final[tuple[Text, ...]] = (
    ("Pzt", "Mon"),
    ("Sal", "Tue"),
    ("Çar", "Wed"),
    ("Per", "Thu"),
    ("Cum", "Fri"),
    ("Cmt", "Sat"),
    ("Paz", "Sun"),
)
FILTER_HOLIDAYS_SKIPPED: Final[Text] = ("beklenmez", "not expected")
FILTER_HOLIDAYS_EXPECTED: Final[Text] = ("beklenen gün sayılır", "count as expected days")

# -- card 8 lease_evidence ------------------------------------------------------------------------

LEASE_NONE: Final[Text] = (
    "İzlenen kaynak yok; erişim kaydı beklenmiyor.",
    "No watched source; no access records are expected.",
)
LEASE_PROBLEMS: Final[Text] = (
    "{n} kira kanıtı sorunu açık.",
    "{n} lease evidence problem(s) open.",
)
LEASE_NO_ROWS: Final[Text] = (
    "Son 24 saatte DHCP ya da ARP kaydı gelmedi.",
    "No DHCP or ARP record arrived in the last 24 hours.",
)
LEASE_OK: Final[Text] = (
    "Son 24 saatte {tables} kayıtları geldi; açık kira kanıtı uyarısı yok.",
    "{tables} records arrived in the last 24 hours; no lease evidence alert is open.",
)
LEASE_ROWS: Final[Text] = ("{n} satır (24 saat)", "{n} rows (24 h)")
#: A lease alert without ``details.title_en``.
LEASE_PROBLEM_EN: Final = "Lease evidence problem"
#: The tables of the lease evidence, in the order of the facts.
LEASE_TABLES: Final[Mapping[str, str]] = {
    "mt_dhcp": "MikroTik DHCP",
    "mt_arp": "MikroTik ARP",
    "fg_dhcp": "FortiGate DHCP",
}

# -- card 9 replica -------------------------------------------------------------------------------

REPLICA_OFF: Final[Text] = (
    "Replika kapalı: mühürlü günlerin ikinci kopyası alınmıyor.",
    "The replica is off: no second copy of the sealed days is made.",
)
REPLICA_ALERT: Final[Text] = (
    "Replika uyarısı açık: kopya gecikti ya da kopyalama başarısız.",
    "A replica alert is open: a copy is late or copying failed.",
)
REPLICA_PENDING: Final[Text] = (
    "{n} mühürlü gün henüz kopyalanmadı (en eskisi {day}).",
    "{n} sealed day(s) not copied yet (the oldest {day}).",
)
REPLICA_NONE: Final[Text] = ("Son 7 günde mühürlü gün yok.", "No sealed day in the last 7 days.")
REPLICA_OK: Final[Text] = (
    "Son 7 günün mühürlü {n} günü replika hedefine kopyalandı ve denetlendi.",
    "All {n} sealed days of the last 7 were copied to the replica target and checked.",
)

# -- card 10 backup -------------------------------------------------------------------------------

BACKUP_OFF: Final[Text] = ("Gece veritabanı yedeği kapalı.", "The nightly database backup is off.")
BACKUP_FAILED: Final[Text] = (
    "Son veritabanı yedeği başarısız ya da yedek uyarısı açık.",
    "The last database backup failed or a backup alert is open.",
)
BACKUP_FIRST: Final[Text] = (
    "İlk veritabanı yedeği henüz alınmadı (Backup görevi 01:40'ta çalışır).",
    "The first database backup has not run yet (the Backup task runs at 01:40).",
)
BACKUP_STALE: Final[Text] = (
    "Son başarılı veritabanı yedeği {at}; {hours} saatten eski.",
    "The last successful database backup was at {at}, more than {hours} hours ago.",
)
BACKUP_NEVER_CONFIRMED: Final[Text] = (
    "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3).",
    "The offline copy of the configuration files was never confirmed (BACKUP.md section 3).",
)
BACKUP_CHANGED: Final[Text] = (
    "Yapılandırma son onaydan ({at}) sonra değişti; yeni bir çevrimdışı kopya alın.",
    "The configuration changed after the last confirmation ({at}); make a new offline copy.",
)
BACKUP_OK: Final[Text] = (
    "Son veritabanı yedeği {at} ({size}); yapılandırma kopyası {confirmed} tarihinde onaylandı.",
    "Last database backup {at} ({size}); configuration copy confirmed on {confirmed}.",
)
BACKUP_NEVER: Final[Text] = ("hiç", "never")

# -- verify on site and the fact labels -----------------------------------------------------------

VERIFY_ON_SITE: Final[Mapping[ComplianceCardId, Text]] = {
    "tsa": (
        "Kontör değeri Kamu SM istemcisinin çıktısından okunur; okuma biçimi sahada "
        "doğrulanmalıdır.",
        "The credit is read from the Kamu SM client's output; verify the format on site.",
    ),
    "content_filter": (
        "Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını kaydettiği ve "
        "ARCILOGGER'a gönderdiği sahada doğrulanmalıdır.",
        "Verify on site that the firewall policies log web and DNS filter events and send them "
        "to ARCILOGGER.",
    ),
    "replica": (
        "Replika hedefinin değiştirilemez (WORM) olduğu ARCILOGGER'dan denetlenemez; sahada "
        "doğrulayın.",
        "Whether the replica target is write-once (WORM) cannot be checked from ARCILOGGER; "
        "verify it on site.",
    ),
}

#: card → fact key → label, in the order the card lists its facts.
FACT_LABELS: Final[Mapping[ComplianceCardId, Mapping[str, Text]]] = {
    "sources": {
        "watched": ("İzlenen kaynak", "Watched sources"),
        "active": ("Veri gönderen", "Sending"),
        "silent": ("Sessiz", "Silent"),
        "heartbeat": ("Kalp atışı süresi", "Heartbeat"),
    },
    "seal_yesterday": {
        "day": ("Gün", "Day"),
        "state": ("Durum", "State"),
        "sealed_at": ("Mühürlendi", "Sealed"),
        "verification": ("Doğrulama", "Verification"),
        "server": ("Damga sunucusu", "Time-stamp server"),
    },
    "chain": {
        "verified": ("Doğrulanmış", "Verified"),
        "not_verified": ("Doğrulanmamış", "Not verified"),
        "failed": ("Başarısız", "Failed"),
        "no_data": ("Veri yok ya da silinmiş", "No data or deleted"),
        "full_check": ("Son tam zincir denetimi", "Last full chain check"),
        "tasks": ("Doğrulama görevleri", "Verification tasks"),
    },
    "retention": {
        "oldest": ("En eski mühürlü gün", "Oldest sealed day"),
        "retention": ("Saklama", "Retention"),
        "deleted": ("Saklama ile silinen gün", "Days deleted by retention"),
    },
    "tsa": {
        "server": ("Damga sunucusu", "Time-stamp server"),
        "credits": ("Kalan kontör", "Credits left"),
        "estimated_days": ("Tahmini gün", "Estimated days"),
        "threshold": ("Eşik", "Threshold"),
        "credit_at": ("Son okuma", "Last reading"),
        "cert_until": ("TSA sertifikası bitişi", "TSA certificate expires"),
        "cert_days": ("Kalan gün", "Days left"),
    },
    "clock": {
        "checked_at": ("Son ölçüm", "Last measurement"),
        "server": ("Sunucu", "Server"),
        "offset": ("Fark", "Offset"),
        "limit": ("Sınır", "Limit"),
        "local_source": ("Yerel saat kaynağı", "Local time source"),
    },
    "content_filter": {
        "last": ("Son kayıt", "Last record"),
        "today_web": ("Bugün web filtresi", "Web filter today"),
        "today_dns": ("Bugün DNS filtresi", "DNS filter today"),
        "days": ("Son 7 gün", "Last 7 days"),
        "profiles": ("Web filtresi profilleri", "Web filter profiles"),
        "dns_profile": ("DNS filtresi profili", "DNS filter profile"),
        "expected_days": ("Beklenen günler", "Expected days"),
        "holidays": ("Resmî tatiller", "Public holidays"),
    },
    "lease_evidence": {
        **{table: (label, label) for table, label in LEASE_TABLES.items()},
        "problems": ("Açık kira kanıtı sorunları", "Open lease evidence problems"),
    },
    "replica": {
        "enabled": ("Replika", "Replica"),
        "last_copy": ("Son kopya", "Last copy"),
        "pending": ("Bekleyen gün", "Days waiting"),
        "stale_hours": ("Gecikme eşiği (saat)", "Delay threshold (hours)"),
    },
    "backup": {
        "last_backup": ("Son veritabanı yedeği", "Last database backup"),
        "size": ("Boyut", "Size"),
        "enabled": ("Gece yedeği", "Nightly backup"),
        "config_copy": ("Yapılandırma kopyası onayı", "Configuration copy confirmed"),
    },
}
