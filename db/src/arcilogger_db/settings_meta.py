"""Labels and help of the database settings for the web interface (ADR-0014, contract 2.13).

``GET /settings`` shows every setting of :data:`arcilogger_db.settings.DEFAULT_SETTINGS` in the
groups of :data:`GROUPS`, each with its Turkish and English label and help from
:data:`SETTINGS`, its kind of value (:attr:`SettingMeta.type`), the bounds of
``INTEGER_RANGES``, whether a change needs a re-authentication (``REAUTH_KEYS``) and whether it
is changed on the console only (``LOCKED_KEYS``). The values themselves come from
``list_settings``.

A test keeps :data:`SETTINGS` to exactly the visible keys: a new setting without a label and a
help text, Turkish and English, fails it. Internal values the code keeps in the same table
(watermarks, import offsets: never in ``DEFAULT_SETTINGS``) are not settings and are not shown.
No text here claims legal compliance.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from arcilogger_db.settings import DEFAULT_SETTINGS, INTEGER_RANGES, LOCKED_KEYS, REAUTH_KEYS

SettingType = Literal["boolean", "integer", "string", "string_list", "integer_list", "object"]


@dataclass(frozen=True)
class Group:
    id: str
    label_tr: str
    label_en: str


#: The groups of the "Uygulama ayarları" page, in their order.
GROUPS: Final[tuple[Group, ...]] = (
    Group("alerts", "Uyarılar ve e-posta", "Alerts and e-mail"),
    Group("lookup", "5651 sorgu ve kira zaman çizelgesi", "5651 lookup and lease timeline"),
    Group("search", "Arama ve canlı akış", "Search and live tail"),
    Group("usage", "Kullanım analizi", "Usage analytics"),
    Group("reports", "Kullanım raporları", "Usage reports"),
    Group("evidence", "Kanıt paketleri", "Evidence packages"),
    Group("security", "Oturum, parola ve gizlilik", "Sessions, passwords and privacy"),
    Group("database", "Veritabanı", "Database"),
)


@dataclass(frozen=True)
class SettingMeta:
    """What the web interface says about one setting; the rest follows from the setting's
    shipped default and the validation rules of :mod:`arcilogger_db.settings`."""

    key: str
    group: str
    label_tr: str
    label_en: str
    help_tr: str
    help_en: str

    @property
    def default(self) -> Any:
        return copy.deepcopy(DEFAULT_SETTINGS[self.key])

    @property
    def type(self) -> SettingType:
        """The JSON kind of the value; a list's items are integers or strings (an empty
        default list holds strings: addresses)."""
        value = DEFAULT_SETTINGS[self.key]
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            numbers = bool(value) and all(type(item) is int for item in value)
            return "integer_list" if numbers else "string_list"
        return "object"

    @property
    def minimum(self) -> int | None:
        """The smallest value of an integer setting (``GET /settings``: ``min``)."""
        return INTEGER_RANGES.get(self.key, (0, None))[0] if self.type == "integer" else None

    @property
    def maximum(self) -> int | None:
        """The largest value of an integer setting, ``None`` without one (``max``)."""
        return INTEGER_RANGES.get(self.key, (0, None))[1] if self.type == "integer" else None

    @property
    def reauth(self) -> bool:
        return self.key in REAUTH_KEYS

    @property
    def locked(self) -> bool:
        return self.key in LOCKED_KEYS


def _group(group: str, *entries: tuple[str, str, str, str, str]) -> list[SettingMeta]:
    return [SettingMeta(key, group, *texts) for key, *texts in entries]


_ALERTS = _group(
    "alerts",
    (
        "alerts.email_recipients",
        "Uyarı e-postası alıcıları",
        "Alert e-mail recipients",
        "Uyarı e-postalarını alan adresler, en çok 20; boş liste: e-posta gönderilmez. SMTP "
        "bağlantısı Ayarlar → E-posta sayfasındadır.",
        "The addresses that receive alert e-mails, at most 20; an empty list sends none. The "
        "SMTP connection is on the Settings → E-mail page.",
    ),
    (
        "alerts.email_min_severity",
        "E-postayla gönderilen en düşük önem",
        "Lowest severity e-mailed",
        "Bu ve daha yüksek önemdeki uyarılar e-postayla gider: info, low, medium, high, critical.",
        "Alerts of this severity and above are e-mailed: info, low, medium, high, critical.",
    ),
    (
        "alerts.email_max_per_hour",
        "Saatte en çok e-posta",
        "E-mails per hour at most",
        "Alıcı başına saatte en çok bu kadar uyarı e-postası; aşılınca kalanlar bir özet "
        "e-postasında sayılır.",
        "At most this many alert e-mails per recipient and hour; beyond it the rest are counted "
        "in a summary e-mail.",
    ),
    (
        "alerts.email_digest_seconds",
        "Toplu e-posta süresi (saniye)",
        "Digest window (seconds)",
        "Bu süre içinde oluşan uyarılar tek e-postada gider; 0: her çalışma hemen gönderir.",
        "Alerts raised within this time go into one e-mail; 0: each run sends at once.",
    ),
    (
        "alerts.email_resolved",
        "Çözülen uyarıları da bildir",
        "Also announce resolved alerts",
        "Açıkken bir uyarı çözüldüğünde de e-posta gönderilir.",
        "When on, an e-mail is also sent when an alert is resolved.",
    ),
    (
        "alerts.web_base_url",
        "E-postadaki web adresi",
        "Web address in the e-mails",
        "E-postalardaki bağlantının adresi (https://…). Boş: sunucunun kendi adresi "
        "([collector] bind) ya da https://arcilogger.internal.",
        "The address of the link in the e-mails (https://…). Empty: the server's own address "
        "([collector] bind) or https://arcilogger.internal.",
    ),
    (
        "alerts.r15_startup_grace_minutes",
        "R15 başlangıç beklemesi (dakika)",
        "R15 start-up grace (minutes)",
        "Worker ya da bir kaynak kaydı başladıktan sonra bu süre dolmadan 'Log kaynağı sessiz' "
        "(R15) uyarısı verilmez.",
        "No 'log source silent' (R15) alert before this time has passed since the worker or a "
        "source row started.",
    ),
    (
        "alerts.r15_never_seen_minutes",
        "Hiç mesaj göndermeyen kaynak (dakika)",
        "Source that never sent (minutes)",
        "Yapılandırılmış ama hiç mesaj göndermemiş bir kaynak, kaydı oluşturulduktan bu kadar "
        "sonra R15 uyarısı verir.",
        "A configured source that never sent a message raises R15 this long after its row was "
        "created.",
    ),
    (
        "alerts.disk_used_percent",
        "Disk doluluk eşiği (%)",
        "Disk usage threshold (%)",
        "Veri ya da sistem diski bu yüzdeden dolu olunca R20-disk uyarısı; %95'ten itibaren "
        "kritik.",
        "R20-disk when the data or system drive is fuller than this percentage; critical from "
        "95 %.",
    ),
    (
        "alerts.seal_overdue_hour",
        "Mühürleme gecikme saati",
        "Sealing overdue hour",
        "Kapanmış bir gün ertesi gün bu saate kadar (yerel saat) mühürlenmemişse R20-seal uyarısı.",
        "R20-seal when a closed day is still not sealed at this hour (local time) of the next day.",
    ),
    (
        "alerts.replica_stale_hours",
        "Replika gecikme eşiği (saat)",
        "Replica delay threshold (hours)",
        "Mühürlü bir gün bundan uzun süre replikada yoksa R20-replica uyarısı.",
        "R20-replica when a sealed day has had no replica copy for longer than this.",
    ),
    (
        "alerts.parse_failure_percent",
        "Ayrıştırılamayan satır eşiği (%)",
        "Unparsed lines threshold (%)",
        "Bir kaynağın son bir saatteki mesajlarının bu yüzdeden fazlası ayrıştırılamazsa "
        "R20-parse uyarısı.",
        "R20-parse when more than this percentage of a source's messages of the last hour "
        "could not be parsed.",
    ),
    (
        "alerts.parse_min_messages",
        "R20-parse için en az mesaj",
        "Fewest messages for R20-parse",
        "Son bir saatte bundan az mesaj gönderen kaynak için oran hesaplanmaz.",
        "No rate is computed for a source that sent fewer messages in the last hour.",
    ),
    (
        "alerts.job_failures",
        "Art arda başarısız çalışma",
        "Failed runs in a row",
        "Bir arka plan işi bu kadar kez art arda başarısız olursa R20-job uyarısı.",
        "R20-job when a background job fails this many times in a row.",
    ),
    (
        "alerts.backup_stale_hours",
        "Yedek yaşı eşiği (saat)",
        "Backup age threshold (hours)",
        "Son başarılı veritabanı yedeği bundan eskiyse R20-backup uyarısı.",
        "R20-backup when the last successful database backup is older than this.",
    ),
    (
        "alerts.backup_missing_hours",
        "İlk yedek için süre (saat)",
        "Time to the first backup (hours)",
        "Kurulumdan bu kadar sonra hiç yedek yoksa R20-backup uyarısı (orta önem).",
        "R20-backup (medium) when there is no backup at all this long after the installation.",
    ),
)

_LOOKUP = _group(
    "lookup",
    (
        "nat.public_ips",
        "Genel IP adresleri (NAT)",
        "Public IP addresses (NAT)",
        "Güvenlik duvarının genel (WAN) adresleri. Boş: servis sağlayıcının yönlendiricisi "
        "ikinci bir NAT yapıyor; genel IP ve porta göre sorgular yalnızca aday listesi verebilir.",
        "The firewall's public (WAN) addresses. Empty: the ISP router does a second NAT; "
        "lookups by public IP and port can only give ranked candidates.",
    ),
    (
        "nat.wan_transip",
        "Güvenlik duvarının WAN adresi (transip)",
        "Firewall WAN address (transip)",
        "FortiGate'in transip alanında görünen kendi WAN adresi (ör. servis sağlayıcının "
        "yönlendiricisinin arkasındaki özel adres). Kurulum yerinde girilir, tahmin edilmez.",
        "The FortiGate's own WAN address as seen in transip (e.g. a private address behind the "
        "ISP router). Entered on site, never assumed.",
    ),
    (
        "lookup.tolerance_seconds",
        "Sorgu toleransı (± saniye)",
        "Lookup tolerance (± seconds)",
        "5651 sorgusunun istenen zamanın iki yanında baktığı süre.",
        "How far on either side of the requested time a 5651 lookup looks.",
    ),
    (
        "lookup.max_candidates",
        "En çok aday",
        "Most candidates",
        "Bir sorgunun döndürdüğü en çok aday sayısı.",
        "The most candidates a lookup returns.",
    ),
    (
        "lookup.max_session_seconds",
        "En uzun oturum (saniye)",
        "Longest session (seconds)",
        "Sorgunun hesaba kattığı en uzun FortiGate oturumu: o anda süren ama sonra loglanan "
        "oturumlar için geriye taranan süre. FortiGate oturum sürelerine göre denetleyin.",
        "The longest FortiGate session a lookup considers: how far back it scans for sessions "
        "running at the time but logged later. Check it against the FortiGate session TTLs.",
    ),
    (
        "lookup.timeout_seconds",
        "Sorgu zaman aşımı (saniye)",
        "Lookup timeout (seconds)",
        "Sorgu bu süreden sonra durdurulur ve zaman aşımı bildirilir.",
        "A lookup is stopped after this time and reports a timeout.",
    ),
    (
        "lookup.port_hint",
        "Port ipucu",
        "Port hint",
        "Adaylar sıralanırken FortiGate portu istenen genel porta eşit olanlar öne alınır. "
        "Servis sağlayıcının yönlendiricisinin kaynak portları koruduğunu yerinde doğrulamadan "
        "açmayın.",
        "Candidates whose FortiGate port equals the requested public port rank first. Verify on "
        "site that the ISP router keeps source ports before switching it on.",
    ),
    (
        "lookup.dns_lookback_seconds",
        "DNS geriye bakış (saniye)",
        "DNS look-back (seconds)",
        "Bu kadar eski bir DNS yanıtı, istenen alan adını bir adrese hâlâ bağlar.",
        "A DNS answer this old still links a requested domain to an address.",
    ),
    (
        "lookup.report_timestamp",
        "Resmî rapora zaman damgası",
        "Time stamp of official reports",
        "Her resmî PDF raporunun SHA-256 değeri [tsa] sağlayıcısıyla damgalanır (Kamu SM ile "
        "rapor başına 1 kontör).",
        "The SHA-256 of every official PDF report is time-stamped with the [tsa] provider (1 "
        "credit per report with Kamu SM).",
    ),
    (
        "lease.default_seconds",
        "Varsayılan kira süresi (saniye)",
        "Default lease time (seconds)",
        "DHCP kiralarının süresi (varsayılan 1 gün); süresi farklı sunucular bir sonraki "
        "ayardadır.",
        "The lease time of the DHCP servers (default one day); servers with another lease time "
        "are in the next setting.",
    ),
    (
        "lease.server_seconds",
        "Sunucuya göre kira süresi",
        "Lease time per server",
        'Kira süresi farklı DHCP sunucuları: {"<sunucu adı>": saniye}, her biri en az 60.',
        'DHCP servers with another lease time: {"<server name>": seconds}, each at least 60.',
    ),
    (
        "lease.timeout_percent",
        "Kira zaman aşımı çarpanı (%)",
        "Lease timeout factor (%)",
        "Son kanıttan sonra açık bir aralık, kira süresinin bu yüzdesi kadar sonra kapanır "
        "(110 = 1,1 kat).",
        "After the last evidence an open interval times out after this percentage of the lease "
        "time (110 = 1.1 times).",
    ),
    (
        "dhcp.server_vlan_regex",
        "DHCP sunucu adından VLAN",
        "VLAN from the DHCP server name",
        "DHCP sunucu adından VLAN numarasını alan düzenli ifade; ilk grup VLAN numarasıdır "
        "(dhcp60 → 60).",
        "The regular expression that takes the VLAN number from the DHCP server name; the first "
        "group is the VLAN number (dhcp60 → 60).",
    ),
    (
        "arp.interval_seconds",
        "ARP kaydı aralığı (saniye)",
        "ARP log interval (seconds)",
        "MikroTik zamanlayıcı betiğinin ARP-LOG aralığı.",
        "The ARP-LOG interval of the MikroTik scheduler script.",
    ),
    (
        "arp.ignore_after_release_seconds",
        "Bırakmadan sonra ARP'yi yok say (saniye)",
        "Ignore ARP after a release (seconds)",
        "Aynı IP ve MAC'in DHCP bırakmasından bu kadar sonraki ARP görüleri eski önbellek "
        "kaydıdır, kullanım kanıtı sayılmaz (0: kapalı; en çok ARP kaydı aralığı).",
        "ARP sightings this long after a DHCP release of the same IP and MAC are stale cache "
        "entries, not evidence of use (0: off; at most the ARP log interval).",
    ),
    (
        "devices.infrastructure_macs",
        "Altyapı MAC adresleri",
        "Infrastructure MAC addresses",
        "IP çakışması denetiminin dışında tutulan yönlendirici ve benzeri cihazların MAC "
        "adresleri.",
        "MAC addresses of routers and similar devices left out of the IP conflict detection.",
    ),
    (
        "devices.oui_file",
        "OUI listesi dosyası",
        "OUI listing file",
        "Sunucuya konmuş IEEE OUI listesinin yolu (boş: üretici adı gösterilmez). Sunucudaki bir "
        "dosya yolu olduğu için yalnızca sunucu konsolundan değiştirilir.",
        "The path of an IEEE OUI listing placed on the server (empty: no vendor names). A file "
        "path on the server: changed on the server console only.",
    ),
    (
        "worker.leases_lag_seconds",
        "Kira zaman çizelgesi gecikmesi (saniye)",
        "Lease timeline lag (seconds)",
        "Kira zaman çizelgesi bundan yeni kanıtı bir sonraki çalışmaya bırakır.",
        "The lease timeline leaves evidence younger than this for its next run.",
    ),
)

_SEARCH = _group(
    "search",
    (
        "search.max_days",
        "En uzun arama aralığı (gün)",
        "Longest search range (days)",
        "Bir aramanın başlangıcı ile bitişi arasında en çok bu kadar gün olabilir (cihaz zamanı).",
        "A search may span at most this many days from its start to its end (device time).",
    ),
    (
        "search.page_size_max",
        "Sayfa başına en çok satır",
        "Most rows per page",
        "Bir arama isteğinin isteyebileceği en çok satır; web arayüzü 100 satır ister.",
        "The most rows one search request may ask for; the web interface asks for 100.",
    ),
    (
        "search.request_budget_ms",
        "Satır arama süresi (ms)",
        "Time for rows (ms)",
        "Bir istek satırları en çok bu kadar milisaniye arar; süre dolunca o ana kadar "
        "bulduklarını taranan aralıkla birlikte gösterir.",
        "One request looks for rows at most this many milliseconds; then it shows what it found "
        "so far, with the interval scanned.",
    ),
    (
        "search.stats_budget_ms",
        "İstatistik süresi (ms)",
        "Time for statistics (ms)",
        "Grafik ve dağılımlar en çok bu kadar milisaniye sayılır; süre dolunca sayılmayan kısım "
        "gösterilir.",
        "The chart and the facets are counted at most this many milliseconds; then the part not "
        "counted is shown.",
    ),
    (
        "search.facet_sample_rows",
        "Dağılımlar için en çok eşleşme",
        "Most matches for facets",
        "Özet tablodan sayılamayan dağılımlar en yeni bu kadar eşleşmeye göre hesaplanır.",
        "Facets the summary table cannot answer are computed from the newest this many matches.",
    ),
    (
        "search.max_concurrent",
        "Aynı anda çalışan arama",
        "Searches running at once",
        "Sunucuda aynı anda çalışabilen arama, sayfa, istatistik ve dışa aktarma istekleri; "
        "veritabanı bağlantı sayısının 2 eksiğini geçmemeli.",
        "Search, page, statistics and export requests the server runs at once; keep it at most "
        "the database pool size minus 2.",
    ),
    (
        "search.runs_per_minute",
        "Dakikada en çok yeni arama",
        "New searches per minute",
        "Bir kullanıcının dakikada başlatabileceği en çok yeni arama.",
        "The most new searches one user may start per minute.",
    ),
    (
        "search.export_max_rows",
        "Dışa aktarmada en çok satır",
        "Most rows per export",
        "Bir dışa aktarma dosyası en çok bu kadar satır içerir; fazlası kesilir ve dosyada "
        "belirtilir.",
        "One export file holds at most this many rows; the rest is cut off and the file says so.",
    ),
    (
        "search.export_max_per_hour",
        "Saatte en çok dışa aktarma",
        "Exports per hour",
        "Bir kullanıcının saatte en çok dışa aktarma sayısı.",
        "The most exports of one user per hour.",
    ),
    (
        "search.export_budget_seconds",
        "Dışa aktarma süresi (saniye)",
        "Time for an export (seconds)",
        "Bir dışa aktarma dosyası en çok bu kadar saniyede hazırlanır; süre dolunca dosya "
        "kesilir ve dosyada belirtilir.",
        "One export file is built in at most this many seconds; then the file is cut off and "
        "says so.",
    ),
    (
        "search.saved_max_per_user",
        "Kullanıcı başına kayıtlı arama",
        "Saved searches per user",
        "Bir kullanıcının saklayabileceği en çok kayıtlı arama; kayıtlı aramalar kişiseldir.",
        "The most saved searches one user may keep; saved searches are personal.",
    ),
    (
        "live.max_streams_total",
        "Sunucuda en çok canlı akış",
        "Live tails on the server",
        "Sunucuda aynı anda açık olabilen en çok canlı akış.",
        "The most live tails open on the server at a time.",
    ),
    (
        "live.max_streams_per_user",
        "Kullanıcı başına canlı akış",
        "Live tails per user",
        "Bir kullanıcının aynı anda açık tutabileceği en çok canlı akış.",
        "The most live tails one user may keep open at a time.",
    ),
    (
        "live.max_lines_per_second",
        "Akış başına saniyede satır",
        "Lines a second per tail",
        "Bir canlı akışın saniyede gösterdiği en çok satır; fazlası sayılır ve gösterilmedi "
        "olarak bildirilir.",
        "The most lines one live tail shows a second; the rest are counted and reported as not "
        "shown.",
    ),
    (
        "live.parse_lines_per_second",
        "Saniyede ayrıştırılan satır",
        "Lines parsed a second",
        "Canlı akışlar için sunucunun saniyede ayrıştırdığı en çok satır; fazlası ayrıştırılmadan "
        "gösterilir ya da alan filtresi uygulanamadığı için bildirilir.",
        "The most lines a second the server parses for the live tails; the rest are shown "
        "unparsed, or reported because a field filter could not be applied.",
    ),
    (
        "live.buffer_lines",
        "Sürdürme için saklanan satır",
        "Lines kept for a resume",
        "Duraklatılan ya da kopan bir akışın sürdürülebilmesi için sunucuda tutulan en yeni "
        "satırlar.",
        "The newest lines the server keeps so that a paused or dropped tail can resume.",
    ),
    (
        "live.max_minutes",
        "Canlı akış süresi (dakika)",
        "Live tail duration (minutes)",
        "Bir canlı akış bu kadar dakika sonra durur (gözetimsiz ekran); Sürdür ile yeniden başlar.",
        "A live tail stops after this many minutes (unattended screen); Resume starts it again.",
    ),
    (
        "live.backfill_lines",
        "Açılışta gösterilen satır",
        "Lines shown on opening",
        "Bir canlı akış açılırken hemen gösterilen en çok son satır; 0: yalnızca yeni satırlar.",
        "The most recent lines shown at once when a live tail opens; 0: new lines only.",
    ),
)

_USAGE = _group(
    "usage",
    (
        "usage.active_bytes_threshold",
        "Aktif dakika eşiği (bayt)",
        "Active minute threshold (bytes)",
        "Bir dakika en az bu kadar arka plan dışı trafikle aktif sayılır.",
        "A minute counts as active with at least this much non-background traffic.",
    ),
    (
        "usage.background_session_bytes",
        "Arka plan oturumu (bayt)",
        "Background session (bytes)",
        "Toplam trafiği (gönderilen ve alınan) bundan az olan oturum arka plan trafiğidir.",
        "Sessions with less traffic in total (sent and received) are background traffic.",
    ),
    (
        "usage.dns_window_seconds",
        "DNS penceresi (saniye)",
        "DNS window (seconds)",
        "Bu kadar eski bir DNS yanıtı hâlâ bir oturumun hedefini adlandırır.",
        "A DNS answer this old still names a session's destination.",
    ),
    (
        "usage.session_overlap_seconds",
        "Gün taşması (saniye)",
        "Day overlap (seconds)",
        "Gün bittikten sonraki bu kadar saniyelik kayıtlar hâlâ o güne sayılır (gece yarısını "
        "geçen oturumlar).",
        "Logs this long after the end of a day still count for it (sessions that run over "
        "midnight).",
    ),
    (
        "usage.background_domains",
        "Arka plan alan adları",
        "Background host names",
        "Arka plan sayılan ana bilgisayar adı kalıpları (* ve ?; *.x kalıbı x'i de kapsar), en "
        "çok 500.",
        "Host name patterns counted as background (* and ?; a *.x pattern also matches x), at "
        "most 500.",
    ),
    (
        "usage.background_appcats",
        "Arka plan uygulama kategorileri",
        "Background application categories",
        "Ana bilgisayar adı olmayan oturumlarda arka plan sayılan FortiGate uygulama kategorileri.",
        "FortiGate application categories counted as background for sessions without a host name.",
    ),
    (
        "usage.background_ports",
        "Arka plan portları",
        "Background ports",
        "Arka plan sayılan hedef portlar (1-65535).",
        "Destination ports counted as background (1-65535).",
    ),
    (
        "usage.work_hours",
        "Mesai saatleri",
        "Work hours",
        "Mesai günleri (1 = Pazartesi … 7 = Pazar) ve saatleri (yerel saat, bitiş hariç); geri "
        "kalan süre ve tatiller mesai dışıdır.",
        "Work days (1 = Monday … 7 = Sunday) and hours (local time, end excluded); the rest of "
        "the time and the holidays are off hours.",
    ),
    (
        "usage.holidays",
        "Tatiller",
        "Holidays",
        "AA-GG her yıl, YYYY-AA-GG bir kez. Yalnızca sabit tarihli resmî tatiller hazır gelir; "
        "dinî bayramların tarihlerini her yıl girin.",
        "MM-DD every year, YYYY-MM-DD once. Only the fixed-date public holidays are shipped; "
        "enter the dates of the religious holidays every year.",
    ),
    (
        "usage.psl_file",
        "Genel Sonek Listesi dosyası",
        "Public Suffix List file",
        "Sunucuya konmuş daha yeni bir public_suffix_list.dat dosyasının yolu (boş: paketteki "
        "liste; hiçbir zaman indirilmez). Sunucudaki bir dosya yolu olduğu için yalnızca sunucu "
        "konsolundan değiştirilir.",
        "The path of a newer public_suffix_list.dat placed on the server (empty: the bundled "
        "list; never downloaded). A file path on the server: changed on the server console "
        "only.",
    ),
    (
        "usage.psl_private_suffixes",
        "Özel sonekler de sayılsın",
        "Count private suffixes too",
        "Açıkken Genel Sonek Listesi'nin özel bölümü (github.io gibi) de sayılır.",
        "When on, the private part of the Public Suffix List (such as github.io) counts too.",
    ),
    (
        "usage.auto_rebuild_days",
        "Kendiliğinden yeniden hesaplanan günler",
        "Days rebuilt automatically",
        "Hesaplandığı girdiler değiştiğinde (cihaz atamaları, kiralar, geç gelen loglar, bu "
        "ayarlar) kendiliğinden yeniden hesaplanan geçmiş gün sayısı.",
        "The days before today rebuilt by themselves when what they were computed from changed "
        "(device assignments, leases, late logs, these settings).",
    ),
    (
        "usage.retention_days",
        "Kullanım özetlerinin saklama süresi (gün)",
        "Usage rollup retention (days)",
        "Bundan eski kullanım özetleri silinir; loglar veritabanında saklama süresi kadar kalır.",
        "Usage rollups older than this are deleted; the logs stay for the database retention.",
    ),
    (
        "usage.urls_per_day",
        "Günlük URL sayısı",
        "URLs per day",
        "Kişi ve gün başına saklanan en yeni URL sayısı.",
        "The newest URLs kept per subject and day.",
    ),
    (
        "usage.blocked_per_day",
        "Günlük engellenen deneme",
        "Blocked attempts per day",
        "Kişi ve gün başına saklanan engellenen deneme kaydı; günlük sayaçlar her zaman tamdır.",
        "Blocked attempts kept per subject and day; the daily counters are always complete.",
    ),
    (
        "usage.pseudonymize_anonymous_role",
        "Anonim yetkide takma ad",
        "Pseudonyms for the anonymous permission",
        "Açıkken usage.anonymous yetkisi olup usage.person yetkisi olmayan görüntüleyiciler kişi "
        "adları yerine takma ad görür.",
        "When on, viewers with usage.anonymous but without usage.person see pseudonyms instead "
        "of names.",
    ),
    (
        "usage.change_min_percent",
        "Dikkat çeken değişiklik (%)",
        "Notable change (%)",
        "Raporlarda dikkat çeken değişiklik: 7 günlük ortalamaya göre en az bu kadar yüzde.",
        "A notable change in the reports: at least this percentage against the 7-day average.",
    ),
    (
        "usage.change_min_seconds",
        "Dikkat çeken değişiklik (saniye)",
        "Notable change (seconds)",
        "Sürelerde bir değişiklik, bir tarafta en az bu kadar saniye olunca dikkat çeker.",
        "A change of time is notable with at least this many seconds on one side.",
    ),
    (
        "usage.change_min_bytes",
        "Dikkat çeken değişiklik (bayt)",
        "Notable change (bytes)",
        "Veri hacminde bir değişiklik, bir tarafta en az bu kadar bayt olunca dikkat çeker (1 "
        "GiB = 1073741824).",
        "A change of volume is notable with at least this many bytes on one side (1 GiB = "
        "1073741824).",
    ),
)

_REPORTS = _group(
    "reports",
    (
        "reports.keep_days",
        "Rapor dosyalarının saklama süresi (gün)",
        "Report file retention (days)",
        "Rapor dosyaları bu kadar gün sonra silinir; çalıştırma kayıtları ve SHA-256 değerleri "
        "kalır.",
        "Report files are deleted after this many days; the runs and their SHA-256 stay.",
    ),
    (
        "reports.max_attachment_mb",
        "En büyük ek boyutu (MB)",
        "Largest attachments (MB)",
        "Ekler toplamda bundan büyükse e-postaya eklenmez; e-posta Raporlar sayfasındaki "
        "indirmeye bağlantı verir.",
        "Attachments larger than this in total are left out; the e-mail links to the download "
        "on the Raporlar page instead.",
    ),
    (
        "reports.catch_up",
        "Kaçan raporu gönder",
        "Send a missed report",
        "Sunucu kapalıyken kaçan zamanlanmış rapor bir kez, en son zamanı için gönderilir "
        "(kapalı: 30 dakikadan çok geciken atlanır).",
        "After the worker was stopped, a missed schedule runs once for its newest occurrence "
        "(off: occurrences more than 30 minutes late are skipped).",
    ),
)

_EVIDENCE = _group(
    "evidence",
    (
        "evidence.keep_days",
        "Kanıt paketlerinin saklama süresi (gün)",
        "Evidence package retention (days)",
        "Kanıt paketleri bu kadar gün sonra exports klasöründen silinir; kanıtın kendisi "
        "kasada kalır.",
        "Evidence packages are deleted from exports after this many days; the evidence itself "
        "stays in the vault.",
    ),
    (
        "evidence.max_unfinished_per_user",
        "Kullanıcı başına bekleyen paket",
        "Unfinished packages per user",
        "Bir kullanıcının aynı anda sırada ya da hazırlanmakta olan en çok paket sayısı.",
        "The most packages one user may have queued or building at a time.",
    ),
    (
        "evidence.max_unfinished_total",
        "Toplam bekleyen paket",
        "Unfinished packages in total",
        "Aynı anda sırada ya da hazırlanmakta olan en çok paket sayısı.",
        "The most packages queued or building at a time.",
    ),
    (
        "evidence.max_requests_per_hour",
        "Saatte en çok paket isteği",
        "Package requests per hour",
        "Bir kullanıcının saatte en çok paket isteği.",
        "The most package requests of one user per hour.",
    ),
    (
        "evidence.min_free_percent",
        "Kalacak boş yer (%)",
        "Free space to keep (%)",
        "Paketten ve bekleyen paketlerden sonra veri diskinde kalması gereken boş yer yüzdesi "
        "(en az bir sonraki ayardaki GiB).",
        "The free share of the data disk that must remain after the package and every waiting "
        "one (at least the GiB of the next setting).",
    ),
    (
        "evidence.min_free_gb",
        "Kalacak boş yer (GiB)",
        "Free space to keep (GiB)",
        "Paketlerden sonra veri diskinde kalması gereken en az boş yer.",
        "The least free space that must remain on the data disk after the packages.",
    ),
    (
        "evidence.exports_max_gb",
        "exports klasörünün en büyük boyutu (GiB)",
        "Largest size of exports (GiB)",
        "exports klasöründeki paketlerin en büyük toplam boyutu.",
        "The largest total size of the packages in the exports folder.",
    ),
)

_SECURITY = _group(
    "security",
    (
        "auth.idle_minutes",
        "Boşta kalma süresi (dakika)",
        "Idle timeout (minutes)",
        "Bu kadar dakika işlem yapılmayan oturum kapanır.",
        "A session without activity for this many minutes ends.",
    ),
    (
        "auth.absolute_hours",
        "En uzun oturum (saat)",
        "Longest session (hours)",
        "Bir oturum en çok bu kadar saat sürer.",
        "A session lasts at most this many hours.",
    ),
    (
        "auth.lockout_attempts",
        "Kilitlemeden önce deneme",
        "Attempts before lockout",
        "Bu kadar hatalı parola denemesinden sonra hesap bir süre kilitlenir.",
        "After this many wrong passwords the account is locked for a while.",
    ),
    (
        "auth.lockout_minutes",
        "Kilit süresi (dakika)",
        "Lockout time (minutes)",
        "Kilitlenen hesabın yeniden denenebilmesi için geçmesi gereken süre.",
        "How long a locked account waits before it can be tried again.",
    ),
    (
        "auth.password_min_length",
        "En kısa parola",
        "Shortest password",
        "Bir parolanın en az karakter sayısı.",
        "The fewest characters of a password.",
    ),
    (
        "auth.totp_required_roles",
        "İki adımlı doğrulama zorunlu roller",
        "Roles that need two-step verification",
        "Bu rollerin kullanıcıları iki adımlı doğrulama (TOTP) kullanmak zorundadır; admin ve "
        "auditor her zaman listededir.",
        "Users of these roles must use two-step verification (TOTP); admin and auditor are "
        "always in the list.",
    ),
    (
        "privacy.notice_version",
        "Aydınlatma metni sürümü",
        "Privacy notice version",
        "Kullanıcıların kabul etmesi gereken KVKK aydınlatma metninin sürümü; değiştirildiğinde "
        "herkes metni yeniden kabul eder.",
        "The version of the KVKK privacy notice users must accept; after a change everyone "
        "accepts it again.",
    ),
)

_DATABASE = _group(
    "database",
    (
        "db.retention_days",
        "Veritabanında saklama süresi (gün)",
        "Database retention (days)",
        "Veritabanının sorgular için tuttuğu log günleri (varsayılan 400). Mühürlü kasadaki ham "
        "loglar bundan etkilenmez.",
        "How many days of logs the database keeps for lookups (default 400). The raw logs in "
        "the sealed vault are not affected.",
    ),
)

#: Every visible setting, by key, in the order of :data:`GROUPS` (then as listed).
SETTINGS: Final[Mapping[str, SettingMeta]] = {
    meta.key: meta
    for meta in (
        *_ALERTS,
        *_LOOKUP,
        *_SEARCH,
        *_USAGE,
        *_REPORTS,
        *_EVIDENCE,
        *_SECURITY,
        *_DATABASE,
    )
}


def setting_meta(key: str) -> SettingMeta:
    """The entry of a visible setting; ``KeyError`` for any other key."""
    return SETTINGS[key]


def group_settings(group: str) -> list[SettingMeta]:
    """The settings of one group of :data:`GROUPS`, in their order."""
    return [meta for meta in SETTINGS.values() if meta.group == group]
