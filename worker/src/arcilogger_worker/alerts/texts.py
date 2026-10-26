"""What an alert e-mail may say about an alert (Turkish first, English second; ADR-0010).

**No log content leaves the server** (spec 0): an e-mail never quotes an alert's title or
details. It is built only from this catalogue — the rule's name, one line on what to do — and
from a few **whitelisted facts** of ``details``, each checked for its shape before it is used
(a source name — a sender inside a configured network or one that is not configured is named with
its IP address, ``ap-172.20.10.23``, ``unknown-172.20.19.9`` —, a day, a number, a drive letter,
a job name, a licence state and the names of licence modules, the IP address of a new sender, of
a message refused for its transport (``wrong-transport``) or of an IP conflict, the section and
the field names of a configuration change and the outcome code of the browser administration).
Anything else an alert carries — raw log lines, error texts, person or device owner names, MAC
addresses, paths, configuration values, user names, the licensee — is never put into an e-mail,
whatever it contains.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from arcilogger_core.admin.messages import MESSAGES as ADMIN_MESSAGES
from arcilogger_core.admin.messages import WARNINGS as ADMIN_WARNINGS
from arcilogger_core.fmt import format_date, format_datetime, format_number
from arcilogger_core.seal import CONFIG_CAUSES

SEVERITY_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "info": ("Bilgi", "Info"),
    "low": ("Düşük", "Low"),
    "medium": ("Orta", "Medium"),
    "high": ("Yüksek", "High"),
    "critical": ("Kritik", "Critical"),
}
STATE_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "open": ("Açık", "Open"),
    "acknowledged": ("Görüldü", "Acknowledged"),
    "resolved": ("Çözüldü", "Resolved"),
    "dismissed": ("Yok sayıldı", "Dismissed"),
}

#: A configured source name (``[[sources]] name``), a network member (``ap-172.20.10.23``) or an
#: unknown sender (``unknown-172.20.19.9``).
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,90}$")
_JOB = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DRIVE = re.compile(r"^(?:[A-Za-z]:|/[A-Za-z0-9._/-]{0,100})$")


@dataclass(frozen=True)
class RuleText:
    name_tr: str
    name_en: str
    action_tr: str
    action_en: str


RULES: Final[Mapping[str, RuleText]] = {
    "R15": RuleText(
        "Log kaynağı sessiz",
        "Log source silent",
        "Cihazın açık ve ağa bağlı olduğunu, syslog ayarının ARCILOGGER sunucusunu gösterdiğini "
        "denetleyin (DEVICE-SETUP.md).",
        "Check that the device is on and connected and that its syslog points to the ARCILOGGER "
        "server (DEVICE-SETUP.md).",
    ),
    "R20-seal": RuleText(
        "Gün mühürlenmedi",
        "Day not sealed",
        "Mühürleme sayfasına ve logs\\core.log dosyasına bakın; Kamu SM erişimini ve kontörü "
        "denetleyin.",
        "Look at the sealing page and logs\\core.log; check the Kamu SM connection and credit.",
    ),
    "R20-seal-config": RuleText(
        "Mühürleme başlatılamıyor (yapılandırma)",
        "Sealing cannot start (configuration)",
        "Eksik olanı ILK-ADIMLAR.md bölüm 3'e göre tamamlayın; Seal görevi 5 dakika içinde "
        "yeniden dener.",
        "Complete what is missing (FIRST-STEPS.md section 3); the Seal task retries within 5 "
        "minutes.",
    ),
    "R20-seal-skip": RuleText(
        "Uzun süre hiç log yok",
        "Long period without any log",
        "Toplayıcının çalıştığını ve sunucu saatini denetleyin.",
        "Check that the collector runs and that the server clock is right.",
    ),
    "R20-clock": RuleText(
        "Saat sapması ya da saat atlaması",
        "Clock offset or clock step",
        "Windows Saat hizmetini ve NTP sunucusunu denetleyin (Set-ArciTime.ps1).",
        "Check the Windows Time service and the NTP server (Set-ArciTime.ps1).",
    ),
    "R20-verify": RuleText(
        "Mühür doğrulaması başarısız",
        "Seal verification failed",
        "Aynı gün inceleyin: Mühürleme sayfasında günü yeniden doğrulayın.",
        "Look into it the same day: verify the day again on the sealing page.",
    ),
    "R20-credit": RuleText(
        "Kamu SM kontörü az",
        "Kamu SM credit low",
        "Kamu SM'den kontör alın (kamusm.md).",
        "Buy credit from Kamu SM (kamusm.md).",
    ),
    "R20-replica": RuleText(
        "İkinci kopya (replika) sorunu",
        "Replica problem",
        "İkinci kopya hedefine ([replica] target) erişimi denetleyin: arcilogger-core replica "
        "--test.",
        "Check access to the replica target ([replica] target): arcilogger-core replica --test.",
    ),
    "R20-retention": RuleText(
        "İmha çalışması",
        "Retention run",
        "İmha tutanağını ve logs\\core.log dosyasını inceleyin.",
        "Review the deletion record and logs\\core.log.",
    ),
    "R20-spool": RuleText(
        "Ham log diske yazılamıyor",
        "Raw logs cannot be written",
        "Veri diskini (boş alan, donanım) hemen denetleyin.",
        "Check the data disk (free space, hardware) at once.",
    ),
    "R20-spool-torn": RuleText(
        "Yarım kalmış ham log satırı kapatıldı",
        "Torn raw log line closed",
        "Elektrik kesintisinin ya da çökmenin nedenini inceleyin.",
        "Find the cause of the power cut or crash.",
    ),
    "R20-disk": RuleText(
        "Disk dolmak üzere",
        "Disk almost full",
        "Boş alan açın ya da diski büyütün; ham kayıtları ve kasayı silmeyin.",
        "Free space or enlarge the disk; never delete raw logs or the vault.",
    ),
    "R20-parse": RuleText(
        "Ayrıştırılamayan satır oranı yüksek",
        "Many lines could not be parsed",
        "Cihaz yazılımı ya da log biçimi değişmiş olabilir (PARSERS.md).",
        "The device firmware or log format may have changed (PARSERS.md).",
    ),
    "R20-job": RuleText(
        "Arka plan işi art arda başarısız",
        "Background job failing repeatedly",
        "logs\\worker.log ve state\\worker-status.json dosyalarına bakın.",
        "Look at logs\\worker.log and state\\worker-status.json.",
    ),
    "R20-backup": RuleText(
        "Veritabanı yedeği sorunu",
        "Database backup problem",
        "Yedekleme görevini ve yedek klasörünü denetleyin (ILK-ADIMLAR.md bölüm 10).",
        "Check the backup task and the backup folder (FIRST-STEPS.md section 10).",
    ),
    "R20-email": RuleText(
        "Uyarı e-postası gönderilemedi",
        "Alert e-mail could not be sent",
        "[smtp] ayarlarını ve smtp.password'u denetleyin: arcilogger-worker alerts test-email.",
        "Check [smtp] and smtp.password: arcilogger-worker alerts test-email.",
    ),
    # Area R (ADR-0012): a scheduled usage report could not be made or e-mailed.
    "R20-report": RuleText(
        "Kullanım raporu gönderilemedi",
        "Usage report could not be delivered",
        "Raporlar sayfasında çalıştırmanın hatasına bakın; e-posta için [smtp] ayarlarını "
        "denetleyin: arcilogger-worker alerts test-email.",
        "See the run's error on the Reports page; for e-mail check [smtp]: arcilogger-worker "
        "alerts test-email.",
    ),
    # The licence (license.check): the paid modules only; no licence state locks a 5651 function.
    "R20-license": RuleText(
        "Ücretli modüller kapanıyor ya da kapalı (lisans)",
        "Paid modules ending or off (licence)",
        "Ayarlar → Lisans sayfasına bakın (LISANS.md); 5651 işlevleri her lisans durumunda "
        "çalışır.",
        "See Settings → Licence (LICENSING.md); the 5651 functions work in every licence state.",
    ),
    "R20-license-limit": RuleText(
        "Lisans sınırı aşıldı",
        "Licence limit exceeded",
        "Ayarlar → Lisans sayfasında sınırı görün (LISANS.md bölüm 6); 5651 işlevleri etkilenmez.",
        "See the limit under Settings → Licence (LICENSING.md section 6); the 5651 functions are "
        "not affected.",
    ),
    "R20-license-maintenance": RuleText(
        "Lisansın bakım süresi",
        "Licence maintenance",
        "Yeni sürümler ve destek için bakımı yenileyin (LISANS.md bölüm 7); lisans geçerli kalır.",
        "Renew maintenance for new versions and support (LICENSING.md section 7); the licence "
        "stays valid.",
    ),
    # Browser administration (ADR-0014): the agent ArciAdmin, ApplySystem and the worker's check
    # of the agent's heartbeat raise R20-admin; the agent raises R20-config-change.
    "R20-admin": RuleText(
        "Tarayıcıdan yönetim sorunu",
        "Browser administration problem",
        "Ayarlar → Değişiklik geçmişi sayfasına ve Olay Görüntüleyicisi'ndeki ARCILOGGER-Admin "
        "kayıtlarına bakın; hizmeti denetleyin: Get-Service ArciAdmin.",
        "Look at Settings → Change history and at the ARCILOGGER-Admin entries in Event Viewer; "
        "check the service: Get-Service ArciAdmin.",
    ),
    "R20-config-change": RuleText(
        "Önemli yapılandırma değişikliği",
        "Significant configuration change",
        "Değişikliği bilinen bir yönetici yapmadıysa hemen inceleyin: Ayarlar → Değişiklik "
        "geçmişi.",
        "If no known administrator made this change, look into it at once: Settings → Change "
        "history.",
    ),
    "new-log-source": RuleText(
        "Yeni log kaynağı",
        "New log source",
        "Bilinen bir cihazsa yapılandırmaya ekleyin (DEVICE-SETUP.md bölüm 4).",
        "If it is a known device, add it to the configuration (DEVICE-SETUP.md section 4).",
    ),
    "new_source": RuleText(
        "Yeni log kaynağı",
        "New log source",
        "Bilinen bir cihazsa yapılandırmaya ekleyin (DEVICE-SETUP.md bölüm 4).",
        "If it is a known device, add it to the configuration (DEVICE-SETUP.md section 4).",
    ),
    "wrong-transport": RuleText(
        "İzin verilmeyen taşıma yolu",
        "Transport not allowed",
        "Cihaz ayarını denetleyin; taklit edilmiş bir gönderim olabilir (DEVICE-SETUP.md 1.1).",
        "Check the device setting; it may be a forged message (DEVICE-SETUP.md 1.1).",
    ),
    "ip_conflict": RuleText(
        "IP çakışması",
        "IP conflict",
        "İki cihazı ağda inceleyin.",
        "Look into the two devices on the network.",
    ),
}
UNKNOWN_RULE: Final = RuleText(
    "ARCILOGGER uyarısı",
    "ARCILOGGER alert",
    "Ayrıntılar ARCILOGGER'da: arcilogger-worker alerts list.",
    "Details in ARCILOGGER: arcilogger-worker alerts list.",
)


def rule_text(rule: str) -> RuleText:
    return RULES.get(rule, UNKNOWN_RULE)


# -- runbooks (the Security page's alert detail; docs/UYARILAR.md "Ne yapılmalı") ---------------

_STEPS: Final[dict[str, tuple[str, str]]] = {
    "R15": (
        "1. Cihazın açık ve ağa bağlı olduğunu denetleyin.\n"
        "2. Cihazın syslog ayarının ARCILOGGER sunucusunu gösterdiğini doğrulayın "
        "(DEVICE-SETUP.md).\n"
        "3. Sunucuda toplayıcının çalıştığını denetleyin: Get-Service ArciCollector.\n"
        "4. Sessiz geçen süre 5651 kayıtlarında bir boşluktur ve günün manifestinde görünür: "
        "aynı gün inceleyin. Kaynak yeniden mesaj gönderince uyarı kendiliğinden kapanır.",
        "1. Check that the device is on and connected.\n"
        "2. Check that its syslog setting points to the ARCILOGGER server (DEVICE-SETUP.md).\n"
        "3. Check that the collector runs on the server: Get-Service ArciCollector.\n"
        "4. The silent time is a gap in the 5651 records and shows in the day's manifest: look "
        "into it the same day. The alert closes by itself once the source sends again.",
    ),
    "R20-seal": (
        "1. Mühürleme sayfasında günün durumunu ve hata iletisini açın.\n"
        "2. D:\\ARCILOGGER\\logs\\core.log dosyasına bakın.\n"
        "3. Kamu SM erişimini ve kontörü denetleyin (kamusm.md).\n"
        "4. Seal görevi günü 5 dakikada bir yeniden dener; 48 saati aşan gecikme kritiktir. Gün "
        "mühürlenince uyarı kendiliğinden kapanır.",
        "1. Open the day's state and error message on the sealing page.\n"
        "2. Look at D:\\ARCILOGGER\\logs\\core.log.\n"
        "3. Check the Kamu SM connection and the credit (kamusm.md).\n"
        "4. The Seal task retries the day every 5 minutes; a delay beyond 48 hours is critical. "
        "The alert closes by itself once the day is sealed.",
    ),
    "R20-seal-config": (
        "1. Ayrıntılardaki nedeni okuyun: Kamu SM kimlik bilgileri, kamusm-roots kökleri, "
        "[tsa] zamane_jar, gizli depo anahtarı, güvenilir konumda olmayan [tsa] java ya da "
        "zamane_jar, ya da kurulumun klasörü olmayan secrets_dir / data_dir.\n"
        "2. Eksik olanı ILK-ADIMLAR.md bölüm 3'e göre tamamlayın (kamusm.md); klasör nedeni "
        "için arcilogger.toml dosyasını sunucuda denetleyin.\n"
        "3. Seal görevi 5 dakika içinde yeniden dener; sonraki başarılı mühürden sonra uyarı "
        "kendiliğinden kapanır.",
        "1. Read the cause in the details: Kamu SM credentials, kamusm-roots roots, "
        "[tsa] zamane_jar, the secret store key, a [tsa] java or zamane_jar outside a trusted "
        "location, or a secrets_dir / data_dir that is not the installation's folder.\n"
        "2. Complete what is missing (FIRST-STEPS.md section 3, kamusm.md); for the folder "
        "cause, check arcilogger.toml on the server.\n"
        "3. The Seal task retries within 5 minutes; the alert closes by itself after the next "
        "successful seal.",
    ),
    "R20-seal-skip": (
        "1. Toplayıcının çalıştığını (Get-Service ArciCollector) ve cihazların log "
        "gönderdiğini denetleyin.\n"
        "2. Sunucu saatini denetleyin (Set-ArciTime.ps1).\n"
        "3. Bu günler sonraki mühürlü günün manifestinde boşluk olarak listelenir: nedeni not "
        "olarak ekleyip uyarıyı kapatın.",
        "1. Check that the collector runs (Get-Service ArciCollector) and that the devices send "
        "logs.\n"
        "2. Check the server clock (Set-ArciTime.ps1).\n"
        "3. These days are listed as a gap in the next sealed day's manifest: add the cause as a "
        "note and close the alert.",
    ),
    "R20-clock": (
        "1. Windows Saat hizmetini ve NTP sunucusunu denetleyin: w32tm /query /status.\n"
        "2. Gerekirse Set-ArciTime.ps1 betiğini yeniden çalıştırın.\n"
        "3. Bir saat atlaması bildirildiyse etkilenen günü Mühürleme sayfasında inceleyin.\n"
        "4. Eşik içindeki sonraki saat ölçümüyle uyarı kendiliğinden kapanır.",
        "1. Check the Windows Time service and the NTP server: w32tm /query /status.\n"
        "2. Run Set-ArciTime.ps1 again if needed.\n"
        "3. If a clock step was reported, look at the affected day on the sealing page.\n"
        "4. The alert closes by itself with the next clock sample within the limit.",
    ),
    "R20-verify": (
        "1. Aynı gün inceleyin: Mühürleme sayfasında günü açın ve hata iletilerini okuyun.\n"
        "2. Günü yeniden doğrulayın (Doğrula).\n"
        "3. Hata sürerse kasaya ve replikaya dokunmadan DENETCI-KILAVUZU.md'ye göre ilerleyin.\n"
        "4. Gün yeniden doğrulanınca uyarı kendiliğinden kapanır.",
        "1. Look into it the same day: open the day on the sealing page, read the errors.\n"
        "2. Verify the day again (Doğrula).\n"
        "3. If it still fails, follow DENETCI-KILAVUZU.md without touching the vault or the "
        "replica.\n"
        "4. The alert closes by itself once the day verifies again.",
    ),
    "R20-credit": (
        "1. Kalan kontörü Mühürleme sayfasının Kontör kutusunda görün.\n"
        "2. Kamu SM'den kontör alın (kamusm.md).\n"
        "3. Credit görevi günde bir kez sorar; eşiğin üstündeki okumayla uyarı kendiliğinden "
        "kapanır.",
        "1. See the remaining credit in the sealing page's credit box.\n"
        "2. Buy credit from Kamu SM (kamusm.md).\n"
        "3. The Credit task asks once a day; a reading at or above the threshold closes the "
        "alert by itself.",
    ),
    "R20-replica": (
        "1. İkinci kopya hedefine ([replica] target) erişimi denetleyin: arcilogger-core "
        "replica --test.\n"
        "2. Ağı, kimlik bilgilerini ve paylaşımdaki boş alanı denetleyin.\n"
        "3. Başarılı bir kopyayla uyarı kendiliğinden kapanır. 'Replika farklı' (kritik) "
        "kendiliğinden kapanmaz: farkı inceleyin, sonucu not olarak ekleyip kapatın.",
        "1. Check access to the replica target ([replica] target): arcilogger-core replica "
        "--test.\n"
        "2. Check the network, the credentials and the free space on the share.\n"
        "3. A successful copy closes the alert by itself. 'Replica differs' (critical) never "
        "closes by itself: look into the difference, add the result as a note and close it.",
    ),
    "R20-retention": (
        "1. İmha tutanağını ve D:\\ARCILOGGER\\logs\\core.log dosyasını inceleyin.\n"
        "2. Reddedilen ya da yarım kalan çalışmanın nedenini giderin; imha bir sonraki "
        "çalışmada yeniden denenir.\n"
        "3. Tamamlanan imha bilgi düzeyindedir: tutanağı arşivleyip uyarıyı kapatın.",
        "1. Review the deletion record and D:\\ARCILOGGER\\logs\\core.log.\n"
        "2. Remove the cause of a refused or incomplete run; the next run tries again.\n"
        "3. A completed deletion is informational: archive the record and close the alert.",
    ),
    "R20-spool": (
        "1. Veri diskini hemen denetleyin: boş alan, donanım, dosya izinleri. Bu sürede gelen "
        "loglar kaydedilemiyor.\n"
        "2. Sorun giderilince kaç mesajın kaybolduğu ikinci bir uyarıyla bildirilir.\n"
        "3. Bu uyarı kendiliğinden kapanmaz: nedeni not olarak ekleyip kapatın.",
        "1. Check the data disk at once: free space, hardware, file rights. Logs arriving now "
        "cannot be recorded.\n"
        "2. Once it is fixed, a second alert says how many messages were lost.\n"
        "3. This alert never closes by itself: add the cause as a note and close it.",
    ),
    "R20-spool-torn": (
        "1. Elektrik kesintisinin ya da çökmenin nedenini inceleyin (baytlar değiştirilmedi).\n"
        "2. Nedeni not olarak ekleyip uyarıyı kapatın.",
        "1. Find the cause of the power cut or crash (no byte was changed).\n"
        "2. Add the cause as a note and close the alert.",
    ),
    "R20-disk": (
        "1. Hangi diskin dolduğunu ayrıntılarda görün.\n"
        "2. Boş alan açın ya da diski büyütün; ham kayıtları (spool), kasayı (vault) ve "
        "yedekleri asla elle silmeyin.\n"
        "3. %95'ten itibaren kritiktir: ham loglar kaybolabilir. Doluluk eşiğin 2 puan altına "
        "inince uyarı kendiliğinden kapanır.",
        "1. See which drive is full in the details.\n"
        "2. Free space or enlarge the disk; never delete raw logs (spool), the vault or the "
        "backups by hand.\n"
        "3. From 95 % it is critical: raw logs may be lost. The alert closes by itself 2 points "
        "below the threshold.",
    ),
    "R20-parse": (
        "1. Hangi kaynağın satırlarının ayrıştırılamadığını ayrıntılarda görün.\n"
        "2. Cihaz yazılımı ya da log biçimi değişmiş olabilir (PARSERS.md).\n"
        "3. Ham loglar etkilenmez ve mühürlenir; aramalar ve sorgular eksik kalabilir. Oran "
        "eşiğin altına inince uyarı kendiliğinden kapanır.",
        "1. See in the details which source's lines could not be parsed.\n"
        "2. The device firmware or the log format may have changed (PARSERS.md).\n"
        "3. Raw logs are not affected and are sealed; searches and lookups may miss rows. The "
        "alert closes by itself once the rate is below the threshold.",
    ),
    "R20-job": (
        "1. Hangi işin başarısız olduğunu ayrıntılarda görün.\n"
        "2. D:\\ARCILOGGER\\logs\\worker.log ve state\\worker-status.json dosyalarına bakın.\n"
        "3. İşin bir sonraki başarılı çalışmasıyla uyarı kendiliğinden kapanır.",
        "1. See in the details which job fails.\n"
        "2. Look at D:\\ARCILOGGER\\logs\\worker.log and state\\worker-status.json.\n"
        "3. The next successful run of the job closes the alert by itself.",
    ),
    "R20-backup": (
        "1. state\\backup\\last.json ve logs\\backup.log dosyalarına bakın.\n"
        "2. Yedekleme görevini (\\ARCILOGGER\\Backup) ve yedek klasöründeki boş alanı denetleyin "
        "(ILK-ADIMLAR.md bölüm 10, YEDEKLEME.md).\n"
        "3. Bir sonraki başarılı yedekle uyarı kendiliğinden kapanır.",
        "1. Look at state\\backup\\last.json and logs\\backup.log.\n"
        "2. Check the backup task (\\ARCILOGGER\\Backup) and the free space of the backup folder "
        "(FIRST-STEPS.md section 10, BACKUP.md).\n"
        "3. The next successful backup closes the alert by itself.",
    ),
    "R20-report": (
        "1. Raporlar sayfasında çalıştırmanın hatasını ve alıcıların teslim durumunu açın.\n"
        "2. Oluşturma hatasında logs\\worker.log dosyasına, teslim hatasında [smtp] ayarlarına ve "
        "alıcı adreslerine bakın (KULLANIM-RAPORLARI.md).\n"
        "3. Sorunlu alıcılara bir rapor ulaşınca ya da sonraki rapor oluşturulunca uyarı "
        "kendiliğinden kapanır.",
        "1. On the Raporlar page open the run's error and the recipients' delivery status.\n"
        "2. For a rendering error look at logs\\worker.log, for a delivery error at [smtp] and the "
        "recipient addresses (USAGE-REPORTS.md).\n"
        "3. The alert closes by itself once the failing recipients get a report or the next "
        "report renders.",
    ),
    "R20-license": (
        "1. Ayarlar → Lisans sayfasında durumu ve tarihleri açın; hangi ücretli modüllerin "
        "kapandığı ya da kapanacağı ayrıntılardadır.\n"
        "2. Deneme ya da süreli lisans bitiyorsa, süresi dolduysa ya da bu sürüm bakım süresinden "
        "sonra yayımlandıysa: sayfadaki istek kodunu lisans verene gönderin, gelen .lic dosyasını "
        "aynı sayfadan yükleyin (LISANS.md bölüm 4).\n"
        "3. Lisans doğrulanamıyor ya da bu sunucu için değilse dosyayı yeniden yükleyin; sunucu "
        "değiştiyse yeni sunucunun istek koduyla yeni lisans isteyin (LISANS.md bölüm 9).\n"
        "4. 5651 işlevleri (toplama, mühürleme, sorgu, kanıt paketleri, uyarılar) hiçbir lisans "
        "durumunda durmaz. Lisans geçerli olunca uyarı kendiliğinden kapanır; kapatırsanız aynı "
        "durum için yeniden açılmaz.",
        "1. Open the state and the dates under Settings → Licence; the details say which paid "
        "modules are off or are about to go off.\n"
        "2. When the trial or a time-limited licence ends, has ended, or this version was "
        "released after the maintenance period: send the request code of that page to the "
        "licensor and upload the .lic you receive on the same page (LICENSING.md section 4).\n"
        "3. When the licence cannot be verified or is not for this server, upload the file "
        "again; after a server change ask for a new licence with the new server's request code "
        "(LICENSING.md section 9).\n"
        "4. The 5651 functions (collection, sealing, lookups, evidence packages, alerts) never "
        "stop in any licence state. The alert closes by itself once the licence is valid; if you "
        "close it, it is not raised again for the same situation.",
    ),
    "R20-license-limit": (
        "1. Ayarlar → Lisans sayfasında sınırı ve kullanımı görün.\n"
        "2. Kullanıcılar: kullanılmayan hesapları devre dışı bırakın ya da daha yüksek sınırlı "
        "bir lisans isteyin; mevcut kullanıcılar çalışmaya ve oturum açmaya devam eder.\n"
        "3. Log kaynakları: kayıtlar toplanmaya ve mühürlenmeye devam eder; artık log "
        "göndermeyen bir kaynak son logundan 30 gün sonra sayılmaz (LISANS.md bölüm 6).\n"
        "4. Sınırın altına inince uyarı kendiliğinden kapanır; kapatırsanız bu aşım için "
        "yeniden açılmaz.",
        "1. See the limit and the use under Settings → Licence.\n"
        "2. Users: deactivate accounts no longer used or ask for a licence with a higher limit; "
        "existing users keep working and signing in.\n"
        "3. Log sources: logs are still collected and sealed; a source that no longer sends stops "
        "counting 30 days after its last log (LICENSING.md section 6).\n"
        "4. The alert closes by itself once the use is within the limit; if you close it, it is "
        "not raised again for this crossing.",
    ),
    "R20-license-maintenance": (
        "1. Ayarlar → Lisans sayfasında bakımın bitiş tarihini görün.\n"
        "2. Bakımı yenilemek için lisans verenle iletişime geçin ve gelen .lic dosyasını aynı "
        "sayfadan yükleyin (LISANS.md bölüm 7).\n"
        "3. Bilgi düzeyindedir: lisans ve kurulu sürüm geçerli kalır; bakım bittikten sonra "
        "yayımlanan sürümler ücretli modülleri açmaz. Yenilenen bakımla uyarı kendiliğinden "
        "kapanır.",
        "1. See the end of maintenance under Settings → Licence.\n"
        "2. To renew maintenance contact the licensor and upload the .lic you receive on the "
        "same page (LICENSING.md section 7).\n"
        "3. Information only: the licence and the installed version stay valid; versions released "
        "after the end of maintenance do not switch the paid modules on. Renewed maintenance "
        "closes the alert by itself.",
    ),
    "R20-admin": (
        "1. Ayrıntılardaki nedeni okuyun: yönetim hizmeti yanıt vermiyor, bir istek reddedildi "
        "(geçersiz imza ya da başka bir kurulum: olası kurcalama), bir değişiklik geri alındı, "
        "elle müdahale gerekiyor ya da güvenlik duvarı, saat veya Zamane adımı uygulanamadı.\n"
        "2. Hizmeti denetleyin (Get-Service ArciAdmin) ve Olay Görüntüleyicisi → Windows "
        "Günlükleri → Uygulama altında ARCILOGGER-Admin kaynağının kayıtlarına bakın.\n"
        "3. Reddedilen istek için Ayarlar → Değişiklik geçmişi sayfasında ve denetim kaydında "
        "isteği kimin hazırladığını inceleyin; beklenmeyen bir imza ya da kurulum hatası varsa "
        "sunucuya erişimi denetleyin.\n"
        "4. Elle müdahale gerekiyorsa sürüm numarası ve adımlar isteğin ayrıntısındadır "
        "(sunucu konsolunda: python -I -m arcilogger_core.admin versions restore <n>).\n"
        "5. Hizmet yeniden yanıt verince 'yanıt vermiyor' uyarısı kendiliğinden kapanır; "
        "diğerlerini sonucu not olarak ekleyip kapatın.",
        "1. Read the cause in the details: the administration service is not answering, a "
        "request was rejected (invalid signature or another installation: possible tampering), "
        "a change was rolled back, manual action is needed, or the firewall, time or Zamane step "
        "could not be applied.\n"
        "2. Check the service (Get-Service ArciAdmin) and the entries of the source "
        "ARCILOGGER-Admin under Event Viewer → Windows Logs → Application.\n"
        "3. For a rejected request, look at who prepared it under Settings → Change history and "
        "in the audit log; for an unexpected signature or installation error check who can "
        "reach the server.\n"
        "4. When manual action is needed, the version number and the steps are in the request's "
        "details (on the server console: python -I -m arcilogger_core.admin versions restore "
        "<n>).\n"
        "5. The 'not answering' alert closes by itself once the service answers again; add the "
        "result of the others as a note and close them.",
    ),
    "R20-config-change": (
        "1. Ayrıntılarda hangi bölümün ve hangi alanların değiştiğini görün.\n"
        "2. Ayarlar → Değişiklik geçmişi sayfasında isteği, denetim kaydında kimin yaptığını "
        "inceleyin.\n"
        "3. Değişikliği bilinen bir yönetici yapmadıysa hemen harekete geçin: bekletilen bir "
        "değişikliği (saklama süresi, replika) süresi dolmadan İptal et ile durdurun ya da Geri "
        "al ile önceki ayarlara dönün; ilgili kullanıcının parolasını değiştirin.\n"
        "4. Bilinen bir yöneticinin değişikliğiyse not ekleyip uyarıyı kapatın.",
        "1. See in the details which section and which fields changed.\n"
        "2. Look at the request under Settings → Change history and at who made it in the audit "
        "log.\n"
        "3. If no known administrator made the change, act at once: stop a held change "
        "(retention, replica) with Cancel before its hold ends, or go back to the previous "
        "settings with Revert; change the password of the user concerned.\n"
        "4. If a known administrator made it, add a note and close the alert.",
    ),
    "R20-email": (
        "1. [smtp] ayarlarını ve smtp.password gizli değerini denetleyin.\n"
        "2. Deneme e-postası gönderin: arcilogger-worker alerts test-email --to <adres>.\n"
        "3. Sıkı giden mod açıksa güvenlik duvarı kuralını denetleyin (Set-ArciFirewall.ps1).\n"
        "4. Adı geçen alıcılara bir sonraki e-posta gidince uyarı kendiliğinden kapanır.",
        "1. Check [smtp] and the secret smtp.password.\n"
        "2. Send a test e-mail: arcilogger-worker alerts test-email --to <address>.\n"
        "3. With strict outbound mode, check the firewall rule (Set-ArciFirewall.ps1).\n"
        "4. The next e-mail that reaches the recipients it names closes the alert by itself.",
    ),
    "new-log-source": (
        "1. Göndericinin adresini ayrıntılarda görün ve hangi cihaz olduğunu belirleyin.\n"
        "2. Bilinen bir cihazsa arcilogger.toml'daki [[sources]] listesine ekleyin "
        "(DEVICE-SETUP.md bölüm 4).\n"
        "3. Bilinmiyorsa güvenlik duvarında engelleyin; sonucu not olarak ekleyip uyarıyı "
        "kapatın.",
        "1. See the sender's address in the details and find out which device it is.\n"
        "2. If it is a known device, add it to [[sources]] in arcilogger.toml (DEVICE-SETUP.md "
        "section 4).\n"
        "3. Otherwise block it in the firewall; add the result as a note and close the alert.",
    ),
    "wrong-transport": (
        "1. Cihazın syslog ayarını denetleyin ([[sources]] transport, DEVICE-SETUP.md 1.1).\n"
        "2. Ayar doğruysa taklit edilmiş bir gönderim olabilir: göndericiyi ağda inceleyin.\n"
        "3. Bu yoldan gelen mesajlar kaydedilmedi; sonucu not olarak ekleyip uyarıyı kapatın.",
        "1. Check the device's syslog setting ([[sources]] transport, DEVICE-SETUP.md 1.1).\n"
        "2. If it is right, the message may be forged: look into the sender on the network.\n"
        "3. Messages over that transport were not recorded; add the result as a note and close "
        "the alert.",
    ),
    "ip_conflict": (
        "1. İki MAC adresini ayrıntılarda ve Cihazlar sayfasında görün.\n"
        "2. Sabit IP verilmiş ya da yanlış yapılandırılmış bir cihaz olabilir: iki cihazı ağda "
        "inceleyin (CORRELATION.md).\n"
        "3. Çakışma süresince o IP için 5651 sorguları aday listesi verebilir. Bir yeniden "
        "hesaplama çakışmayı artık görmezse uyarı kendiliğinden kapanır.",
        "1. See both MAC addresses in the details and on the devices page.\n"
        "2. A device with a static IP or a wrong configuration may be the cause: look into both "
        "devices on the network (CORRELATION.md).\n"
        "3. During the conflict, 5651 lookups of that IP may give candidates. The alert closes "
        "by itself when a rebuild no longer sees the conflict.",
    ),
}
#: Rule → (Turkish, English): what to do, step by step (one step per line). Shown on the web
#: only (the e-mail carries the one-line ``action_*``); the same advice as docs/UYARILAR.md.
#: The ingest's database alert of a new sender (``new_source``) is the collector's problem.
RUNBOOKS: Final[Mapping[str, tuple[str, str]]] = {
    **_STEPS,
    "new_source": _STEPS["new-log-source"],
}
UNKNOWN_RUNBOOK: Final = (
    "Başlığı ve ayrıntıları inceleyin; kural kodunun anlamı docs/UYARILAR.md'dedir.",
    "Review the title and the details; docs/ALERTS.md explains the rule code.",
)


def runbook(rule: str) -> tuple[str, str]:
    """What to do about an alert of ``rule``, Turkish and English (step by step)."""
    return RUNBOOKS.get(rule, UNKNOWN_RUNBOOK)


# -- whitelisted facts ----------------------------------------------------------------------------


def source_name(details: Mapping[str, Any]) -> str | None:
    value = details.get("source")
    return value if isinstance(value, str) and _SOURCE.match(value) else None


def _int(details: Mapping[str, Any], key: str) -> int | None:
    value = details.get(key)
    return value if type(value) is int else None


def _day(value: Any) -> str | None:
    try:
        return format_date(date.fromisoformat(str(value)))
    except ValueError:
        return None


def _moment(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return format_datetime(parsed) if parsed.tzinfo is not None else None


def _ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def _days(value: Any, limit: int = 10) -> list[str]:
    days = [shown for item in value if (shown := _day(item))] if isinstance(value, list) else []
    return days[:limit] + (["…"] if len(days) > limit else [])


Facts = tuple[str, str]


def _r15(d: Mapping[str, Any]) -> Facts | None:
    source, minutes = source_name(d), _int(d, "threshold_minutes")
    if source is None:
        return None
    if d.get("never_seen") is True:
        return (f"{source} kaynağından hiç mesaj gelmedi.", f"No message from {source} ever.")
    if minutes is None:
        return None
    last = _moment(d.get("last_seen"))
    tail_tr, tail_en = (f" Son mesaj: {last}.", f" Last message: {last}.") if last else ("", "")
    return (
        f"{source} kaynağından {minutes} dakikadan uzun süredir mesaj gelmedi.{tail_tr}",
        f"No message from {source} for more than {minutes} minutes.{tail_en}",
    )


def _seal(d: Mapping[str, Any]) -> Facts | None:
    day = _day(d.get("day"))
    if day is None:
        return None
    if d.get("overdue") is True:
        return (
            f"{day} günü hâlâ mühürlenmedi.",
            f"{day} is still not sealed.",
        )
    attempts = _int(d, "attempts")
    if attempts is None:
        return (f"{day} günü mühürlenemedi.", f"{day} could not be sealed.")
    return (
        f"{day} günü mühürlenemedi ({attempts}. deneme).",
        f"{day} could not be sealed (attempt {attempts}).",
    )


def _seal_config(d: Mapping[str, Any]) -> Facts | None:
    cause = d.get("cause")
    return CONFIG_CAUSES.get(cause) if isinstance(cause, str) else None


def _clock(d: Mapping[str, Any]) -> Facts | None:
    offset = _int(d, "offset_us")
    if offset is None:
        return None
    ms = format_number(offset / 1000, 0, "tr"), format_number(offset / 1000, 0, "en")
    return (f"Saat sapması {ms[0]} ms.", f"Clock offset {ms[1]} ms.")


def _verify(d: Mapping[str, Any]) -> Facts | None:
    days = _days(d.get("failed_days"))
    if not days:
        return None
    return (
        f"Doğrulanamayan gün(ler): {', '.join(days)}.",
        f"Day(s) that do not verify: {', '.join(days)}.",
    )


def _credit(d: Mapping[str, Any]) -> Facts | None:
    credits, threshold = _int(d, "credits"), _int(d, "threshold")
    if credits is None or threshold is None:
        return None
    return (
        f"Kalan kontör {format_number(credits, 0, 'tr')} (eşik {format_number(threshold, 0)}).",
        f"Credit left {format_number(credits, 0, 'en')} (threshold "
        f"{format_number(threshold, 0, 'en')}).",
    )


def _replica(d: Mapping[str, Any]) -> Facts | None:
    hours = _int(d, "threshold_hours")
    if d.get("stale") is not True or hours is None:
        return None
    days = _days(d.get("missing_days"))
    if days:
        return (
            f"{hours} saatten uzun süredir replikada olmayan mühürlü gün(ler): {', '.join(days)}.",
            f"Sealed day(s) without a replica copy for more than {hours} hours: {', '.join(days)}.",
        )
    last = _moment(d.get("last_success"))
    if last is None:
        return (
            f"Replika hiç başarılı olmadı ({hours} saatten uzun süredir).",
            f"The replica never succeeded (for more than {hours} hours).",
        )
    return (
        f"Son başarılı kopya {hours} saatten eski ({last}).",
        f"The last successful copy is older than {hours} hours ({last}).",
    )


def _disk(d: Mapping[str, Any]) -> Facts | None:
    drive = d.get("drive")
    used, threshold = _int(d, "used_percent"), _int(d, "threshold_percent")
    if not isinstance(drive, str) or not _DRIVE.match(drive) or used is None or threshold is None:
        return None
    return (
        f"{drive} diski %{used} dolu (eşik %{threshold}).",
        f"Drive {drive} is {used}% full (threshold {threshold}%).",
    )


def _parse(d: Mapping[str, Any]) -> Facts | None:
    source, failed, total = source_name(d), _int(d, "failed"), _int(d, "total")
    if source is None or failed is None or total is None:
        return None
    return (
        f"{source}: son bir saatte {format_number(total, 0, 'tr')} satırın "
        f"{format_number(failed, 0, 'tr')} tanesi ayrıştırılamadı.",
        f"{source}: {format_number(failed, 0, 'en')} of {format_number(total, 0, 'en')} lines "
        "of the last hour could not be parsed.",
    )


def _job(d: Mapping[str, Any]) -> Facts | None:
    job, failures = d.get("job"), _int(d, "failures")
    if not isinstance(job, str) or not _JOB.match(job) or failures is None:
        return None
    return (
        f"{job} işi art arda {failures} kez başarısız oldu.",
        f"Job {job} failed {failures} times in a row.",
    )


def _backup(d: Mapping[str, Any]) -> Facts | None:
    case = d.get("case")
    hours = _int(d, "threshold_hours")
    if d.get("key") == "prune":  # raised by the Backup task: the dump itself is fine
        return (
            "Eski veritabanı yedekleri silinemedi.",
            "Old database backups could not be deleted.",
        )
    if case == "failed":
        return ("Son veritabanı yedeği başarısız.", "The last database backup failed.")
    if case == "stale" and hours is not None:
        return (
            f"Son başarılı veritabanı yedeği {hours} saatten eski.",
            f"The last successful database backup is older than {hours} hours.",
        )
    if case == "missing":
        return ("Henüz veritabanı yedeği yok.", "No database backup yet.")
    if case == "unreadable":
        return ("Yedek durum dosyası okunamıyor.", "The backup state file is unreadable.")
    return None


def _report(d: Mapping[str, Any]) -> Facts | None:
    runs = [item for item in d.get("runs") or [] if type(item) is int][-5:]
    recipients = d.get("recipients")
    count = len(recipients) if isinstance(recipients, list) else 0
    if not runs:
        return None
    numbers = ", ".join(str(item) for item in runs)
    if d.get("render_failed") is True and not count:
        return (
            f"Kullanım raporu oluşturulamadı (çalıştırma {numbers}).",
            f"A usage report could not be made (run {numbers}).",
        )
    return (
        f"Kullanım raporu {count} alıcıya gönderilemedi (çalıştırma {numbers}).",
        f"A usage report could not be sent to {count} recipient(s) (run {numbers}).",
    )


#: Why ``R20-license`` reports the paid modules off (``details.case``: the licence state, or the
#: trial's clock problem): the start of its sentence; ``{until}`` is ``details.until``.
LICENSE_OFF: Final[Mapping[str, tuple[str, str]]] = {
    "trial_expired": ("Deneme süresi doldu", "The trial has ended"),
    "trial_clock_inconsistent": (
        "Deneme başlangıcı sistem saatinden ileride, deneme süresi kullanılamıyor",
        "The trial start lies after the current time, the trial is unavailable",
    ),
    "invalid": ("Lisans dosyası doğrulanamadı", "The licence file cannot be verified"),
    "wrong_server": ("Lisans bu sunucu için değil", "The licence is not for this server"),
    "expired": ("Lisansın süresi {until} tarihinde doldu", "The licence expired on {until}"),
    "version_not_covered": (
        "Bu sürüm bakım süresinden ({until} tarihine kadar) sonra yayımlandı",
        "This version was released after the maintenance period (until {until})",
    ),
}


def _modules(value: Any) -> tuple[str, str] | None:
    """The names of the licence modules ``value`` lists (keys of the registry only)."""
    if not isinstance(value, list):
        return None
    try:  # loaded for a licence alert only: without the licence package there are no names
        from arcilogger_core.license.modules import module
    except Exception:
        return None
    names = []
    for key in value:
        try:
            item = module(key)
        except (KeyError, TypeError):
            continue
        names.append((item.name_tr, item.name_en))
    if not names:
        return None
    return ", ".join(tr for tr, _ in names), ", ".join(en for _, en in names)


def _license(d: Mapping[str, Any]) -> Facts | None:
    case, days, modules = d.get("case"), _int(d, "days_left"), _modules(d.get("modules"))
    until = _day(d.get("until")) or _moment(d.get("until"))
    if modules is None:
        return None
    if case == "trial_ending" and days is not None and until:
        return (
            f"Deneme süresinin bitmesine {days} gün kaldı ({until}); ardından ücretli modüller "
            f"kapanır: {modules[0]}. 5651 işlevleri çalışmaya devam eder.",
            f"{days} day(s) left of the trial ({until}); then the paid modules go off: "
            f"{modules[1]}. The 5651 functions keep working.",
        )
    if case == "expiring" and days is not None and until:
        left_tr, left_en = (
            (f"{days} gün kaldı", f"{days} day(s) left")
            if days
            else ("bugün son gün", "today is the last day")
        )
        return (
            f"Lisans {until} tarihine kadar geçerli ({left_tr}); ardından ücretli modüller "
            f"kapanır: {modules[0]}. 5651 işlevleri çalışmaya devam eder.",
            f"The licence is valid until {until} ({left_en}); then the paid modules go off: "
            f"{modules[1]}. The 5651 functions keep working.",
        )
    texts = LICENSE_OFF.get(case) if isinstance(case, str) else None
    if texts is None or ("{until}" in texts[0] and not until):
        return None
    head_tr, head_en = (text.format(until=until) for text in texts)
    return (
        f"{head_tr}: ücretli modüller kapalı ({modules[0]}). 5651 işlevleri çalışmaya devam "
        "ediyor.",
        f"{head_en}: the paid modules are off ({modules[1]}). The 5651 functions keep working.",
    )


def _license_limit(d: Mapping[str, Any]) -> Facts | None:
    limit, used, top = d.get("limit"), _int(d, "used"), _int(d, "max")
    unconfigured = _int(d, "unconfigured")
    if used is None or top is None:
        return None
    if limit == "users":
        return (
            f"Kullanıcı sayısı lisans sınırını aşıyor ({used}/{top}): yeni kullanıcı eklenemez. "
            "5651 işlevleri etkilenmez.",
            f"The number of users exceeds the licence limit ({used}/{top}): no new user can be "
            "added. The 5651 functions are not affected.",
        )
    if limit != "log_sources":
        return None
    tail_tr, tail_en = (
        (f"; {unconfigured} yapılandırılmamış", f"; {unconfigured} unconfigured")
        if unconfigured
        else ("", "")
    )
    return (
        f"Log kaynağı sayısı lisans sınırını aşıyor ({used}/{top}{tail_tr}). Kayıtlar "
        "toplanmaya ve mühürlenmeye devam ediyor.",
        f"The number of log sources exceeds the licence limit ({used}/{top}{tail_en}). Logs are "
        "still collected and sealed.",
    )


def _license_maintenance(d: Mapping[str, Any]) -> Facts | None:
    days, until = _int(d, "days_left"), _day(d.get("until"))
    if days is None or until is None:
        return None
    if days < 0:
        return (
            f"Bakım süresi doldu ({until}): yeni sürümler ve destek için bakımı yenileyin. "
            "Lisans ve kurulu sürüm geçerli kalır.",
            f"Maintenance has ended ({until}): renew it for new versions and support. The "
            "licence and the installed version stay valid.",
        )
    when_tr, when_en = (
        (f"{days} gün sonra bitiyor", f"ends in {days} day(s)")
        if days
        else ("bugün bitiyor", "ends today")
    )
    return (
        f"Bakım süresi {when_tr} ({until}): yeni sürümler ve destek için yenileyin. Lisans "
        "geçerli kalır.",
        f"Maintenance {when_en} ({until}): renew it for new versions and support. The licence "
        "stays valid.",
    )


#: The sections of the browser administration (``R20-config-change`` ``details.section``,
#: ``arcilogger_core.admin.contract.SECTIONS``) as the web interface names them.
CONFIG_SECTIONS: Final[Mapping[str, tuple[str, str]]] = {
    "general": ("Genel", "General"),
    "tsa": ("Kamu SM ve zaman damgası", "Kamu SM and time stamps"),
    "smtp": ("E-posta (SMTP)", "E-mail (SMTP)"),
    "replica": ("Replika", "Replica"),
    "retention": ("Saklama ve imha", "Retention"),
    "backup": ("Veritabanı yedeği", "Database backup"),
    "sources": ("Log kaynakları", "Log sources"),
}
#: A configuration path (``smtp.host``, ``sources.switch-2.enabled``: a field name, never a
#: value; ``arcilogger_core.admin.contract.ConfigPath``).
_CONFIG_PATH = re.compile(r"^[a-z_]+(?:\.[a-z0-9][a-z0-9_-]*)*$")
#: Most field names listed in an ``R20-config-change`` sentence.
PATHS_LISTED: Final = 10


def _config_change(d: Mapping[str, Any]) -> Facts | None:
    """``R20-config-change``: the section and the names of the fields that changed (ADR-0014:
    no value, no user name)."""
    section = d.get("section")
    names = CONFIG_SECTIONS.get(section) if isinstance(section, str) else None
    if names is None:
        return None
    raw = d.get("paths")
    paths = [
        path
        for path in (raw if isinstance(raw, list) else [])
        if isinstance(path, str) and len(path) <= 200 and _CONFIG_PATH.match(path)
    ]
    listed = ", ".join(paths[:PATHS_LISTED]) + (", …" if len(paths) > PATHS_LISTED else "")
    tail = f" ({listed})" if listed else ""
    return (
        f"Yapılandırma değişti: {names[0]}{tail}.",
        f"Configuration changed: {names[1]}{tail}.",
    )


def _admin(d: Mapping[str, Any]) -> Facts | None:
    """``R20-admin``: the agent's heartbeat (the worker's check), else the outcome code of the
    agent or ApplySystem with its own text (:data:`arcilogger_core.admin.messages.MESSAGES`), or
    the warning code of what ApplySystem left out (``source_outside_policy``, ``WARNINGS``)."""
    if d.get("case") == "heartbeat":
        minutes = _int(d, "minutes")
        silent_tr, silent_en = (
            ("hiç yanıt vermedi", "has never answered")
            if minutes is None
            else (f"{minutes} dakikadır yanıt vermiyor", f"has not answered for {minutes} minutes")
        )
        return (
            f"Yönetim hizmeti (ArciAdmin) {silent_tr}: tarayıcıdan yapılan değişiklikler "
            "uygulanmıyor. 5651 kayıtlarının toplanması ve mühürlenmesi etkilenmez.",
            f"The administration service (ArciAdmin) {silent_en}: changes made in the browser "
            "are not applied. Collecting and sealing the 5651 records is not affected.",
        )
    code = d.get("code")
    if not isinstance(code, str):
        return None
    return ADMIN_MESSAGES.get(code) or ADMIN_WARNINGS.get(code)


def _sender(d: Mapping[str, Any]) -> Facts | None:
    address = _ip(d.get("address"))
    if address is None:
        return None
    return (f"Gönderici adresi: {address}.", f"Sender address: {address}.")


def _conflict(d: Mapping[str, Any]) -> Facts | None:
    address = _ip(d.get("ip"))
    if address is None:
        return None
    return (f"Çakışan IP adresi: {address}.", f"Conflicting IP address: {address}.")


DESCRIBERS: Final[Mapping[str, Callable[[Mapping[str, Any]], Facts | None]]] = {
    "R15": _r15,
    "R20-seal": _seal,
    "R20-seal-config": _seal_config,
    "R20-clock": _clock,
    "R20-verify": _verify,
    "R20-credit": _credit,
    "R20-replica": _replica,
    "R20-disk": _disk,
    "R20-parse": _parse,
    "R20-job": _job,
    "R20-backup": _backup,
    "R20-report": _report,
    "R20-license": _license,
    "R20-license-limit": _license_limit,
    "R20-license-maintenance": _license_maintenance,
    "R20-admin": _admin,
    "R20-config-change": _config_change,
    "new-log-source": _sender,
    "new_source": _sender,
    "wrong-transport": _sender,
    "ip_conflict": _conflict,
}
GENERIC: Final = (
    "Ayrıntılar ARCILOGGER'da (e-posta günlük içeriği taşımaz).",
    "Details in ARCILOGGER (the e-mail carries no log content).",
)


def facts(rule: str, details: Mapping[str, Any]) -> Facts | None:
    """One short Turkish and English sentence from whitelisted facts, ``None`` without one."""
    describer = DESCRIBERS.get(rule)
    return describer(details) if describer is not None else None


def describe(rule: str, details: Mapping[str, Any]) -> Facts:
    """One short Turkish and English sentence about the alert, from whitelisted facts only."""
    return facts(rule, details) or GENERIC
